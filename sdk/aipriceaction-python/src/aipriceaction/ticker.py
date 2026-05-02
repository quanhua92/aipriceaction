from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Ticker:
    """Single OHLCV record with optional MA values and scores."""

    symbol: str
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int

    # Optional MA values
    ma10: float | None = None
    ma20: float | None = None
    ma50: float | None = None
    ma100: float | None = None
    ma200: float | None = None

    # Optional MA scores (% distance from MA)
    ma10_score: float | None = None
    ma20_score: float | None = None
    ma50_score: float | None = None
    ma100_score: float | None = None
    ma200_score: float | None = None

    # Optional change metrics
    close_changed: float | None = None
    volume_changed: float | None = None
    total_money_changed: float | None = None
