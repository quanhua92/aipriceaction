"""Classify observed price/volume differences without changing ingestion guards."""

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime

from scripts.compare_vn_feeds import FIELDS, same


def price_class(left, right):
    if same(left, right, FIELDS[:4]):
        return "equal"
    for decimals, label in ((2, "two_decimal_rounding"), (0, "integer_rounding")):
        if all(
            math.isclose(left[field], round(right[field], decimals), rel_tol=0, abs_tol=1e-8)
            for field in FIELDS[:4]
        ):
            return label
    ratios = [right[field] / left[field] for field in FIELDS[:4]]
    if max(ratios) - min(ratios) <= max(ratios) * 1e-6:
        return "uniform_price_ratio"
    return "nonuniform_price_difference"


def day(stamp):
    return datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d")


def diagnose(local, source, samples=20):
    if not 0 <= samples <= 20:
        raise ValueError("Use at most twenty diagnostic samples")
    shared = sorted(local.keys() & source.keys())
    prices, bands, examples, volume_dates = (
        Counter(),
        Counter(),
        defaultdict(list),
        defaultdict(list),
    )
    maximum = 0.0
    for stamp in shared:
        left, right = local[stamp], source[stamp]
        kind = price_class(left, right)
        if kind != "equal":
            prices[kind] += 1
            difference = max(
                100 * abs(left[field] - right[field]) / max(abs(left[field]), abs(right[field]))
                for field in FIELDS[:4]
            )
            maximum = max(maximum, difference)
            band = (
                "below_0.01pct"
                if difference < 0.01
                else "0.01_to_below_1pct"
                if difference < 1
                else "at_least_1pct"
            )
            bands[band] += 1
            if len(examples[kind]) < samples:
                examples[kind].append(
                    {
                        "time": stamp,
                        "sqlite": {field: left[field] for field in FIELDS[:4]},
                        "source": {field: right[field] for field in FIELDS[:4]},
                        "maximum_symmetric_difference_pct": difference,
                    }
                )
        if left["volume"] != right["volume"]:
            volume_dates[day(stamp)].append(stamp)
    # Day totals include every observed bar on that side, not only shared rows.
    local_days, source_days = defaultdict(dict), defaultdict(dict)
    for stamp, row in local.items():
        local_days[day(stamp)][stamp] = row
    for stamp, row in source.items():
        source_days[day(stamp)][stamp] = row
    volume_days = []
    for date, stamps in sorted(volume_dates.items()):
        left, right = local_days[date], source_days[date]
        volume_days.append(
            {
                "date": date,
                "differing_shared_rows": len(stamps),
                "timestamps_match": left.keys() == right.keys(),
                "sqlite_rows": len(left),
                "source_rows": len(right),
                "sqlite_volume_total": sum(row["volume"] for row in left.values()),
                "source_volume_total": sum(row["volume"] for row in right.values()),
                "sqlite_only_rows": len(left.keys() - right.keys()),
                "source_only_rows": len(right.keys() - left.keys()),
                "shared_volume_net_difference": sum(
                    right[stamp]["volume"] - left[stamp]["volume"] for stamp in stamps
                ),
                "samples": [
                    {
                        "time": stamp,
                        "sqlite_volume": left[stamp]["volume"],
                        "source_volume": right[stamp]["volume"],
                    }
                    for stamp in stamps[:samples]
                ],
            }
        )
    return {
        "publication_license": False,
        "shared_rows": len(shared),
        "price_classes": dict(prices),
        "price_difference_bands": dict(bands),
        "maximum_symmetric_price_difference_pct": maximum,
        "price_examples": dict(examples),
        "volume_disagreements": sum(len(stamps) for stamps in volume_dates.values()),
        "volume_days": volume_days,
        "limitations": [
            "Rounding and uniform price ratios describe values; they do not prove a corporate action, correct price basis or publication license.",
            "Day totals cover observed bars; matching timestamps do not independently prove complete trading minutes.",
            "Samples are bounded; classifications, totals and counts cover every shared timestamp.",
        ],
    }
