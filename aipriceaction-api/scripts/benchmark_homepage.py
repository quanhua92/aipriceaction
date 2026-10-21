"""Benchmark the exact concurrent API burst loaded by the home page.

This read-only check keeps the observed URL list, including duplicates, because
the browser sends those requests together. Its default ``instant`` gate is a
1-second maximum for both each request and the full page burst. Use
``--bypass-response-cache`` to measure uncached ticker reads as a second run.
"""

import argparse
import asyncio
import csv
import hashlib
import io
import json
import math
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

HOME_PAGE_URLS = (
    "/tickers?limit=1&ma=true&ema=true&mode=vn&format=csv",
    "/tickers?limit=1&ma=true&ema=true&mode=crypto&format=csv",
    "/tickers?limit=1&ma=true&ema=true&mode=yahoo&format=csv",
    "/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date=2026-10-21&ema=true",
    "/tickers?interval=1D&limit=1&mode=vn&ema=true&format=csv",
    "/health",
    "/tickers/group",
    "/tickers?limit=1&ma=false&mode=vn&format=csv",
    "/tickers/group?mode=crypto",
    "/tickers?limit=1&ma=false&mode=crypto&format=csv",
    "/tickers/group?mode=yahoo",
    "/tickers?limit=1&ma=false&mode=yahoo&format=csv",
    "/tickers/name",
    "/tickers/name?mode=crypto",
    "/tickers/name?mode=yahoo",
    "/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date=2026-10-21&ema=true",
    "/tickers?limit=1&ma=false&mode=vn&format=csv",
    "/tickers?limit=1&ma=false&mode=crypto&format=csv",
    "/tickers?limit=1&ma=false&mode=yahoo&format=csv",
)
EXTRA_BROWSER_URLS = (
    "/tickers?symbol=VNINDEX&interval=1D&mode=all&limit=1&ema=true&format=csv",
    "/tickers?interval=1D&limit=110&mode=all&ma=false&format=csv",
    "/tickers?symbol=VIC&interval=1D&limit=256&mode=all&ema=true&format=csv",
    "/tickers?symbol=VNINDEX&interval=1D&limit=256&mode=all&ema=true&format=csv",
    "/tickers?symbol=VIC&interval=1D&limit=512&mode=all&ema=true&format=csv",
    "/analysis/volume-profile?symbol=VNINDEX&date=2026-10-21&bins=10&mode=all",
)
TICKER_CASES = tuple(
    f"/tickers?limit=1&ma={ma}&ema={ema}&mode={source}&format=csv"
    for source in ("vn", "crypto", "yahoo")
    for ma, ema in (("true", "false"), ("true", "true"), ("false", "false"), ("false", "true"))
) + (
    "/tickers?limit=1&ma=true&mode=vn&format=csv",
    "/tickers?limit=1&ma=true&mode=crypto&format=csv",
    "/tickers?limit=1&ma=true&mode=yahoo&format=csv",
    "/tickers?limit=1&ma=false&mode=vn&format=csv",
    "/tickers?limit=1&ma=false&mode=crypto&format=csv",
    "/tickers?limit=1&ma=false&mode=yahoo&format=csv",
    "/tickers?interval=1D&limit=1&mode=vn&ema=true&format=csv",
    "/tickers?symbol=VCB&interval=1D&limit=1&mode=vn&ma=false&format=csv",
    "/tickers?symbol=VCB&interval=1D&limit=1&mode=vn&ma=true&ema=false&format=csv",
    "/tickers?symbol=VCB&interval=1D&limit=1&mode=vn&ma=true&ema=true&format=csv",
    "/tickers?symbol=VCB&symbol=FPT&interval=1D&limit=1&mode=vn&ma=false&format=csv",
    "/tickers?symbol=VCB&symbol=FPT&interval=1D&limit=1&mode=vn&ma=true&ema=false&format=csv",
    "/tickers?symbol=VCB&symbol=FPT&interval=1D&limit=1&mode=vn&ma=true&ema=true&format=csv",
    "/tickers?symbol=VNINDEX&interval=1D&mode=all&limit=1&ema=true&format=csv",
    "/tickers?interval=1D&limit=110&mode=all&ma=false&format=csv",
    "/tickers?symbol=VIC&interval=1D&limit=256&mode=all&ema=true&format=csv",
    "/tickers?symbol=VNINDEX&interval=1D&limit=256&mode=all&ema=true&format=csv",
    "/tickers?symbol=VIC&interval=1D&limit=512&mode=all&ema=true&format=csv",
)
ANALYSIS_CASES = (
    "/analysis/top-performers?mode=vn&date=2026-10-21&ema=true&limit=10&min_volume=0&snap=false",
    "/analysis/ma-scores-by-sector?mode=vn&date=2026-10-21&ema=true&ma_period=20&snap=false",
    "/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date=2026-10-21&ema=true&snap=false",
    "/analysis/rrg?algorithm=mascore&mode=vn&trails=5&min_volume=1000&date=2026-10-21&ema=true&snap=false",
    "/analysis/volume-profile?symbol=VNINDEX&date=2026-10-21&bins=10&mode=vn",
)
READ_ENDPOINT_CASES = TICKER_CASES + HOME_PAGE_URLS + EXTRA_BROWSER_URLS + ANALYSIS_CASES + (
    "/tickers/info?ticker=VCB",
    "/tickers/info",
    "/tickers/group?mode=all",
    "/tickers/name?mode=all",
)


def validate_base_url(value):
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or parts.hostname not in {"127.0.0.1", "localhost", "::1", "192.168.1.13"}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("Use the local API or 192.168.1.13 web-proxy URL without credentials")
    if parts.query:
        raise ValueError("Base URL must not contain a query string")
    return value.rstrip("/")


def request_url(base_url, path, bypass_response_cache):
    parts = urlsplit(base_url + path)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if bypass_response_cache and parts.path.endswith("/tickers"):
        query.extend((("cache", "false"), ("redis", "false"), ("snap", "false")))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def inspect_response(path, content_type, content):
    result = {"content_type": content_type, "bytes": len(content)}
    if path.split("?", 1)[0].endswith("/tickers") and "csv" in content_type:
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
        result["rows"] = len(rows)
        if not rows:
            raise ValueError("CSV response contains no ticker rows")
        result["latest_time"] = max((row.get("time", "") for row in rows), default="")
    elif "json" in content_type:
        payload = json.loads(content)
        if path.split("?", 1)[0].startswith("/analysis/"):
            data = payload.get("data", {})
            row_key = next(
                (key for key in ("tickers", "performers", "sectors", "profile") if key in data),
                None,
            )
            rows = data.get(row_key, []) if row_key else []
            result["rows"] = len(rows) if isinstance(rows, list) else 1
            if "error" in payload:
                raise ValueError(f"API returned an error body: {payload['error']}")
            if row_key and not rows:
                raise ValueError(f"{row_key} response contains no rows")
        elif isinstance(payload, dict):
            result["rows"] = len(payload)
    return result


def percentile(values, quantile):
    return round(sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)], 2)


async def benchmark(args):
    base_url = validate_base_url(args.base_url)
    if not 1 <= args.timeout <= 120 or not 1 <= args.instant_ms <= 120_000:
        raise ValueError("Choose a timeout of 1–120 seconds and an instant target of 1–120000 ms")
    if not 1 <= args.concurrency <= 32:
        raise ValueError("Choose 1–32 concurrent requests")

    if args.tickers_only and args.all_read_endpoints:
        raise ValueError("Choose only one of --tickers-only or --all-read-endpoints")
    paths = (
        TICKER_CASES
        if args.tickers_only
        else READ_ENDPOINT_CASES
        if args.all_read_endpoints
        else HOME_PAGE_URLS
    )
    requests = [
        {
            "index": index + 1,
            "path": path,
            "url": request_url(base_url, path, args.bypass_response_cache),
        }
        for index, path in enumerate(paths)
    ]
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    results = []
    wall_started = time.perf_counter()
    request_slots = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        trust_env=False,
        follow_redirects=False,
    ) as client:

        async def fetch(request):
            started = None
            result = {
                "index": request["index"],
                "path": request["path"],
                "url": request["url"],
            }
            try:
                async with request_slots:
                    started = time.perf_counter()
                    response = await client.get(request["url"])
                    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
                result["status"] = response.status_code
                result["content_encoding"] = response.headers.get("content-encoding", "identity")
                result["wire_bytes"] = response.num_bytes_downloaded
                response.raise_for_status()
                result.update(
                    inspect_response(
                        request["path"],
                        response.headers.get("content-type", ""),
                        response.content,
                    )
                )
                result["sha256"] = hashlib.sha256(response.content).hexdigest()
            except Exception as exc:
                result["elapsed_ms"] = (
                    round((time.perf_counter() - started) * 1000, 2) if started else 0
                )
                result["error"] = (
                    str(exc)
                    if isinstance(exc, (ValueError, httpx.HTTPStatusError))
                    else type(exc).__name__
                )
            return result

        results = await asyncio.gather(*(fetch(request) for request in requests))

    wall_ms = round((time.perf_counter() - wall_started) * 1000, 2)
    elapsed = [row["elapsed_ms"] for row in results]
    slow = [row for row in results if row["elapsed_ms"] > args.instant_ms]
    failed = [row for row in results if "error" in row or row.get("status") != 200]
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "request_count": len(requests),
        "concurrency": args.concurrency,
        "response_cache_bypassed": args.bypass_response_cache,
        "instant_target_ms": args.instant_ms,
        "page_wall_ms": wall_ms,
        "max_request_ms": max(elapsed, default=0),
        "p50_request_ms": round(statistics.median(elapsed), 2) if elapsed else 0,
        "p95_request_ms": percentile(elapsed, 0.95) if elapsed else 0,
        "over_target": len(slow),
        "failed": len(failed),
        "instant": not failed and not slow and wall_ms <= args.instant_ms,
        "requests": results,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        report["report"] = str(args.report)
    print(json.dumps(report, indent=2))
    return 0 if report["instant"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://192.168.1.13:5173/aipriceaction-api",
        help="Local API origin and optional Vite proxy prefix",
    )
    parser.add_argument("--concurrency", type=int, default=len(HOME_PAGE_URLS))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument(
        "--instant-ms",
        type=int,
        default=1000,
        help="Maximum allowed page wall time and per-request latency",
    )
    parser.add_argument(
        "--bypass-response-cache",
        action="store_true",
        help="Append cache=false to ticker requests to expose the uncached read cost",
    )
    parser.add_argument(
        "--tickers-only",
        action="store_true",
        help="Run every source/MA/EMA combination plus single-, selected-multi-, and history-window ticker cases",
    )
    parser.add_argument(
        "--all-read-endpoints",
        action="store_true",
        help="Run ticker, metadata, homepage, and analysis GET cases without mutation endpoints",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/homepage-api-benchmark.json"),
        help="Small JSON report path",
    )
    raise SystemExit(asyncio.run(benchmark(parser.parse_args())))
