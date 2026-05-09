"""Settings tab: UI preferences and API configuration."""

import os

from textual.containers import Vertical, Horizontal
from textual.widgets import Static, Input, Button, Select

from aipriceaction.settings import settings

from .user_settings import load_settings, save_settings

# Maps SDK settings attribute -> settings.json key -> hint widget id
_API_FIELDS: list[tuple[str, str, str, str]] = [
    ("openai_api_key", "api_key", "OPENAI_API_KEY", "hint-api-key"),
    ("openai_base_url", "openai_base_url", "OPENAI_BASE_URL", "hint-base-url"),
    ("openai_model", "openai_model", "OPENAI_MODEL", "hint-model"),
]


def _value_from_env_or_dotenv(sdk_attr: str, settings_json_key: str) -> bool:
    """Check if a field's effective value comes from .env / env, not settings.json."""
    sdk_val = getattr(settings, sdk_attr, "")
    if not sdk_val:
        return False
    # If settings.json has a different (non-empty) value, the user explicitly
    # configured it there — treat settings.json as the source.
    saved_val = load_settings().get(settings_json_key, "")
    if saved_val and saved_val != sdk_val:
        return False
    # If settings.json is empty (or matches SDK), the value originates from
    # .env / env / SDK default.
    if not saved_val:
        return True
    return False


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
    .section-header {
        margin-top: 1;
        margin-bottom: 0;
        color: $text-muted;
    }
    .env-hint {
        color: $text-muted;
        padding-left: 12;
        height: auto;
        text-style: italic;
    }
    """

    def compose(self):
        yield Static("[bold]Settings[/bold]")
        yield Static("")
        yield Static("[dim]─ General ─[/dim]", classes="section-header")
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
        yield Static("")
        yield Static("[dim]─ API Configuration ─[/dim]", classes="section-header")
        with Horizontal(classes="setting-row"):
            yield Static("API Key:", classes="setting-label")
            yield Input(value="", password=True, id="setting-api-key", classes="setting-input")
        yield Static("", id="hint-api-key", classes="env-hint")
        with Horizontal(classes="setting-row"):
            yield Static("Base URL:", classes="setting-label")
            yield Input(value="", id="setting-base-url", classes="setting-input")
        yield Static("", id="hint-base-url", classes="env-hint")
        with Horizontal(classes="setting-row"):
            yield Static("Model:", classes="setting-label")
            yield Input(value="", id="setting-model", classes="setting-input")
        yield Static("", id="hint-model", classes="env-hint")
        yield Button("Apply", id="apply-btn", variant="primary")

    def on_mount(self) -> None:
        """Pre-fill fields from persisted settings and show source hints."""
        saved = load_settings()
        self.query_one("#setting-ticker", Input).value = saved.get("ticker", "VNINDEX")
        self.query_one("#setting-interval", Select).value = saved.get("interval", "1D")
        self.query_one("#setting-language", Select).value = saved.get("language", "en")
        self.query_one("#setting-api-key", Input).value = saved.get("api_key", "")
        self.query_one("#setting-base-url", Input).value = saved.get("openai_base_url", "")
        self.query_one("#setting-model", Input).value = saved.get("openai_model", "")

        self._update_hints()

    def _update_hints(self) -> None:
        """Show the source of each API field value."""
        for sdk_attr, _json_key, env_var, hint_id in _API_FIELDS:
            if _value_from_env_or_dotenv(sdk_attr, _json_key):
                source = os.environ.get(env_var) and "environment" or ".env file"
                self.query_one(f"#{hint_id}", Static).update(
                    f"[green]Using {env_var} from {source}[/]"
                )
            else:
                self.query_one(f"#{hint_id}", Static).update(
                    "[dim]Value from settings (saved below)[/]"
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-btn":
            ticker = self.query_one("#setting-ticker", Input).value.strip().upper()
            interval = self.query_one("#setting-interval", Select).value
            language = self.query_one("#setting-language", Select).value
            api_key = self.query_one("#setting-api-key", Input).value.strip()
            base_url = self.query_one("#setting-base-url", Input).value.strip()
            model = self.query_one("#setting-model", Input).value.strip()

            if ticker:
                self.app.ticker = ticker
            if interval:
                self.app.interval = interval
            if language:
                self.app.language = language
                from aipriceaction import AIContextBuilder
                self.app.builder = AIContextBuilder(lang=language)
                try:
                    from .agents import AgentSession, AgentConfig
                    self.app.agent = AgentSession(AgentConfig(lang=language))
                    self.app._agent_lang = language
                except Exception:
                    self.app.agent = None
                    self.app._agent_lang = None

            data: dict = {
                "ticker": ticker,
                "interval": interval,
                "language": language,
            }
            # Only persist API fields when .env/env is NOT providing the value,
            # so external config always takes priority.
            _field_values = {"api_key": api_key, "openai_base_url": base_url, "openai_model": model}
            for sdk_attr, json_key, env_var, _hint_id in _API_FIELDS:
                if not _value_from_env_or_dotenv(sdk_attr, json_key):
                    data[json_key] = _field_values[json_key]

            save_settings(data)
            self._update_hints()

            parts = [f"{ticker} / {interval} / {language}"]
            has_api_key = settings.openai_api_key or api_key
            if has_api_key:
                parts.append("API key set")
            self.app.notify(f"Settings applied: {', '.join(parts)}")
