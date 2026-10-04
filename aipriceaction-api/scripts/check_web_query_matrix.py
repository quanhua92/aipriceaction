"""Read-only configured-universe query audit against a loopback API.

Report missing ranges and indicator warmup failures without counting them as
successful compatibility checks. This does not certify freshness or numerical
identity with the legacy backend.
"""

import argparse
import asyncio
import hashlib
import json
import math
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import base_interval, date_bounds, parse_time
from scripts.check_retained_vn_daily import read_snapshot

INTERVALS = ("1m", "5m", "15m", "30m", "1h", "4h", "1D", "1W", "2W", "1M")


def validate_prices(row, source, symbol, interval):
    values = [row[field] for field in ("open", "high", "low", "close")]
    futures = source == "yahoo" and symbol.endswith("=F")
    assert all(
        isinstance(value, (int, float)) and math.isfinite(value) and (value > 0 or futures)
        for value in values
    ), "Invalid price"
    ranged = (
        [row["close"]]
        if source == "sjc"
        else [row["open"]]
        if futures and base_interval(interval) == "1D"
        else [row["open"], row["close"]]
    )
    assert row["low"] <= min(ranged) <= max(ranged) <= row["high"], "Invalid OHLC range"


async def check(args):
    if urlsplit(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use a loopback replacement API")
    if not 1 <= args.workers <= 4:
        raise ValueError("Use one to four readers")
    lower, upper = date_bounds(args.start_date), date_bounds(args.end_date, end=True)
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("Start date must not follow end date")
    settings = Settings.from_env()
    watchlist = json.loads(settings.watchlist.read_text())
    cases = []
    for source, entries in watchlist.items():
        for entry in entries:
            symbol = entry if isinstance(entry, str) else entry["symbol"]
            intervals = ("1D",) if source == "sjc" else tuple(args.interval or INTERVALS)
            for interval in intervals:
                for indicator in ("none", "sma", "ema"):
                    cases.append(
                        dict(source=source, symbol=symbol, interval=interval, indicator=indicator)
                    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    captures = args.report.with_suffix("")
    assert not args.report.exists() and not captures.exists(), "Preserve existing audit evidence"
    captures.mkdir()
    _, _, before = read_snapshot(settings.database.resolve())
    results = []
    queue = asyncio.Queue()
    for index, case in enumerate(cases):
        queue.put_nowait((index, case))
    async with httpx.AsyncClient(base_url=args.url, timeout=45) as client:

        async def reader():
            while not queue.empty():
                index, case = queue.get_nowait()
                params = dict(
                    symbol=case["symbol"],
                    mode="yahoo" if case["source"] == "sjc" else case["source"],
                    interval=case["interval"],
                    limit=20,
                    ma=str(case["indicator"] != "none").lower(),
                    ema=str(case["indicator"] == "ema").lower(),
                    cache="false",
                )
                if args.start_date:
                    params["start_date"] = args.start_date
                if args.end_date:
                    params["end_date"] = args.end_date
                started = time.perf_counter()
                result = dict(case=index, **case, params=params)
                try:
                    response = await client.get("/tickers", params=params)
                    raw = response.content
                    checksum = hashlib.sha256(raw).hexdigest()
                    path = captures / f"{index:04d}-{checksum}.response"
                    path.write_bytes(raw)
                    result.update(
                        status=response.status_code,
                        bytes=len(raw),
                        sha256=checksum,
                        capture=str(path),
                    )
                    if response.status_code != 200:
                        result["failure"] = response.text[:1000]
                    else:
                        payload = response.json()
                        assert payload, (
                            "Missing historical data"
                            if lower is not None or upper is not None
                            else "Missing data"
                        )
                        assert set(payload) == {case["symbol"]}, "Response symbol mismatch"
                        rows = payload[case["symbol"]]
                        assert 0 < len(rows) <= 20, "Missing or oversized result"
                        times = [parse_time(row["time"]) for row in rows]
                        assert times == sorted(set(times)), "Dates repeat or are unordered"
                        assert lower is None or times[0] >= lower, "Result precedes start date"
                        assert upper is None or times[-1] <= upper, "Result follows end date"
                        for row in rows:
                            validate_prices(row, case["source"], case["symbol"], case["interval"])
                            assert (
                                isinstance(row["volume"], (int, float))
                                and math.isfinite(row["volume"])
                                and row["volume"] >= 0
                            ), "Invalid volume"
                        result.update(
                            rows=len(rows),
                            first=rows[0]["time"],
                            last=rows[-1]["time"],
                            undefined_ma200=sum(row.get("ma200") is None for row in rows),
                        )
                except (httpx.HTTPError, ValueError, TypeError, KeyError, AssertionError) as exc:
                    result["failure"] = (
                        str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
                    )
                result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
                results.append(result)
                if len(results) % 100 == 0:
                    print(
                        json.dumps(
                            dict(
                                completed=len(results),
                                total=len(cases),
                                failed=sum("failure" in row for row in results),
                            )
                        ),
                        flush=True,
                    )

        await asyncio.gather(*(reader() for _ in range(args.workers)))
    _, _, after = read_snapshot(settings.database.resolve())
    assert before == after, "VN daily records, candle counts or operational metadata changed"
    results.sort(key=lambda row: row["case"])
    failures = [row for row in results if "failure" in row]
    report = dict(
        checked_at=datetime.now(UTC).isoformat(),
        url=args.url,
        workers=args.workers,
        configured_series=sum(len(entries) for entries in watchlist.values()),
        intervals=list(args.interval or INTERVALS),
        start_date=args.start_date,
        end_date=args.end_date,
        requests=len(results),
        passed_requests=len(results) - len(failures),
        failed_requests=len(failures),
        passed=not failures,
        statuses=dict(Counter(str(row.get("status", "transport_error")) for row in results)),
        main_snapshot_unchanged=True,
        snapshot_scope="VN daily record versions, total candle count and operational tables including epoch; other candle versions are not individually hashed.",
        before=before,
        after=after,
        results=results,
        limitation="Query coverage/schema audit; undefined warmup is reported, and freshness/legacy numerical identity is not certified.",
    )
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"before", "after", "results"}
            }
        ),
        flush=True,
    )
    return int(bool(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3001")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--interval", action="append", choices=INTERVALS)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--report", type=Path, default=Path("data/web-query-matrix.json"))
    raise SystemExit(asyncio.run(check(parser.parse_args())))
