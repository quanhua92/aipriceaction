"""Key bindings and tab shortcuts for AIPriceActionApp."""

from textual.binding import Binding, BindingType

_TAB_KEYS: dict[str, str] = {
    "1": "chat",
    "2": "tickers-vn",
    "3": "tickers-crypto",
    "4": "tickers-global",
    "5": "workflows",
    "6": "settings",
}

BINDINGS: list[BindingType] = [
    Binding("ctrl+q", "confirm_quit", "Quit", key_display="ctrl+q", priority=True),
    Binding("ctrl+c", "cancel_stream", "Stop chat", key_display="ctrl+c", priority=True),
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
