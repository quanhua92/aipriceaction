"""Main application: TabbedContent shell with shared state."""

import asyncio

from textual import work
from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import TabbedContent, TabPane, Header, Footer, Input, Select

from .bindings import BINDINGS
from .actions import AppActions
from .theme import AI_GREEN, SCREEN_CSS
from .chat import ChatTab
from .workflows import WorkflowsTab
from .ticker_data import TickerDataTab
from .settings_tab import SettingsTab
from .user_settings import load_settings


class AIPriceActionApp(AppActions, App):
    """AIPriceAction Terminal TUI."""

    TITLE = "AIPriceAction Terminal"
    SUB_TITLE = "AI-powered ticker analysis"
    CSS = SCREEN_CSS
    BINDINGS = BINDINGS

    # Reactive state shared across tabs
    ticker: reactive[str] = reactive("VNINDEX")
    interval: reactive[str] = reactive("1D")
    language: reactive[str] = reactive("en")
    ticker_options: reactive[list[tuple[str, str]]] = reactive(list)

    def __init__(self, resume_session: str | None = None, **kwargs):
        self._resume_session = resume_session
        super().__init__(**kwargs)

    def on_mount(self) -> None:
        self.register_theme(AI_GREEN)
        self.theme = "ai-green"
        saved = load_settings()
        self.ticker = saved["ticker"]
        self.interval = saved["interval"]
        self.language = saved["language"]
        self.agent = None
        self._agent_lang: str | None = None
        from aipriceaction import AIContextBuilder
        from aipriceaction import AIPriceAction as AAPClient
        from .user_settings import resolve_ma_type
        self.builder = AIContextBuilder(lang=self.language, ma_type=resolve_ma_type())
        self.client = AAPClient(use_live=True)
        self._load_ticker_options()
        # Populate SettingsTab widgets with loaded values
        self.query_one("#setting-ticker", Input).value = self.ticker
        self.query_one("#setting-interval", Select).value = self.interval
        self.query_one("#setting-language", Select).value = self.language
        self.query_one("#chat-input-field", Input).focus()

    @work(exclusive=True)
    async def _load_ticker_options(self) -> None:
        """Load ticker list from SDK and populate ticker_options reactive."""
        try:
            tickers = await asyncio.to_thread(self.client.get_tickers)
            source_tags = {"vn": "[VN]", "crypto": "[CRYPTO]", "yahoo": "[YH]", "sjc": "[SJC]"}
            options = []
            for t in tickers:
                tag = source_tags.get(t.source, f"[{t.source.upper()}]")
                label = f"{tag} {t.ticker}"
                if t.name:
                    label += f" - {t.name}"
                options.append((label, t.ticker))
            options.sort(key=lambda x: x[0])
            self.ticker_options = options
        except Exception as e:
            self.notify(f"Failed to load tickers: {e}", severity="error")

    def _ensure_agent(self) -> bool:
        """Lazy-create AgentSession on first use. Returns False if API key is missing."""
        if self.agent is not None and self._agent_lang == self.language:
            return True
        from aipriceaction.settings import settings
        if not settings.openai_api_key:
            self.agent = None
            return False
        from .agents import AgentSession, AgentConfig
        self.agent = AgentSession(AgentConfig(lang=self.language))
        self._agent_lang = self.language
        return True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="chat"):
            with TabPane("Chat", id="chat"):
                yield ChatTab(resume_session=self._resume_session)
            with TabPane("Workflows", id="workflows"):
                yield WorkflowsTab()
            with TabPane("Vietnam", id="tickers-vn"):
                yield TickerDataTab(mode="vn")
            with TabPane("Crypto", id="tickers-crypto"):
                yield TickerDataTab(mode="crypto")
            with TabPane("Global", id="tickers-global"):
                yield TickerDataTab(mode="global")
            with TabPane("Settings", id="settings"):
                yield SettingsTab()
        yield Footer()


def main(resume_session: str | None = None):
    """Entry point."""
    app = AIPriceActionApp(resume_session=resume_session)
    app.run()


if __name__ == "__main__":
    main()
