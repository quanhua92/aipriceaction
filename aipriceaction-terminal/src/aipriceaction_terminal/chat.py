"""Chat tab: message history + input + slash commands."""

import asyncio
from datetime import datetime
from pathlib import Path

from textual import work
from textual.widgets import RichLog, Input, Static
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen

from .widgets import ChatInput
from .utils import write_error, write_export_result, stream_agent_to_log


def _resolve_tui_question(
    builder: object,
    ticker: str,
    question_index: int | None,
    custom_question: str | None,
) -> str:
    """Resolve the analysis question for TUI /analyze command."""
    if custom_question:
        return custom_question

    templates = builder.questions("single")
    if not templates:
        return f"Analyze {ticker} based on the provided data."

    idx = question_index if question_index is not None else 0
    idx = max(0, min(idx, len(templates) - 1))
    template = templates[idx]

    try:
        return template["question"].format(ticker=ticker)
    except KeyError:
        return template["question"]


class ThinkingModal(Screen[None]):
    """Modal overlay showing full thinking text."""

    BINDINGS = [("ctrl+o", "close")]

    DEFAULT_CSS = """
    ThinkingModal {
        align: center middle;
    }

    #thinking-dialog {
        width: 90%;
        height: 80%;
        max-width: 160;
        border: thick $accent;
        background: $surface;
        padding: 0;
    }

    #thinking-title {
        height: 3;
        width: 100%;
        content-align: center middle;
        color: $text;
        text-style: bold;
        border-bottom: solid $accent;
        padding: 0 1;
    }

    #thinking-scroll {
        width: 100%;
        height: 1fr;
        padding: 1;
        color: $text-muted;
    }
    """

    def __init__(self, history: list[tuple[str, str]]) -> None:
        super().__init__()
        self._history = history

    def compose(self):
        count = len(self._history)
        with Vertical(id="thinking-dialog"):
            yield Static(
                f"Thinking ({count} {'entry' if count == 1 else 'entries'}) (Esc / Ctrl+O to close)",
                id="thinking-title",
            )
            with VerticalScroll(id="thinking-scroll"):
                for i, (ts, text) in enumerate(self._history):
                    if i > 0:
                        yield Static("─" * 60, classes="thinking-separator")
                    yield Static(f"[bold]{ts}[/bold]", classes="thinking-ts")
                    yield Static(text, classes="thinking-text")

    def on_mount(self) -> None:
        scroll = self.query_one("#thinking-scroll", VerticalScroll)
        scroll.can_focus = True
        scroll.focus()

    def action_close(self) -> None:
        self.app.pop_screen()


class ChatTab(Vertical):
    """Chat interface for AI ticker analysis."""

    BINDINGS = [("ctrl+o", "show_thinking", "Thinking")]

    DEFAULT_CSS = """
    ChatTab {
        height: 100%;
    }
    #chat-log {
        height: 1fr;
        border: solid $accent;
        padding: 1;
        overflow-x: hidden;
    }
    #thinking-area {
        height: 3;
        border: solid $accent;
        padding: 0 1;
        overflow-y: hidden;
        color: $text-muted;
        text-style: italic;
    }
    #thinking-area.hidden {
        display: none;
    }
    #chat-input {
        height: 3;
    }
    """

    def compose(self):
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True, min_width=1)
        yield Static(id="thinking-area", classes="hidden")
        yield ChatInput(id="chat-input")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._thinking_history: list[tuple[str, str]] = []

    def on_mount(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.can_focus = False
        log.write(
            "[bold cyan]AIPriceAction Terminal[/bold cyan]\n"
            "Type [bold]/help[/bold] for available commands.\n"
        )

    def _show_thinking_area(self, text: str) -> None:
        """Show the thinking area with truncated text."""
        area = self.query_one("#thinking-area", Static)
        area.remove_class("hidden")
        truncated = text[-200:] if len(text) > 200 else text
        area.update(truncated)

    def _hide_thinking_area(self) -> None:
        """Hide the thinking area and clear its content."""
        area = self.query_one("#thinking-area", Static)
        area.add_class("hidden")
        area.update("")

    def _store_thinking(self, text: str) -> None:
        """Store complete thinking text with timestamp for later viewing with Ctrl+O."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._thinking_history.append((ts, text))

    def action_show_thinking(self) -> None:
        """Push a modal with full thinking history, or pop if already showing."""
        if isinstance(self.app.screen_stack[-1], ThinkingModal):
            self.app.pop_screen()
            return
        if self._thinking_history:
            self.app.push_screen(ThinkingModal(self._thinking_history))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""

        # Save to history via ChatInput
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.push_history(text)

        log = self.query_one("#chat-log", RichLog)

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            log.write(f"[bold cyan]You:[/bold cyan] {text}")
            self._run_agent_chat(text)

    def _handle_slash_command(self, text: str) -> None:
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        log = self.query_one("#chat-log", RichLog)

        if cmd == "/help":
            log.write(
                "[bold yellow]Available commands:[/bold yellow]\n"
                "  /analyze <ticker> [interval|index] [--question TEXT]\n"
                "             Analyze ticker with AI (e.g. /analyze VIC)\n"
                "             Use index 0-5 to pick question template\n"
                "             Use --question for custom question\n"
                "  /export <ticker> [tickers...] [--interval 1D] [--path ~/dir/]\n"
                "                        - Export AI context to markdown file\n"
                "  /deep-research [q]   - Multi-agent deep research\n"
                "  /exit                - Quit the application\n"
                "  /help                - Show this help message\n"
                "  /clear               - Clear chat history\n"
            )
        elif cmd == "/clear":
            log.clear()
            if self.app.agent is not None:
                self.app.agent.clear_history()
        elif cmd == "/exit":
            self.app.exit()
        elif cmd == "/analyze":
            self._handle_analyze(text, parts)
        elif cmd == "/deep-research":
            question = " ".join(parts[1:]) if len(parts) > 1 else ""
            log.write("[bold cyan]You:[/bold cyan] /deep-research" + (f" {question}" if question else ""))
            log.write(
                "[bold yellow]Deep research is not yet implemented.[/bold yellow]\n"
                "[dim]This will eventually run the multi-agent LangGraph pipeline "
                "(supervisor -> parallel workers -> aggregator -> reviewer).[/dim]\n"
            )
        elif cmd == "/deep-research":
            question = " ".join(parts[1:]) if len(parts) > 1 else ""
            log.write("[bold cyan]You:[/bold cyan] /deep-research" + (f" {question}" if question else ""))
            log.write(
                "[bold yellow]Deep research is not yet implemented.[/bold yellow]\n"
                "[dim]This will eventually run the multi-agent LangGraph pipeline "
                "(supervisor -> parallel workers -> aggregator -> reviewer).[/dim]\n"
            )
        elif cmd == "/export":
            args = text.split()[1:]  # skip /export
            tickers: list[str] = []
            interval: str | None = None
            out_path: str | None = None
            i = 0
            while i < len(args):
                if args[i] == "--interval" and i + 1 < len(args):
                    interval = args[i + 1]
                    i += 2
                elif args[i] == "--path" and i + 1 < len(args):
                    out_path = args[i + 1]
                    i += 2
                else:
                    tickers.append(args[i])
                    i += 1
            if not tickers:
                log.write(
                    "[bold red]Usage: /export <ticker> [tickers...] "
                    "[--interval 1D] [--path ~/dir/][/bold red]"
                )
                return
            interval = interval or self.app.interval
            export_dir = Path(out_path).expanduser() if out_path else Path("~/aipriceaction-exports").expanduser()
            ticker_label = "_".join(tickers)
            log.write(f"[bold cyan]You:[/bold cyan] /export {ticker_label} --interval {interval}")
            log.write("[dim]Building context and exporting...[/dim]")
            self._run_export(tickers, interval, export_dir)
        else:
            log.write(f"[bold red]Unknown command:[/bold red] {cmd}")

    _KNOWN_INTERVALS = frozenset(("1m", "5m", "15m", "30m", "1h", "4h", "1D", "1W", "1M"))

    def _handle_analyze(self, text: str, parts: list[str]) -> None:
        """Parse /analyze command and dispatch to _run_analyze."""
        log = self.query_one("#chat-log", RichLog)
        rest = text[len("/analyze"):].strip()

        if not rest:
            log.write(
                "[bold red]Usage:[/bold red] /analyze <ticker> [interval|index] [--question TEXT]\n"
                "  e.g. /analyze VIC\n"
                "       /analyze STB 1h\n"
                "       /analyze VCB 2\n"
                "       /analyze VCB --question What is the support level?"
            )
            return

        # Parse --question flag
        custom_question: str | None = None
        if "--question" in rest:
            rest, _, custom_question = rest.partition("--question")
            custom_question = custom_question.strip()
            rest = rest.strip()

        tokens = rest.split()
        ticker = tokens[0].upper()

        # Determine question_index and interval
        question_index: int | None = None
        interval = self.app.interval

        if len(tokens) > 1:
            second = tokens[1]
            if second in self._KNOWN_INTERVALS:
                interval = second
            elif second.isdigit():
                question_index = int(second)

        log.write(f"[bold cyan]You:[/bold cyan] /analyze {ticker} {interval}")
        log.write("[dim]Building context and analyzing...[/dim]")
        self._run_analyze(ticker, interval, question_index=question_index, custom_question=custom_question)

    @work(exclusive=True)
    async def _run_analyze(
        self,
        ticker: str,
        interval: str,
        *,
        question_index: int | None = None,
        custom_question: str | None = None,
    ) -> None:
        """Build context and stream AI analysis for a ticker."""
        log = self.query_one("#chat-log", RichLog)
        try:
            if not self.app._ensure_agent():
                log.write(
                    "[bold yellow]API key not configured.[/bold yellow]\n"
                    "Set it in the Settings tab or run [bold]aipa setup[/bold]."
                )
                return

            builder = self.app.builder

            # Build context without system prompt (agent has it already)
            context = await asyncio.to_thread(
                builder.build, ticker=ticker, interval=interval,
                include_system_prompt=False,
            )

            log.write(f"[dim]Context ready: {len(context):,} chars[/dim]")

            # Resolve question
            question = _resolve_tui_question(
                builder, ticker, question_index, custom_question,
            )

            # Compose the message for the agent
            message = (
                f"<analysis_context>\n{context}\n</analysis_context>\n\n"
                f"{question}\n\n"
                f"Base your analysis ONLY on the provided data above."
            )

            await stream_agent_to_log(
                log,
                self.app.agent,
                message,
                on_thinking_update=self._show_thinking_area,
                on_thinking_done=self._on_thinking_done,
            )
        except Exception as e:
            write_error(log, e)

    @work(exclusive=True)
    async def _run_export(
        self, tickers: list[str], interval: str, export_dir: Path
    ) -> None:
        """Build AI context and export to markdown file."""
        try:
            builder = self.app.builder

            if len(tickers) == 1:
                context = await asyncio.to_thread(
                    builder.build, ticker=tickers[0], interval=interval
                )
            else:
                context = await asyncio.to_thread(
                    builder.build, tickers=tickers, interval=interval
                )

            export_dir.mkdir(parents=True, exist_ok=True)
            ticker_label = "_".join(tickers)
            date_str = datetime.now().strftime("%Y-%m-%d")
            filename = f"{ticker_label}_{interval}_{date_str}.md"
            filepath = export_dir / filename

            await asyncio.to_thread(filepath.write_text, context, encoding="utf-8")

            log = self.query_one("#chat-log", RichLog)
            write_export_result(log, str(filepath), len(context))
        except Exception as e:
            log = self.query_one("#chat-log", RichLog)
            write_error(log, e)

    @work(exclusive=True)
    async def _run_agent_chat(self, message: str) -> None:
        """Stream an agent response into the chat log."""
        log = self.query_one("#chat-log", RichLog)
        if not self.app._ensure_agent():
            log.write(
                "[bold yellow]API key not configured.[/bold yellow]\n"
                "Set it in the Settings tab or run [bold]aipa setup[/bold]."
            )
            return
        try:
            await stream_agent_to_log(
                log,
                self.app.agent,
                message,
                on_thinking_update=self._show_thinking_area,
                on_thinking_done=self._on_thinking_done,
            )
        except Exception as e:
            log.write(f"[bold red]Agent error: {e}[/bold red]\n")

    def _on_thinking_done(self, text: str) -> None:
        """Called when a thinking block finishes. Store and collapse."""
        if not text or len(text.strip()) <= 1:
            self._hide_thinking_area()
            return
        self._store_thinking(text)
        self._hide_thinking_area()
        log = self.query_one("#chat-log", RichLog)
        log.write(f"[dim]Thought for {len(text)} chars (Ctrl+O to view)[/dim]")
