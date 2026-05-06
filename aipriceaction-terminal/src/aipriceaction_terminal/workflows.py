"""Workflows tab: nested tabs for different workflow types."""

import asyncio

from textual import work
from textual.containers import Vertical, Horizontal
from textual.widgets import (
    Static, RichLog, Input, Button, Select, TabbedContent, TabPane,
)

from .utils import write_context_result, write_error


class AnalyzePane(Vertical):
    """Single-ticker context analysis workflow."""

    DEFAULT_CSS = """
    AnalyzePane {
        padding: 1 2;
    }
    .wf-row {
        height: auto;
        margin-bottom: 1;
    }
    #wf-output {
        height: 1fr;
        border: solid $accent;
        padding: 1;
    }
    .wf-label {
        width: 10;
        height: auto;
    }
    .wf-input-short {
        width: 20;
    }
    """


    def compose(self):
        with Horizontal(classes="wf-row"):
            yield Static("Ticker:", classes="wf-label")
            yield Input(value="VNINDEX", id="wf-ticker", classes="wf-input-short")
            yield Static("Interval:", classes="wf-label")
            yield Select(
                [("1m", "1m"), ("1h", "1h"), ("1D", "1D")],
                value="1D",
                allow_blank=False,
                id="wf-interval",
            )
        with Horizontal(classes="wf-row"):
            yield Button("Analyze", id="wf-analyze-btn", variant="primary")
        yield RichLog(id="wf-output", highlight=True, markup=True)

    def on_mount(self) -> None:
        self.query_one("#wf-ticker", Input).value = self.app.ticker
        self.query_one("#wf-interval", Select).value = self.app.interval
        self.query_one("#wf-output", RichLog).write(
            "[dim italic]Enter a ticker and click Analyze to build AI context.[/dim italic]\n"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "wf-analyze-btn":
            return
        self._do_analyze()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "wf-ticker":
            self._do_analyze()

    def _do_analyze(self) -> None:
        ticker = self.query_one("#wf-ticker", Input).value.strip().upper()
        interval = self.query_one("#wf-interval", Select).value
        if not ticker:
            self.app.notify("Please enter a ticker symbol", severity="error")
            return
        log = self.query_one("#wf-output", RichLog)
        log.write(f"[bold cyan]Analyze:[/bold cyan] {ticker} ({interval})")
        log.write("[dim]Building context...[/dim]")
        self._run_analyze(ticker, interval)

    @work(exclusive=True)
    async def _run_analyze(self, ticker: str, interval: str) -> None:
        try:
            builder = self.app.builder
            context = await asyncio.to_thread(
                builder.build, ticker=ticker, interval=interval
            )
            log = self.query_one("#wf-output", RichLog)
            write_context_result(log, ticker, interval, context)
        except Exception as e:
            log = self.query_one("#wf-output", RichLog)
            write_error(log, e)


class DeepResearchPane(Vertical):
    """Multi-agent deep research workflow."""

    DEFAULT_CSS = """
    DeepResearchPane {
        padding: 1 2;
    }
    .dr-row {
        height: auto;
        margin-bottom: 1;
    }
    .dr-label {
        width: 10;
        height: auto;
    }
    #dr-question {
        width: 1fr;
    }
    #dr-output {
        height: 1fr;
        border: solid $accent;
        padding: 1;
    }
    #dr-btn {
        margin-left: 1;
    }
    """

    def compose(self):
        with Horizontal(classes="dr-row"):
            yield Static("Question:", classes="dr-label")
            yield Input(
                value="",
                placeholder="Enter research question...",
                id="dr-question",
            )
            yield Button("Deep Research", id="dr-btn", variant="success")
        yield RichLog(id="dr-output", highlight=True, markup=True)

    def on_mount(self) -> None:
        self.query_one("#dr-output", RichLog).write(
            "[bold yellow]Deep research is not yet implemented.[/bold yellow]\n\n"
            "[dim]This will run the multi-agent LangGraph pipeline:\n"
            "  supervisor -> parallel workers -> aggregator -> reviewer\n\n"
            "Enter a question and click Deep Research when ready.[/dim]\n"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "dr-btn":
            return
        self._do_deep_research()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "dr-question":
            self._do_deep_research()

    def _do_deep_research(self) -> None:
        question = self.query_one("#dr-question", Input).value.strip()
        log = self.query_one("#dr-output", RichLog)
        log.write(
            f"[bold cyan]Deep Research:[/bold cyan]"
            + (f" {question}" if question else "")
        )
        log.write(
            "[bold yellow]Not yet implemented.[/bold yellow]\n"
            "[dim]Will trigger multi_agent.main() in a future update.[/dim]\n"
        )


class WorkflowsTab(Vertical):
    """Workflows container with nested tabs for each workflow type."""

    DEFAULT_CSS = """
    WorkflowsTab {
        height: 100%;
    }
    WorkflowsTab TabbedContent {
        height: 1fr;
    }
    """

    def compose(self):
        with TabbedContent(initial="wf-analyze"):
            with TabPane("Analyze", id="wf-analyze"):
                yield AnalyzePane()
            with TabPane("Deep Research", id="wf-deep-research"):
                yield DeepResearchPane()
