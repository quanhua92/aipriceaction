"""Measure native candle history with a fresh cache against local RustFS.

Report actual downloaded object bytes, not cloud billing estimates. Empty and
failed requests never count as successful cold/warm checks. Main data and the
ordinary API cache remain unchanged.
"""

import argparse
import hashlib
import json
import resource
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from aipriceaction_api.archive import Archive, S3Store
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository

try:
    from scripts.check_retained_vn_daily import read_snapshot
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    # Direct script execution puts this directory, rather than its parent, on
    # sys.path. Module execution and importing the tool keep the package path.
    from check_retained_vn_daily import read_snapshot


class MeasuredStore(S3Store):
    def __init__(self, settings):
        super().__init__(settings)
        self.lock = threading.Lock()
        self.transfers = []

    def download(self, key, path):
        super().download(key, path)
        with self.lock:
            self.transfers.append({"object_key": key, "bytes": Path(path).stat().st_size})


def benchmark(args):
    settings = Settings.from_env()
    if settings.archive_backend != "s3" or urlsplit(settings.s3_endpoint or "").hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("Use a loopback RustFS archive")
    if not 1 <= args.workers <= settings.read_concurrency or not 1 <= args.max_symbols <= 100:
        raise ValueError("Choose bounded concurrency and ticker budgets")
    lo, hi = date_bounds(args.start_date), date_bounds(args.end_date, True)
    if not 0 <= hi - lo < 366 * 86400:
        raise ValueError("Supply a date range of up to one calendar year")
    if args.report.exists():
        raise ValueError("Choose a new report path to preserve prior evidence")
    watchlist = json.loads(settings.watchlist.read_text())[args.source][: args.max_symbols]
    symbols, not_applicable = [], []
    for entry in watchlist:
        symbol = entry if isinstance(entry, str) else entry["symbol"]
        if isinstance(entry, dict) and entry.get("history_start"):
            if hi < date_bounds(entry["history_start"]):
                if not entry.get("history_start_source"):
                    raise ValueError("Excluding pre-listing history requires a configured source")
                not_applicable.append(
                    {
                        "symbol": symbol,
                        "history_start": entry["history_start"],
                        "source": entry["history_start_source"],
                        "reason": "Entire range precedes configured verified history start",
                    }
                )
                continue
        symbols.append(symbol)
    repo = Repository(settings.database)
    _, _, before = read_snapshot(settings.database.resolve())
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "source": args.source,
        "interval": args.interval,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "selected_tickers": len(watchlist),
        "eligible_tickers": len(symbols),
        "not_applicable": not_applicable,
        "workers": args.workers,
        "measurements": [],
        "limitation": "Observed local reads and downloaded bytes; no cloud billing or production capacity claim.",
    }
    with tempfile.TemporaryDirectory(prefix="aipa-history-bench-") as cache:
        isolated = replace(settings, cache_dir=Path(cache))
        store = MeasuredStore(isolated)
        _ = store.client  # Initialize once before concurrent downloads.
        history = History(repo, Archive(repo, isolated, store), isolated)

        def query(symbol):
            begun = time.perf_counter()
            result = {"symbol": symbol}
            try:
                rows = history.read(args.source, symbol, args.interval, lo, hi)
                if not rows:
                    raise DataError("No candles in the requested range")
                result.update(
                    rows=len(rows),
                    payload_sha256=hashlib.sha256(
                        json.dumps([row.record() for row in rows], sort_keys=True).encode()
                    ).hexdigest(),
                )
            except DataError as exc:
                result["error"] = str(exc)
            result["elapsed_ms"] = round((time.perf_counter() - begun) * 1000, 2)
            return result

        previous = None
        for phase in ("cold", "warm"):
            begun, offset = time.perf_counter(), len(store.transfers)
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                results = list(pool.map(query, symbols))
            identities = [{k: v for k, v in row.items() if k != "elapsed_ms"} for row in results]
            if previous is not None and previous != identities:
                raise ValueError("Cold and warm candle values/provenance differ")
            previous = identities
            transfers = store.transfers[offset:]
            latencies = [row["elapsed_ms"] for row in results if "error" not in row]
            report["measurements"].append(
                {
                    "phase": phase,
                    "wall_ms": round((time.perf_counter() - begun) * 1000, 2),
                    "median_success_ms": statistics.median(latencies) if latencies else None,
                    "max_success_ms": max(latencies) if latencies else None,
                    "successful_requests": len(latencies),
                    "failed_requests": len(results) - len(latencies),
                    "downloaded_bytes": sum(row["bytes"] for row in transfers),
                    "downloaded_objects": len(transfers),
                    "downloads": transfers,
                    "queries": results,
                }
            )
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    _, _, after = read_snapshot(settings.database.resolve())
    report.update(
        peak_rss_bytes=peak if sys.platform == "darwin" else peak * 1024,
        sqlite_bytes=settings.database.stat().st_size,
        main_snapshot_unchanged=before == after,
        equal_cold_warm_results=True,
    )
    report["passed"] = before == after and all(
        m["successful_requests"] > 0 and m["failed_requests"] == 0 for m in report["measurements"]
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "measurements"}))
    for measurement in report["measurements"]:
        print(
            json.dumps({k: v for k, v in measurement.items() if k not in {"queries", "downloads"}})
        )
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"), default="vn")
    parser.add_argument("--interval", choices=("1D", "1h", "1m"), default="1D")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-symbols", type=int, default=100)
    parser.add_argument("--report", type=Path, required=True)
    raise SystemExit(0 if benchmark(parser.parse_args()) else 1)
