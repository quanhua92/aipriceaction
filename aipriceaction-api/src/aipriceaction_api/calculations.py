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
    return enhance_selected(rows, iv, range(len(rows)), ma=ma, ema=ema)


def enhance_selected(rows, iv, indices, ma=True, ema=False, periods=None):
    """Enhance selected positions using the shared NumPy indicator kernel.

    This avoids materializing discarded outputs. It is useful for latest-row
    APIs, which need hundreds of warm-up candles but return only the final one.
    """
    if not rows or not indices:
        return []
    indices = list(indices)
    symbol = rows[0].symbol
    return enhance_many_selected(
        {symbol: rows},
        {symbol: indices},
        iv,
        ma=ma,
        ema=ema,
        periods=periods,
    )[symbol]


def enhance_latest_many(contexts, closes_by_symbol, iv, ma=True, ema=False):
    """Enhance one latest candle per symbol, vectorizing indicator work.

    The SQLite loader supplies only the latest candle pair plus compact close
    histories. NumPy processes the same time-ordered floating-point recurrence
    across ticker columns, avoiding a Python Candle object and result dict for
    every warmup bar.
    """
    import numpy as np

    symbols = [
        symbol
        for symbol, rows in contexts.items()
        if rows and len(closes_by_symbol.get(symbol, ())) > 0
    ]
    if not symbols:
        return {}
    lengths = np.fromiter(
        (len(closes_by_symbol[symbol]) for symbol in symbols), dtype=np.int64, count=len(symbols)
    )
    max_length = int(lengths.max())
    closes = np.zeros((len(symbols), max_length), dtype=np.float64)
    for index, symbol in enumerate(symbols):
        values = closes_by_symbol[symbol]
        if len(values) != lengths[index]:
            raise ValueError(f"Close context length differs for {symbol}")
        closes[index, : len(values)] = values

    averages = {}
    if ma:
        for period in PERIODS:
            state = np.zeros(len(symbols), dtype=np.float64)
            if ema:
                seed_count = np.minimum(lengths, period)
                totals = np.zeros(len(symbols), dtype=np.float64)
                multiplier = 2 / (period + 1)
                decay = 1 - multiplier
                for position in range(max_length):
                    seeding = seed_count > position
                    totals[seeding] += closes[seeding, position]
                    seeded = seed_count == position + 1
                    state[seeded] = totals[seeded] / seed_count[seeded]
                    recurring = (lengths > position) & (seed_count <= position)
                    state[recurring] = (
                        closes[recurring, position] * multiplier + state[recurring] * decay
                    )
            else:
                starts = np.maximum(0, lengths - period)
                totals = np.zeros(len(symbols), dtype=np.float64)
                for position in range(max_length):
                    included = (position >= starts) & (position < lengths)
                    totals[included] += closes[included, position]
                state = totals / np.minimum(lengths, period)
            averages[period] = state

    result = {}
    for index, symbol in enumerate(symbols):
        rows = contexts[symbol]
        current = rows[-1]
        dt = datetime.fromtimestamp(current.time, UTC)
        output = {
            "symbol": current.symbol,
            "time": dt.strftime(
                "%Y-%m-%d" if iv in ("1D", "1W", "2W", "1M") else "%Y-%m-%dT%H:%M:%S"
            ),
            "open": current.open,
            "high": current.high,
            "low": current.low,
            "close": current.close,
            "volume": current.volume,
        }
        for period, values in averages.items():
            value = float(values[index])
            if value > 0:
                output[f"ma{period}"] = value
                output[f"ma{period}_score"] = ((current.close - value) / value) * 100
        if len(rows) > 1:
            previous = rows[-2]
            if previous.close > 0:
                output["close_changed"] = ((current.close - previous.close) / previous.close) * 100
            current_volume = float(current.volume)
            previous_volume = float(previous.volume)
            if previous_volume > 0:
                output["volume_changed"] = (
                    (current_volume - previous_volume) / previous_volume
                ) * 100
            if previous.close > 0:
                output["total_money_changed"] = (current.close - previous.close) * current_volume
        result[symbol] = [output]
    return result


def enhance_many_selected(contexts, indices_by_symbol, iv, ma=True, ema=False, periods=None):
    """Enhance selected rows for many symbols with NumPy indicator batches.

    Histories are grouped by length and processed in small symbol batches. The
    per-window accumulation follows the scalar Rust summation order, while EMA
    recurrence runs once per bar across all symbols in a batch.
    """
    import numpy as np

    selected = {}
    groups = defaultdict(list)
    for symbol, rows in contexts.items():
        indices = list(indices_by_symbol.get(symbol, ()))
        if not rows or not indices:
            selected[symbol] = []
            continue
        unique = sorted(set(indices))
        if any(index < 0 or index >= len(rows) for index in unique):
            raise IndexError("enhancement index is outside the candle rows")
        groups[len(rows)].append((symbol, rows, indices, unique))

    requested_periods = tuple(PERIODS if periods is None else periods) if ma else ()
    for length, members in groups.items():
        for offset in range(0, len(members), 8):
            batch = members[offset : offset + 8]
            closes = np.asarray(
                [[row.close for row in item[1]] for item in batch], dtype=np.float64
            )
            wanted_at_index = defaultdict(list)
            values_by_member = [{} for _ in batch]
            for member_index, (_symbol, _rows, _indices, unique) in enumerate(batch):
                for index in unique:
                    wanted_at_index[index].append(member_index)

            for period in requested_periods:
                if ema:
                    seed_count = min(period, length)
                    seed = np.add.accumulate(closes[:, :seed_count], axis=1)[:, -1] / seed_count
                    state = seed.copy()
                    seed_index = seed_count - 1
                    for member_index in wanted_at_index.get(seed_index, ()):
                        values_by_member[member_index][seed_index, period] = float(
                            state[member_index]
                        )
                    multiplier = 2 / (period + 1)
                    decay = 1 - multiplier
                    for index in range(seed_count, length):
                        state = closes[:, index] * multiplier + state * decay
                        for member_index in wanted_at_index.get(index, ()):
                            values_by_member[member_index][index, period] = float(
                                state[member_index]
                            )
                elif length >= period:
                    selected_windows = [
                        index for index in wanted_at_index if index >= period - 1
                    ]
                    if len(selected_windows) >= max(1, length // 4):
                        windows = np.lib.stride_tricks.sliding_window_view(
                            closes, period, axis=1
                        )
                        cumulative = np.add.accumulate(windows, axis=2)[:, :, -1]
                        for index in selected_windows:
                            ma_values = cumulative[:, index - period + 1] / period
                            for member_index in wanted_at_index[index]:
                                values_by_member[member_index][index, period] = float(
                                    ma_values[member_index]
                                )
                    else:
                        # Short RRG trails and latest-row requests need only a
                        # few windows. Summing those selected windows avoids
                        # building cumulative values for every historical bar.
                        for index in selected_windows:
                            members_at_index = wanted_at_index[index]
                            member_positions = np.asarray(members_at_index, dtype=np.intp)
                            window = closes[
                                member_positions, index - period + 1 : index + 1
                            ]
                            ma_values = np.add.accumulate(window, axis=1)[:, -1] / period
                            for row_index, member_index in enumerate(members_at_index):
                                values_by_member[member_index][index, period] = float(
                                    ma_values[row_index]
                                )
                else:
                    cumulative = np.add.accumulate(closes, axis=1)
                    for index, member_indices in wanted_at_index.items():
                        ma_values = cumulative[:, index] / (index + 1)
                        for member_index in member_indices:
                            values_by_member[member_index][index, period] = float(
                                ma_values[member_index]
                            )

            for member_index, (symbol, rows, indices, _unique) in enumerate(batch):
                output = []
                for index in indices:
                    row = rows[index]
                    dt = datetime.fromtimestamp(row.time, UTC)
                    record = {
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
                    for period in requested_periods:
                        value = values_by_member[member_index].get((index, period), 0.0)
                        if value > 0:
                            record[f"ma{period}"] = value
                            record[f"ma{period}_score"] = ((row.close - value) / value) * 100
                    if index:
                        previous = rows[index - 1]
                        if previous.close > 0:
                            record["close_changed"] = (
                                (row.close - previous.close) / previous.close
                            ) * 100
                        current_volume = float(row.volume)
                        previous_volume = float(previous.volume)
                        if previous_volume > 0:
                            record["volume_changed"] = (
                                (current_volume - previous_volume) / previous_volume
                            ) * 100
                        if previous.close > 0:
                            record["total_money_changed"] = (
                                row.close - previous.close
                            ) * current_volume
                    output.append(record)
                selected[symbol] = output
    return selected


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


def jdk_many(series, p):
    """Calculate JdK RRG points across equal-length ticker arrays in NumPy.

    RRG securities often share the same benchmark dates. Grouping by aligned
    length lets NumPy process ticker rows together while the per-window
    ``add.accumulate`` keeps the scalar Rust summation order.
    """
    if len(series) < 8:
        return [jdk(security, benchmark, p) for security, benchmark in series]

    import numpy as np

    groups = defaultdict(list)
    results = [None] * len(series)
    for index, (security, benchmark) in enumerate(series):
        if len(security) != len(benchmark) or len(security) < 3 * p + 1:
            continue
        groups[len(security)].append((index, security, benchmark))

    def weighted_average(values):
        output = np.zeros_like(values)
        if values.shape[1] < p:
            return output
        windows = np.lib.stride_tricks.sliding_window_view(values, p, axis=1)
        weights = np.arange(1, p + 1, dtype=np.float64)
        weighted = windows * weights
        totals = np.add.accumulate(weighted, axis=2)[:, :, -1]
        output[:, p - 1 :] = totals / (p * (p + 1) / 2)
        return output

    def normalized(values):
        output = np.full_like(values, 100.0)
        if values.shape[1] < p:
            return output
        windows = np.lib.stride_tricks.sliding_window_view(values, p, axis=1)
        means = np.add.accumulate(windows, axis=2)[:, :, -1] / p
        differences = windows - means[:, :, None]
        variance = np.add.accumulate(differences * differences, axis=2)[:, :, -1] / p
        deviations = np.sqrt(variance)
        target = output[:, p - 1 :]
        valid = deviations != 0
        current = values[:, p - 1 :]
        target[valid] = 100 + 10 * (current[valid] - means[valid]) / deviations[valid]
        return output

    for members in groups.values():
        for offset in range(0, len(members), 32):
            chunk = members[offset : offset + 32]
            securities = np.asarray([item[1] for item in chunk], dtype=np.float64)
            benchmarks = np.asarray([item[2] for item in chunk], dtype=np.float64)
            relative = np.zeros_like(securities)
            np.divide(securities, benchmarks, out=relative, where=benchmarks != 0)
            ratio = weighted_average(weighted_average(relative))
            previous = ratio[:, :-1]
            momentum = np.zeros_like(previous)
            np.divide(
                ratio[:, 1:] - previous,
                previous,
                out=momentum,
                where=previous != 0,
            )
            ratio_norm = normalized(ratio)
            momentum_norm = normalized(momentum)
            for row_index, (original_index, _security, _benchmark) in enumerate(chunk):
                results[original_index] = [
                    (float(x), float(y))
                    for x, y in zip(
                        ratio_norm[row_index, p:],
                        momentum_norm[row_index, p - 1 :],
                        strict=True,
                    )
                ]
    return results


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
    tick_ranges = []
    expanded_ticks = 0
    minimum_tick = None
    maximum_tick = None
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
        tick_ranges.append((lo, hi, row.volume / (hi - lo + 1)))
        expanded_ticks += hi - lo + 1
        minimum_tick = lo if minimum_tick is None else min(minimum_tick, lo)
        maximum_tick = hi if maximum_tick is None else max(maximum_tick, hi)
    if not tick_ranges:
        return None
    span = maximum_tick - minimum_tick + 1
    if expanded_ticks >= 8192 and span <= 2_000_000:
        # Dense NumPy slice updates replace the inner per-price Python loop on
        # long minute histories. Each candle still updates levels in newest-
        # first order, so every level keeps Rust's sequential f64 addition.
        import numpy as np

        distribution = np.zeros(span, dtype=np.float64)
        for lo, hi, volume in tick_ranges:
            distribution[lo - minimum_tick : hi - minimum_tick + 1] += volume
        indices = np.flatnonzero(distribution)
        profile = [
            (int(index + minimum_tick) * tick, float(distribution[index])) for index in indices
        ]
    else:
        distribution = defaultdict(float)
        for lo, hi, volume in tick_ranges:
            # Rust aggregates by integer tick index, then converts each price
            # to f64 after all per-index volume additions are complete.
            for index in range(lo, hi + 1):
                distribution[index] += volume
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
