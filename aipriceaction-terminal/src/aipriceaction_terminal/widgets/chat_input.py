"""Chat input with slash command autocomplete and message history."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Input
from textual_autocomplete import AutoComplete, DropdownItem


_COMMANDS: list[tuple[str, str]] = [
    ("/analyze <ticker> [interval]", "/analyze"),
    ("/clear", "/clear"),
    ("/deep-research [question]", "/deep-research"),
    ("/exit", "/exit"),
    ("/export <ticker> [ticker...] [--interval] [--path]", "/export"),
    ("/help", "/help"),
]


class _CommandAutoComplete(AutoComplete):
    """AutoComplete that only activates for slash commands."""

    DEFAULT_CSS = """
    _CommandAutoComplete AutoCompleteList {
        max-height: 10;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(prevent_default_enter=True, **kwargs)

    def should_show_dropdown(self, search_string: str) -> bool:
        """Only show dropdown when input starts with /."""
        return search_string.startswith("/") and self.option_list.option_count > 0

    def _complete(self, option_index: int) -> None:
        """Fill input with command name + trailing space."""
        if not self.display or self.option_list.option_count == 0:
            return
        option_list = self.option_list
        option = option_list.get_option_at_index(option_index)
        completion_value = getattr(option, "id", None) or option.value
        # Append trailing space so user can immediately type args
        completion_value += " "
        with self.prevent(Input.Changed):
            self.apply_completion(completion_value, self._get_target_state())
        self.post_completion()


class ChatInput(Vertical):
    """Chat input with slash command autocomplete and message history."""

    DEFAULT_CSS = """
    ChatInput {
        height: auto;
    }
    ChatInput Input {
        width: 1fr;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._history: list[str] = []
        self._history_index: int = -1

    def compose(self):
        yield Input(placeholder="Type a message or /analyze <ticker>...", id="chat-input-field")
        yield _CommandAutoComplete(
            target="#chat-input-field",
            candidates=self._get_candidates,
        )

    # --- value property -------------------------------------------------------

    @property
    def value(self) -> str:
        return self.query_one("#chat-input-field", Input).value

    @value.setter
    def value(self, val: str) -> None:
        self.query_one("#chat-input-field", Input).value = val

    # --- Candidate provider for AutoComplete -----------------------------------

    def _get_candidates(self, state) -> list[DropdownItem]:
        """Return matching slash commands based on input text."""
        text = state.text.strip()
        if not text.startswith("/"):
            return []
        # Extract the command word (before any space / args)
        cmd_word = text.split()[0]
        query = cmd_word[1:].lower()
        # If the command is already a complete match, no need to suggest
        for _, cmd in _COMMANDS:
            if cmd[1:].lower() == query:
                return []
        matches = []
        for label, cmd in _COMMANDS:
            if query in cmd[1:].lower():
                matches.append(DropdownItem(label, id=cmd))
        return matches

    # --- History management ---------------------------------------------------

    def push_history(self, text: str) -> None:
        """Add a submitted message to history (call from ChatTab on submit)."""
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._history_index = -1

    def _history_up(self) -> None:
        if not self._history:
            return
        if self._history_index == -1:
            self._draft = self.query_one("#chat-input-field", Input).value
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        else:
            return
        self.query_one("#chat-input-field", Input).value = self._history[self._history_index]

    def _history_down(self) -> None:
        if self._history_index == -1:
            return
        self._history_index += 1
        if self._history_index >= len(self._history):
            self._history_index = -1
            self.query_one("#chat-input-field", Input).value = getattr(self, "_draft", "")
        else:
            self.query_one("#chat-input-field", Input).value = self._history[self._history_index]

    def on_key(self, event) -> None:
        """Arrow up/down for history (only when autocomplete dropdown is closed)."""
        if self.app.focused != self.query_one("#chat-input-field", Input):
            return
        autocomplete = self.query_one(_CommandAutoComplete)
        # Let autocomplete handle arrow keys when dropdown is visible
        if autocomplete.display:
            return
        if event.key == "up":
            self._history_up()
            event.stop()
        elif event.key == "down":
            self._history_down()
            event.stop()
