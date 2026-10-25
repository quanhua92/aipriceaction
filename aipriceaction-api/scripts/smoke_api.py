#!/usr/bin/env python3
"""Run a sequential, read-only smoke check across the public API surface.

By default, an empty SJC series is reported as EXPECTED_FAIL because the
production SJC feed is currently unavailable. Use --require-sjc to make that
condition fail the run. Mutating and token-protected routes are deliberately
not called.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import httpx

LOG = logging.getLogger("smoke_api")


@dataclass(frozen=True)
class Probe:
    name: str
    path: str
    params: dict[str, str] | None = None
    check: str = "json"
    symbol: str | None = None


def probes(reference_date: str | None) -> list[Probe]:
    """Build representative calls for each public API feature and provider."""
    items = [
        Probe("health", "/health", check="object"),
        Probe(
            "VN broad OHLCV CSV with MA+EMA",
            "/tickers",
            {"limit": "1", "ma": "true", "ema": "true", "mode": "vn", "format": "csv"},
            check="csv",
        ),
        Probe(
            "VN broad OHLCV CSV without MA",
            "/tickers",
            {"limit": "1", "ma": "false", "mode": "vn", "format": "csv"},
            check="csv",
        ),
        Probe(
            "Crypto broad OHLCV CSV with MA+EMA",
            "/tickers",
            {
                "limit": "1",
                "ma": "true",
                "ema": "true",
                "mode": "crypto",
                "format": "csv",
            },
            check="csv",
        ),
        Probe(
            "Yahoo broad OHLCV CSV with MA+EMA",
            "/tickers",
            {
                "limit": "1",
                "ma": "true",
                "ema": "true",
                "mode": "yahoo",
                "format": "csv",
            },
            check="csv",
        ),
        Probe(
            "All-mode broad OHLCV",
            "/tickers",
            {"limit": "1", "ma": "false", "mode": "all", "format": "json"},
            check="nonempty-object",
        ),
        Probe(
            "VN ticker groups (Vietnamese labels)",
            "/tickers/group",
            {"mode": "vn", "lang": "vi"},
            check="nonempty-object",
        ),
        Probe("Crypto ticker groups", "/tickers/group", {"mode": "crypto"}, check="nonempty-object"),
        Probe("Yahoo ticker groups", "/tickers/group", {"mode": "yahoo"}, check="nonempty-object"),
        Probe("All ticker groups", "/tickers/group", {"mode": "all"}, check="nonempty-object"),
        Probe("VN ticker names", "/tickers/name", {"mode": "vn"}, check="nonempty-object"),
        Probe("Crypto ticker names", "/tickers/name", {"mode": "crypto"}, check="nonempty-object"),
        Probe("Yahoo ticker names", "/tickers/name", {"mode": "yahoo"}, check="nonempty-object"),
        Probe("All ticker names", "/tickers/name", {"mode": "all"}, check="nonempty-object"),
        Probe("VN ticker info", "/tickers/info", {"ticker": "FPT"}, check="object"),
        Probe("VN stock directory", "/tickers/directory", check="directory"),
        Probe(
            "SJC OHLCV (known empty series)",
            "/tickers",
            {
                "symbol": "SJC-GOLD",
                "interval": "1D",
                "limit": "1",
                "ma": "false",
                "mode": "yahoo",
                "format": "json",
            },
            check="expected-empty-series",
            symbol="SJC-GOLD",
        ),
        Probe(
            "BTCUSDT daily OHLCV",
            "/tickers",
            {
                "symbol": "BTCUSDT",
                "interval": "1D",
                "limit": "1",
                "ma": "false",
                "mode": "crypto",
                "format": "json",
            },
            check="series",
            symbol="BTCUSDT",
        ),
        Probe(
            "BTCUSDT minute OHLCV",
            "/tickers",
            {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "limit": "1",
                "ma": "false",
                "mode": "crypto",
                "format": "json",
            },
            check="series",
            symbol="BTCUSDT",
        ),
        Probe(
            "AAPL daily OHLCV",
            "/tickers",
            {
                "symbol": "AAPL",
                "interval": "1D",
                "limit": "1",
                "ma": "false",
                "mode": "yahoo",
                "format": "json",
            },
            check="series",
            symbol="AAPL",
        ),
        Probe(
            "Explorer page",
            "/explorer",
            check="html",
        ),
        Probe(
            "Public static index",
            "/public/index.html",
            check="html",
        ),
    ]

    # Anchor analysis calls to the latest available FPT daily bar rather than
    # assuming that the current calendar day is a Vietnamese trading day.
    if reference_date:
        items.extend(
            [
                Probe(
                    "Top performers",
                    "/analysis/top-performers",
                    {
                        "mode": "vn",
                        "date": reference_date,
                        "limit": "5",
                        "min_volume": "0",
                    },
                ),
                Probe(
                    "MA scores by sector",
                    "/analysis/ma-scores-by-sector",
                    {
                        "mode": "vn",
                        "date": reference_date,
                        "ma_period": "20",
                        "top_per_sector": "1",
                    },
                ),
                Probe(
                    "Volume profile",
                    "/analysis/volume-profile",
                    {"symbol": "FPT", "mode": "vn", "date": reference_date, "bins": "10"},
                ),
                Probe(
                    "RRG analysis",
                    "/analysis/rrg",
                    {
                        "algorithm": "jdk",
                        "mode": "vn",
                        "trails": "5",
                        "min_volume": "1000",
                        "benchmark": "VNINDEX",
                        "period": "10",
                        "date": reference_date,
                        "ema": "true",
                    },
                ),
            ]
        )
    else:
        LOG.warning("Could not read a latest FPT date; skipping date-dependent analysis routes")
    return items


def response_detail(probe: Probe, response: httpx.Response, require_sjc: bool) -> tuple[str, str]:
    if not 200 <= response.status_code < 300:
        raise ValueError(f"HTTP {response.status_code}")

    if probe.check == "csv":
        content_type = response.headers.get("content-type", "").lower()
        if "csv" not in content_type:
            raise ValueError(f"Expected CSV content type, got {content_type or 'missing'}")
        rows = list(csv.reader(io.StringIO(response.text)))
        if len(rows) < 2 or not rows[0]:
            raise ValueError("CSV response has no data rows")
        return "PASS", f"CSV rows={len(rows) - 1}"

    if probe.check == "html":
        body = response.text.lstrip().lower()
        if "<html" not in body and "<!doctype html" not in body:
            raise ValueError("Expected an HTML document")
        return "PASS", f"HTML bytes={len(response.content)}"

    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Response is not valid JSON") from exc

    if not isinstance(payload, (dict, list)):
        raise ValueError(f"Expected JSON object or array, got {type(payload).__name__}")

    if probe.check == "directory":
        stocks = payload.get("stocks") if isinstance(payload, dict) else None
        if not isinstance(stocks, list) or not stocks:
            raise ValueError("Directory has no stock rows")
        return "PASS", f"stocks={len(stocks)}"

    if probe.check in {"nonempty-object", "object"}:
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object")
        if probe.check == "nonempty-object" and not payload:
            raise ValueError("JSON object is empty")
        return "PASS", f"keys={len(payload)}"

    if probe.check == "series":
        rows = payload.get(probe.symbol) if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"No OHLCV rows for {probe.symbol}")
        latest = rows[-1].get("time") if isinstance(rows[-1], dict) else None
        return "PASS", f"rows={len(rows)} latest={latest}"

    if probe.check == "expected-empty-series":
        rows = payload.get(probe.symbol) if isinstance(payload, dict) else None
        if isinstance(rows, list) and rows:
            return "PASS", f"rows={len(rows)} (SJC data is now present)"
        if payload not in ({}, {probe.symbol: []}):
            raise ValueError("SJC returned an unexpected JSON shape")
        if require_sjc:
            raise ValueError("SJC series is empty and --require-sjc was set")
        return "EXPECTED_FAIL", "no SJC OHLCV rows (known gap)"

    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    return "PASS", f"keys={len(payload)}"


def run_probe(client: httpx.Client, probe: Probe, require_sjc: bool) -> dict:
    started = perf_counter()
    result = {
        "name": probe.name,
        "method": "GET",
        "path": probe.path,
        "params": probe.params or {},
        "status_code": None,
        "elapsed_ms": None,
        "result": "FAIL",
        "detail": None,
        "latest_time": None,
    }
    try:
        response = client.get(probe.path, params=probe.params)
        result["status_code"] = response.status_code
        result["result"], result["detail"] = response_detail(probe, response, require_sjc)
        if probe.check == "series":
            payload = response.json()
            rows = payload.get(probe.symbol, []) if isinstance(payload, dict) else []
            if rows and isinstance(rows[-1], dict):
                result["latest_time"] = rows[-1].get("time")
    except (httpx.HTTPError, ValueError) as exc:
        result["detail"] = f"{type(exc).__name__}: {str(exc)[:240]}"
    result["elapsed_ms"] = round((perf_counter() - started) * 1000, 1)
    query = f"?{httpx.QueryParams(result['params'])}" if result["params"] else ""
    LOG.info(
        "%-14s %7.1f ms GET %s%s%s",
        result["result"],
        result["elapsed_ms"],
        probe.path,
        query,
        f" HTTP {result['status_code']} — {result['detail']}" if result["detail"] else "",
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("AIPA_API_URL", "http://127.0.0.1:8899"),
        help="API origin (default: AIPA_API_URL or http://127.0.0.1:8899)",
    )
    parser.add_argument("--timeout", type=float, default=45.0, help="Per-request timeout in seconds")
    parser.add_argument(
        "--require-sjc",
        action="store_true",
        help="Treat the known empty SJC response as a failure instead of EXPECTED_FAIL",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Optionally write all probe results as JSON to this path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")
    base_url = args.base_url.rstrip("/")
    LOG.info("API smoke run: %s (sequential, read-only; timeout %.1fs)", base_url, args.timeout)

    results = []
    with httpx.Client(base_url=base_url, timeout=args.timeout, follow_redirects=True) as client:
        anchor = run_probe(
            client,
            Probe(
                "VN FPT daily OHLCV (analysis date)",
                "/tickers",
                {
                    "symbol": "FPT",
                    "interval": "1D",
                    "limit": "1",
                    "ma": "false",
                    "mode": "vn",
                    "format": "json",
                },
                check="series",
                symbol="FPT",
            ),
            args.require_sjc,
        )
        results.append(anchor)
        stamp = anchor.get("latest_time")
        ref_date = stamp[:10] if isinstance(stamp, str) and len(stamp) >= 10 else None
        if ref_date:
            LOG.info("Analysis reference date from latest FPT daily bar: %s", ref_date)
        for probe in probes(ref_date):
            results.append(run_probe(client, probe, args.require_sjc))

    LOG.info("SKIP            GET /sync/{key:path} — requires a sync secret and key")
    LOG.info("SKIP            POST /sync/{key:path}, POST /tickers/refresh — mutating routes")
    LOG.info("SKIP            GET /openapi.json — disabled by API configuration")
    failures = sum(result["result"] == "FAIL" for result in results)
    expected = sum(result["result"] == "EXPECTED_FAIL" for result in results)
    passed = sum(result["result"] == "PASS" for result in results)
    LOG.info("SUMMARY: PASS=%d EXPECTED_FAIL=%d FAIL=%d", passed, expected, failures)

    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(
            json.dumps(
                {
                    "base_url": base_url,
                    "reference_date": ref_date,
                    "summary": {"pass": passed, "expected_fail": expected, "fail": failures},
                    "results": results,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        LOG.info("JSON report: %s", args.json_report)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
