"""Ticker data tab: placeholder for raw OHLCV data view."""

from textual.containers import Vertical
from textual.widgets import Static

_MODE_LABELS = {
    "vn": "VN Tickers",
    "crypto": "Crypto",
    "global": "Global",
}


class TickerDataTab(Vertical):
    """Display raw OHLCV data for a ticker source."""

    DEFAULT_CSS = """
    TickerDataTab {
        padding: 1 2;
    }
    """

    def __init__(self, mode: str = "vn", **kwargs):
        super().__init__(**kwargs)
        self.mode = mode

    def compose(self):
        label = _MODE_LABELS.get(self.mode, self.mode)
        yield Static(f"[bold]{label}[/bold]", id="td-title")
        yield Static(
            "[dim italic]This tab will display ticker data in a table format.\n"
            "Not yet implemented.[/dim italic]",
            id="td-hint",
        )
