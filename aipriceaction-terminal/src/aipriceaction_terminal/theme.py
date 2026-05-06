"""Custom Dracula-inspired green theme and screen CSS."""

from textual.theme import Theme

AI_GREEN = Theme(
    name="ai-green",
    primary="#50fa7b",
    secondary="#8be9fd",
    accent="#50fa7b",
    warning="#f1fa8c",
    error="#ff5555",
    success="#50fa7b",
    background="#1a1e2e",
    surface="#282a36",
    panel="#44475a",
    dark=True,
)

SCREEN_CSS = """
Screen {
    layout: vertical;
}
TabbedContent {
    height: 1fr;
}
TabbedContent TabPane {
    height: 1fr;
}
"""
