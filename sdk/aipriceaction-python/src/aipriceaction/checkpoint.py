"""Persistent checkpoint saver for LangGraph graphs.

Provides a drop-in replacement for InMemorySaver that persists checkpoint
state to disk, with callback hooks to extract worker analysis results into
dedicated files per session run.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import tempfile
import time
import uuid
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any, Callable

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.memory import InMemorySaver

logger = logging.getLogger(__name__)

PostPutCallback = Callable[[dict[str, Any], Path], None]
"""Called after each put() with (channel_values, session_dir)."""


# ── UUID v7 fallback ──


def _uuid7() -> str:
    """Generate a UUID v7 string. Uses stdlib on Python 3.13+, inline fallback otherwise."""
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())
    # Inline UUID v7 generator: 48-bit timestamp ms + 4-bit version + 12-bit rand + 2-bit variant + 62-bit rand
    timestamp_ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)
    uuid_int = (timestamp_ms & 0xFFFFFFFFFFFF) << 80
    uuid_int |= 0x7000 << 64  # version 7
    uuid_int |= int.from_bytes(rand_bytes[:2], "big") << 64
    uuid_int |= (rand_bytes[2] & 0x3F) | 0x80  # variant 1
    uuid_int |= int.from_bytes(rand_bytes[3:], "big") << 8
    return str(uuid.UUID(int=uuid_int))


# ── JSON serialization helpers ──


def _safe_json(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable objects for human-readable output."""
    if isinstance(obj, dict):
        return {k: _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_json(v) for v in obj]
    if isinstance(obj, BaseMessage):
        return {
            "type": type(obj).__name__,
            "content": obj.content[:500] if isinstance(obj.content, str) else _safe_json(obj.content),
            "name": getattr(obj, "name", None),
        }
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, bytes):
        return f"<bytes:{len(obj)}>"
    return str(obj)


# ── Atomic file write ──


def _atomic_write(path: Path, content: bytes) -> None:
    """Write *content* to *path* atomically (tmp + rename)."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_bytes(content)
        tmp.rename(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ── PersistentCheckpointSaver ──


class PersistentCheckpointSaver(
    BaseCheckpointSaver[str], AbstractContextManager, AbstractAsyncContextManager
):
    """Drop-in replacement for InMemorySaver with write-through disk persistence.

    All checkpoint CRUD delegates to an internal ``InMemorySaver``.  The
    ``put()`` method additionally serialises state to the session folder and
    invokes any registered *post-put* callbacks.

    Args:
        base_dir: Root directory for session folders.
            Defaults to ``$TMPDIR/aipriceaction-checkpoints/``.
        session_id: Existing session ID to resume from.  A new UUID v7 is
            generated when omitted.
        callbacks: Optional list of ``PostPutCallback`` functions invoked
            after each ``put()``.
    """

    def __init__(
        self,
        *,
        base_dir: Path | str | None = None,
        session_id: str | None = None,
        callbacks: list[PostPutCallback] | None = None,
    ) -> None:
        super().__init__()
        self._inner = InMemorySaver()
        self._callbacks = callbacks or []
        self._session_id = session_id or _uuid7()

        if base_dir is None:
            base_dir = Path(tempfile.gettempdir()) / "aipriceaction-checkpoints"
        self._base_dir = Path(base_dir)
        self._session_dir = self._base_dir / self._session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)

        # Restore from disk if resuming
        state_file = self._session_dir / "state.pkl"
        if session_id and state_file.exists():
            try:
                self._restore(state_file)
                logger.info("Restored session %s from %s", self._session_id, state_file)
            except Exception:
                logger.exception("Failed to restore session %s", self._session_id)

    # ── Properties ──

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    # ── Context managers (pass through) ──

    def __enter__(self) -> PersistentCheckpointSaver:
        self._inner.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        self._persist_state()
        return self._inner.__exit__(exc_type, exc_value, traceback)

    async def __aenter__(self) -> PersistentCheckpointSaver:
        await self._inner.__aenter__()
        return self

    async def __aexit__(
        self,
        __exc_type: type[BaseException] | None,
        __exc_value: BaseException | None,
        __traceback: TracebackType | None,
    ) -> bool | None:
        self._persist_state()
        return await self._inner.__aexit__(__exc_type, __exc_value, __traceback)

    # ── Checkpoint CRUD (delegate to inner) ──

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self._inner.get_tuple(config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ):
        return self._inner.list(config, filter=filter, before=before, limit=limit)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        result = self._inner.put(config, checkpoint, metadata, new_versions)
        self._on_put(checkpoint)
        return result

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._inner.put_writes(config, writes, task_id, task_path)

    def delete_thread(self, thread_id: str) -> None:
        self._inner.delete_thread(thread_id)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await self._inner.aget_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ):
        async for item in self._inner.alist(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        result = await self._inner.aput(config, checkpoint, metadata, new_versions)
        self._on_put(checkpoint)
        return result

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await self._inner.aput_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await self._inner.adelete_thread(thread_id)

    def get_next_version(self, current: str | None, channel: None) -> str:
        return self._inner.get_next_version(current, channel)

    # ── Persistence ──

    def _on_put(self, checkpoint: Checkpoint) -> None:
        """Persist state to disk and invoke callbacks after a put()."""
        try:
            self._persist_state()
            self._persist_latest_json(checkpoint)
            # Invoke callbacks with channel_values from the checkpoint
            channel_values = checkpoint.get("channel_values", {})
            for cb in self._callbacks:
                try:
                    cb(channel_values, self._session_dir)
                except Exception:
                    logger.exception("PostPutCallback failed")
        except Exception:
            logger.exception("Checkpoint persistence failed")

    def _persist_state(self) -> None:
        """Pickle the inner InMemorySaver state for exact restore."""
        state_file = self._session_dir / "state.pkl"
        # Convert defaultdicts (which contain unpicklable lambdas) to plain dicts
        storage: dict = {}
        for thread_id, ns_dict in self._inner.storage.items():
            storage[thread_id] = {}
            for ns, cp_dict in ns_dict.items():
                storage[thread_id][ns] = dict(cp_dict)

        writes: dict = {}
        for key, val in self._inner.writes.items():
            writes[key] = dict(val)

        blobs: dict = dict(self._inner.blobs)

        _atomic_write(state_file, pickle.dumps(
            {"storage": storage, "writes": writes, "blobs": blobs},
            protocol=pickle.HIGHEST_PROTOCOL,
        ))

    def _persist_latest_json(self, checkpoint: Checkpoint) -> None:
        """Write a human-readable JSON snapshot of the latest checkpoint."""
        latest_file = self._session_dir / "latest.json"
        channel_values = checkpoint.get("channel_values", {})
        safe = _safe_json(channel_values)
        try:
            _atomic_write(latest_file, json.dumps(safe, indent=2, default=str, ensure_ascii=False).encode("utf-8"))
        except Exception:
            logger.exception("Failed to write latest.json")

    def _restore(self, state_file: Path) -> None:
        """Restore inner InMemorySaver state from a pickle file."""
        with open(state_file, "rb") as f:
            state = pickle.load(f)
        # Populate fresh InMemorySaver defaultdicts with restored plain dicts
        new_inner = InMemorySaver()
        for thread_id, ns_dict in state["storage"].items():
            for ns, cp_dict in ns_dict.items():
                new_inner.storage[thread_id][ns].update(cp_dict)
        for key, val in state["writes"].items():
            new_inner.writes[key].update(val)
        new_inner.blobs.update(state["blobs"])
        self._inner = new_inner


# ── Helper ──


def load_session(session_dir: str | Path) -> PersistentCheckpointSaver:
    """Restore a ``PersistentCheckpointSaver`` from an existing session folder.

    Args:
        session_dir: Path to the session folder containing ``state.pkl``.

    Returns:
        A new ``PersistentCheckpointSaver`` with restored state.

    Raises:
        FileNotFoundError: If ``state.pkl`` does not exist in the session folder.
    """
    session_dir = Path(session_dir)
    state_file = session_dir / "state.pkl"
    if not state_file.exists():
        raise FileNotFoundError(f"No state.pkl found in {session_dir}")
    return PersistentCheckpointSaver(session_id=session_dir.name, base_dir=session_dir.parent)
