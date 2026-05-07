"""Ticker autocomplete widget using textual-autocomplete."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Input
from textual_autocomplete import AutoComplete, DropdownItem


class _TickerAutoComplete(AutoComplete):
    """AutoComplete that fills the input with the ticker symbol (option.id) on selection."""

    def _complete(self, option_index: int) -> None:
        """Override to use option.id (ticker symbol) instead of option.value (label)."""
        if not self.display or self.option_list.option_count == 0:
            return
        option_list = self.option_list
        option = option_list.get_option_at_index(option_index)
        # Use option.id (the ticker symbol) if available, fall back to option.value (label)
        completion_value = getattr(option, "id", None) or option.value
        with self.prevent(Input.Changed):
            self.apply_completion(completion_value, self._get_target_state())
        self.post_completion()


class TickerSelect(Vertical):
    """Autocomplete ticker input with filtered dropdown."""

    DEFAULT_CSS = """
    TickerSelect {
        width: 1fr;
        height: auto;
    }
    TickerSelect Input {
        width: 1fr;
    }
    """

    def __init__(self, value: str = "VNINDEX", **kwargs):
        super().__init__(**kwargs)
        self._desired_value = value
        self._all_options: list[tuple[str, str]] = [(value, value)]

    def compose(self):
        yield Input(value=self._desired_value, id="ticker-input")
        yield _TickerAutoComplete(
            target="#ticker-input",
            candidates=self._get_candidates,
            id="ticker-autocomplete",
        )

    def on_mount(self) -> None:
        self.watch(self.app, "ticker_options", self._on_ticker_options, init=False)

    # --- value property -------------------------------------------------------

    @property
    def value(self) -> str:
        return self.query_one("#ticker-input", Input).value.strip()

    @value.setter
    def value(self, val: str) -> None:
        self.query_one("#ticker-input", Input).value = val

    # --- Options loading ------------------------------------------------------

    def _on_ticker_options(self, old_value, new_value: list[tuple[str, str]]) -> None:
        """React when the app loads ticker options."""
        self._all_options = new_value
        if self._desired_value and any(v == self._desired_value for _, v in new_value):
            self.value = self._desired_value
        else:
            self.value = new_value[0][1] if new_value else ""

    # --- Candidate provider for AutoComplete -----------------------------------

    def _get_candidates(self, state) -> list[DropdownItem]:
        """Return filtered dropdown items based on current input text."""
        query = state.text.strip().lower()
        matches = []
        for label, val in self._all_options:
            if not query or query in label.lower() or query in val.lower():
                matches.append(DropdownItem(label, id=val))
        return matches
