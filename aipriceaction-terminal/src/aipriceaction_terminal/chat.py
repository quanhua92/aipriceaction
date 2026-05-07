"""Chat tab: message history + input + slash commands."""

import asyncio

from textual import on
from textual import work
from textual.widgets import RichLog, Input
from textual.containers import Vertical

from .utils import write_context_result, write_error


class ChatTab(Vertical):
    """Chat interface for AI ticker analysis."""

    DEFAULT_CSS = """
    ChatTab {
        height: 100%;
    }
    #chat-log {
        height: 1fr;
        border: solid $accent;
        padding: 1;
    }
    #chat-input {
        height: 3;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._history: list[str] = []
        self._history_index: int = -1

    def compose(self):
        yield RichLog(id="chat-log", highlight=True, markup=True)
        yield Input(placeholder="Type a message or /analyze <ticker>...", id="chat-input")

    def on_mount(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(
            "[bold cyan]AIPriceAction Terminal[/bold cyan]\n"
            "Type [bold]/help[/bold] for available commands.\n"
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""

        # Save to history (skip duplicates of the most recent entry)
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._history_index = -1

        log = self.query_one("#chat-log", RichLog)

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            log.write(f"[bold cyan]You:[/bold cyan] {text}")
            log.write(
                "[dim italic]AI responses not yet implemented. "
                "Use /analyze to build ticker context.[/dim italic]\n"
            )

    @on(Input.Changed, "#chat-input")
    def _on_chat_input_changed(self, event: Input.Changed) -> None:
        """Reset history index when user types, so arrow keys re-navigate from the end."""
        if self._history_index != -1:
            # Only reset if the value actually changed (not from our own history nav)
            pass

    def _history_up(self) -> None:
        """Navigate to the previous (older) history entry."""
        if not self._history:
            return
        if self._history_index == -1:
            # Starting fresh — save current unsent text, go to most recent
            self._draft = self.query_one("#chat-input", Input).value
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        else:
            return
        self.query_one("#chat-input", Input).value = self._history[self._history_index]

    def _history_down(self) -> None:
        """Navigate to the next (newer) history entry."""
        if self._history_index == -1:
            return
        self._history_index += 1
        if self._history_index >= len(self._history):
            # Past the end — restore the draft
            self._history_index = -1
            self.query_one("#chat-input", Input).value = getattr(self, "_draft", "")
        else:
            self.query_one("#chat-input", Input).value = self._history[self._history_index]

    def on_key(self, event) -> None:
        """Handle arrow up/down for history navigation when chat input is focused."""
        if self.app.focused != self.query_one("#chat-input", Input):
            return
        if event.key == "up":
            self._history_up()
            event.stop()
        elif event.key == "down":
            self._history_down()
            event.stop()

    def _handle_slash_command(self, text: str) -> None:
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        log = self.query_one("#chat-log", RichLog)

        if cmd == "/help":
            log.write(
                "[bold yellow]Available commands:[/bold yellow]\n"
                "  /analyze <ticker> [interval] - Build AI context (e.g. /analyze VIC or /analyze STB 1h)\n"
                "  /deep-research [q]   - Multi-agent deep research (not yet implemented)\n"
                "  /exit                - Quit the application\n"
                "  /help                - Show this help message\n"
                "  /clear               - Clear chat history\n"
            )
        elif cmd == "/clear":
            log.clear()
        elif cmd == "/exit":
            self.app.exit()
        elif cmd == "/analyze":
            if not arg:
                log.write("[bold red]Usage: /analyze <ticker> [interval][/bold red] (e.g. /analyze VIC or /analyze STB 1h)")
                return
            interval = parts[2] if len(parts) > 2 else self.app.interval
            ticker = arg
            log.write(f"[bold cyan]You:[/bold cyan] /analyze {ticker} {interval}")
            log.write("[dim]Building context...[/dim]")
            self._run_analyze(ticker, interval)
        elif cmd == "/deep-research":
            question = " ".join(parts[1:]) if len(parts) > 1 else ""
            log.write("[bold cyan]You:[/bold cyan] /deep-research" + (f" {question}" if question else ""))
            log.write(
                "[bold yellow]Deep research is not yet implemented.[/bold yellow]\n"
                "[dim]This will eventually run the multi-agent LangGraph pipeline "
                "(supervisor -> parallel workers -> aggregator -> reviewer).[/dim]\n"
            )
        else:
            log.write(f"[bold red]Unknown command:[/bold red] {cmd}")

    @work(exclusive=True)
    async def _run_analyze(self, ticker: str, interval: str) -> None:
        """Build AI context for a ticker in a background worker."""
        try:
            builder = self.app.builder

            context = await asyncio.to_thread(
                builder.build, ticker=ticker, interval=interval
            )

            log = self.query_one("#chat-log", RichLog)
            write_context_result(log, ticker, interval, context)
        except Exception as e:
            log = self.query_one("#chat-log", RichLog)
            write_error(log, e)
