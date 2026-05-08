"""Stream events and callback handler for TUI integration."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StreamEventType(Enum):
    TOKEN = "token"
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
            # Tool calls
            if getattr(message, "tool_calls", None):
                if self.show_tool_calls:
                    for tc in message.tool_calls:
                        args_preview = json.dumps(tc["args"], ensure_ascii=False)
                        events.append(StreamEvent(
                            type=StreamEventType.TOOL_CALL_START,
                            content=f"{tc['name']}({args_preview})",
                        ))
            # Content tokens (partial chunks from streaming)
            if message.content:
                events.append(StreamEvent(
                    type=StreamEventType.TOKEN,
                    content=message.content,
                ))

        elif msg_type in ("ToolMessage",):
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
