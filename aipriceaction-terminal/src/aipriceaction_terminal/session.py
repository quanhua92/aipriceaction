"""Persistent chat sessions stored as JSONL files."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from .user_settings import _CONFIG_DIR

_SESSIONS_DIR = _CONFIG_DIR / "sessions"


@dataclass
class ChatMessage:
    """A single chat message stored in JSONL."""

    ts: str
    type: str  # user, assistant, tool_call, tool_result, error, system
    content: str
    metadata: dict = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> ChatMessage:
        d = json.loads(line)
        return cls(ts=d["ts"], type=d["type"], content=d["content"], metadata=d.get("metadata", {}))


@dataclass
class SessionMeta:
    """Metadata for a chat session."""

    session_id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    language: str = "en"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SessionMeta:
        return cls(**d)


class SessionManager:
    """Manages persistent chat sessions on disk."""

    def __init__(self) -> None:
        self._sessions_dir = _SESSIONS_DIR
        self._current_id: str | None = None
        self._current_messages: list[ChatMessage] = []

    @property
    def current_session_id(self) -> str | None:
        return self._current_id

    @property
    def current_messages(self) -> list[ChatMessage]:
        return list(self._current_messages)

    def _session_dir(self, session_id: str) -> Path:
        return self._sessions_dir / session_id

    def _chat_file(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "chat.jsonl"

    def _meta_file(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "meta.json"

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _now_ts(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    def create_session(self, title: str = "New Chat", language: str = "en") -> str:
        """Create a new session folder with meta.json, set as current."""
        session_id = str(uuid.uuid4())
        sdir = self._session_dir(session_id)
        sdir.mkdir(parents=True, exist_ok=True)

        now = self._now_ts()
        meta = SessionMeta(
            session_id=session_id,
            title=title[:50],
            created_at=now,
            updated_at=now,
            message_count=0,
            language=language,
        )
        self._meta_file(session_id).write_text(
            json.dumps(meta.to_dict(), indent=2, ensure_ascii=False)
        )

        # Create empty chat.jsonl
        self._chat_file(session_id).write_text("", encoding="utf-8")

        self._current_id = session_id
        self._current_messages = []
        return session_id

    def append_message(self, msg: ChatMessage) -> None:
        """Append a message to the current session's JSONL and update meta."""
        if self._current_id is None:
            return

        chat_file = self._chat_file(self._current_id)
        with open(chat_file, "a", encoding="utf-8") as f:
            f.write(msg.to_jsonl() + "\n")

        self._current_messages.append(msg)

        # Update meta
        meta = self._load_meta(self._current_id)
        if meta:
            meta.updated_at = self._now_ts()
            meta.message_count = len(self._current_messages)
            # Auto-title from first user message
            if meta.title == "New Chat" and msg.type == "user" and msg.content.strip():
                meta.title = msg.content.strip()[:50]
            self._meta_file(self._current_id).write_text(
                json.dumps(meta.to_dict(), indent=2, ensure_ascii=False)
            )

    def load_session(self, session_id: str) -> list[ChatMessage]:
        """Load all messages from a session, set as current. Returns messages."""
        chat_file = self._chat_file(session_id)
        if not chat_file.exists():
            return []

        messages: list[ChatMessage] = []
        for line in chat_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                messages.append(ChatMessage.from_jsonl(line))

        self._current_id = session_id
        self._current_messages = messages
        return messages

    def list_sessions(self) -> list[SessionMeta]:
        """Return all sessions sorted by updated_at desc."""
        if not self._sessions_dir.exists():
            return []

        metas: list[SessionMeta] = []
        for sdir in self._sessions_dir.iterdir():
            if not sdir.is_dir():
                continue
            meta_file = sdir / "meta.json"
            if meta_file.exists():
                try:
                    meta = SessionMeta.from_dict(json.loads(meta_file.read_text()))
                    metas.append(meta)
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue

        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas

    def _load_meta(self, session_id: str) -> SessionMeta | None:
        meta_file = self._meta_file(session_id)
        if not meta_file.exists():
            return None
        try:
            return SessionMeta.from_dict(json.loads(meta_file.read_text()))
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def export_to_markdown(self, session_id: str | None = None, output_path: Path | None = None) -> Path:
        """Export session messages to a markdown file. Returns the filepath."""
        sid = session_id or self._current_id
        if not sid:
            raise ValueError("No session to export")

        meta = self._load_meta(sid)
        messages = self.load_session(sid) if sid != self._current_id else self._current_messages

        lines: list[str] = []
        lines.append(f"# {meta.title if meta else 'Chat Session'}")
        lines.append("")
        if meta:
            lines.append(f"**Session ID:** `{meta.session_id}`")
            lines.append(f"**Created:** {meta.created_at}")
            lines.append(f"**Messages:** {meta.message_count}")
        lines.append(f"**Exported:** {self._now_iso()}")
        lines.append("")
        lines.append("---")
        lines.append("")

        for msg in messages:
            ts_short = msg.ts.split("T")[1][:8] if "T" in msg.ts else msg.ts[:8]
            if msg.type == "user":
                lines.append(f"**You** ({ts_short}):")
                lines.append(f"{msg.content}")
                lines.append("")
            elif msg.type == "assistant":
                lines.append(f"**AI** ({ts_short}):")
                lines.append(f"{msg.content}")
                lines.append("")
            elif msg.type == "tool_call":
                lines.append(f"*Tool call* ({ts_short}): `{msg.content}`")
                lines.append("")
            elif msg.type == "tool_result":
                char_count = msg.metadata.get("char_count", len(msg.content))
                lines.append(f"*Tool result* ({ts_short}): [{char_count:,} chars]")
                lines.append("")
            elif msg.type == "error":
                lines.append(f"**Error** ({ts_short}): {msg.content}")
                lines.append("")
            elif msg.type == "system":
                lines.append(f"*System* ({ts_short}): {msg.content}")
                lines.append("")

        if output_path is None:
            short_id = sid[:8] if sid else "unknown"
            output_path = Path.home() / f"aipriceaction-chat-{short_id}.md"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path
