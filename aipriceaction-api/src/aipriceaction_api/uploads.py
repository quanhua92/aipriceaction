"""Persistent Notes markdown upload routes used by the sibling web app."""

import asyncio
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import Response

from .responses import RustJSONResponse as JSONResponse

MAX_MARKDOWN_SIZE = 5 * 1024 * 1024
MARKDOWN_FILENAME = "content.md"
ALLOWED_EXTENSIONS = {"md", "markdown", "txt"}
_INVALID_FILENAME = re.compile(r'[<>:"|?*\\/\x00-\x1f\x7f-\x9f]')


def _error(status: int, message: str):
    return JSONResponse({"success": False, "error": message}, status_code=status)


def _session_id(value: str) -> tuple[str | None, str | None]:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return None, "Invalid session_id format"
    return str(parsed), None


def _safe_filename(value: str) -> str:
    return _INVALID_FILENAME.sub("_", value).lstrip(".")[:200]


def _session_dir(root: Path | str, session_id: str) -> Path:
    return _safe_directory(root, session_id)


def _safe_directory(root: Path | str, *parts: str, create=False) -> Path:
    if os.fspath(root) == "":
        # Rust's empty PathBuf fails when canonicalized during safe path checks.
        raise OSError("Empty upload storage path")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    anchor = root.resolve(strict=True)
    current = anchor
    for part in parts:
        current = current / part
        if create:
            try:
                current.mkdir()
            except FileExistsError:
                pass
        try:
            info = current.lstat()
        except FileNotFoundError:
            return current
        if not stat.S_ISDIR(info.st_mode):
            raise OSError("Upload storage path is not a directory")
        resolved = current.resolve(strict=True)
        if not resolved.is_relative_to(anchor):
            raise OSError("Upload storage path escapes its root")
        current = resolved
    return current


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Upload storage path is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(fd)


def _secret_digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _valid_secret(metadata: dict, secret: str) -> bool:
    expected = metadata.get("secret_sha256")
    # Accept existing sessions written by the dormant Rust uploader.
    if expected is None and isinstance(metadata.get("secret"), str):
        expected = _secret_digest(metadata["secret"])
    return isinstance(expected, str) and hmac.compare_digest(expected, _secret_digest(secret))


def _read_metadata(path: Path) -> dict | None:
    try:
        value = json.loads(_read_regular_file(path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _create_metadata(session: Path, session_id: str, secret: str) -> bool:
    """Publish complete metadata atomically; return False if another writer won."""
    session.mkdir(parents=True, exist_ok=True)
    target = session / "metadata.json"
    record = {
        "session_id": session_id,
        "secret_sha256": _secret_digest(secret),
        "is_public": True,
        "created_at": datetime.now(UTC).isoformat(),
    }
    fd, temp_name = tempfile.mkstemp(prefix=".metadata-", dir=session)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_name, target)
            return True
        except FileExistsError:
            return False
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _storage_usage(root: Path) -> tuple[int, int]:
    if not root.exists():
        return 0, 0
    total = 0
    sessions = 0
    for directory in root.iterdir():
        try:
            info = directory.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or directory.name.startswith("."):
            continue
        if (directory / "metadata.json").is_file():
            sessions += 1
        for path in directory.rglob("*"):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and path.name != "metadata.json":
                try:
                    total += info.st_size
                except FileNotFoundError:
                    continue
    return total, sessions


@asynccontextmanager
async def _quota_guard(root: Path | str):
    """Coordinate quota checks across API processes sharing this volume."""
    if os.fspath(root) == "":
        # Let the route's normal safe-path check return Rust's 500 response.
        yield
        return
    root = Path(root)
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(root / ".quota.lock", flags, 0o600)
    try:
        await asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            await asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def router_for(settings) -> APIRouter:
    root = settings.uploads_dir
    router = APIRouter(default_response_class=JSONResponse)
    session_locks: dict[str, asyncio.Lock] = {}
    lock_users: dict[str, int] = {}
    registry_lock = asyncio.Lock()
    quota_lock = asyncio.Lock()

    @asynccontextmanager
    async def serialized(session_id: str):
        async with registry_lock:
            lock = session_locks.setdefault(session_id, asyncio.Lock())
            lock_users[session_id] = lock_users.get(session_id, 0) + 1
        try:
            async with lock:
                yield
        finally:
            async with registry_lock:
                lock_users[session_id] -= 1
                if lock_users[session_id] == 0:
                    session_locks.pop(session_id, None)
                    lock_users.pop(session_id, None)

    @router.post("/upload/markdown")
    async def upload_markdown(
        session_id: str = Query(...),
        secret: str = Query(...),
        files: list[UploadFile] = File(..., alias="file"),  # noqa: B008
    ):
        normalized_id, error = _session_id(session_id)
        if error:
            return _error(400, error)
        assert normalized_id is not None
        if len(secret) < 8:
            return _error(400, "Secret must be at least 8 characters")
        if len(files) != 1:
            return _error(400, "Exactly one markdown file is required")

        file = files[0]
        original_name = file.filename or ""
        stored_name = _safe_filename(original_name)
        if stored_name != MARKDOWN_FILENAME:
            return _error(400, f"Expected filename: {MARKDOWN_FILENAME}")
        extension = stored_name.rsplit(".", 1)[-1].lower()
        if extension not in ALLOWED_EXTENSIONS:
            return _error(400, "Invalid file extension. Expected: md, markdown, txt")
        data = await file.read(MAX_MARKDOWN_SIZE + 1)
        if len(data) > MAX_MARKDOWN_SIZE:
            return _error(413, "File size exceeds 5MB limit")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return _error(415, "Invalid file type. Expected markdown/text content")

        # The web application creates one immutable content.md per session.
        # Serialize writes/deletes and reserve quota before publishing metadata.
        async with quota_lock:
            async with _quota_guard(root), serialized(normalized_id):
                try:
                    session = _session_dir(root, normalized_id)
                except OSError:
                    return _error(500, "Invalid upload storage path")
                metadata_path = session / "metadata.json"
                metadata = _read_metadata(metadata_path) if metadata_path.exists() else None
                if metadata_path.exists():
                    if metadata is None:
                        return _error(500, "Failed to read session metadata")
                    if not _valid_secret(metadata, secret):
                        return _error(403, "Invalid secret for this session")
                    markdown_dir = session / "markdown"
                    if markdown_dir.exists():
                        try:
                            markdown_dir = _safe_directory(root, normalized_id, "markdown")
                        except OSError:
                            return _error(500, "Invalid upload storage path")
                        if any(markdown_dir.iterdir()):
                            return _error(409, f"File already exists: {MARKDOWN_FILENAME}")

                current_bytes, session_count = await asyncio.to_thread(_storage_usage, root)
                if current_bytes + len(data) > settings.uploads_max_bytes:
                    return _error(507, "Notes storage quota exceeded")
                if metadata is None and session_count >= settings.uploads_max_sessions:
                    return _error(507, "Notes session limit reached")

                try:
                    session = _safe_directory(root, normalized_id, create=True)
                    markdown_dir = _safe_directory(root, normalized_id, "markdown", create=True)
                except OSError:
                    return _error(500, "Invalid upload storage path")
                target = markdown_dir / MARKDOWN_FILENAME
                fd, temp_name = tempfile.mkstemp(prefix=".upload-", dir=markdown_dir)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    try:
                        os.link(temp_name, target)
                    except FileExistsError:
                        return _error(409, f"File already exists: {MARKDOWN_FILENAME}")

                    if metadata is None and not _create_metadata(session, normalized_id, secret):
                        target.unlink(missing_ok=True)
                        current = _read_metadata(metadata_path)
                        if current is None or not _valid_secret(current, secret):
                            return _error(403, "Invalid secret for this session")
                        return _error(409, f"File already exists: {MARKDOWN_FILENAME}")
                except OSError:
                    target.unlink(missing_ok=True)
                    if metadata is None:
                        shutil.rmtree(session, ignore_errors=True)
                    return _error(500, "Failed to save file")
                finally:
                    Path(temp_name).unlink(missing_ok=True)

        return {
            "success": True,
            "session_id": normalized_id,
            "files": [
                {
                    "original_name": original_name,
                    "stored_name": MARKDOWN_FILENAME,
                    "size_bytes": len(data),
                    "content_type": "text/markdown; charset=utf-8",
                    "url": f"/uploads/{normalized_id}/markdown/{MARKDOWN_FILENAME}",
                }
            ],
        }

    @router.get("/uploads/{session_id:path}/markdown/{filename:path}")
    async def fetch_markdown(
        session_id: str,
        filename: str,
        request: Request,
        secret: str | None = None,
    ):
        # Rust accepts this optional query field for Markdown retrieval, but
        # its Markdown access policy is based on is_public and ignores secret.
        # Keep it in the Python signature so OpenAPI advertises the same route.
        _ = secret
        raw_path = request.scope.get("raw_path", b"").split(b"?", 1)[0]
        raw_segments = raw_path.removeprefix(b"/uploads/").split(b"/")
        # Match Axum's literal route segment even though ASGI exposes a decoded
        # scope path. Percent-encoded text in `markdown` is not a Rust route.
        if (
            len(raw_segments) < 3
            or raw_segments[1] != b"markdown"
            or not raw_segments[2]
            or len(raw_segments) > 3
        ):
            return JSONResponse({"error": "not found"}, status_code=404)

        raw_session = raw_segments[0].lower()
        if b"%2f" in raw_session:
            return _error(400, "Invalid session_id format")
        # A literal slash is a route boundary. Axum's one-segment session ID
        # route does not match a nested literal path.
        if "/" in session_id:
            return JSONResponse({"error": "not found"}, status_code=404)

        # ASGI decodes an encoded slash before routing, while Axum keeps the
        # encoded separator within its filename segment and rejects it as a
        # traversal attempt. Literal nested paths remain unmatched.
        if "/" in filename:
            raw_filename = raw_segments[2].lower()
            if b"%2f" not in raw_filename:
                return JSONResponse({"error": "not found"}, status_code=404)
            return _error(400, "Invalid filename: path traversal detected")
        normalized_id, error = _session_id(session_id)
        if error:
            return _error(400, error)
        assert normalized_id is not None
        if (
            not filename
            or filename != _safe_filename(filename)
            or filename.startswith(".")
            or ".." in filename
        ):
            return _error(400, "Invalid filename: path traversal detected")
        async with serialized(normalized_id):
            try:
                session = _session_dir(root, normalized_id)
                markdown_dir = _safe_directory(root, normalized_id, "markdown")
            except OSError:
                return _error(500, "Invalid upload storage path")
            metadata = _read_metadata(session / "metadata.json")
            if metadata is None:
                return _error(404, "Session does not exist")
            if not metadata.get("is_public", False):
                return _error(403, "Access denied")
            target = markdown_dir / filename
            try:
                data = await asyncio.to_thread(_read_regular_file, target)
            except FileNotFoundError:
                return _error(404, "File not found")
            except OSError:
                return _error(500, "Invalid upload storage path")
        return Response(
            content=data,
            media_type="text/markdown",
            headers={
                "content-disposition": f'inline; filename="{filename}"',
                "cache-control": "public, max-age=3600",
                "x-content-type-options": "nosniff",
            },
        )

    @router.delete("/uploads/{session_id}")
    async def delete_session(session_id: str, secret: str = Query(...)):
        normalized_id, error = _session_id(session_id)
        if error:
            return _error(400, error)
        assert normalized_id is not None
        if len(secret) < 8:
            return _error(400, "Secret must be at least 8 characters")
        async with quota_lock:
            async with _quota_guard(root), serialized(normalized_id):
                try:
                    session = _session_dir(root, normalized_id)
                except OSError:
                    return _error(500, "Invalid upload storage path")
                metadata = _read_metadata(session / "metadata.json")
                if metadata is None:
                    return _error(404, "Session does not exist")
                if not _valid_secret(metadata, secret):
                    return _error(403, "Invalid secret")
                try:
                    markdown_dir = _safe_directory(root, normalized_id, "markdown")
                    images_dir = _safe_directory(root, normalized_id, "images")
                except OSError:
                    markdown_count = images_count = 0
                else:
                    markdown_count = _regular_file_count(markdown_dir)
                    images_count = _regular_file_count(images_dir)
                await asyncio.to_thread(shutil.rmtree, session)
        return {
            "success": True,
            "message": "Session deleted successfully",
            "session_id": normalized_id,
            "files_deleted": {"markdown": markdown_count, "images": images_count},
        }

    return router


def _regular_file_count(directory: Path) -> int:
    try:
        children = directory.iterdir()
    except OSError:
        return 0
    count = 0
    for path in children:
        try:
            if stat.S_ISREG(path.lstat().st_mode):
                count += 1
        except FileNotFoundError:
            continue
    return count
