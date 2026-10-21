import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from aipriceaction_api.app import create_app
from aipriceaction_api.config import Settings


class _ChunkedBody(httpx.SyncByteStream):
    def __init__(self, body: bytes):
        self.body = body

    def __iter__(self):
        yield self.body


def make_client(
    tmp_path,
    *,
    max_bytes=512 * 1024 * 1024,
    max_sessions=10_000,
    uploads_dir=None,
):
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        uploads_dir=tmp_path / "uploads" if uploads_dir is None else uploads_dir,
        uploads_max_bytes=max_bytes,
        uploads_max_sessions=max_sessions,
        archive_backend="filesystem",
    )
    return create_app(settings)


def test_markdown_upload_fetch_and_session_delete(tmp_path):
    app = make_client(tmp_path)
    session_id = str(uuid.uuid4())
    content = "# A local note\n\nMarkdown body.\n"
    with TestClient(app) as client:
        upload = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.md", content.encode(), "text/markdown")},
        )
        assert upload.status_code == 200
        payload = upload.json()
        assert payload["success"] is True
        assert payload["session_id"] == session_id
        assert payload["files"] == [
            {
                "original_name": "content.md",
                "stored_name": "content.md",
                "size_bytes": len(content.encode()),
                "content_type": "text/markdown; charset=utf-8",
                "url": f"/uploads/{session_id}/markdown/content.md",
            }
        ]
        assert (tmp_path / "uploads" / session_id / "metadata.json").is_file()

        fetched = client.get(payload["files"][0]["url"])
        assert fetched.status_code == 200
        assert fetched.content.decode() == content
        assert fetched.headers["content-type"].startswith("text/markdown")
        assert fetched.headers["content-disposition"] == 'inline; filename="content.md"'
        assert fetched.headers["cache-control"] == "public, max-age=3600"
        assert fetched.headers["x-content-type-options"] == "nosniff"

        # Rust's retrieval query accepts this optional field, but public
        # Markdown access ignores it; private sessions remain private.
        fetched_with_secret = client.get(f"{payload['files'][0]['url']}?secret=unused-secret-value")
        assert fetched_with_secret.status_code == 200
        assert fetched_with_secret.content.decode() == content

        duplicate = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"replacement", "text/markdown")},
        )
        assert duplicate.status_code == 409
        assert duplicate.json() == {
            "success": False,
            "error": "File already exists: content.md",
        }

        denied = client.delete(f"/uploads/{session_id}?secret=another-long-secret")
        assert denied.status_code == 403

        deleted = client.delete(f"/uploads/{session_id}?secret=long-enough-secret")
        assert deleted.status_code == 200
        assert deleted.json() == {
            "success": True,
            "message": "Session deleted successfully",
            "session_id": session_id,
            "files_deleted": {"markdown": 1, "images": 0},
        }
        assert not (tmp_path / "uploads" / session_id).exists()
        assert client.get(payload["files"][0]["url"]).status_code == 404


def test_markdown_fetch_openapi_and_private_policy_match_rust(tmp_path):
    app = make_client(tmp_path)
    session_id = str(uuid.uuid4())
    with TestClient(app) as client:
        upload = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"private note", "text/markdown")},
        )
        assert upload.status_code == 200

        path_parameters = app.openapi()["paths"]["/uploads/{session_id}/markdown/{filename}"][
            "get"
        ]["parameters"]
        secret_parameter = next(item for item in path_parameters if item["name"] == "secret")
        assert secret_parameter["in"] == "query"
        assert secret_parameter["required"] is False

        metadata_path = tmp_path / "uploads" / session_id / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["is_public"] = False
        metadata_path.write_text(json.dumps(metadata))

        markdown_url = f"/uploads/{session_id}/markdown/content.md"
        for query in ("", "?secret=wrong-secret", "?secret=long-enough-secret"):
            denied = client.get(markdown_url + query)
            assert denied.status_code == 403
            assert denied.json() == {"success": False, "error": "Access denied"}


def test_markdown_upload_validation_and_missing_file_errors(tmp_path):
    app = make_client(tmp_path)
    session_id = str(uuid.uuid4())
    with TestClient(app) as client:
        short_secret = client.post(
            f"/upload/markdown?session_id={session_id}&secret=short",
            files={"file": ("content.md", b"note", "text/markdown")},
        )
        assert short_secret.status_code == 400

        invalid_extension = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.html", b"<h1>no</h1>", "text/html")},
        )
        assert invalid_extension.status_code == 400

        invalid_utf8 = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"\xff", "text/markdown")},
        )
        assert invalid_utf8.status_code == 415
        assert not (tmp_path / "uploads" / session_id).exists()

        created = client.post(
            f"/upload/markdown?session_id={session_id}&secret=different-secret",
            files={"file": ("content.md", b"note", "text/markdown")},
        )
        assert created.status_code == 200
        wrong_secret = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"replacement", "text/markdown")},
        )
        assert wrong_secret.status_code == 403

        invalid_uuid = client.get("/uploads/not-a-uuid/markdown/content.md")
        assert invalid_uuid.status_code == 400
        missing_session = client.get(f"/uploads/{uuid.uuid4()}/markdown/content.md")
        assert missing_session.status_code == 404
        missing_file = client.get(f"/uploads/{session_id}/markdown/missing.md")
        assert missing_file.status_code == 404


def test_empty_uploads_path_matches_rust_invalid_storage_response(tmp_path):
    app = make_client(tmp_path, uploads_dir="")
    session_id = str(uuid.uuid4())
    with TestClient(app) as client:
        response = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"note", "text/markdown")},
        )

    assert response.status_code == 500
    assert response.json() == {"success": False, "error": "Invalid upload storage path"}


def test_same_markdown_name_race_never_overwrites_note(tmp_path):
    app = make_client(tmp_path)
    session_id = str(uuid.uuid4())

    with TestClient(app) as client:

        def upload(content):
            return client.post(
                f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
                files={"file": ("content.md", content, "text/markdown")},
            )

        with ThreadPoolExecutor(max_workers=2) as callers:
            responses = list(callers.map(upload, (b"first", b"second")))
        assert sorted(response.status_code for response in responses) == [200, 409]
        fetched = client.get(f"/uploads/{session_id}/markdown/content.md")
    assert fetched.status_code == 200
    assert fetched.content in (b"first", b"second")


def test_upload_size_boundaries_and_aggregate_quota(tmp_path):
    app = make_client(tmp_path)
    with TestClient(app) as client:
        for size, expected_status in (
            (5 * 1024 * 1024 - 1, 200),
            (5 * 1024 * 1024, 200),
            (5 * 1024 * 1024 + 1, 413),
        ):
            session_id = str(uuid.uuid4())
            response = client.post(
                f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
                files={"file": ("content.md", b"x" * size, "text/markdown")},
            )
            assert response.status_code == expected_status
            if expected_status != 200:
                assert not (tmp_path / "uploads" / session_id).exists()

        multiple = client.post(
            f"/upload/markdown?session_id={uuid.uuid4()}&secret=long-enough-secret",
            files=[
                ("file", ("content.md", b"first", "text/markdown")),
                ("file", ("second.md", b"second", "text/markdown")),
            ],
        )
        assert multiple.status_code == 400

    quota_root = tmp_path / "quota"
    quota_app = make_client(quota_root, max_bytes=10, max_sessions=1)
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    with TestClient(quota_app) as client:
        first = client.post(
            f"/upload/markdown?session_id={first_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"123456", "text/markdown")},
        )
        assert first.status_code == 200
        too_many_sessions = client.post(
            f"/upload/markdown?session_id={second_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"1", "text/markdown")},
        )
        assert too_many_sessions.status_code == 507
        assert too_many_sessions.json()["error"] == "Notes session limit reached"
        assert not (quota_root / "uploads" / second_id).exists()

    byte_limited_app = make_client(tmp_path / "byte-quota", max_bytes=10, max_sessions=2)
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    with TestClient(byte_limited_app) as client:
        assert (
            client.post(
                f"/upload/markdown?session_id={first_id}&secret=long-enough-secret",
                files={"file": ("content.md", b"123456", "text/markdown")},
            ).status_code
            == 200
        )
        too_many_bytes = client.post(
            f"/upload/markdown?session_id={second_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"12345", "text/markdown")},
        )
        assert too_many_bytes.status_code == 507
        assert too_many_bytes.json()["error"] == "Notes storage quota exceeded"
        assert not (tmp_path / "byte-quota" / "uploads" / second_id).exists()


def test_upload_storage_rejects_symlinked_session_paths(tmp_path):
    app = make_client(tmp_path)
    session_id = str(uuid.uuid4())
    outside = tmp_path / "outside"
    outside.mkdir()
    session = tmp_path / "uploads" / session_id
    (session / "markdown").parent.mkdir(parents=True)
    (session / "markdown").symlink_to(outside, target_is_directory=True)

    with TestClient(app) as client:
        upload = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"do not escape", "text/markdown")},
        )
        assert upload.status_code == 500
        assert upload.json() == {"success": False, "error": "Invalid upload storage path"}
        assert not (outside / "content.md").exists()


def test_upload_rejects_populated_symlinked_markdown_directory(tmp_path):
    app = make_client(tmp_path)
    session_id = str(uuid.uuid4())
    outside = tmp_path / "outside"
    markdown_dir = tmp_path / "uploads" / session_id / "markdown"

    with TestClient(app) as client:
        initial = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"initial note", "text/markdown")},
        )
        assert initial.status_code == 200

        (markdown_dir / "content.md").unlink()
        markdown_dir.rmdir()
        outside.mkdir()
        external_file = outside / "content.md"
        external_file.write_text("must remain outside", encoding="utf-8")
        markdown_dir.symlink_to(outside, target_is_directory=True)

        rejected = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"do not overwrite", "text/markdown")},
        )

    assert rejected.status_code == 500
    assert rejected.json() == {"success": False, "error": "Invalid upload storage path"}
    assert external_file.read_text(encoding="utf-8") == "must remain outside"


def test_upload_persists_across_app_recreation(tmp_path):
    session_id = str(uuid.uuid4())
    first_app = make_client(tmp_path)
    with TestClient(first_app) as client:
        upload = client.post(
            f"/upload/markdown?session_id={session_id}&secret=long-enough-secret",
            files={"file": ("content.md", b"persisted note", "text/markdown")},
        )
        assert upload.status_code == 200

    second_app = make_client(tmp_path)
    with TestClient(second_app) as client:
        fetched = client.get(f"/uploads/{session_id}/markdown/content.md")
    assert fetched.status_code == 200
    assert fetched.content == b"persisted note"


def test_upload_quota_is_shared_across_app_instances(tmp_path):
    shared_uploads = tmp_path / "shared-uploads"
    first_app = make_client(
        tmp_path / "app-one", max_bytes=10, max_sessions=2, uploads_dir=shared_uploads
    )
    second_app = make_client(
        tmp_path / "app-two", max_bytes=10, max_sessions=2, uploads_dir=shared_uploads
    )
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())

    with TestClient(first_app) as first_client, TestClient(second_app) as second_client:
        with ThreadPoolExecutor(max_workers=2) as callers:
            responses = list(
                callers.map(
                    lambda pair: pair[0].post(
                        f"/upload/markdown?session_id={pair[1]}&secret=long-enough-secret",
                        files={"file": ("content.md", b"123456", "text/markdown")},
                    ),
                    ((first_client, first_id), (second_client, second_id)),
                )
            )

    assert sorted(response.status_code for response in responses) == [200, 507]
    assert (
        sum(
            (shared_uploads / session / "markdown" / "content.md").exists()
            for session in (first_id, second_id)
        )
        == 1
    )


def test_notes_preflight_advertises_only_methods_registered_for_route(tmp_path):
    app = make_client(tmp_path)
    with TestClient(app) as client:
        session_id = str(uuid.uuid4())
        for path, method, allow in (
            ("/upload/markdown", "POST", "POST"),
            (f"/uploads/{session_id}/markdown/content.md", "GET", "GET,HEAD"),
            (f"/uploads/{session_id}", "DELETE", "DELETE"),
        ):
            response = client.options(
                path,
                headers={
                    "Origin": "https://aipriceaction.com",
                    "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == "https://aipriceaction.com"
            assert method in response.headers["access-control-allow-methods"]
            assert response.headers["allow"] == allow


def test_notes_method_not_allowed_includes_rust_compatible_allow_header(tmp_path):
    app = make_client(tmp_path)
    session_id = str(uuid.uuid4())
    with TestClient(app) as client:
        probes = (
            ("GET", "/upload/markdown", "POST"),
            (
                "POST",
                f"/uploads/{session_id}/markdown/content.md",
                "GET,HEAD",
            ),
            ("GET", f"/uploads/{session_id}", "DELETE"),
        )
        for method, path, allowed in probes:
            response = client.request(method, path)
            assert response.status_code == 405
            assert response.content == b""
            assert response.headers["allow"] == allowed


@pytest.mark.parametrize(
    ("session_id", "secret", "content_type", "status", "content_type_out", "body"),
    [
        (
            "550e8400-e29b-41d4-a716-446655440001",
            "long-enough-secret",
            "text/plain",
            400,
            "text/plain; charset=utf-8",
            b"Invalid `boundary` for `multipart/form-data` request",
        ),
        (
            "550e8400-e29b-41d4-a716-446655440001",
            "long-enough-secret",
            "multipart/form-data; boundary=",
            400,
            "application/json",
            b'{"success":false,"error":"Invalid multipart form"}',
        ),
        (
            "550e8400-e29b-41d4-a716-446655440001",
            "long-enough-secret",
            "multipart/form-data; boundary=x",
            400,
            "application/json",
            b'{"success":false,"error":"Invalid multipart form"}',
        ),
        (
            "not-a-uuid",
            "long-enough-secret",
            "multipart/form-data; boundary=x",
            400,
            "application/json",
            b'{"success":false,"error":"Invalid session_id format"}',
        ),
        (
            "550e8400-e29b-41d4-a716-446655440001",
            "short",
            "multipart/form-data; boundary=x",
            400,
            "application/json",
            b'{"success":false,"error":"Secret must be at least 8 characters"}',
        ),
    ],
)
def test_chunked_notes_upload_limit_matches_rust_precedence(
    tmp_path, session_id, secret, content_type, status, content_type_out, body
):
    app = make_client(tmp_path)
    body_limit = Settings().body_limit + 64 * 1024
    chunk = b"x" * (body_limit + 1)
    with TestClient(app) as client:
        response = client.post(
            f"/upload/markdown?session_id={session_id}&secret={secret}",
            content=_ChunkedBody(chunk),
            headers={"content-type": content_type},
        )

    assert response.status_code == status
    assert response.headers["content-type"] == content_type_out
    assert response.content == body


@pytest.mark.parametrize(
    "content_type",
    [
        "multipart/form-data; boundary=x",
        "multipart/form-data; boundary=",
    ],
)
def test_empty_notes_multipart_body_matches_rust_extractor_error(
    tmp_path, content_type
):
    app = make_client(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/upload/markdown?session_id=550e8400-e29b-41d4-a716-446655440001"
            "&secret=long-enough-secret",
            content=b"",
            headers={"content-type": content_type},
        )

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/json"
    assert response.content == b'{"success":false,"error":"Invalid multipart form"}'


def test_notes_declared_content_length_limit_precedes_query_and_multipart(tmp_path):
    app = make_client(tmp_path)
    body_limit = Settings().body_limit + 64 * 1024
    with TestClient(app) as client:
        response = client.post(
            "/upload/markdown?session_id=not-a-uuid&secret=short",
            content=b"x",
            headers={
                "content-length": str(body_limit + 1),
                "content-type": "text/plain",
            },
        )

    assert response.status_code == 413
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.content == b"length limit exceeded"
