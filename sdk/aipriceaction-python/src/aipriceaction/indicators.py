"""MA indicator calculation, ported from the Rust backend.

Matches src/models/indicators.rs and src/services/aggregator.rs exactly.
"""

from __future__ import annotations

_MA_PERIODS = [10, 20, 50, 100, 200]


def calculate_sma(closes: list[float], period: int) -> list[float]:
    """Simple Moving Average with expanding window for short datasets.

    Mirrors Rust's calculate_sma in src/models/indicators.rs.
    """
    n = len(closes)
    values = [0.0] * n
    if period == 0 or n == 0:
        return values

    # Full window SMA
    for i in range(period - 1, n):
        window = closes[i + 1 - period : i + 1]
        values[i] = sum(window) / period

    # Expanding window for the first `period - 1` bars
    effective_start = 0 if n < period else period - 1
    for i in range(effective_start, min(period - 1, n)):
        values[i] = sum(closes[: i + 1]) / (i + 1)

    return values


def calculate_ema(closes: list[float], period: int) -> list[float]:
    """Exponential Moving Average seeded with SMA.

    Mirrors Rust's calculate_ema in src/models/indicators.rs.
    """
    n = len(closes)
    values = [0.0] * n
    if period == 0 or n == 0:
        return values

    # Seed with SMA of available data
    seed_len = min(period, n)
    values[seed_len - 1] = sum(closes[:seed_len]) / seed_len

    k = 2.0 / (period + 1)
    for i in range(seed_len, n):
        values[i] = closes[i] * k + values[i - 1] * (1.0 - k)

    return values


def calculate_ma_score(close: float, ma: float) -> float:
    """((close - ma) / ma) * 100"""
    if ma == 0.0:
        return 0.0
    return ((close - ma) / ma) * 100.0


def compute_indicators(
    closes: list[float],
    volumes: list[int],
    *,
    use_ema: bool = False,
) -> dict[str, list[float | None]]:
    """Compute all MA indicators and change metrics for a single ticker.

    Mirrors the aggregation logic in Rust's src/services/aggregator.rs.

    Returns a dict with keys: ma10..ma200, ma10_score..ma200_score,
    close_changed, volume_changed, total_money_changed.
    All lists are the same length as closes.
    """
    n = len(closes)
    calc = calculate_ema if use_ema else calculate_sma

    result: dict[str, list[float | None]] = {}

    for period in _MA_PERIODS:
        ma_values = calc(closes, period)
        result[f"ma{period}"] = [v if v > 0.0 else None for v in ma_values]
        result[f"ma{period}_score"] = [
            calculate_ma_score(closes[i], v) if v > 0.0 else None
            for i, v in enumerate(ma_values)
        ]

    # Change metrics (need previous bar)
    close_changed: list[float | None] = [None] * n
    volume_changed: list[float | None] = [None] * n
    total_money_changed: list[float | None] = [None] * n

    for i in range(1, n):
        prev_close = closes[i - 1]
        prev_vol = volumes[i - 1]

        if prev_close > 0.0:
            close_changed[i] = ((closes[i] - prev_close) / prev_close) * 100.0

        if prev_vol > 0:
            volume_changed[i] = ((volumes[i] - prev_vol) / prev_vol) * 100.0

        total_money_changed[i] = (closes[i] - prev_close) * volumes[i]

    result["close_changed"] = close_changed
    result["volume_changed"] = volume_changed
    result["total_money_changed"] = total_money_changed

    return result
