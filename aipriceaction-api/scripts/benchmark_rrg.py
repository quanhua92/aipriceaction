"""Bounded, read-only RRG timing and response-consistency check for loopback APIs.

Run the same request against Rust and Python with matching data/cache state. Use
the first report's SHA-256 as ``--expected-sha256`` on the second run. The script
does not clear caches or mutate server state. A cold label is descriptive; the
operator must arrange and verify the actual cache state.
"""

import argparse
import asyncio
import hashlib
import json
import math
import re
import statistics
import time
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def percentile(values, quantile):
    return round(sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)], 2)


def fingerprint(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_payload(payload, mode, requested_date):
    if not isinstance(payload, dict):
        raise ValueError("rrg_response_not_object")
    if (
        payload.get("analysis_type") != "rrg"
        or payload.get("analysis_date") != requested_date
        or type(payload.get("total_analyzed")) is not int
        or not isinstance(payload.get("data"), dict)
    ):
        raise ValueError("rrg_response_contract_mismatch")
    data = payload["data"]
    if (
        data.get("algorithm") != "jdk"
        or data.get("benchmark") != "VNINDEX"
        or data.get("period") != 10
        or not isinstance(data.get("tickers"), list)
    ):
        raise ValueError("rrg_response_contract_mismatch")
    tickers = data["tickers"]
    if not tickers:
        raise ValueError("rrg_response_has_no_tickers")
    if payload["total_analyzed"] != len(tickers):
        raise ValueError("rrg_response_count_mismatch")
    required = {"symbol", "rs_ratio", "rs_momentum", "raw_rs", "close", "volume", "trails"}
    if mode == "all":
        required.add("source")
    for ticker in tickers:
        if not isinstance(ticker, dict) or not required <= ticker.keys():
            raise ValueError("rrg_ticker_contract_mismatch")
        if not isinstance(ticker["symbol"], str) or not ticker["symbol"]:
            raise ValueError("rrg_ticker_symbol_invalid")
        for name in ("rs_ratio", "rs_momentum", "raw_rs", "close"):
            value = ticker[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"rrg_ticker_{name}_invalid")
        if type(ticker["volume"]) is not int:
            raise ValueError("rrg_ticker_volume_invalid")
        trails = ticker["trails"]
        if not isinstance(trails, list) or len(trails) > 5:
            raise ValueError("rrg_ticker_trails_invalid")
        if any(
            not isinstance(point, dict)
            or not isinstance(point.get("date"), str)
            or any(
                isinstance(point.get(name), bool)
                or not isinstance(point.get(name), (int, float))
                or not math.isfinite(point[name])
                for name in ("rs_ratio", "rs_momentum")
            )
            for point in trails
        ):
            raise ValueError("rrg_ticker_trail_point_invalid")


def validate_url(url):
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "This rehearsal requires a loopback API origin without credentials or a path"
        )
    return f"{parts.scheme}://{parts.netloc}"


async def benchmark(args):
    origin = validate_url(args.url)
    if not 1 <= args.samples <= 64 or not 0 <= args.warmups <= 5:
        raise ValueError("Choose 1–64 samples and 0–5 warmups")
    if not 1 <= args.workers <= 8:
        raise ValueError("Choose 1–8 concurrent requests")
    if (
        not 1 <= args.timeout <= 180
        or not 1 <= args.target_ms <= 180_000
        or not 15 <= args.wall_budget <= 900
    ):
        raise ValueError(
            "Choose a timeout of 1–180 seconds, target of 1–180000 ms, and wall budget of 15–900 seconds"
        )
    if args.expected_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", args.expected_sha256):
        raise ValueError("Expected SHA-256 must contain 64 hexadecimal characters")
    if args.report and args.report.exists():
        raise ValueError(f"Refusing to overwrite existing report: {args.report}")
    try:
        parsed_date = date.fromisoformat(args.date)
    except ValueError as exc:
        raise ValueError("Use a valid YYYY-MM-DD RRG date") from exc
    if parsed_date.isoformat() != args.date:
        raise ValueError("Use a zero-padded YYYY-MM-DD RRG date")

    params = [
        ("algorithm", "jdk"),
        ("mode", args.mode),
        ("trails", "5"),
        ("min_volume", "1000"),
        ("benchmark", "VNINDEX"),
        ("period", "10"),
        ("date", args.date),
        ("ema", "true"),
    ]
    hashes = set()
    expected_hash = None
    errors = []
    measurements = []
    stop_after_error = asyncio.Event()
    wall_started = time.perf_counter()
    timeout = httpx.Timeout(args.timeout)

    async with httpx.AsyncClient(base_url=origin, timeout=timeout) as client:

        async def request(label, semaphore=None):
            nonlocal expected_hash
            queued = time.perf_counter()
            try:
                if semaphore is not None:
                    await semaphore.acquire()
            except asyncio.CancelledError:
                raise
            started = time.perf_counter()
            result = {"phase": label}
            result["queue_ms"] = round((started - queued) * 1000, 2)
            try:
                if stop_after_error.is_set():
                    result["skipped"] = "prior_request_failed"
                else:
                    response = await client.get("/analysis/rrg", params=params)
                    result["status"] = response.status_code
                    result["decoded_bytes"] = len(response.content)
                    response.raise_for_status()
                    payload = response.json()
                    validate_payload(payload, args.mode, args.date)
                    digest = fingerprint(payload)
                    hashes.add(digest)
                    result["payload_sha256"] = digest
                    if expected_hash is None:
                        expected_hash = digest
                    elif digest != expected_hash:
                        result["error"] = "payload_changed"
            except Exception as exc:
                result["error"] = result.get("error") or (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else f"HTTP {result['status']}"
                    if "status" in result
                    else type(exc).__name__
                )
            except asyncio.CancelledError:
                result["error"] = "cancelled_by_wall_budget"
                raise
            finally:
                result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
                if "error" in result:
                    errors.append(result)
                    stop_after_error.set()
                measurements.append(result)
                if semaphore is not None:
                    semaphore.release()
            return result

        timed_out = False
        try:
            async with asyncio.timeout(args.wall_budget):
                for _ in range(args.warmups):
                    await request("warmup")
                    if stop_after_error.is_set():
                        break
                semaphore = asyncio.Semaphore(args.workers)
                if not stop_after_error.is_set():
                    await asyncio.gather(
                        *(request("sample", semaphore) for _ in range(args.samples))
                    )
        except TimeoutError:
            timed_out = True
            errors.append({"error": "wall_budget_exceeded"})

    warmups = [row for row in measurements if row["phase"] == "warmup"]
    samples = [row for row in measurements if row["phase"] == "sample"]
    successful = [row for row in samples if "error" not in row and "skipped" not in row]
    latencies = [row["elapsed_ms"] for row in successful]
    queue_times = [row["queue_ms"] for row in samples]
    p95_ms = percentile(latencies, 0.95) if latencies else None
    response_consistent = len(hashes) == 1
    expected_hash_match = args.expected_sha256 is None or (
        len(hashes) == 1 and next(iter(hashes)).lower() == args.expected_sha256.lower()
    )
    within_target = p95_ms is not None and p95_ms <= args.target_ms
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "url": origin,
        "cache_state_label": args.cache_state,
        "request": {
            "path": "/analysis/rrg",
            "params": params,
        },
        "workers": args.workers,
        "planned_samples": args.samples,
        "warmups": warmups,
        "sample_count": len(samples),
        "wall_budget_seconds": args.wall_budget,
        "wall_budget_exceeded": timed_out,
        "samples": samples,
        "errors": errors,
        "summary": {
            "successful_samples": len(successful),
            "median_ms": round(statistics.median(latencies), 2) if latencies else None,
            "p95_ms": p95_ms,
            "max_ms": max(latencies) if latencies else None,
            "queue_p95_ms": percentile(queue_times, 0.95) if queue_times else None,
            "payload_sha256": next(iter(hashes)) if len(hashes) == 1 else None,
            "response_consistent": response_consistent,
            "expected_payload_sha256": args.expected_sha256,
            "matches_expected_payload": expected_hash_match,
            "target_ms": args.target_ms,
            "within_target": within_target,
        },
        "elapsed_wall_seconds": round(time.perf_counter() - wall_started, 2),
        "passed": not errors and response_consistent and expected_hash_match and within_target,
        "note": "Cache state is operator-supplied; this script never clears caches.",
    }
    rendered = json.dumps(report, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8899")
    parser.add_argument("--date", required=True, help="RRG date in YYYY-MM-DD format")
    parser.add_argument("--mode", choices=("vn", "crypto", "yahoo", "all"), default="vn")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--wall-budget", type=int, default=240)
    parser.add_argument("--target-ms", type=int, default=25_000)
    parser.add_argument(
        "--cache-state", choices=("unspecified", "cold", "warm"), default="unspecified"
    )
    parser.add_argument(
        "--expected-sha256", help="Fail if the response hash differs (for cross-server comparison)"
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        return asyncio.run(benchmark(args))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
