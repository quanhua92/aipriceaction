"""Stream events and callback handler for TUI integration."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StreamEventType(Enum):
    TOKEN = "token"
    THINKING = "thinking"
    TOOL_CALL_START = "tool_call_start"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    DONE = "done"


@dataclass
class StreamEvent:
    """A single event from the agent stream."""

    type: StreamEventType
    content: str = ""
    metadata: dict = field(default_factory=dict)


StreamCallback = Callable[[StreamEvent], Awaitable[None]]


def _extract_reasoning_content(message: Any) -> str:
    """Extract reasoning/thinking content from an AIMessageChunk.

    Checks multiple fields where different providers store reasoning tokens:
    - ``additional_kwargs["reasoning_content"]`` — OpenRouter, DeepSeek, XAI, Groq
    - ``content_blocks`` with ``type="reasoning"`` — Anthropic, OpenAI
    """
    # Check additional_kwargs first (most common for OpenAI-compatible providers)
    ak = getattr(message, "additional_kwargs", None)
    if ak and isinstance(ak, dict) and "reasoning_content" in ak:
        content = ak["reasoning_content"]
        if content:
            return content

    # Check content_blocks for type="reasoning"
    blocks = getattr(message, "content_blocks", None)
    if blocks:
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "reasoning":
                text = block.get("reasoning", "")
                if text:
                    return text
            elif hasattr(block, "type") and block.type == "reasoning":
                text = getattr(block, "reasoning", "")
                if text:
                    return text

    return ""


class StreamCallbackHandler:
    """Converts LangChain/LangGraph stream events to StreamEvents for TUI rendering.

    Supports ``stream_mode="messages"`` (token-by-token) and
    ``stream_mode="updates"`` (batched per node).
    """

    def __init__(
        self,
        *,
        show_tool_calls: bool = True,
        show_tool_results: bool = False,
    ) -> None:
        self.show_tool_calls = show_tool_calls
        self.show_tool_results = show_tool_results
        # Buffer tool calls during streaming; keyed by tool_call_id.
        # Values accumulate raw args string from tool_call_chunks.
        self._pending_tool_calls: dict[str, dict[str, str]] = {}
        # OpenAI streaming only sends `id` on the first chunk per tool call.
        # Subsequent chunks carry only `index` + partial `args`.
        # This maps index → tool_call_id so we can associate them.
        self._index_to_id: dict[int, str] = {}

    def process_agent_event(self, event: Any) -> list[StreamEvent]:
        """Convert a single LangGraph stream event to StreamEvents.

        ``stream_mode="messages"`` yields tuples::

            (AIMessageChunk(content="Hello"), metadata_dict)

        ``stream_mode="updates"`` yields ``UpdatesStreamPart`` dicts::

            {"agent": {"messages": [AIMessage(...)]}}
        """
        events: list[StreamEvent] = []

        # Handle stream_mode="messages" — yields (message, metadata) tuples
        if isinstance(event, tuple) and len(event) == 2:
            message, _metadata = event
            return self._process_message(message)

        # Handle stream_mode="updates" — yields dicts like {"agent": {"messages": [...]}}
        if isinstance(event, dict):
            for _node_name, update in event.items():
                for msg in update.get("messages", []):
                    events.extend(self._process_message(msg))
            return events

        return events

    def _process_message(self, message: Any) -> list[StreamEvent]:
        """Process a single message (AIMessageChunk or ToolMessage)."""
        events: list[StreamEvent] = []
        msg_type = type(message).__name__

        if msg_type in ("AIMessageChunk", "AIMessage"):
            # Reasoning/thinking tokens
            reasoning = _extract_reasoning_content(message)
            if reasoning:
                events.append(StreamEvent(
                    type=StreamEventType.THINKING,
                    content=reasoning,
                ))

            # Tool calls — accumulate raw chunks, emit parsed call when ToolMessage arrives
            tool_call_chunks = getattr(message, "tool_call_chunks", None)
            if tool_call_chunks:
                for tc_chunk in tool_call_chunks:
                    _get = tc_chunk.get if isinstance(tc_chunk, dict) else lambda k, d=None: getattr(tc_chunk, k, d)
                    tc_id = _get("id") or ""
                    tc_index = _get("index")

                    # First chunk has id + index → record the mapping
                    if tc_id and tc_index is not None:
                        self._index_to_id[tc_index] = tc_id
                    # Subsequent chunks only have index → look up the id
                    elif not tc_id and tc_index is not None:
                        tc_id = self._index_to_id.get(tc_index, "")

                    if not tc_id:
                        continue
                    if tc_id not in self._pending_tool_calls:
                        self._pending_tool_calls[tc_id] = {"name": "", "args_str": ""}
                    entry = self._pending_tool_calls[tc_id]
                    chunk_name = _get("name") or ""
                    if chunk_name:
                        entry["name"] = chunk_name
                    chunk_args = _get("args") or ""
                    if chunk_args:
                        entry["args_str"] += chunk_args
            elif getattr(message, "tool_calls", None):
                # Fallback for non-streaming AIMessage (e.g. stream_mode="updates")
                for tc in message.tool_calls:
                    tc_id = tc.get("id", "")
                    if not tc_id:
                        continue
                    if tc_id not in self._pending_tool_calls:
                        self._pending_tool_calls[tc_id] = {"name": "", "args_str": ""}
                    entry = self._pending_tool_calls[tc_id]
                    if tc.get("name"):
                        entry["name"] = tc["name"]
                    if isinstance(tc.get("args"), dict):
                        entry["args_str"] = json.dumps(tc["args"], ensure_ascii=False)

            # Content tokens (partial chunks from streaming)
            if message.content:
                events.append(StreamEvent(
                    type=StreamEventType.TOKEN,
                    content=message.content,
                ))

        elif msg_type in ("ToolMessage",):
            # Flush buffered tool call now that execution is complete
            tc_id = getattr(message, "tool_call_id", "")
            if tc_id and tc_id in self._pending_tool_calls and self.show_tool_calls:
                tc_info = self._pending_tool_calls.pop(tc_id)
                raw_args = tc_info.get("args_str", "")
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {}
                args_preview = json.dumps(args, ensure_ascii=False)
                events.append(StreamEvent(
                    type=StreamEventType.TOOL_CALL_START,
                    content=f"{tc_info['name']}({args_preview})",
                ))

            if self.show_tool_results:
                events.append(StreamEvent(
                    type=StreamEventType.TOOL_RESULT,
                    content=message.content,
                ))
            else:
                char_count = len(message.content)
                events.append(StreamEvent(
                    type=StreamEventType.TOOL_RESULT,
                    content=f"[{char_count:,} chars]",
                ))

        return events
