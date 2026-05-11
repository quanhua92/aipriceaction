"""Top/worst performers analysis — port of Rust performers.rs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


INDEX_TICKERS: frozenset[str] = frozenset({
    # Exchange boards
    "VNINDEX",
    # Blue-chip / size-based
    "VN30", "VN30F1M", "HNX30",
    "VN100", "VNMIDCAP", "VNSMALLCAP", "VNALLSHARE", "VNXALLSHARE",
    # Sector indices
    "VNMITECH", "VNUTI", "VNCONS", "VNCOND", "VNHEAL", "VNIND",
    # Financial indices
    "VNFIN", "VNFINLEAD", "VNFINSELECT",
    # Specialty / thematic
    "VNDIAMOND", "VNDIVIDEND",
    # Other
    "VNREAL", "VNENE",
})

# Heuristic: a VN ticker is exactly 3 uppercase ASCII letters (matches Rust is_vn_ticker)
_VN_TICKER_LENGTH = 3


def _is_vn_ticker(symbol: str) -> bool:
    return len(symbol) == _VN_TICKER_LENGTH and symbol.isascii() and symbol.isupper()


def _is_index_ticker(symbol: str) -> bool:
    return symbol.upper() in INDEX_TICKERS


@dataclass
class PerformerInfo:
    symbol: str
    close: float
    volume: int
    value: float  # close * volume — trading value
    close_changed: Optional[float]
    volume_changed: Optional[float]
    ma10: Optional[float]
    ma20: Optional[float]
    ma50: Optional[float]
    ma100: Optional[float]
    ma200: Optional[float]
    ma10_score: Optional[float]
    ma20_score: Optional[float]
    ma50_score: Optional[float]
    ma100_score: Optional[float]
    ma200_score: Optional[float]
    sector: Optional[str]
    total_money_changed: Optional[float]
    source: Optional[str] = None


# Maps sort_by string to the PerformerInfo attribute name
_SORT_FIELDS: dict[str, str] = {
    "close_changed": "close_changed",
    "volume": "volume",
    "value": "value",
    "volume_changed": "volume_changed",
    "ma10_score": "ma10_score",
    "ma20_score": "ma20_score",
    "ma50_score": "ma50_score",
    "ma100_score": "ma100_score",
    "ma200_score": "ma200_score",
    "total_money_changed": "total_money_changed",
}


class _SortDescNoneLast:
    """Sort key: descending values, None sorts last.

    ``sorted(items, key=lambda x: _SortDescNoneLast(getattr(x, attr)))``
    produces descending order with all None values at the end.
    """

    __slots__ = ("_val",)

    def __init__(self, val: object) -> None:
        self._val = val

    def __lt__(self, other: _SortDescNoneLast) -> bool:  # type: ignore[override]
        a, b = self._val, other._val
        if a is None and b is None:
            return False
        if a is None:
            return False  # None sorts after everything
        if b is None:
            return True  # real value sorts before None
        return float(a) > float(b)  # desc: higher first

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _SortDescNoneLast):
            return NotImplemented
        a, b = self._val, other._val
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return float(a) == float(b)

    def __le__(self, other: _SortDescNoneLast) -> bool:
        return self == other or self < other  # type: ignore[override]

    def __gt__(self, other: _SortDescNoneLast) -> bool:
        return not self <= other  # type: ignore[override]

    def __ge__(self, other: _SortDescNoneLast) -> bool:
        return not self < other  # type: ignore[override]


class _SortAscNoneLast:
    """Sort key: ascending values, None sorts last.

    ``sorted(items, key=lambda x: _SortAscNoneLast(getattr(x, attr)))``
    produces ascending order with all None values at the end.
    """

    __slots__ = ("_val",)

    def __init__(self, val: object) -> None:
        self._val = val

    def __lt__(self, other: _SortAscNoneLast) -> bool:  # type: ignore[override]
        a, b = self._val, other._val
        if a is None and b is None:
            return False
        if a is None:
            return False  # None sorts after everything
        if b is None:
            return True  # real value sorts before None
        return float(a) < float(b)  # asc: lower first

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _SortAscNoneLast):
            return NotImplemented
        a, b = self._val, other._val
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return float(a) == float(b)

    def __le__(self, other: _SortAscNoneLast) -> bool:
        return self == other or self < other  # type: ignore[override]

    def __gt__(self, other: _SortAscNoneLast) -> bool:
        return not self <= other  # type: ignore[override]

    def __ge__(self, other: _SortAscNoneLast) -> bool:
        return not self < other  # type: ignore[override]


def build_performers(
    live_data: dict[str, list[dict]],
    sector_map: dict[str, str],
    *,
    sort_by: str = "close_changed",
    direction: str = "desc",
    limit: int = 10,
    min_volume: int = 10000,
    source: str | None = None,
) -> tuple[list[PerformerInfo], list[PerformerInfo]]:
    """Build top and worst performers from live daily data.

    Args:
        live_data: Mapping of ticker -> list of candle dicts from
            ``fetch_live_data("1D", ma=True)``. Only the last candle
            per ticker is used.
        sector_map: Mapping of ticker symbol -> sector/group name,
            typically built from ``get_tickers()``.
        sort_by: Field to rank by. One of ``close_changed``, ``volume``,
            ``value`` (close × volume), ``volume_changed``,
            ``ma10_score`` .. ``ma200_score``, ``total_money_changed``.
        direction: ``"desc"`` (default) to put strongest first in *top*,
            ``"asc"`` to put weakest first.
        limit: How many entries in each returned list (clamped 1–100).
        min_volume: Minimum volume threshold — applied to VN tickers
            only (crypto/global tickers are exempt).
        source: Included in each ``PerformerInfo.source`` for display.

    Returns:
        ``(top_performers, worst_performers)`` — two lists of
        *limit* entries each.
    """
    limit = max(1, min(limit, 100))

    attr = _SORT_FIELDS.get(sort_by, "close_changed")
    performers: list[PerformerInfo] = []

    for symbol, candles in live_data.items():
        if not candles:
            continue

        if _is_index_ticker(symbol):
            continue

        c = candles[-1]

        volume = c.get("volume") or 0
        # Only apply min_volume filter to VN tickers (mirrors Rust)
        if _is_vn_ticker(symbol) and volume < min_volume:
            continue

        close = float(c.get("close") or 0)

        performers.append(PerformerInfo(
            symbol=symbol,
            close=close,
            volume=int(volume),
            value=close * int(volume),
            close_changed=_opt_float(c.get("close_changed")),
            volume_changed=_opt_float(c.get("volume_changed")),
            ma10=_opt_float(c.get("ma10")),
            ma20=_opt_float(c.get("ma20")),
            ma50=_opt_float(c.get("ma50")),
            ma100=_opt_float(c.get("ma100")),
            ma200=_opt_float(c.get("ma200")),
            ma10_score=_opt_float(c.get("ma10_score")),
            ma20_score=_opt_float(c.get("ma20_score")),
            ma50_score=_opt_float(c.get("ma50_score")),
            ma100_score=_opt_float(c.get("ma100_score")),
            ma200_score=_opt_float(c.get("ma200_score")),
            sector=sector_map.get(symbol),
            total_money_changed=_opt_float(c.get("total_money_changed")),
            source=source,
        ))

    # Sort desc — highest first, None values sort last
    desc_sorted = sorted(
        performers,
        key=lambda p: _SortDescNoneLast(getattr(p, attr)),
    )
    # Sort asc — lowest first, None values sort last
    asc_sorted = sorted(
        performers,
        key=lambda p: _SortAscNoneLast(getattr(p, attr)),
    )

    if direction == "asc":
        top = asc_sorted[:limit]
        worst = desc_sorted[:limit]
    else:
        top = desc_sorted[:limit]
        worst = asc_sorted[:limit]

    return top, worst


def _opt_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
