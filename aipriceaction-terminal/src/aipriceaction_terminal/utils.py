"""Shared display helpers for RichLog output formatting."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from textual.widgets import RichLog

if TYPE_CHECKING:
    pass


def write_context_result(
    log: RichLog, ticker: str, interval: str, context: str
) -> None:
    """Write a formatted context build result to a RichLog."""
    lines = context.split("\n")
    log.write(
        f"[bold green]Context built[/bold green] for [bold]{ticker}[/bold] "
        f"({interval}) - {len(context):,} chars, {len(lines)} lines\n"
    )
    log.write("[dim]" + context + "[/dim]")
    log.write("")


def write_error(log: RichLog, error: Exception) -> None:
    """Write a formatted error to a RichLog."""
    log.write(f"[bold red]Error:[/bold red] {error}")


def write_export_result(log: RichLog, filepath: str, context_len: int) -> None:
    """Write a formatted export success message to a RichLog."""
    log.write(
        f"[bold green]Exported[/bold green] {context_len:,} chars to "
        f"[bold]{filepath}[/bold]\n"
    )


async def stream_agent_to_log(
    log: RichLog,
    agent: object,
    message: str,
    *,
    on_thinking_update: Callable[[str], None] | None = None,
    on_thinking_done: Callable[[str], None] | None = None,
    on_message: Callable[[dict], None] | None = None,
) -> str:
    """Stream an agent response into a RichLog. Returns the full response text.

    This is the shared streaming loop used by both ChatTab and AnalyzePane
    to render agent output with thinking display, tool call indicators, and
    token accumulation.

    Args:
        log: The RichLog widget to write output into.
        agent: An AgentSession instance with a .stream() method.
        message: The user message to send to the agent.
        on_thinking_update: Called with accumulated thinking text on each chunk.
        on_thinking_done: Called with complete thinking text when thinking ends.
        on_message: Called with a dict {"ts", "type", "content", "metadata"}
            for each persistable event (tool_call, tool_result, error, assistant).
            Thinking tokens are NOT emitted.

    Returns:
        The full response text from the agent.
    """
    from .agents.callbacks import StreamEventType

    buffer: list[str] = []
    thinking_buf: list[str] = []
    full_response: list[str] = []

    def flush() -> None:
        if buffer:
            text = "".join(buffer)
            full_response.append(text)
            log.write(text)
            buffer.clear()

    def collapse_thinking() -> None:
        if thinking_buf:
            text = "".join(thinking_buf)
            thinking_buf.clear()
            if len(text.strip()) <= 1:
                if on_thinking_update:
                    on_thinking_update("")
                return
            if on_thinking_done:
                on_thinking_done(text)

    def _emit(msg_type: str, content: str, metadata: dict | None = None) -> None:
        if on_message:
            on_message({
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f"),
                "type": msg_type,
                "content": content,
                "metadata": metadata or {},
            })

    async for event in agent.stream(message):
        if event.type == StreamEventType.THINKING:
            thinking_buf.append(event.content)
            if on_thinking_update:
                on_thinking_update("".join(thinking_buf))

        elif event.type == StreamEventType.TOKEN:
            if thinking_buf:
                collapse_thinking()
            buffer.append(event.content)
            if "\n" in event.content:
                flush()

        elif event.type == StreamEventType.DONE:
            if thinking_buf:
                collapse_thinking()
            flush()
            log.write("")
            response_text = "".join(full_response)
            if response_text.strip():
                _emit("assistant", response_text)

        else:
            flush()
            if event.type == StreamEventType.TOOL_CALL_START:
                log.write(f"[dim italic]{event.content}[/dim italic]")
                _emit("tool_call", event.content)
            elif event.type == StreamEventType.TOOL_RESULT:
                log.write(f"[dim]{event.content}[/dim]")
                _emit("tool_result", event.content, {"char_count": len(event.content)})
            elif event.type == StreamEventType.ERROR:
                log.write(f"[bold red]{event.content}[/bold red]")
                _emit("error", event.content)

    return "".join(full_response)
