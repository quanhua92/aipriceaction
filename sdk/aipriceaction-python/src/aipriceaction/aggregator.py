"""Client-side OHLCV aggregation ported from aipriceaction/src/services/aggregator.rs.

Supports aggregating base intervals into higher timeframes:
  1m  -> 5m, 15m, 30m  (minute alignment within hour)
  1h  -> 4h             (hour alignment with offset)
  1D  -> 1W, 2W, 1M    (ISO week / calendar month)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

# target_interval -> (base_interval, base_bars_per_candle)
_AGGREGATED_INTERVALS: dict[str, tuple[str, int]] = {
    "5m": ("1m", 5),
    "15m": ("1m", 15),
    "30m": ("1m", 30),
    "4h": ("1h", 4),
    "1W": ("1D", 5),
    "2W": ("1D", 10),
}

_NATIVE_INTERVALS = {"1D", "1h", "1m"}

_OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def resolve_interval(
    interval: str,
) -> tuple[str, Optional[str]]:
    """Resolve an interval string to (base_interval, agg_interval).

    Returns (base, None) for native intervals, (base, target) for aggregated.

    Examples:
        resolve_interval("1D")   -> ("1D", None)
        resolve_interval("15m")  -> ("1m", "15m")
        resolve_interval("1W")   -> ("1D", "1W")
    """
    # Normalize aliases first
    upper = interval.upper()
    alias_map: dict[str, str] = {"DAILY": "1D", "HOURLY": "1h", "MINUTE": "1m"}
    if upper in alias_map:
        return alias_map[upper], None

    if interval in _NATIVE_INTERVALS:
        return interval, None

    if interval in _AGGREGATED_INTERVALS:
        base = _AGGREGATED_INTERVALS[interval][0]
        return base, interval

    raise ValueError(
        f"Invalid interval '{interval}'. "
        f"Native: 1D, 1h, 1m. Aggregated: {', '.join(_AGGREGATED_INTERVALS)}"
    )


def base_bars_per_candle(interval: str) -> int:
    """Return the number of base bars per aggregated candle."""
    if interval in _AGGREGATED_INTERVALS:
        return _AGGREGATED_INTERVALS[interval][1]
    return 1


# ── Bucket functions (exact ports from Rust) ──────────────────────────


def _bucket_minute(time: datetime, bucket_minutes: int) -> datetime:
    """Align a timestamp to the start of its minute-bucket within the hour."""
    minutes_since_hour = time.minute
    bucket_start = (minutes_since_hour // bucket_minutes) * bucket_minutes
    return time.replace(minute=bucket_start, second=0, microsecond=0)


def _bucket_hour(
    time: datetime, bucket_hours: int, offset_hours: int
) -> datetime:
    """Align a timestamp to an hour-bucket with offset.

    Shifts back by offset, aligns to midnight boundaries, then shifts forward.
    For VN stocks offset=2 (market open 09:00 ICT = 02:00 UTC).
    For crypto offset=0 (midnight UTC alignment).
    """
    shifted = time - timedelta(hours=offset_hours)
    hours_since_midnight = shifted.hour
    bucket_start = (hours_since_midnight // bucket_hours) * bucket_hours
    aligned = shifted.replace(hour=bucket_start, minute=0, second=0, microsecond=0)
    return aligned + timedelta(hours=offset_hours)


def _bucket_week(time: datetime) -> datetime:
    """Align to the Monday of the ISO week."""
    days_from_monday = time.weekday()  # Monday=0
    monday = time - timedelta(days=days_from_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _bucket_2week(time: datetime) -> datetime:
    """Align to even/odd ISO week boundaries."""
    week_start = _bucket_week(time)
    iso_week = time.isocalendar()[1]
    if iso_week % 2 == 0:
        return week_start
    return week_start - timedelta(weeks=1)


def _compute_bucket(time_str: str, target_interval: str, source: str) -> str:
    """Compute the bucket key for a given time string and target interval."""
    t = _parse_time(time_str)
    if target_interval in ("5m", "15m", "30m"):
        bucket_minutes = int(target_interval[:-1])
        bucket = _bucket_minute(t, bucket_minutes)
    elif target_interval == "4h":
        offset = 2 if source == "vn" else 0
        bucket = _bucket_hour(t, 4, offset)
    elif target_interval == "1W":
        bucket = _bucket_week(t)
    elif target_interval == "2W":
        bucket = _bucket_2week(t)
    else:
        raise ValueError(f"Unknown aggregated interval: {target_interval}")
    return bucket.isoformat()


def _parse_time(time_str: str) -> datetime:
    """Parse a time string from S3 CSV data to a UTC datetime."""
    s = str(time_str).strip().replace("T", " ")
    # Handle date-only strings
    if len(s) == 10:
        s += " 00:00:00"
    if "+" in s[10:] or s.endswith("Z"):
        # Has timezone info
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    # Assume UTC
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# ── Main aggregation function ─────────────────────────────────────────


def aggregate_ohlcv(
    df: pd.DataFrame,
    target_interval: str,
    source: str = "vn",
) -> pd.DataFrame:
    """Aggregate base OHLCV data into a higher timeframe.

    Args:
        df: DataFrame with columns: time, open, high, low, close, volume.
            Must be for a single symbol (no 'symbol' column needed).
        target_interval: Target interval (e.g. "5m", "15m", "4h", "1W", "1M").
        source: Data source ("vn" or "crypto"). Affects hour alignment offset.

    Returns:
        DataFrame with the same OHLCV columns, aggregated by bucket.
    """
    if df.empty:
        return df

    if target_interval not in _AGGREGATED_INTERVALS:
        raise ValueError(
            f"Not an aggregated interval: {target_interval}. "
            f"Valid: {', '.join(_AGGREGATED_INTERVALS)}"
        )

    # Compute bucket key for each row
    df = df.copy()
    df["_bucket"] = df["time"].apply(
        lambda t: _compute_bucket(str(t), target_interval, source)
    )

    # Sort by time within each bucket for correct open/close
    df = df.sort_values("time")

    # Group by bucket and aggregate
    grouped = df.groupby("_bucket", sort=True)

    agg_result = grouped.agg(
        time=("time", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index(drop=True)

    # Ensure column order
    agg_result = agg_result[_OHLCV_COLUMNS]

    return agg_result
