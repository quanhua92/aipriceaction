import math
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime

from .domain import PERIODS, DataError, bucket


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
        result[n - 1] = sum(values[:n]) / n
        k = 2 / (period + 1)
        for i in range(n, len(values)):
            result[i] = values[i] * k + result[i - 1] * (1 - k)
    else:
        running = 0.0
        for i, value in enumerate(values):
            running += value
            if i >= period:
                running -= values[i - period]
            if i >= period - 1:
                result[i] = running / period
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
                out[f"ma{p}_score"] = (row.close / values[i] - 1) * 100
        if i:
            prev = rows[i - 1]
            if prev.close > 0:
                out["close_changed"] = (row.close / prev.close - 1) * 100
            if prev.volume > 0:
                out["volume_changed"] = (row.volume / prev.volume - 1) * 100
            out["total_money_changed"] = (row.close - prev.close) * row.volume
        result.append(out)
    return result


def wma(values, p):
    result = [0.0] * len(values)
    weights = p * (p + 1) / 2
    for i in range(p - 1, len(values)):
        result[i] = sum(v * (j + 1) for j, v in enumerate(values[i + 1 - p : i + 1])) / weights
    return result


def normalize(values, p):
    result = [100.0] * len(values)
    for i in range(p - 1, len(values)):
        window = values[i + 1 - p : i + 1]
        mean = sum(window) / p
        std = math.sqrt(sum((v - mean) ** 2 for v in window) / p)
        if std:
            result[i] = 100 + 10 * (values[i] - mean) / std
    return result


def jdk(security, benchmark, p):
    if len(security) != len(benchmark) or len(security) < 3 * p + 1:
        return None
    rs = [s / b if b else 0 for s, b in zip(security, benchmark, strict=True)]
    ratio = wma(wma(rs, p), p)
    momentum = [(ratio[i] / ratio[i - 1] - 1) if ratio[i - 1] else 0 for i in range(1, len(ratio))]
    x, y = normalize(ratio, p), normalize(momentum, p)
    return [(x[i + 1], y[i]) for i in range(p - 1, len(y))]


def volume_profile(rows, symbol, source, bins=50, value_area=70):
    from .domain import INDEXES, DataError

    if not rows:
        raise DataError(f"No minute data found for {symbol}", 404)
    avg = sum((r.high + r.low) / 2 for r in rows) / len(rows)
    if source == "vn":
        tick = 0.01 if symbol in INDEXES else 10 if avg < 10000 else 50 if avg < 50000 else 100
    else:
        tick = 0.0001 if avg < 1 else 0.01 if avg < 100 else 0.1 if avg < 1000 else 1
    # Rust rounds half away from zero, unlike Python's round.
    distribution = defaultdict(float)
    for row in rows:
        if not row.volume:
            continue
        lo, hi = math.floor(row.low / tick + 0.5), math.floor(row.high / tick + 0.5)
        if hi - lo > 1_000_000:
            raise DataError("Minute price range exceeds volume-profile resource limit", 400)
        vol = row.volume / (hi - lo + 1)
        for i in range(lo, hi + 1):
            distribution[i * tick] += vol
    if not distribution:
        raise DataError("No volume profile data generated", 404)
    profile = sorted(distribution.items())
    bins = max(2, min(200, bins))
    if len(profile) > bins:
        lo, hi = profile[0][0], profile[-1][0]
        size = (hi - lo) / bins
        volumes = [0.0] * bins
        for price, vol in profile:
            volumes[min(bins - 1, int((price - lo) / size))] += vol
        profile = [(lo + (i + 0.5) * size, vol) for i, vol in enumerate(volumes) if vol]
    total = sum(v for _, v in profile)
    peak = max(range(len(profile)), key=lambda i: (profile[i][1], i))
    lo = hi = peak
    area = profile[peak][1]
    while area < total * max(60, min(90, value_area)) / 100 and (lo > 0 or hi < len(profile) - 1):
        below = profile[lo - 1][1] if lo else 0
        above = profile[hi + 1][1] if hi + 1 < len(profile) else 0
        if below > above or hi == len(profile) - 1:
            lo -= 1
            area += profile[lo][1]
        else:
            hi += 1
            area += profile[hi][1]
    mean = sum(p * v for p, v in profile) / total
    std = math.sqrt(sum((p - mean) ** 2 * v for p, v in profile) / total)
    median = profile[0][0]
    cumulative = 0.0
    levels = []
    for price, vol in profile:
        previous = cumulative
        cumulative += vol
        if previous < total / 2 <= cumulative:
            median = price
        levels.append(
            {
                "price": price,
                "volume": vol,
                "percentage": vol / total * 100,
                "cumulative_percentage": cumulative / total * 100,
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
            "price": profile[peak][0],
            "volume": profile[peak][1],
            "percentage": profile[peak][1] / total * 100,
        },
        "value_area": {
            "low": profile[lo][0],
            "high": profile[hi][0],
            "volume": area,
            "percentage": area / total * 100,
        },
        "profile": levels,
        "statistics": {
            "mean_price": mean,
            "median_price": median,
            "std_deviation": std,
            "skewness": sum((p - mean) ** 3 * v for p, v in profile) / total / std**3 if std else 0,
        },
    }
