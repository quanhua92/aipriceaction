"""Settings tab: placeholder for configuration."""

from textual.containers import Vertical, Horizontal
from textual.widgets import Static, Input, Button, Select

from .user_settings import save_settings


class SettingsTab(Vertical):
    """Settings configuration."""

    DEFAULT_CSS = """
    SettingsTab {
        padding: 1 2;
    }
    .setting-row {
        height: auto;
        margin-bottom: 1;
    }
    #apply-btn {
        margin-top: 1;
    }
    .setting-label {
        width: 12;
    }
    .setting-input {
        width: 20;
    }
    #setting-language {
        width: 20;
    }
    """

    def compose(self):
        yield Static("[bold]Settings[/bold] (placeholder)")
        yield Static("")
        with Horizontal(classes="setting-row"):
            yield Static("Ref Ticker:", classes="setting-label")
            yield Input(value="VNINDEX", id="setting-ticker", classes="setting-input")
        with Horizontal(classes="setting-row"):
            yield Static("Interval:", classes="setting-label")
            yield Select(
                [("1m", "1m"), ("1h", "1h"), ("1D", "1D")],
                value="1D",
                allow_blank=False,
                id="setting-interval",
            )
        with Horizontal(classes="setting-row"):
            yield Static("Language:", classes="setting-label")
            yield Select(
                [("English", "en"), ("Tiếng Việt", "vn")],
                value="en",
                allow_blank=False,
                id="setting-language",
            )
        yield Button("Apply", id="apply-btn", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-btn":
            ticker = self.query_one("#setting-ticker", Input).value.strip().upper()
            interval = self.query_one("#setting-interval", Select).value
            language = self.query_one("#setting-language", Select).value

            if ticker:
                self.app.ticker = ticker
            if interval:
                self.app.interval = interval
            if language:
                self.app.language = language
                from aipriceaction import AIContextBuilder
                self.app.builder = AIContextBuilder(lang=language)
                from .agents import AgentSession, AgentConfig
                self.app.agent = AgentSession(AgentConfig(lang=language))

            self.app.notify(f"Settings applied: {ticker} / {interval} / {language}")
            save_settings({"ticker": ticker, "interval": interval, "language": language})
