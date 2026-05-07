"""Ticker autocomplete widget using textual-autocomplete."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Input
from textual_autocomplete import AutoComplete, DropdownItem


class _TickerAutoComplete(AutoComplete):
    """AutoComplete that fills the input with the ticker symbol (option.id) on selection."""

    DEFAULT_CSS = """
    _TickerAutoComplete AutoCompleteList {
        max-height: 20;
    }
    """

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

    def should_show_dropdown(self, search_string: str) -> bool:
        """Show dropdown even when search string is empty (show all on focus)."""
        return self.option_list.option_count > 0

    def get_matches(
        self,
        target_state,
        candidates: list[DropdownItem],
        search_string: str,
    ) -> list[DropdownItem]:
        """Override to boost ticker symbol matches above company name matches.

        The default fuzzy matcher only sees the label (e.g. '[VN] VIC - Tập đoàn
        VINGROUP') and can rank VICEM companies above the actual VIC ticker.
        We add a score boost when the query matches the ticker symbol (option.id).
        """
        if not search_string:
            return list(candidates)

        query_lower = search_string.lower()
        boosted: list[tuple[DropdownItem, float]] = []
        match = self._fuzzy_search.match

        for candidate in candidates:
            label_score, offsets = match(query_lower, candidate.value)
            # Check if query matches the ticker symbol (option.id)
            ticker_id = getattr(candidate, "id", None) or ""
            id_match = query_lower in ticker_id.lower() if ticker_id else False

            if id_match and label_score > 0:
                # Ticker symbol match + label match → highest priority
                final_score = 2.0 + label_score
            elif id_match:
                # Ticker symbol matches but label doesn't (e.g. short ticker)
                final_score = 1.5
            elif label_score > 0:
                final_score = label_score
            else:
                continue

            highlighted = self.apply_highlights(candidate.main, offsets)
            item = type(candidate)(
                main=highlighted,
                prefix=candidate.prefix,
                id=candidate.id,
                disabled=candidate.disabled,
            )
            boosted.append((item, final_score))

        boosted.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in boosted]


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
        """Return all options; the library's fuzzy matcher handles filtering."""
        return [DropdownItem(label, id=val) for label, val in self._all_options]
