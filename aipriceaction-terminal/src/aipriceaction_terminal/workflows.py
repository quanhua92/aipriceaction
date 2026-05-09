"""Workflows tab: nested tabs for different workflow types."""

from textual import work
from textual.containers import Vertical, Horizontal
from textual.widgets import (
    Static, RichLog, Input, Button, Select, TabbedContent, TabPane,
)
from textual import on

from .utils import write_error
from .analyze import run_tui_analyze
from .widgets import TickerSelect


class AnalyzePane(Vertical):
    """Single-ticker AI analysis workflow."""

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
    #wf-custom-question {
        width: 1fr;
    }
    """


    def compose(self):
        with Horizontal(classes="wf-row"):
            yield Static("Ticker:", classes="wf-label")
            yield TickerSelect(value="VNINDEX", id="wf-ticker")
            yield Static("Interval:", classes="wf-label")
            yield Select(
                [("1m", "1m"), ("1h", "1h"), ("1D", "1D")],
                value="1D",
                allow_blank=False,
                id="wf-interval",
            )
        with Horizontal(classes="wf-row"):
            yield Static("Question:", classes="wf-label")
            yield Select(
                [("Default", "default")],
                value="default",
                allow_blank=False,
                id="wf-question",
            )
            yield Input(
                placeholder="Custom question (overrides dropdown)...",
                id="wf-custom-question",
            )
        with Horizontal(classes="wf-row"):
            yield Button("Analyze", id="wf-analyze-btn", variant="primary")
        yield RichLog(id="wf-output", highlight=True, markup=True)

    def on_mount(self) -> None:
        interval_select = self.query_one("#wf-interval", Select)
        if hasattr(self.app, "interval"):
            interval_select.value = self.app.interval
        if hasattr(self.app, "builder"):
            self._populate_question_select()
        self.query_one("#wf-output", RichLog).write(
            "[dim italic]Select a ticker, pick a question (optional), and click Analyze.[/dim italic]\n"
        )

    def _populate_question_select(self) -> None:
        """Populate question dropdown from the question bank."""
        builder = self.app.builder
        templates = builder.questions("single")
        options = [(t["title"], str(i)) for i, t in enumerate(templates)]
        question_select = self.query_one("#wf-question", Select)
        question_select.set_options(options)

    @on(Input.Changed, "#wf-custom-question")
    def _on_custom_question_changed(self, event: Input.Changed) -> None:
        """When user types a custom question, reset dropdown to Default."""
        if event.input.value.strip():
            self.query_one("#wf-question", Select).value = "default"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "wf-analyze-btn":
            return
        self._do_analyze()

    def _do_analyze(self) -> None:
        ticker = self.query_one("#wf-ticker", TickerSelect).value
        interval = self.query_one("#wf-interval", Select).value

        # Resolve question: custom question overrides dropdown
        custom_question = self.query_one("#wf-custom-question", Input).value.strip()
        question_select_value = self.query_one("#wf-question", Select).value
        question_index: int | None = None
        if not custom_question and question_select_value not in ("default", None):
            question_index = int(question_select_value)

        log = self.query_one("#wf-output", RichLog)
        q_label = custom_question[:50] if custom_question else f"template {question_index or 0}"
        log.write(f"[bold cyan]Analyze:[/bold cyan] {ticker} ({interval}) [{q_label}]")
        log.write("[dim]Building context and analyzing...[/dim]")
        self._run_analyze(ticker, interval, question_index=question_index, custom_question=custom_question or None)

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
        log = self.query_one("#wf-output", RichLog)
        try:
            if not self.app._ensure_agent():
                log.write("[red]Error: No API key configured. Run 'aipa setup' or set OPENAI_API_KEY.[/red]")
                return
            await run_tui_analyze(
                log, self.app.agent, self.app.builder, ticker, interval,
                question_index=question_index,
                custom_question=custom_question,
            )
        except Exception as e:
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
            "[bold cyan]Deep Research:[/bold cyan]"
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
