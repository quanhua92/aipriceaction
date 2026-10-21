import math
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime

from .domain import PERIODS, DataError, bucket

# Matches Rust constants::vci_worker::INDEX_TICKERS. Other VN instruments,
# including non-listed index aliases, use the ordinary price-based tick size.
_VOLUME_PROFILE_INDEX_TICKERS = frozenset(
    {
        "VNINDEX",
        "VN30",
        "VN30F1M",
        "HNX30",
        "VN100",
        "VNMIDCAP",
        "VNSMALLCAP",
        "VNALLSHARE",
        "VNXALLSHARE",
        "VNMITECH",
        "VNUTI",
        "VNCONS",
        "VNCOND",
        "VNHEAL",
        "VNIND",
        "VNFIN",
        "VNFINLEAD",
        "VNFINSELECT",
        "VNDIAMOND",
        "VNDIVIDEND",
        "VNREAL",
        "VNENE",
    }
)


def _rust_sum(values):
    """Sum in iterator order, matching Rust's sequential f64 Iterator::sum."""
    total = 0.0
    for value in values:
        total += value
    return total


def aggregate(rows, iv, source, validate_basis=None):
    groups = defaultdict(list)
    for row in rows:
        groups[bucket(row.time, iv, source)].append(row)
    result = []
    for time, bars in sorted(groups.items()):
        if len({(r.revision, r.provider) for r in bars}) > 1:
            if validate_basis is None:
                raise DataError("Incompatible adjustment revisions within an aggregation bucket")
            validate_basis(bars)
        first, last = bars[0], bars[-1]
        result.append(
            replace(
                first,
                time=time,
                open=first.open,
                high=max(c.high for c in bars),
                low=min(c.low for c in bars),
                close=last.close,
                volume=sum(c.volume for c in bars),
            )
        )
    return result


def moving_average(values, period, ema=False):
    result = [0.0] * len(values)
    if not values:
        return result
    if ema:
        n = min(period, len(values))
        result[n - 1] = _rust_sum(values[:n]) / n
        k = 2 / (period + 1)
        for i in range(n, len(values)):
            result[i] = values[i] * k + result[i - 1] * (1 - k)
    else:
        for i in range(period - 1, len(values)):
            result[i] = _rust_sum(values[i + 1 - period : i + 1]) / period
        # Rust expands only when the entire series is shorter than the period.
        if len(values) < period:
            for i, _value in enumerate(values):
                result[i] = _rust_sum(values[: i + 1]) / (i + 1)
    return result


def enhance(rows, iv, ma=True, ema=False):
    averages = {p: moving_average([r.close for r in rows], p, ema) for p in PERIODS} if ma else {}
    result = []
    for i, row in enumerate(rows):
        dt = datetime.fromtimestamp(row.time, UTC)
        out = {
            "symbol": row.symbol,
            "time": dt.strftime(
                "%Y-%m-%d" if iv in ("1D", "1W", "2W", "1M") else "%Y-%m-%dT%H:%M:%S"
            ),
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
        }
        for p, values in averages.items():
            if values[i] > 0:
                out[f"ma{p}"] = values[i]
                out[f"ma{p}_score"] = ((row.close - values[i]) / values[i]) * 100
        if i:
            prev = rows[i - 1]
            if prev.close > 0:
                out["close_changed"] = ((row.close - prev.close) / prev.close) * 100
            current_volume = float(row.volume)
            previous_volume = float(prev.volume)
            if previous_volume > 0:
                out["volume_changed"] = ((current_volume - previous_volume) / previous_volume) * 100
            if prev.close > 0:
                out["total_money_changed"] = (row.close - prev.close) * current_volume
        result.append(out)
    return result


def enhance_selected(rows, iv, indices, ma=True, ema=False):
    """Enhance selected row positions without materializing discarded outputs.

    Indicator seeds and recurrence use the same ordered floating-point
    operations as ``enhance``. This is especially useful for latest-row APIs,
    which need hundreds of warm-up candles but return only the final candle.
    """
    if not rows or not indices:
        return []
    indices = list(indices)
    unique_indices = sorted(set(indices))
    if any(index < 0 or index >= len(rows) for index in unique_indices):
        raise IndexError("enhancement index is outside the candle rows")

    averages = {}
    if ma:
        closes = [row.close for row in rows]
        for period in PERIODS:
            selected = {}
            if ema:
                seed_count = min(period, len(closes))
                seed_index = seed_count - 1
                if seed_index in unique_indices:
                    selected[seed_index] = _rust_sum(closes[:seed_count]) / seed_count
                if unique_indices and unique_indices[-1] > seed_index:
                    value = _rust_sum(closes[:seed_count]) / seed_count
                    multiplier = 2 / (period + 1)
                    for index in range(seed_count, unique_indices[-1] + 1):
                        value = closes[index] * multiplier + value * (1 - multiplier)
                        if index in unique_indices:
                            selected[index] = value
            else:
                for index in unique_indices:
                    if index >= period - 1:
                        selected[index] = (
                            _rust_sum(closes[index + 1 - period : index + 1]) / period
                        )
                    elif len(closes) < period:
                        selected[index] = _rust_sum(closes[: index + 1]) / (index + 1)
            averages[period] = selected

    result_by_index = {}
    for index in unique_indices:
        row = rows[index]
        dt = datetime.fromtimestamp(row.time, UTC)
        out = {
            "symbol": row.symbol,
            "time": dt.strftime(
                "%Y-%m-%d" if iv in ("1D", "1W", "2W", "1M") else "%Y-%m-%dT%H:%M:%S"
            ),
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
        }
        for period, values in averages.items():
            value = values.get(index, 0.0)
            if value > 0:
                out[f"ma{period}"] = value
                out[f"ma{period}_score"] = ((row.close - value) / value) * 100
        if index:
            previous = rows[index - 1]
            if previous.close > 0:
                out["close_changed"] = ((row.close - previous.close) / previous.close) * 100
            current_volume = float(row.volume)
            previous_volume = float(previous.volume)
            if previous_volume > 0:
                out["volume_changed"] = (
                    (current_volume - previous_volume) / previous_volume
                ) * 100
            if previous.close > 0:
                out["total_money_changed"] = (row.close - previous.close) * current_volume
        result_by_index[index] = out
    return [result_by_index[index] for index in indices]


def wma(values, p):
    result = [0.0] * len(values)
    weights = p * (p + 1) / 2
    for i in range(p - 1, len(values)):
        result[i] = (
            _rust_sum(v * (j + 1) for j, v in enumerate(values[i + 1 - p : i + 1])) / weights
        )
    return result


def normalize(values, p):
    result = [100.0] * len(values)
    for i in range(p - 1, len(values)):
        window = values[i + 1 - p : i + 1]
        mean = _rust_sum(window) / p
        std = math.sqrt(_rust_sum((v - mean) * (v - mean) for v in window) / p)
        if std:
            result[i] = 100 + 10 * (values[i] - mean) / std
    return result


def jdk(security, benchmark, p):
    if len(security) != len(benchmark) or len(security) < 3 * p + 1:
        return None
    rs = [s / b if b else 0 for s, b in zip(security, benchmark, strict=True)]
    ratio = wma(wma(rs, p), p)
    momentum = [
        (ratio[i] - ratio[i - 1]) / ratio[i - 1] if ratio[i - 1] else 0
        for i in range(1, len(ratio))
    ]
    x, y = normalize(ratio, p), normalize(momentum, p)
    return [(x[i + 1], y[i]) for i in range(p - 1, len(y))]


def volume_profile(rows, symbol, source, bins=50, value_area=70):
    from .domain import DataError

    if not rows:
        raise DataError(f"No minute data found for {symbol}", 404)
    # Rust receives Redis and SQL rows newest-first. History.read is
    # chronological, so normalize before sequential f64 reductions and per-row
    # volume accumulation to make both backends use the same operation order.
    rows = sorted(rows, key=lambda row: row.time, reverse=True)
    avg = _rust_sum((r.high + r.low) / 2 for r in rows) / len(rows)
    if source == "vn":
        tick = (
            0.01
            if symbol.upper() in _VOLUME_PROFILE_INDEX_TICKERS
            else 10
            if avg < 10000
            else 50
            if avg < 50000
            else 100
        )
    else:
        tick = 0.0001 if avg < 1 else 0.01 if avg < 100 else 0.1 if avg < 1000 else 1
    # Rust rounds half away from zero, unlike Python's round.
    distribution = defaultdict(float)
    for row in rows:
        if not row.volume:
            continue
        lo, hi = (
            _round_half_away_from_zero(row.low / tick),
            _round_half_away_from_zero(row.high / tick),
        )
        if hi < lo:
            continue
        if hi - lo + 1 > 1_000_000:
            raise DataError("Minute price range exceeds volume-profile resource limit", 400)
        vol = row.volume / (hi - lo + 1)
        for i in range(lo, hi + 1):
            # Rust aggregates by integer tick index, then converts each price
            # to f64 after all per-index volume additions are complete.
            distribution[i] += vol
    if not distribution:
        return None
    profile = sorted((index * tick, volume) for index, volume in distribution.items())
    # Rust sums the sorted integer-tick profile before aggregating display bins;
    # preserve that denominator even when bin accumulation rounds differently.
    total = _rust_sum(v for _, v in profile)
    bins = max(2, min(200, bins))
    if len(profile) > bins:
        lo, hi = profile[0][0], profile[-1][0]
        size = (hi - lo) / bins
        volumes = [0.0] * bins
        for price, vol in profile:
            volumes[min(bins - 1, int((price - lo) / size))] += vol
        profile = [(lo + (i + 0.5) * size, vol) for i, vol in enumerate(volumes) if vol]
    peak = max(range(len(profile)), key=lambda i: (profile[i][1], i))
    poc_price = profile[peak][0]
    # The Rust implementation resolves the POC back to a profile index using
    # the first level within one cent. This matters for low-priced crypto and
    # Yahoo symbols where several adjacent levels can be closer than $0.01.
    poc_index = next(
        (i for i, (price, _volume) in enumerate(profile) if abs(price - poc_price) < 0.01),
        0,
    )
    lo = hi = poc_index
    area = profile[poc_index][1]
    # Rust's f64::clamp leaves a NaN input as NaN. That makes the target NaN,
    # and the comparison false, so the value area remains the POC bin.
    clamped_value_area = value_area if math.isnan(value_area) else max(60, min(90, value_area))
    while area < total * (clamped_value_area / 100) and (lo > 0 or hi < len(profile) - 1):
        below = profile[lo - 1][1] if lo else 0
        above = profile[hi + 1][1] if hi + 1 < len(profile) else 0
        if below > above or hi == len(profile) - 1:
            lo -= 1
            area += profile[lo][1]
        else:
            hi += 1
            area += profile[hi][1]
    mean = _rust_sum(p * v for p, v in profile) / total
    std = math.sqrt(_rust_sum((p - mean) * (p - mean) * v for p, v in profile) / total)
    median = float(profile[0][0])
    cumulative_percentage = 0.0
    cumulative_volume = 0.0
    median_found = False
    levels = []
    for price, vol in profile:
        cumulative_volume += vol
        if not median_found and cumulative_volume >= total / 2:
            median = float(price)
            median_found = True
        percentage = (vol / total) * 100
        cumulative_percentage += percentage
        levels.append(
            {
                "price": float(price),
                "volume": vol,
                "percentage": percentage,
                    "cumulative_percentage": cumulative_percentage,
            }
        )
    low = min(r.low for r in rows if r.volume)
    high = max(r.high for r in rows if r.volume)
    return {
        "symbol": symbol,
        "total_volume": sum(r.volume for r in rows),
        "total_minutes": len(rows),
        "price_range": {"low": low, "high": high, "spread": high - low},
        "poc": {
            "price": float(profile[peak][0]),
            "volume": profile[peak][1],
            "percentage": profile[peak][1] / total * 100,
        },
        "value_area": {
            "low": float(profile[lo][0]),
            "high": float(profile[hi][0]),
            "volume": area,
            "percentage": area / total * 100,
        },
        "profile": levels,
        "statistics": {
            "mean_price": mean,
            "median_price": median,
            "std_deviation": std,
            "skewness": _rust_sum((p - mean) * (p - mean) * (p - mean) * v for p, v in profile)
            / total
            / (std * std * std)
            if std
            else 0,
        },
    }


def _round_half_away_from_zero(value):
    """Match Rust f64::round for finite positive or negative prices."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)
