"""Custom user watchlists stored in ~/.aipriceaction/watchlist.json."""

import json
from pathlib import Path

from .predefined_watchlists import is_predefined

_CONFIG_DIR = Path.home() / ".aipriceaction"
_WATCHLIST_FILE = _CONFIG_DIR / "watchlist.json"


def load_watchlists() -> dict[str, list[str]]:
    if _WATCHLIST_FILE.exists():
        return json.loads(_WATCHLIST_FILE.read_text())
    return {}


def save_watchlists(data: dict[str, list[str]]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _WATCHLIST_FILE.write_text(json.dumps(data, indent=2))


def get_watchlist(name: str) -> list[str] | None:
    name = name.upper()
    from .predefined_watchlists import get_predefined_tickers

    predefined = get_predefined_tickers(name)
    if predefined:
        return predefined
    custom = load_watchlists()
    return custom.get(name)


def set_watchlist(name: str, tickers: list[str]) -> None:
    name = name.upper()
    if is_predefined(name):
        msg = f"'{name}' is a predefined watchlist and cannot be overwritten"
        raise ValueError(msg)
    watchlists = load_watchlists()
    watchlists[name] = tickers
    save_watchlists(watchlists)


def delete_watchlist(name: str) -> bool:
    name = name.upper()
    watchlists = load_watchlists()
    if name in watchlists:
        del watchlists[name]
        save_watchlists(watchlists)
        return True
    return False
