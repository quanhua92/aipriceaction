"""Volume-by-price histogram analysis — port of Rust volume_profile.rs.

Pure Python implementation (no numpy/scipy). Takes a pandas DataFrame of
1-minute OHLCV bars and produces a volume profile with POC, value area,
and volume-weighted statistics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


# -- Tick size helpers (mirrors Rust) -----------------------------------------


def _get_tick_size_vn(avg_price: float, symbol: str) -> float:
    """Tick size for Vietnamese stocks based on average price."""
    if symbol.upper() in _VN_INDEX_TICKERS:
        return 0.01
    if avg_price < 10_000.0:
        return 10.0
    if avg_price < 50_000.0:
        return 50.0
    return 100.0


def _get_tick_size_crypto(avg_price: float) -> float:
    """Tick size for crypto based on average price."""
    if avg_price < 1.0:
        return 0.0001
    if avg_price < 100.0:
        return 0.01
    if avg_price < 1_000.0:
        return 0.1
    return 1.0


_VN_INDEX_TICKERS: frozenset[str] = frozenset({
    "VNINDEX", "VN30", "VN30F1M", "HNX30",
    "VN100", "VNMIDCAP", "VNSMALLCAP", "VNALLSHARE", "VNXALLSHARE",
    "VNMITECH", "VNUTI", "VNCONS", "VNCOND", "VNHEAL", "VNIND",
    "VNFIN", "VNFINLEAD", "VNFINSELECT",
    "VNDIAMOND", "VNDIVIDEND",
    "VNREAL", "VNENE",
})


# -- Data classes --------------------------------------------------------------


@dataclass
class PriceRange:
    low: float
    high: float
    spread: float


@dataclass
class PointOfControl:
    price: float
    volume: float
    percentage: float


@dataclass
class ValueArea:
    low: float
    high: float
    volume: float
    percentage: float


@dataclass
class PriceLevelVolume:
    price: float
    volume: float
    percentage: float = 0.0
    cumulative_percentage: float = 0.0


@dataclass
class VolumeStatistics:
    mean_price: float
    median_price: float
    std_deviation: float
    skewness: float


@dataclass
class VolumeProfileResult:
    symbol: str
    total_volume: int
    total_minutes: int
    price_range: PriceRange
    poc: PointOfControl
    value_area: ValueArea
    profile: list[PriceLevelVolume] = field(default_factory=list)
    statistics: VolumeStatistics | None = None


# -- Internal helpers ----------------------------------------------------------


def _aggregate_into_bins(
    profile: list[PriceLevelVolume],
    num_bins: int,
) -> list[PriceLevelVolume]:
    """Merge tick-level profile into *num_bins* equally-spaced bins."""
    if len(profile) <= num_bins:
        return profile

    if not profile:
        return profile

    price_min = profile[0].price
    price_max = profile[-1].price
    bin_size = (price_max - price_min) / num_bins
    if bin_size <= 0.0:
        return profile

    bins: list[PriceLevelVolume] = [
        PriceLevelVolume(price=0.0, volume=0.0)
        for _ in range(num_bins)
    ]

    for level in profile:
        idx = int(math.floor((level.price - price_min) / bin_size))
        idx = min(idx, num_bins - 1)
        bins[idx].volume += level.volume
        bins[idx].price = price_min + (idx + 0.5) * bin_size

    return [b for b in bins if b.volume > 0.0]


def _add_percentages(
    profile: list[PriceLevelVolume],
    total_volume: float,
) -> None:
    """Add percentage and cumulative_percentage to each level (mutates)."""
    cumulative = 0.0
    for level in profile:
        level.percentage = (level.volume / total_volume * 100.0) if total_volume > 0.0 else 0.0
        cumulative += level.percentage
        level.cumulative_percentage = cumulative


def _calculate_value_area(
    profile: list[PriceLevelVolume],
    poc_price: float,
    total_volume: float,
    target_pct: float,
) -> ValueArea:
    """Expand from POC until *target_pct* of total volume is captured."""
    if not profile or total_volume == 0.0:
        return ValueArea(low=0.0, high=0.0, volume=0.0, percentage=0.0)

    target_volume = total_volume * (target_pct / 100.0)

    # Find POC index (closest match within 0.01 tolerance)
    poc_idx = 0
    for i, p in enumerate(profile):
        if abs(p.price - poc_price) < 0.01:
            poc_idx = i
            break

    va_low_idx = poc_idx
    va_high_idx = poc_idx
    accumulated = profile[poc_idx].volume

    while accumulated < target_volume:
        vol_below = profile[va_low_idx - 1].volume if va_low_idx > 0 else 0.0
        vol_above = profile[va_high_idx + 1].volume if va_high_idx < len(profile) - 1 else 0.0

        if vol_below == 0.0 and vol_above == 0.0:
            break

        if vol_below > vol_above and va_low_idx > 0:
            va_low_idx -= 1
            accumulated += profile[va_low_idx].volume
        elif va_high_idx < len(profile) - 1:
            va_high_idx += 1
            accumulated += profile[va_high_idx].volume
        elif va_low_idx > 0:
            va_low_idx -= 1
            accumulated += profile[va_low_idx].volume
        else:
            break

    return ValueArea(
        low=profile[va_low_idx].price,
        high=profile[va_high_idx].price,
        volume=accumulated,
        percentage=(accumulated / total_volume * 100.0) if total_volume > 0.0 else 0.0,
    )


def _calculate_statistics(
    profile: list[PriceLevelVolume],
    total_volume: float,
) -> VolumeStatistics:
    """Volume-weighted mean, median, std deviation, skewness."""
    if not profile or total_volume == 0.0:
        return VolumeStatistics(
            mean_price=0.0, median_price=0.0, std_deviation=0.0, skewness=0.0,
        )

    # Mean (volume-weighted)
    mean_price = sum(p.price * p.volume for p in profile) / total_volume

    # Median
    cumulative = 0.0
    median_price = profile[0].price
    for level in profile:
        cumulative += level.volume
        if cumulative >= total_volume / 2.0:
            median_price = level.price
            break

    # Variance & std deviation
    variance = sum(
        (p.price - mean_price) ** 2 * p.volume for p in profile
    ) / total_volume
    std_deviation = math.sqrt(variance)

    # Skewness
    if std_deviation > 0.0:
        m3 = sum(
            (p.price - mean_price) ** 3 * p.volume for p in profile
        ) / total_volume
        skewness = m3 / (std_deviation ** 3)
    else:
        skewness = 0.0

    return VolumeStatistics(
        mean_price=mean_price,
        median_price=median_price,
        std_deviation=std_deviation,
        skewness=skewness,
    )


# -- Public API ----------------------------------------------------------------


def compute_volume_profile(
    df: pd.DataFrame,
    symbol: str,
    *,
    source: str = "vn",
    bins: int = 50,
    value_area_pct: float = 70.0,
) -> VolumeProfileResult:
    """Compute volume-by-price profile from 1-minute OHLCV bars.

    Args:
        df: DataFrame with columns ``time, open, high, low, close, volume``.
            Typically fetched via ``client.get_ohlcv(ticker, interval="1m")``.
        symbol: Ticker symbol (used for tick-size logic).
        source: ``"vn"`` for Vietnamese stock tick sizes, anything else
            (e.g. ``"crypto"``) for crypto tick sizes.
        bins: Number of price bins in the output profile (clamped 2–200).
        value_area_pct: Target percentage for the value area (clamped 60–90).

    Returns:
        A ``VolumeProfileResult`` with POC, value area, statistics, and
        the binned profile.
    """
    bins = max(2, min(bins, 200))
    value_area_pct = max(60.0, min(value_area_pct, 90.0))

    if df is None or df.empty:
        return VolumeProfileResult(
            symbol=symbol,
            total_volume=0,
            total_minutes=0,
            price_range=PriceRange(0.0, 0.0, 0.0),
            poc=PointOfControl(0.0, 0.0, 0.0),
            value_area=ValueArea(0.0, 0.0, 0.0, 0.0),
            statistics=VolumeStatistics(0.0, 0.0, 0.0, 0.0),
        )

    # Compute average price from (high+low)/2
    avg_price = float(((df["high"] + df["low"]) / 2.0).mean())

    # Determine tick size
    if source == "vn":
        tick_size = _get_tick_size_vn(avg_price, symbol)
    else:
        tick_size = _get_tick_size_crypto(avg_price)

    # Build tick-level volume map
    profile_map: dict[int, float] = {}
    session_low = math.inf
    session_high = -math.inf

    for _, row in df.iterrows():
        vol = float(row["volume"]) if row["volume"] else 0.0
        if vol == 0.0:
            continue

        low = float(row["low"])
        high = float(row["high"])
        session_low = min(session_low, low)
        session_high = max(session_high, high)

        low_idx = round(low / tick_size)
        high_idx = round(high / tick_size)
        num_steps = high_idx - low_idx + 1
        if num_steps <= 0:
            continue

        vol_per_step = vol / num_steps
        for idx in range(low_idx, high_idx + 1):
            profile_map[idx] = profile_map.get(idx, 0.0) + vol_per_step

    if not profile_map:
        return VolumeProfileResult(
            symbol=symbol,
            total_volume=0,
            total_minutes=len(df),
            price_range=PriceRange(0.0, 0.0, 0.0),
            poc=PointOfControl(0.0, 0.0, 0.0),
            value_area=ValueArea(0.0, 0.0, 0.0, 0.0),
            statistics=VolumeStatistics(0.0, 0.0, 0.0, 0.0),
        )

    # Convert to sorted list
    profile: list[PriceLevelVolume] = [
        PriceLevelVolume(price=idx * tick_size, volume=vol)
        for idx, vol in sorted(profile_map.items())
    ]

    total_volume = sum(p.volume for p in profile)
    total_volume_raw = int(df["volume"].sum())

    # Aggregate into bins
    profile = _aggregate_into_bins(profile, bins)

    # Add percentages
    _add_percentages(profile, total_volume)

    if not profile:
        return VolumeProfileResult(
            symbol=symbol,
            total_volume=total_volume_raw,
            total_minutes=len(df),
            price_range=PriceRange(session_low, session_high, session_high - session_low),
            poc=PointOfControl(0.0, 0.0, 0.0),
            value_area=ValueArea(0.0, 0.0, 0.0, 0.0),
            statistics=VolumeStatistics(0.0, 0.0, 0.0, 0.0),
        )

    # POC
    poc_level = max(profile, key=lambda p: p.volume)
    poc = PointOfControl(
        price=poc_level.price,
        volume=poc_level.volume,
        percentage=(poc_level.volume / total_volume * 100.0) if total_volume > 0.0 else 0.0,
    )

    # Value area
    value_area = _calculate_value_area(profile, poc.price, total_volume, value_area_pct)

    # Statistics
    statistics = _calculate_statistics(profile, total_volume)

    return VolumeProfileResult(
        symbol=symbol,
        total_volume=total_volume_raw,
        total_minutes=len(df),
        price_range=PriceRange(session_low, session_high, session_high - session_low),
        poc=poc,
        value_area=value_area,
        profile=profile,
        statistics=statistics,
    )
