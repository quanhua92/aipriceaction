"""Compare valid subsets of captured daily responses while preserving rejected dates.

Diagnostic only: no requests, database copies or publication. Native replays use
the unchanged runtime parser via MockTransport; ingestion still rejects bad pages.
"""

import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

import httpx

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds, parse_time
from aipriceaction_api.providers import Providers
from scripts.compare_vn_feeds import FIELDS, compare, same


async def replay_native(settings, symbol, feed, raw, start, before):
    payload = json.loads(raw)
    if payload.get("symbol", symbol) != symbol:
        raise ValueError("Capture symbol mismatch")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=raw))
    # All responses come from MockTransport; live-provider pacing is unnecessary.
    providers = Providers(
        replace(settings, proxies=(), allow_direct=True, requests_per_minute=1_000_000),
        transport=transport,
    )
    count = min(10000, max(100, (before - start) // 86400 + 1))
    windows = [(start, before)]
    rows, rejected = {}, []
    try:
        while windows:
            first, last = windows.pop()
            if first >= last:
                continue
            try:
                page = await providers.page(
                    "vn", symbol, "1D", last, count=count, start=first, provider=feed
                )
            except DataError as exc:
                match = re.search(r"at Unix (\d+): (.+)", str(exc))
                if not match or len(rejected) >= 1000:
                    raise
                stamp = int(match[1])
                if stamp % 86400 or not first <= stamp < last:
                    raise ValueError("Rejected timestamp outside replay window") from exc
                rejected.append({"time": stamp, "reason": match[2]})
                windows.extend([(first, stamp), (stamp + 86400, last)])
                continue
            if len(page.rows) >= count:
                raise ValueError("Replay may be truncated at the original page cap")
            for row in page.rows:
                value = {key: asdict(row)[key] for key in ("time", *FIELDS)}
                if not first <= row.time < last or (
                    row.time in rows and not same(rows[row.time], value)
                ):
                    raise ValueError("Conflicting or out-of-window replay candle")
                rows[row.time] = value
    finally:
        await providers.close()
    return [rows[t] for t in sorted(rows)], sorted(rejected, key=lambda r: r["time"])


def replay_legacy(symbol, raw, start, before):
    payload = json.loads(raw)
    rows, rejected = {}, []
    for item in payload[symbol]:
        if item.get("symbol") != symbol:
            raise ValueError("Capture symbol mismatch")
        value = {"time": parse_time(item["time"]), **{key: item[key] for key in FIELDS}}
        stamp = value["time"]
        if not start <= stamp < before:
            raise ValueError("Legacy capture outside requested window")
        try:
            Candle("vn", symbol, "1D", **value).validate()
        except DataError as exc:
            rejected.append({"time": stamp, "reason": str(exc)})
            continue
        if stamp in rows and not same(rows[stamp], value):
            raise ValueError("Conflicting legacy duplicate")
        rows[stamp] = value
    return [rows[t] for t in sorted(rows)], rejected


async def run(args):
    original = json.loads((args.audit / "report.json").read_text())
    if original["intervals"] != ["1D"]:
        raise ValueError("Replay requires a daily-only comparison")
    args.output.mkdir(parents=True, exist_ok=False)
    settings = Settings.from_env()
    start = date_bounds(original["daily_start"])
    before = date_bounds(original["end_date"], end=True) + 1
    replays, results, errors = [], [], []
    counts = Counter()
    for symbol in original["symbols"]:
        feeds = {}
        for feed in original["feeds"]:
            record = json.loads((args.audit / feed / (symbol + "-1D.json")).read_text())
            if "rows" in record:
                feeds[feed] = record["rows"]
                continue
            try:
                if feed == "legacy":
                    raw = Path(record["capture"]).read_bytes()
                    if record["http_status"] != 200:
                        raise ValueError("Unsuccessful captured response")
                    expected = Path(record["capture"]).stem.rsplit("-", 1)[-1]
                    if hashlib.sha256(raw).hexdigest() != expected:
                        raise ValueError("Legacy capture checksum mismatch")
                    rows, rejected = replay_legacy(symbol, raw, start, before)
                else:
                    captures = record.get("captures", [])
                    if len(captures) != 1 or captures[0]["status"] != 200:
                        raise ValueError("Expected one successful original native response")
                    raw = Path(captures[0]["path"]).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != captures[0]["sha256"]:
                        raise ValueError("Capture checksum mismatch")
                    rows, rejected = await replay_native(settings, symbol, feed, raw, start, before)
                replay = {
                    "symbol": symbol,
                    "feed": feed,
                    "capture_sha256": hashlib.sha256(raw).hexdigest(),
                    "rows": rows,
                    "rejected": rejected,
                    "diagnostic_only": True,
                }
                (args.output / (symbol + "-" + feed + ".json")).write_text(
                    json.dumps(replay, indent=2) + "\n"
                )
                replays.append(
                    {"symbol": symbol, "feed": feed, "valid_rows": len(rows), "rejected": rejected}
                )
                feeds[feed] = rows
            except (DataError, ValueError, KeyError, OSError) as exc:
                errors.append({"symbol": symbol, "feed": feed, "error": str(exc)})
        comparison = compare(feeds)
        counts.update(comparison["counts"])
        results.append({"symbol": symbol, "interval": "1D", **comparison})
    report = {
        "diagnostic_only": True,
        "canonical_publication": False,
        "perfect_data_proven": False,
        "scope": "Validated subsets of original captured daily responses; malformed dates remain rejected.",
        "audit_directory": str(args.audit),
        "counts": dict(counts),
        "replays": replays,
        "errors": errors,
        "comparisons": results,
        "limitations": [
            "No fresh provider observations or ingestion acceptance are implied.",
            "Rejected dates remain incomplete; valid subsets are not publication candidates.",
            "Agreement and parser validity do not establish market truth.",
        ],
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps({"replayed_feeds": len(replays), "errors": len(errors), "counts": dict(counts)})
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
