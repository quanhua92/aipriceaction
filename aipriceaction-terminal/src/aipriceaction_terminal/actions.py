"""Action handlers for AIPriceActionApp."""

import time

from textual.widgets import TabbedContent, Input, Select, Button, TextArea


class AppActions:
    """Mixin providing all action_* methods for AIPriceActionApp."""

    _quit_requested_at: float = 0.0

    def action_switch_tab(self, tab_id: str) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = tab_id

    def action_focus_none(self) -> None:
        """Blur any focused widget, or dismiss a modal if one is showing."""
        from .chat import ThinkingModal
        if isinstance(self.screen_stack[-1], ThinkingModal):
            self.pop_screen()
            return
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
