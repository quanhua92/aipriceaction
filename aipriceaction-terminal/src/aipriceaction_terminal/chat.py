"""Chat tab: message history + input + slash commands."""

import asyncio
from datetime import datetime
from pathlib import Path

from textual import work
from textual.binding import Binding
from textual.widgets import Input, Static
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen

from .widgets import ChatInput, SafeRichLog
from .utils import write_error, write_export_result, stream_agent_to_log
from .session import SessionManager, ChatMessage
from .analyze import run_tui_analyze


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

    BINDINGS = [Binding("ctrl+o", "show_thinking", "Thinking", key_display="ctrl+o")]

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
        yield SafeRichLog(id="chat-log", highlight=True, markup=True, wrap=True, min_width=1)
        yield Static(id="thinking-area", classes="hidden")
        yield ChatInput(id="chat-input")

    def __init__(self, resume_session: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self._thinking_history: list[tuple[str, str]] = []
        self._session = SessionManager()
        self._resumed_history: list[ChatMessage] = []
        self._resume_session = resume_session

    def on_mount(self) -> None:
        log = self.query_one("#chat-log", SafeRichLog)
        log.can_focus = False

        if self._resume_session:
            # Resume an existing session
            self._load_session(self._resume_session, log)
        else:
            # Auto-create a new session
            self._session.create_session()

        log.write(
            "[bold cyan]AIPriceAction Terminal[/bold cyan]\n"
            "Type [bold]/help[/bold] for available commands.\n"
        )

    def _make_on_message(self) -> callable:
        """Return an on_message callback that persists agent events to the session."""
        def _cb(msg_dict: dict) -> None:
            self._session.append_message(
                ChatMessage(
                    ts=msg_dict["ts"],
                    type=msg_dict["type"],
                    content=msg_dict["content"],
                    metadata=msg_dict.get("metadata", {}),
                )
            )
        return _cb

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

    def _build_resumed_prefix(self) -> str | None:
        """Build <chat_history> prefix from resumed session, then clear it."""
        if not self._resumed_history:
            return None

        lines = ["<chat_history>"]
        for msg in self._resumed_history:
            if msg.type == "user":
                lines.append(f"User: {msg.content}")
            elif msg.type == "assistant":
                lines.append(f"AI: {msg.content}")
        lines.append("</chat_history>")
        lines.append("")

        self._resumed_history = []
        return "\n".join(lines)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""

        # Save to history via ChatInput
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.push_history(text)

        log = self.query_one("#chat-log", SafeRichLog)

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            # Persist user message
            self._session.append_message(
                ChatMessage(
                    ts=datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                    type="user",
                    content=text,
                )
            )
            log.write(f"[bold cyan]You:[/bold cyan] {text}")
            self._run_agent_chat(text)

    def _handle_slash_command(self, text: str) -> None:
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        log = self.query_one("#chat-log", SafeRichLog)

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
                "  /save [path]         - Export chat to markdown (default: ~/aipriceaction-chat-<id>.md)\n"
                "  /resume              - List saved sessions\n"
                "  /resume <index>      - Load session by number from list\n"
                "  /resume <session_id> - Load session by UUID\n"
                "  /sessions            - Alias for /resume\n"
                "  /new                 - Start new chat session\n"
                "  /exit                - Quit the application\n"
                "  /help                - Show this help message\n"
                "  /clear               - Clear chat display only"
            )
        elif cmd == "/clear":
            log.clear()
        elif cmd == "/new":
            log.clear()
            if getattr(self.app, "agent", None) is not None:
                self.app.agent.clear_history()
            self._session.create_session()
            self._resumed_history = []
            log.write("[dim]New session started.[/dim]\n")
        elif cmd == "/exit":
            self.app.exit()
        elif cmd == "/analyze":
            self._handle_analyze(text, parts)
        elif cmd == "/deep-research":
            question = " ".join(parts[1:]) if len(parts) > 1 else ""
            log.write("[bold cyan]You:[/bold cyan] /deep-research" + (f" {question}" if question else ""))
            log.write("[dim]Starting multi-agent deep research...[/dim]\n")
            self._run_deep_research(question)
        elif cmd == "/save":
            self._handle_save(arg)
        elif cmd == "/resume" or cmd == "/sessions":
            self._handle_resume(arg)
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

    def _handle_save(self, path_arg: str | None) -> None:
        """Handle /save command to export current session to markdown."""
        log = self.query_one("#chat-log", SafeRichLog)
        try:
            output_path = Path(path_arg).expanduser() if path_arg else None
            result_path = self._session.export_to_markdown(output_path=output_path)
            log.write(
                f"[bold green]Chat exported[/bold green] to [bold]{result_path}[/bold]\n"
            )
        except ValueError as e:
            log.write(f"[bold red]Cannot export:[/bold red] {e}\n")
        except Exception as e:
            write_error(log, e)

    def _handle_resume(self, arg: str | None) -> None:
        """Handle /resume and /sessions commands."""
        log = self.query_one("#chat-log", SafeRichLog)

        sessions = self._session.list_sessions()
        if not sessions:
            log.write("[dim]No saved sessions found.[/dim]\n")
            return

        # No argument: list recent sessions
        if arg is None:
            recent = sessions[:50]
            total = len(sessions)
            log.write("[bold yellow]Saved sessions:[/bold yellow]\n")
            for i, meta in enumerate(recent):
                log.write(
                    f"  [bold cyan]{i}[/bold cyan]  {meta.title}\n"
                    f"      {meta.updated_at}  |  {meta.message_count} messages  |  {meta.session_id[:12]}...\n"
                )
            if total > 50:
                log.write(f"[dim]Showing 50 of {total} sessions. Use /resume <session_id> for older ones.[/dim]\n")
            log.write(
                "[dim]Use /resume <number> or /resume <session_id> to load a session.[/dim]\n"
            )
            return

        # Try numeric index first
        if arg.isdigit():
            idx = int(arg)
            if 0 <= idx < len(sessions):
                self._load_session(sessions[idx].session_id, log)
                return
            else:
                log.write(f"[bold red]Invalid index:[/bold red] {idx}. Range: 0-{len(sessions) - 1}\n")
                return

        # Try matching by session ID prefix
        matches = [s for s in sessions if s.session_id.startswith(arg)]
        if len(matches) == 1:
            self._load_session(matches[0].session_id, log)
        elif len(matches) > 1:
            log.write(
                f"[bold red]Ambiguous session ID:[/bold red] {len(matches)} sessions match '{arg}'. "
                "Use a longer prefix.\n"
            )
        else:
            log.write(f"[bold red]No session found matching:[/bold red] {arg}\n")

    def _load_session(self, session_id: str, log: SafeRichLog) -> None:
        """Load a session, replay messages into the log, and set up context restoration."""
        messages = self._session.load_session(session_id)
        if not messages:
            log.write("[bold red]Session has no messages.[/bold red]\n")
            return

        meta = None
        for s in self._session.list_sessions():
            if s.session_id == session_id:
                meta = s
                break

        log.clear()
        log.write(
            f"[bold green]Session resumed:[/bold green] {meta.title if meta else session_id[:12]}\n"
        )

        for msg in messages:
            if msg.type == "user":
                log.write(f"[bold cyan]You:[/bold cyan] {msg.content}")
            elif msg.type == "assistant":
                log.write(msg.content)
                log.write("")
            elif msg.type == "tool_call":
                log.write(f"[dim italic]{msg.content}[/dim italic]")
            elif msg.type == "tool_result":
                log.write(f"[dim]{msg.content}[/dim]")
            elif msg.type == "error":
                log.write(f"[bold red]{msg.content}[/bold red]")
            elif msg.type == "system":
                log.write(f"[dim]{msg.content}[/dim]")

        log.write("")

        # Set resumed history for LLM context restoration on next message
        self._resumed_history = [m for m in messages if m.type in ("user", "assistant")]

        if getattr(self.app, "agent", None) is not None:
            self.app.agent.clear_history()

    _KNOWN_INTERVALS = frozenset(("1m", "5m", "15m", "30m", "1h", "4h", "1D", "1W", "1M"))

    def _handle_analyze(self, text: str, parts: list[str]) -> None:
        """Parse /analyze command and dispatch to _run_analyze."""
        log = self.query_one("#chat-log", SafeRichLog)
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

        # Persist user message for /analyze
        display_text = f"/analyze {ticker} {interval}"
        self._session.append_message(
            ChatMessage(
                ts=datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                type="user",
                content=display_text,
            )
        )

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
        log = self.query_one("#chat-log", SafeRichLog)
        try:
            if not self.app._ensure_agent():
                log.write(
                    "[bold yellow]API key not configured.[/bold yellow]\n"
                    "Set it in the Settings tab or run [bold]aipa setup[/bold]."
                )
                return

            # Build resumed context prefix (cleared after first use)
            prefix = self._build_resumed_prefix()

            await run_tui_analyze(
                log, self.app.agent, self.app.builder, ticker, interval,
                question_index=question_index,
                custom_question=custom_question,
                prefix=prefix,
                on_thinking_update=self._show_thinking_area,
                on_thinking_done=self._on_thinking_done,
                on_message=self._make_on_message(),
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

            log = self.query_one("#chat-log", SafeRichLog)
            write_export_result(log, str(filepath), len(context))
        except Exception as e:
            log = self.query_one("#chat-log", SafeRichLog)
            write_error(log, e)

    @work(exclusive=True)
    async def _run_agent_chat(self, message: str) -> None:
        """Stream an agent response into the chat log."""
        log = self.query_one("#chat-log", SafeRichLog)
        if not self.app._ensure_agent():
            log.write(
                "[bold yellow]API key not configured.[/bold yellow]\n"
                "Set it in the Settings tab or run [bold]aipa setup[/bold]."
            )
            return
        try:
            # Prepend resumed context if applicable
            prefix = self._build_resumed_prefix()
            if prefix:
                message = prefix + message

            await stream_agent_to_log(
                log,
                self.app.agent,
                message,
                on_thinking_update=self._show_thinking_area,
                on_thinking_done=self._on_thinking_done,
                on_message=self._make_on_message(),
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
        log = self.query_one("#chat-log", SafeRichLog)
        log.write(f"[dim]Thought for {len(text)} chars (Ctrl+O to view)[/dim]")

    @work(exclusive=True)
    async def _run_deep_research(self, question: str) -> None:
        """Run deep research pipeline and stream output to chat log."""
        from .deep_research import run_deep_research

        log = self.query_one("#chat-log", SafeRichLog)
        try:
            if not self.app._ensure_agent():
                log.write(
                    "[bold yellow]API key not configured.[/bold yellow]\n"
                    "Set it in the Settings tab or run [bold]aipa setup[/bold]."
                )
                return

            def _output(text: str) -> None:
                log.write(text)

            report = await run_deep_research(
                question=question,
                lang=getattr(self.app, "lang", None),
                output=_output,
            )
            # Persist assistant message
            self._session.append_message(
                ChatMessage(
                    ts=datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                    type="assistant",
                    content=report,
                )
            )
        except SystemExit:
            log.write("[bold red]OPENAI_API_KEY is not set.[/bold red]\n")
        except Exception as e:
            write_error(log, e)
