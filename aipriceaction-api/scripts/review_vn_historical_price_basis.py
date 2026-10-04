"""Trace historical scale exceptions selected automatically from a completed VCI review."""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.volume_corrections import apply_records
from scripts.artifact_budget import ArtifactBudget
from scripts.compare_vn_feeds import FEEDS, FIELDS
from scripts.ohlcv_disagreements import price_class
from scripts.probe_vn_minute_basis import session
from scripts.replay_vci_candidate_pages import replay_record


def date(stamp):
    return datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d")


def read(con, symbol, interval, first, before):
    return [
        dict(row)
        for row in con.execute(
            "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval=? AND time>=? AND time<? ORDER BY time",
            (symbol, interval, first, before),
        )
    ]


def timeline(local, source, daily):
    local_days, source_days = defaultdict(dict), defaultdict(dict)
    for rows, grouped in ((local, local_days), (source, source_days)):
        for row in rows:
            if row["time"] in grouped[date(row["time"])]:
                raise DataError("Duplicate historical minute timestamp")
            grouped[date(row["time"])][row["time"]] = row
    days, regimes = [], []
    for day in sorted(local_days.keys() | source_days.keys()):
        left, right = local_days[day], source_days[day]
        shared = left.keys() & right.keys()
        classes, ratios = Counter(), []
        for stamp in sorted(shared):
            kind = price_class(left[stamp], right[stamp])
            classes[kind] += 1
            if kind == "uniform_price_ratio":
                ratios.extend(right[stamp][field] / left[stamp][field] for field in FIELDS[:4])
        label = (
            next(iter(classes)) if len(classes) == 1 else "mixed" if classes else "no_shared_rows"
        )
        ratio = median(ratios) if ratios else None
        aggregates = {
            "sqlite_minutes": session(list(left.values())),
            "vci_minutes": session(list(right.values())),
        }
        witnesses = {}
        for feed, values in daily.items():
            witness = values.get(day)
            if witness is None:
                continue
            witnesses[feed] = {
                "ohlcv": {field: witness[field] for field in FIELDS},
                "minute_comparisons": {
                    basis: {
                        "maximum_price_difference_vnd": max(
                            abs(derived[field] - witness[field]) for field in FIELDS[:4]
                        ),
                        "volume_difference": derived["volume"] - witness["volume"],
                    }
                    for basis, derived in aggregates.items()
                    if derived is not None
                },
            }
        days.append(
            {
                "date": day,
                "shared_rows": len(shared),
                "sqlite_rows": len(left),
                "vci_rows": len(right),
                "sqlite_only_rows": len(left.keys() - right.keys()),
                "vci_only_rows": len(right.keys() - left.keys()),
                "price_classes": dict(classes),
                "class": label,
                "uniform_ratio_median": ratio,
                "uniform_ratio_minimum": min(ratios) if ratios else None,
                "uniform_ratio_maximum": max(ratios) if ratios else None,
                "observed_minute_aggregates": aggregates,
                "daily_witnesses": witnesses,
            }
        )
        identity = (label, round(ratio, 6) if ratio is not None else None)
        if (
            not regimes
            or (regimes[-1]["class"], regimes[-1]["observed_ratio_rounded_6dp"]) != identity
        ):
            regimes.append(
                {
                    "first_observed_date": day,
                    "last_observed_date": day,
                    "class": label,
                    "observed_ratio_rounded_6dp": identity[1],
                    "observed_dates": 0,
                    "shared_rows": 0,
                }
            )
        regimes[-1]["last_observed_date"] = day
        regimes[-1]["observed_dates"] += 1
        regimes[-1]["shared_rows"] += len(shared)
    return {"days": days, "observed_regimes": regimes}


def summarize(days):
    price_checks, volume_exceptions = {}, []
    for feed in ("sqlite_daily", *FEEDS):
        price_checks[feed] = {}
        for basis in ("sqlite_minutes", "vci_minutes"):
            values = [
                day["daily_witnesses"][feed]["minute_comparisons"][basis]
                for day in days
                if feed in day["daily_witnesses"]
                and basis in day["daily_witnesses"][feed]["minute_comparisons"]
            ]
            price_checks[feed][basis] = {
                "observed_days": len(values),
                "price_within_1_vnd_days": sum(
                    value["maximum_price_difference_vnd"] <= 1 for value in values
                ),
                "maximum_price_difference_vnd": max(
                    (value["maximum_price_difference_vnd"] for value in values), default=None
                ),
                "exact_volume_days": sum(value["volume_difference"] == 0 for value in values),
            }
    for day in days:
        matching = [
            feed
            for feed in FEEDS
            if feed != "legacy"
            and feed in day["daily_witnesses"]
            and day["daily_witnesses"][feed]["minute_comparisons"]
            .get("vci_minutes", {})
            .get("volume_difference")
            == 0
        ]
        if len(matching) < 2:
            volume_exceptions.append(
                {
                    "date": day["date"],
                    "matching_native_feeds": matching,
                    "vci_observed_volume": (
                        day["observed_minute_aggregates"]["vci_minutes"] or {}
                    ).get("volume"),
                    "native_daily_volumes": {
                        feed: witness["ohlcv"]["volume"]
                        for feed, witness in day["daily_witnesses"].items()
                        if feed in FEEDS and feed != "legacy"
                    },
                }
            )
    return {
        "daily_comparisons": price_checks,
        "two_exact_native_volume_witness_days": len(days) - len(volume_exceptions),
        "native_volume_witness_exceptions": volume_exceptions,
        "publication_license": False,
    }


def compact_coherence(detail):
    """Count every observed day while retaining bounded diagnostic examples."""
    summary = summarize(detail["days"])
    shortages = summary.pop("native_volume_witness_exceptions")
    summary["native_volume_witness_exception_count"] = len(shortages)
    summary["native_volume_witness_exception_samples"] = shortages[:20]
    counts = {}
    samples = {}
    for basis in ("sqlite_minutes", "vci_minutes"):
        observed, price_supported, volume_supported = 0, 0, 0
        exceptions = []
        for day in detail["days"]:
            aggregate = day["observed_minute_aggregates"][basis]
            if aggregate is None:
                continue
            observed += 1
            price_feeds, volume_feeds = [], []
            for feed in ("vps", "vndirect", "dnse"):
                witness = day["daily_witnesses"].get(feed)
                if witness is None:
                    continue
                comparison = witness["minute_comparisons"][basis]
                if comparison["maximum_price_difference_vnd"] <= 1:
                    price_feeds.append(feed)
                if comparison["volume_difference"] == 0:
                    volume_feeds.append(feed)
            price_supported += len(price_feeds) >= 2
            volume_supported += len(volume_feeds) >= 2
            if len(price_feeds) < 2 or len(volume_feeds) < 2:
                if len(exceptions) < 20:
                    exceptions.append(
                        {
                            "date": day["date"],
                            "aggregate": aggregate,
                            "price_within_1_vnd_feeds": price_feeds,
                            "exact_volume_feeds": volume_feeds,
                            "daily_witnesses": day["daily_witnesses"],
                        }
                    )
        counts[basis] = {
            "observed_days": observed,
            "two_native_price_within_1_vnd_days": price_supported,
            "two_exact_native_volume_days": volume_supported,
        }
        samples[basis] = exceptions
    classes = Counter()
    for day in detail["days"]:
        classes.update(day["price_classes"])
    return {
        "summary": summary,
        "observed_day_count": len(detail["days"]),
        "observed_regimes": detail["observed_regimes"],
        "price_classes": dict(classes),
        "daily_coherence_counts": counts,
        "daily_coherence_exception_samples": samples,
        "samples_per_category": 20,
        "publication_license": False,
    }


async def run(args):
    raw_review = (args.review / "report.json").read_bytes()
    reviewed = json.loads(raw_review)
    if (
        not reviewed["completed"]
        or reviewed["proofs_sha256"] != hashlib.sha256(args.proofs.read_bytes()).hexdigest()
    ):
        raise DataError("Use a completed VCI review with its exact proof catalog")
    all_series = getattr(args, "all_series", False)
    candidates = [
        row["symbol"]
        for row in reviewed["series"]
        if all_series
        or row["sqlite_comparison"]["providers"]["vci"]["diagnostics"]["price_classes"].get(
            "uniform_price_ratio"
        )
    ]
    if len(candidates) != len(set(candidates)) or len(candidates) > 100:
        raise DataError("Duplicate or excessive historical basis candidates")
    daily_report_bytes = (args.daily / "report.json").read_bytes()
    daily_report = json.loads(daily_report_bytes)
    if (
        daily_report["feeds"] != list(FEEDS)
        or daily_report["intervals"] != ["1D"]
        or not set(candidates) <= set(daily_report["symbols"])
        or daily_report["canonical_publication"]
        or len(daily_report["symbols"]) != len(set(daily_report["symbols"]))
        or daily_report["requests"] != len(daily_report["symbols"]) * len(FEEDS)
    ):
        raise DataError("Daily comparison does not cover the selected historical exceptions")
    settings = replace(
        Settings.from_env(), vci_history_fallback=True, vci_volume_proofs=args.proofs
    )
    result = {
        "remote_requests": False,
        "canonical_publication": False,
        "perfect_data_proven": False,
        "review_sha256": hashlib.sha256(raw_review).hexdigest(),
        "proofs_sha256": reviewed["proofs_sha256"],
        "daily_report_sha256": hashlib.sha256(daily_report_bytes).hexdigest(),
        "selection": (
            "all series in the completed VCI review, including incomplete source captures"
            if all_series
            else "all uniform-price-ratio exceptions in the completed VCI review"
        ),
        "compact_diagnostic_only": all_series,
        "selected_symbols": candidates,
        "series": [],
        "completed": False,
        "limitations": [
            "Observed ratios and transition dates are descriptions, not inferred adjustment factors or corporate-action proof.",
            "Daily references are saved normalized observations with original errors retained; native daily parsers are not replayed here.",
            "Minute session aggregates cover observed candles, not independently proven complete market sessions.",
            "Contiguous regimes follow observed dates; they do not prove trading or listing on absent dates.",
            "Price agreement within one VND is a reported comparison, not a publication license.",
            "Exact native witness counts do not apply scoped representation or volume-only allowances; shortages remain diagnostic findings, not a revocation of existing handoffs.",
            "This review does not change source selection, historical candles, or the active proof catalog.",
        ],
    }
    with sqlite3.connect(settings.database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        for symbol in candidates:
            path = args.review / f"{symbol}-record.json"
            raw = path.read_bytes()
            record = json.loads(raw)
            if not (
                daily_report["daily_start"]
                <= record["start_date"]
                <= record["end_date"]
                <= daily_report["end_date"]
            ):
                raise DataError("Historical minute scope outside saved daily comparison")
            replay = await replay_record(settings, symbol, record, retain_rows=True)
            source = [asdict(row) for row in replay.pop("rows")]
            first, before = (
                date_bounds(record["start_date"]),
                date_bounds(record["end_date"], end=True) + 1,
            )

            receipts = [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM volume_corrections WHERE source='vn' AND symbol=? AND interval='1m' AND time>=? AND time<? ORDER BY time,id",
                    (symbol, first, before),
                )
            ]
            local = [
                asdict(row)
                for row in apply_records(
                    [Candle(**row) for row in read(con, symbol, "1m", first, before)], receipts
                )
            ]
            daily = {
                "sqlite_daily": {
                    date(row["time"]): row for row in read(con, symbol, "1D", first, before)
                }
            }
            identities = {}
            for feed in FEEDS:
                path = args.daily / feed / f"{symbol}-1D.json"
                raw_daily = path.read_bytes()
                daily_record = json.loads(raw_daily)
                values = daily_record.get("rows", [])
                if len({date(row["time"]) for row in values}) != len(values):
                    raise DataError("Duplicate saved daily witness date")
                daily[feed] = {date(row["time"]): row for row in values}
                identities[feed] = {
                    "path": str(path),
                    "sha256": hashlib.sha256(raw_daily).hexdigest(),
                    "original_error": daily_record.get("error"),
                }
            detail = timeline(local, source, daily)
            result["series"].append(
                {
                    "symbol": symbol,
                    "record_sha256": hashlib.sha256(raw).hexdigest(),
                    "replay": replay,
                    "volume_correction_receipts": len(receipts),
                    "daily_observations": identities,
                    **(
                        compact_coherence(detail)
                        if all_series
                        else {"summary": summarize(detail["days"]), **detail}
                    ),
                }
            )
            print(
                json.dumps(
                    {"symbol": symbol, "observed_days": len(detail["days"])}
                    if all_series
                    else {"symbol": symbol, "regimes": detail["observed_regimes"]}
                ),
                flush=True,
            )
    result["completed"] = True
    args.output.mkdir(parents=True, exist_ok=False)
    ArtifactBudget(args.output, 8 * 1024 * 1024).write(
        args.output / "report.json", (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--all-series",
        action="store_true",
        help="Review every saved series, retaining bounded daily-coherence examples only",
    )
    asyncio.run(run(parser.parse_args()))
