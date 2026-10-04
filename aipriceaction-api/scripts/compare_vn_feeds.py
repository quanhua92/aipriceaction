"""Read-only four-feed comparison; agreement is evidence, never a truth oracle."""

import argparse
import asyncio
import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, replace
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

import httpx

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds, parse_time
from aipriceaction_api.providers import Providers
from scripts.stage_yahoo_daily_history import RecordingTransport

FEEDS = ("vps", "vndirect", "dnse", "legacy")
NATIVE_FEEDS = ("vps", "vndirect", "dnse", "vci")
FIELDS = ("open", "high", "low", "close", "volume")


def same(a, b, fields=FIELDS):
    return all(
        a[field] == b[field]
        if field == "volume"
        else math.isclose(a[field], b[field], rel_tol=1e-9, abs_tol=1e-8)
        for field in fields
    )


def compare(feeds):
    """Compare every pair, then report four-way agreement and individual outliers."""
    available = {name: {r["time"]: r for r in rows} for name, rows in feeds.items()}
    pairs = {}
    for a, b in combinations(available, 2):
        left, right = available[a], available[b]
        shared = sorted(left.keys() & right.keys())
        differences = {
            t: max(
                100 * abs(left[t][f] - right[t][f]) / max(abs(left[t][f]), abs(right[t][f]), 1e-300)
                for f in FIELDS[:4]
            )
            for t in shared
        }
        pairs[f"{a}:{b}"] = {
            "shared": len(shared),
            "only_left": sorted(left.keys() - right.keys()),
            "only_right": sorted(right.keys() - left.keys()),
            "price_disagreements": [t for t in shared if not same(left[t], right[t], FIELDS[:4])],
            "volume_disagreements": [t for t in shared if left[t]["volume"] != right[t]["volume"]],
            "maximum_symmetric_price_difference_pct": max(differences.values(), default=0),
            "price_difference_ge_1pct": [t for t in shared if differences[t] >= 1],
        }
    union = sorted(set().union(*(r.keys() for r in available.values())))
    counts, outliers, issues = Counter(), Counter(), []
    for stamp in union:
        present = {feed: rows[stamp] for feed, rows in available.items() if stamp in rows}
        if len(present) != 4:
            counts["incomplete_four_feed_coverage"] += 1
            issues.append({"time": stamp, "kind": "coverage", "present": list(present)})
            continue
        if all(same(present[a], present[b]) for a, b in combinations(present, 2)):
            counts["four_feed_agreement"] += 1
            continue
        counts["four_feed_disagreement"] += 1
        dissenters = []
        for feed in present:
            others = [other for other in present if other != feed]
            if all(same(present[a], present[b]) for a, b in combinations(others, 2)):
                outliers[feed] += 1
                dissenters.append(feed)
        issues.append({"time": stamp, "kind": "values", "three_agree_outlier": dissenters})
    return {
        "counts": dict(counts),
        "three_agree_outliers": dict(outliers),
        "pairs": pairs,
        "issues": issues,
    }


async def run(args):
    native = getattr(args, "native_providers", False)
    selected_feeds = NATIVE_FEEDS if native else FEEDS
    base = Settings.from_env()
    settings = replace(
        base,
        proxies=(),
        allow_direct=True,
        vci_history_fallback=native or base.vci_history_fallback,
    )
    entries = json.loads(settings.watchlist.read_text())["vn"]
    symbols = args.symbol or [e if isinstance(e, str) else e["symbol"] for e in entries]
    if len(set(symbols)) != len(symbols):
        raise ValueError("Duplicate symbols")
    intervals = args.interval or ["1D", "1h", "1m"]
    args.output.mkdir(parents=True, exist_ok=False)
    results = {(symbol, iv): {} for symbol in symbols for iv in intervals}
    completed = 0

    async def collect(feed):
        nonlocal completed
        root = args.output / feed
        root.mkdir()
        transport = RecordingTransport(root) if feed != "legacy" else None
        providers = Providers(settings, transport=transport) if transport else None
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                for symbol, iv in results:
                    start_date = args.daily_start if iv == "1D" else args.intraday_start
                    start = date_bounds(start_date)
                    before = date_bounds(args.end_date, end=True) + 1
                    if start >= before or before > date_bounds(
                        datetime.now(UTC).strftime("%Y-%m-%d")
                    ):
                        raise ValueError("Use ordered, completed UTC date ranges")
                    record = {"start_date": start_date, "end_date": args.end_date}
                    first_capture = len(transport.captures) if transport else 0
                    try:
                        if providers:
                            # Large enough for these bounded recent windows. An exact
                            # cap is an error, not a silently accepted truncated result.
                            count = (
                                min(10000, max(100, (before - start) // 86400 + 1))
                                if iv == "1D"
                                else {"1h": 100, "1m": 1000}[iv]
                            )
                            page = await providers.page(
                                "vn", symbol, iv, before, count=count, start=start, provider=feed
                            )
                            if len(page.rows) >= count:
                                raise ValueError("Window may be truncated at page cap")
                            rows = [asdict(r) for r in page.rows]
                        else:
                            params = dict(
                                symbol=symbol,
                                mode="vn",
                                interval=iv,
                                start_date=start_date,
                                end_date=args.end_date,
                                limit=10000,
                                ma="false",
                                cache="false",
                            )
                            response = await client.get(
                                "https://api.aipriceaction.com/tickers", params=params
                            )
                            raw = response.content
                            digest = hashlib.sha256(raw).hexdigest()
                            capture = root / f"{symbol}-{iv}-{digest}.response"
                            capture.write_bytes(raw)
                            record["capture"] = str(capture)
                            record["http_status"] = response.status_code
                            response.raise_for_status()
                            body = response.json()
                            if not isinstance(body, dict) or symbol not in body:
                                raise ValueError("Unexpected legacy response envelope")
                            rows = []
                            for item in body[symbol]:
                                if item.get("symbol") != symbol:
                                    raise ValueError("Wrong legacy symbol")
                                row = {k: item[k] for k in FIELDS}
                                row["time"] = parse_time(item["time"])
                                Candle("vn", symbol, iv, **row).validate()
                                rows.append(row)
                            if len(rows) >= 10000:
                                raise ValueError("Legacy response may be truncated")
                        selected = {}
                        for row in rows:
                            if not start <= row["time"] < before:
                                raise ValueError("Response outside requested range")
                            stamp = row["time"]
                            if stamp in selected and not same(row, selected[stamp]):
                                raise ValueError("Conflicting duplicate timestamp")
                            selected[stamp] = {key: row[key] for key in ("time", *FIELDS)}
                        record["rows"] = [selected[t] for t in sorted(selected)]
                    except Exception as exc:
                        # Avoid raw network exception strings that may contain URLs.
                        record["error"] = (
                            str(exc)
                            if isinstance(exc, (ValueError, DataError))
                            else type(exc).__name__
                        )
                    if transport:
                        record["captures"] = transport.captures[first_capture:]
                    path = root / f"{symbol}-{iv}.json"
                    path.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
                    results[symbol, iv][feed] = record
                    completed += 1
                    if completed % 20 == 0:
                        print(
                            json.dumps({"completed": completed, "total": len(results) * 4}),
                            flush=True,
                        )
        finally:
            if providers:
                await providers.close()

    await asyncio.gather(*(collect(feed) for feed in selected_feeds))
    comparisons = []
    errors = []
    total_counts, total_outliers = Counter(), Counter()
    for (symbol, iv), records in results.items():
        feeds = {feed: r["rows"] for feed, r in records.items() if "rows" in r}
        for feed, record in records.items():
            if "error" in record:
                errors.append(
                    {"symbol": symbol, "interval": iv, "feed": feed, "error": record["error"]}
                )
        comparison = compare(feeds)
        total_counts.update(comparison["counts"])
        total_outliers.update(comparison["three_agree_outliers"])
        comparisons.append({"symbol": symbol, "interval": iv, **comparison})
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "feeds": selected_feeds,
        "symbols": symbols,
        "intervals": intervals,
        "requests": completed,
        "daily_start": args.daily_start,
        "intraday_start": args.intraday_start,
        "end_date": args.end_date,
        "counts": dict(total_counts),
        "three_agree_outliers": dict(total_outliers),
        "errors": errors,
        "comparisons": comparisons,
        "canonical_publication": False,
        "limitations": [
            "No feed is authoritative; agreement does not prove accuracy.",
            "Legacy may share an upstream with a native feed; four feeds are not four independent witnesses.",
            "Coverage uses observed timestamp unions, not an independent exchange calendar.",
            "Single bounded pages; no claim of full retained-history validation.",
            "Prices ignore only floating representation noise; volume comparison is exact.",
        ],
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {k: report[k] for k in ("requests", "counts", "three_agree_outliers")}
            | {"errors": len(errors)}
        ),
        flush=True,
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--interval", choices=("1D", "1h", "1m"), action="append")
    parser.add_argument("--daily-start", required=True)
    parser.add_argument("--intraday-start", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--native-providers",
        action="store_true",
        help="Compare VPS/VNDirect/DNSE/VCI directly instead of using legacy as the fourth feed",
    )
    asyncio.run(run(parser.parse_args()))
