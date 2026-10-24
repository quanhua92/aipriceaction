"""Warm and verify the exact 19-route home-page burst on one API replica."""

import argparse
import asyncio
import csv
import io
import json
import time

import httpx

from .homepage_routes import CORE_HOME_PAGE_URLS


def validate_body(path, response):
    content_type = response.headers.get("content-type", "")
    if path.startswith("/tickers?") and "format=csv" in path:
        rows = list(csv.DictReader(io.StringIO(response.text)))
        if not rows:
            raise ValueError("CSV response contains no ticker rows")
        return
    if "json" not in content_type:
        if not response.content:
            raise ValueError("response body is empty")
        return
    payload = response.json()
    if isinstance(payload, dict) and "error" in payload:
        raise ValueError(f"API returned an error body: {payload['error']}")
    if path.startswith("/analysis/rrg?"):
        rows = payload.get("data", {}).get("tickers", [])
        if not rows:
            raise ValueError("RRG response contains no tickers")
    elif not payload:
        raise ValueError("JSON response is empty")


async def burst(client, concurrency):
    request_slots = asyncio.Semaphore(concurrency)

    async def fetch(path):
        started = None
        try:
            async with request_slots:
                started = time.perf_counter()
                response = await client.get(path)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            response.raise_for_status()
            validate_body(path, response)
            return {"path": path, "status": response.status_code, "elapsed_ms": elapsed_ms}
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2) if started else 0
            return {
                "path": path,
                "status": None,
                "elapsed_ms": elapsed_ms,
                "error": str(exc) if isinstance(exc, (ValueError, httpx.HTTPStatusError)) else type(exc).__name__,
            }

    started = time.perf_counter()
    requests = await asyncio.gather(*(fetch(path) for path in CORE_HOME_PAGE_URLS))
    wall_ms = round((time.perf_counter() - started) * 1000, 2)
    elapsed = [request["elapsed_ms"] for request in requests]
    failed = [request for request in requests if request.get("status") != 200]
    return {
        "wall_ms": wall_ms,
        "max_request_ms": max(elapsed, default=0),
        "failed": len(failed),
        "over_target": sum(value > 1000 for value in elapsed),
        "requests": requests,
    }


async def warm(args):
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    rounds = []
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=timeout,
        limits=limits,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        for index in range(1, args.max_rounds + 1):
            first = await burst(client, args.concurrency)
            verify = await burst(client, args.concurrency)
            rounds.append({"round": index, "warm": first, "verify": verify})
            if first["failed"] or verify["failed"]:
                break
            if (
                verify["wall_ms"] <= args.instant_ms
                and verify["max_request_ms"] <= args.instant_ms
            ):
                return {"base_url": args.base_url, "instant": True, "rounds": rounds}
    return {"base_url": args.base_url, "instant": False, "rounds": rounds}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8899")
    parser.add_argument("--concurrency", type=int, default=len(CORE_HOME_PAGE_URLS))
    parser.add_argument("--instant-ms", type=int, default=1000)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--max-rounds", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 32:
        parser.error("--concurrency must be between 1 and 32")
    if not 1 <= args.instant_ms <= 120_000:
        parser.error("--instant-ms must be between 1 and 120000")
    if not 1 <= args.timeout <= 120:
        parser.error("--timeout must be between 1 and 120 seconds")
    if not 1 <= args.max_rounds <= 5:
        parser.error("--max-rounds must be between 1 and 5")
    result = asyncio.run(warm(args))
    print(json.dumps(result, indent=2))
    return 0 if result["instant"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
