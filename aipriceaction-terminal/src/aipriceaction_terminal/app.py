"""Main application: TabbedContent shell with shared state."""

import asyncio
import time

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.reactive import reactive
from textual.widgets import TabbedContent, TabPane, Header, Footer, Input, Select, Button, TextArea

from .theme import AI_GREEN, SCREEN_CSS
from .chat import ChatTab
from .workflows import WorkflowsTab
from .ticker_data import TickerDataTab
from .settings_tab import SettingsTab


class AIPriceActionApp(App):
    """AIPriceAction Terminal TUI."""

    TITLE = "AIPriceAction Terminal"
    SUB_TITLE = "AI-powered ticker analysis"
    CSS = SCREEN_CSS

    # Reactive state shared across tabs
    ticker: reactive[str] = reactive("VNINDEX")
    interval: reactive[str] = reactive("1D")
    language: reactive[str] = reactive("en")
    ticker_options: reactive[list[tuple[str, str]]] = reactive(list)

    _quit_requested_at: float = 0.0

    # -- Key bindings (whichkey-style footer) --
    BINDINGS: list[BindingType] = [
        Binding("ctrl+q", "confirm_quit", "Quit", key_display="ctrl+q", priority=True),
        Binding("1", "switch_tab('chat')", "Chat", key_display="1"),
        Binding("2", "switch_tab('tickers-vn')", "Vietnam", key_display="2"),
        Binding("3", "switch_tab('tickers-crypto')", "Crypto", key_display="3"),
        Binding("4", "switch_tab('tickers-global')", "Global", key_display="4"),
        Binding("5", "switch_tab('workflows')", "Workflows", key_display="5"),
        Binding("6", "switch_tab('settings')", "Settings", key_display="6"),
        Binding("escape", "focus_none", "Back", key_display="esc", priority=True),
        Binding("enter", "focus_first_input", "Focus input", key_display="enter"),
        Binding("?", "show_help", "Help", key_display="?"),
    ]

    def on_mount(self) -> None:
        self.register_theme(AI_GREEN)
        self.theme = "ai-green"
        from aipriceaction import AIContextBuilder
        from aipriceaction import AIPriceAction as AAPClient
        self.builder = AIContextBuilder(lang=self.language)
        self.client = AAPClient()
        self._load_ticker_options()
        self.query_one("#chat-input", Input).focus()

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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="chat"):
            with TabPane("Chat", id="chat"):
                yield ChatTab()
            with TabPane("Vietnam", id="tickers-vn"):
                yield TickerDataTab(mode="vn")
            with TabPane("Crypto", id="tickers-crypto"):
                yield TickerDataTab(mode="crypto")
            with TabPane("Global", id="tickers-global"):
                yield TickerDataTab(mode="global")
            with TabPane("Workflows", id="workflows"):
                yield WorkflowsTab()
            with TabPane("Settings", id="settings"):
                yield SettingsTab()
        yield Footer()

    def action_switch_tab(self, tab_id: str) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = tab_id

    def action_focus_none(self) -> None:
        """Blur any focused widget to restore app-level key bindings."""
        self.set_focus(None)

    def action_focus_first_input(self) -> None:
        """Focus the first Input or Select in the active tab (respects nested tabs)."""
        # Let Enter pass through to widgets that handle it themselves
        if isinstance(self.focused, (Input, Select, Button, TextArea)):
            return

        tabs = self.query_one(TabbedContent)
        active_pane = tabs.query(f"TabPane#{tabs.active}").first()
        if active_pane is None:
            return

        # Find the innermost active TabbedContent within this pane
        container = active_pane
        try:
            nested = active_pane.query_one("TabbedContent")
            inner_pane = nested.query(f"TabPane#{nested.active}").first()
            if inner_pane:
                container = inner_pane
        except Exception:
            pass

        try:
            first_input = container.query(Input).first()
            first_input.focus()
            return
        except Exception:
            pass
        try:
            first_select = container.query(Select).first()
            first_select.focus()
        except Exception:
            pass

    def action_confirm_quit(self) -> None:
        """Quit on second press within 2 seconds, otherwise show warning."""
        now = time.monotonic()
        if now - self._quit_requested_at < 2.0:
            self.exit()
        else:
            self._quit_requested_at = now
            self.notify("Press ctrl+q again to quit", severity="warning")

    def action_show_help(self) -> None:
        self.app.notify(
            "1-6: Switch tabs | ctrl+q: Quit | "
            "esc: Back | enter: Focus input | "
            "Chat: /help for commands",
            title="Keyboard Shortcuts",
        )


def main():
    """Entry point."""
    app = AIPriceActionApp()
    app.run()


if __name__ == "__main__":
    main()
