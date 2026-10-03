"""Bounded, read-only concurrent HTTP rehearsal against a loopback API.

Workers should be stopped for stable-payload comparison. Ordinary response
caching is disabled; sequential baselines warm the archive object cache. This
records observed local performance, not a production capacity estimate.
"""

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aipriceaction_api.config import Settings


def percentile(values, quantile):
    return round(sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)], 2)


def fingerprint(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


async def benchmark(args):
    if urlsplit(args.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("This rehearsal requires a loopback API")
    if not 1 <= args.workers <= 8 or not 1 <= args.seconds <= 120:
        raise ValueError("Choose 1–8 workers and a duration of 1–120 seconds")
    if not 1 <= args.max_requests <= 5000:
        raise ValueError("Choose a request budget of 1–5000")
    watchlist = json.loads(Settings.from_env().watchlist.read_text())
    vn = [entry if isinstance(entry, str) else entry["symbol"] for entry in watchlist["vn"]]
    if not 1 <= len(vn) <= 100:
        raise ValueError("Select 1–100 VN tickers for this rehearsal")
    cases = []

    def candles(label, symbols, **params):
        cases.append(
            {
                "label": label,
                "path": "/tickers",
                "params": [("symbol", symbol) for symbol in symbols]
                + list({"cache": "false", "ema": "true", **params}.items()),
                "symbols": symbols,
            }
        )

    for symbol in vn:
        candles("vn_daily", [symbol], mode="vn", interval="1D", limit=20)
        candles("vn_15m", [symbol], mode="vn", interval="15m", limit=200)
    candles("vn_bulk_daily", vn, mode="vn", interval="1D", limit=20)
    for symbol in ("FPT", "VCB", "MBB", "VIC", "VHM", "HPG"):
        candles(
            "vn_archive_daily",
            [symbol],
            mode="vn",
            interval="1D",
            start_date="2019-01-01",
            end_date="2020-12-31",
        )
    for symbol in watchlist["crypto"]:
        symbol = symbol if isinstance(symbol, str) else symbol["symbol"]
        candles("crypto_minute", [symbol], mode="crypto", interval="1m", limit=10000)
    for symbol in ("AAPL", "^GSPC", "^DJI"):
        candles("global_weekly", [symbol], mode="yahoo", interval="1W", limit=20)
    cases.append({"label": "health", "path": "/health", "params": [], "symbols": []})
    baselines, observations = [], []
    errors = []
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "url": args.url,
        "workers": args.workers,
        "duration_budget_seconds": args.seconds,
        "request_budget": args.max_requests,
        "selected_vn_tickers": len(vn),
        "response_cache": False,
        "archive_object_cache": "warmed by sequential baselines",
    }
    async with httpx.AsyncClient(base_url=args.url, timeout=45) as client:

        async def request(index, compare):
            case = cases[index]
            start = time.perf_counter()
            measurement = {"case": index, "label": case["label"]}
            try:
                response = await client.get(case["path"], params=case["params"])
                measurement["status"] = response.status_code
                measurement["decoded_bytes"] = len(response.content)
                response.raise_for_status()
                payload = response.json()
                if case["label"] == "health":
                    assert payload["storage"]["engine"] == "sqlite"
                    assert payload["storage"]["series"]
                else:
                    assert set(payload) == set(case["symbols"])
                    assert all(payload[symbol] for symbol in case["symbols"])
                    measurement["rows"] = sum(len(rows) for rows in payload.values())
                    digest = fingerprint(payload)
                    measurement["payload_sha256"] = digest
                    if compare:
                        assert digest == baselines[index]["payload_sha256"], "Payload changed"
            except Exception as exc:
                measurement["error"] = (
                    str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
                )
            measurement["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
            return measurement

        for index in range(len(cases)):
            measurement = await request(index, False)
            baselines.append(measurement)
            if "error" in measurement:
                errors.append(measurement)
        begun = time.perf_counter()
        deadline = begun + args.seconds
        cursor = 0

        async def reader():
            nonlocal cursor
            while time.perf_counter() < deadline and cursor < args.max_requests:
                position = cursor
                cursor += 1
                result = await request(position % len(cases), True)
                observations.append(result)
                if "error" in result:
                    errors.append(result)

        if not errors:
            await asyncio.gather(*(reader() for _ in range(args.workers)))
        report["concurrent_wall_seconds"] = round(time.perf_counter() - begun, 2)
    grouped = defaultdict(list)
    for measurement in observations:
        grouped[measurement["label"]].append(measurement)
    report["summary"] = {
        label: {
            "requests": len(rows),
            "failed": sum("error" in row for row in rows),
            "median_ms": round(statistics.median(row["elapsed_ms"] for row in rows), 2),
            "p95_ms": percentile([row["elapsed_ms"] for row in rows], 0.95),
            "max_ms": max(row["elapsed_ms"] for row in rows),
            "decoded_bytes": sum(row.get("decoded_bytes", 0) for row in rows),
        }
        for label, rows in sorted(grouped.items())
    }
    report["failed"] = len(errors)
    report["passed"] = not errors and bool(observations)
    report["cases"] = cases
    report["baselines"] = baselines
    report["observations"] = observations
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"cases", "baselines", "observations"}},
            indent=2,
        )
    )
    if errors:
        print(json.dumps({"errors": errors[:20]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3001")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--max-requests", type=int, default=1000)
    parser.add_argument("--report", type=Path, default=Path("data/http-concurrency-benchmark.json"))
    raise SystemExit(asyncio.run(benchmark(parser.parse_args())))
