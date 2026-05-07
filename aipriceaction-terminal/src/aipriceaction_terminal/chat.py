"""Chat tab: message history + input + slash commands."""

import asyncio
from datetime import datetime
from pathlib import Path

from textual import work
from textual.widgets import RichLog, Input
from textual.containers import Vertical

from .widgets import ChatInput
from .utils import write_context_result, write_error, write_export_result


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

    def compose(self):
        yield RichLog(id="chat-log", highlight=True, markup=True)
        yield ChatInput(id="chat-input")

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

        # Save to history via ChatInput
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.push_history(text)

        log = self.query_one("#chat-log", RichLog)

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            log.write(f"[bold cyan]You:[/bold cyan] {text}")
            log.write(
                "[dim italic]AI responses not yet implemented. "
                "Use /analyze to build ticker context.[/dim italic]\n"
            )

    def _handle_slash_command(self, text: str) -> None:
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        log = self.query_one("#chat-log", RichLog)

        if cmd == "/help":
            log.write(
                "[bold yellow]Available commands:[/bold yellow]\n"
                "  /analyze <ticker> [interval] - Build AI context (e.g. /analyze VIC or /analyze STB 1h)\n"
                "  /export <ticker> [tickers...] [--interval 1D] [--path ~/dir/]\n"
                "                        - Export AI context to markdown file\n"
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
