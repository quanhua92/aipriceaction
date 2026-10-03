"""Read-only cold/warm profile measurements against local RustFS.

Uses a fresh temporary object cache and the configured VN watchlist. This never
publishes archives, modifies candles, or changes production routing.
"""

import argparse
import json
import resource
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from aipriceaction_api.analysis import Analysis
from aipriceaction_api.archive import Archive
from aipriceaction_api.catalog import Catalog
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository


def benchmark(args):
    settings = Settings.from_env()
    if urlsplit(settings.s3_endpoint or "").hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("This rehearsal requires a loopback S3 endpoint")
    if not 1 <= args.workers <= settings.read_concurrency or not 1 <= args.max_symbols <= 100:
        raise ValueError("Choose bounded concurrency and ticker budgets")
    if not 0 <= date_bounds(args.end_date) - date_bounds(args.start_date) < 366 * 86400:
        raise ValueError("Supply a date range of up to one calendar year")
    watchlist = json.loads(settings.watchlist.read_text())["vn"]
    symbols = [e if isinstance(e, str) else e["symbol"] for e in watchlist][: args.max_symbols]
    repo = Repository(settings.database)
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "workers": args.workers,
        "tickers": len(symbols),
        "measurements": [],
    }
    with tempfile.TemporaryDirectory(prefix="aipa-profile-bench-") as cache:
        isolated = replace(settings, cache_dir=Path(cache))
        history = History(repo, Archive(repo, isolated), isolated)
        analysis = Analysis(history, Catalog(isolated))

        def query(symbol):
            begun = time.perf_counter()
            try:
                result = analysis.profile(
                    symbol, start_date=args.start_date, end_date=args.end_date
                )
                status = {"symbol": symbol, "rows": result["total_analyzed"]}
            except DataError as exc:
                result = None
                status = {"symbol": symbol, "error": str(exc)}
            status["elapsed_ms"] = round((time.perf_counter() - begun) * 1000, 2)
            return status, result

        previous = None
        for phase in ("cold", "warm"):
            begun = time.perf_counter()
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                results = list(pool.map(query, symbols))
            payloads = [r[1] for r in results]
            if previous is not None:
                assert previous == payloads, "Cached and uncached profile values differ"
            previous = payloads
            elapsed = [r[0]["elapsed_ms"] for r in results]
            report["measurements"].append(
                {
                    "phase": phase,
                    "wall_ms": round((time.perf_counter() - begun) * 1000, 2),
                    "median_ms": statistics.median(elapsed),
                    "max_ms": max(elapsed),
                    "cached_bytes": sum(p.stat().st_size for p in Path(cache).glob("*.parquet")),
                    "queries": [r[0] for r in results],
                }
            )
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["peak_rss_bytes"] = peak if sys.platform == "darwin" else peak * 1024
    report["sqlite_bytes"] = settings.database.stat().st_size
    report["equal_cold_warm_values"] = True
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "measurements"}))
    for measurement in report["measurements"]:
        print(
            json.dumps(
                {k: v for k, v in measurement.items() if k != "queries"}
                | {
                    "rows": sum(q.get("rows", 0) for q in measurement["queries"]),
                    "failed": sum("error" in q for q in measurement["queries"]),
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-symbols", type=int, default=55)
    parser.add_argument("--report", type=Path, default=Path("data/archive-profile-benchmark.json"))
    benchmark(parser.parse_args())
