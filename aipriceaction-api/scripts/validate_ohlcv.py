"""Automated read-only VN OHLCV checks; report exceptions without choosing a winner.

Inventories all selected sources, compares all four VN providers, then compares
their returned values with live SQLite in one read-only transaction. Keeps small
reports and provider captures, never creates test databases or publishes candles.
"""

import argparse
import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import date_bounds
from scripts.compare_vn_feeds import FIELDS, NATIVE_FEEDS, same
from scripts.compare_vn_feeds import run as compare_feeds
from scripts.inventory_retained_windows import inventory


def local_comparisons(settings, root, comparison):
    results = []
    with sqlite3.connect(settings.database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        for symbol in comparison["symbols"]:
            for interval in comparison["intervals"]:
                start = date_bounds(
                    comparison["daily_start" if interval == "1D" else "intraday_start"]
                )
                before = date_bounds(comparison["end_date"], end=True) + 1
                local = {
                    r["time"]: dict(r)
                    for r in con.execute(
                        "SELECT time,open,high,low,close,volume FROM candles "
                        "WHERE source='vn' AND symbol=? AND interval=? AND time>=? AND time<?",
                        (symbol, interval, start, before),
                    )
                }
                peers = {}
                returned = {}
                for feed in NATIVE_FEEDS:
                    record = json.loads((root / feed / f"{symbol}-{interval}.json").read_text())
                    if "rows" not in record:
                        continue
                    rows = {r["time"]: r for r in record["rows"]}
                    returned[feed] = rows
                    shared = sorted(local.keys() & rows.keys())
                    peers[feed] = {
                        "shared": len(shared),
                        "provider_only": sorted(rows.keys() - local.keys()),
                        "sqlite_only": sorted(local.keys() - rows.keys()),
                        "price_disagreements": [
                            t for t in shared if not same(local[t], rows[t], FIELDS[:4])
                        ],
                        "volume_disagreements": [
                            t for t in shared if local[t]["volume"] != rows[t]["volume"]
                        ],
                    }
                agreed = []
                if len(returned) == len(NATIVE_FEEDS):
                    shared_peers = set.intersection(*(set(rows) for rows in returned.values()))
                    agreed = [
                        t
                        for t in sorted(shared_peers)
                        if all(
                            same(returned[NATIVE_FEEDS[0]][t], rows[t])
                            for rows in returned.values()
                        )
                    ]
                conflicts = [
                    t
                    for t in agreed
                    if t in local and not same(local[t], returned[NATIVE_FEEDS[0]][t])
                ]
                results.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "sqlite_rows": len(local),
                        "providers": peers,
                        "unanimous_provider_conflicts": conflicts,
                        "missing_unanimous_provider_timestamps": [
                            t for t in agreed if t not in local
                        ],
                    }
                )
    return results


def exceptions(comparison, local):
    issues = []
    for error in comparison["errors"]:
        issues.append({**error, "kind": "provider_error"})
    for row in comparison["comparisons"]:
        identity = {"symbol": row["symbol"], "interval": row["interval"]}
        if not row["counts"]:
            issues.append({**identity, "kind": "no_provider_observations"})
        if row["counts"].get("incomplete_four_feed_coverage"):
            issues.append({**identity, "kind": "provider_coverage", "counts": row["counts"]})
        if row["counts"].get("four_feed_disagreement"):
            issues.append(
                {
                    **identity,
                    "kind": "provider_values",
                    "counts": row["counts"],
                    "outliers": row["three_agree_outliers"],
                }
            )
    for row in local:
        identity = {"symbol": row["symbol"], "interval": row["interval"]}
        if not row["sqlite_rows"]:
            issues.append({**identity, "kind": "no_local_observations"})
        for key in ("unanimous_provider_conflicts", "missing_unanimous_provider_timestamps"):
            if row.get(key):
                issues.append({**identity, "kind": key, "timestamps": row[key]})
        for provider, peer in row["providers"].items():
            counts = {
                k: len(peer[k])
                for k in (
                    "provider_only",
                    "sqlite_only",
                    "price_disagreements",
                    "volume_disagreements",
                )
            }
            if any(counts.values()):
                issues.append(
                    {
                        **identity,
                        "kind": "sqlite_provider_difference",
                        "provider": provider,
                        "counts": counts,
                    }
                )
    return issues


async def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    settings = Settings.from_env()
    state = inventory(settings)
    (args.output / "inventory.json").write_text(json.dumps(state, indent=2) + "\n")
    comparison = await compare_feeds(
        SimpleNamespace(
            output=args.output / "providers",
            symbol=args.symbol,
            interval=args.interval,
            daily_start=args.daily_start,
            intraday_start=args.intraday_start,
            end_date=args.end_date,
            native_providers=True,
        )
    )
    local = local_comparisons(settings, args.output / "providers", comparison)
    (args.output / "sqlite-comparison.json").write_text(json.dumps(local, indent=2) + "\n")
    issues = exceptions(comparison, local)
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "canonical_publication": False,
        "perfect_data_proven": False,
        "symbols": comparison["symbols"],
        "intervals": comparison["intervals"],
        "providers": comparison["feeds"],
        "provider_windows": comparison["requests"],
        "exception_count": len(issues),
        "exceptions": issues,
        "limitations": [
            "Bounded recent windows; retained-year completeness remains a separate gate.",
            "Provider agreement does not prove market truth or independent upstreams.",
            "Timestamp differences are candidates, not proof of missing trading sessions.",
            "Local comparison covers SQLite only; cold archive validation is separate.",
            "This report never changes provider selection, data or recovery licenses.",
            "VCI is enabled for minute probes only; unsupported intervals remain explicit provider errors.",
        ],
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"symbols": len(report["symbols"]), "exceptions": len(issues)}))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--interval", choices=("1D", "1h", "1m"), action="append")
    parser.add_argument("--daily-start", required=True)
    parser.add_argument("--intraday-start", required=True)
    parser.add_argument("--end-date", required=True)
    asyncio.run(run(parser.parse_args()))
