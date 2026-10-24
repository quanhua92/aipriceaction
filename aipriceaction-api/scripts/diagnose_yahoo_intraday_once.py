#!/usr/bin/env python3
"""Read-only one-off diagnosis of Yahoo intraday coverage for one symbol.

Checks the local watchlist and SQLite metadata, current public API rows, the
configured Yahoo provider adapter, and the archived Rust worker contract. It
does not write to SQLite, S3, or the API.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent
sys.path.insert(0, str(API_ROOT / "src"))

from aipriceaction_api.config import Settings  # noqa: E402
from aipriceaction_api.providers import Providers  # noqa: E402


def utc_time(value):
    if value is None:
        return None
    try:
        stamp = int(value)
        if stamp == 0:
            return None
        # Repository operational timestamps use nanoseconds; candle and archive
        # boundaries use ordinary Unix seconds.
        if abs(stamp) >= 100_000_000_000_000:
            stamp /= 1_000_000_000
        elif abs(stamp) >= 100_000_000_000:
            stamp /= 1_000
        return datetime.fromtimestamp(stamp, UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)


def clean_error(value, limit=240):
    if value is None:
        return None
    value = str(value)
    value = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", value)
    return value[:limit]


def row_dates(rows):
    times = []
    for row in rows:
        value = row.get("time") or row.get("date") or row.get("timestamp")
        if value is None:
            continue
        try:
            text = str(value).replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            times.append(parsed.astimezone(UTC))
        except ValueError:
            continue
    if not times:
        return {"first": None, "last": None}
    return {"first": min(times).isoformat(), "last": max(times).isoformat()}


def candle_dates(rows):
    if not rows:
        return {"first": None, "last": None}
    return {
        "first": utc_time(min(row.time for row in rows)),
        "last": utc_time(max(row.time for row in rows)),
    }


def load_ingest_config(path, catalog_dir, ingest_universe, symbol):
    result = {
        "watchlist_path": str(path),
        "catalog_dir": str(catalog_dir),
        "ingest_universe": ingest_universe,
        "symbol_override_present": False,
        "catalog_contains_symbol": False,
        "scheduled_for_ingestion": False,
        "effective_intervals": [],
    }
    if path.exists():
        try:
            watchlist = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            result["watchlist_error"] = clean_error(exc)
            watchlist = {}
    else:
        result["watchlist_error"] = "watchlist file not found"
        watchlist = {}

    for item in watchlist.get("yahoo", []):
        entry = {"symbol": item} if isinstance(item, str) else item
        if str(entry.get("symbol", "")).upper() == symbol.upper():
            result["symbol_override_present"] = True
            result["override_intervals"] = entry.get("intervals")
            break

    catalog_path = catalog_dir / "global_tickers.json"
    try:
        catalog = json.loads(catalog_path.read_text())
        result["catalog_path"] = str(catalog_path)
        result["catalog_contains_symbol"] = any(
            str(item.get("symbol", "")).upper() == symbol.upper()
            for item in catalog.get("data", [])
        )
    except (OSError, ValueError, AttributeError):
        result["catalog_error"] = "catalog file missing or invalid"

    if ingest_universe == "catalog":
        result["scheduled_for_ingestion"] = result["catalog_contains_symbol"]
    elif ingest_universe == "watchlist":
        result["scheduled_for_ingestion"] = result["symbol_override_present"]
    else:
        result["configuration_error"] = "unknown INGEST_UNIVERSE value"

    if result["scheduled_for_ingestion"]:
        result["effective_intervals"] = ["1D", "1h", "1m"]
        result["intervals_reason"] = (
            "Every selected Yahoo symbol is probed at all three native intervals."
        )
    return result


def table_columns(connection, table):
    try:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def inspect_sqlite(path, symbol):
    result = {"path": str(path), "exists": path.is_file(), "intervals": {}}
    if not result["exists"]:
        result["error"] = "database file not found"
        return result

    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        result["error"] = clean_error(exc)
        return result

    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        result["tables_available"] = sorted(tables)

        if "tickers" in tables:
            ticker = connection.execute(
                "SELECT * FROM tickers WHERE source='yahoo' AND symbol=?", (symbol.upper(),)
            ).fetchone()
            result["ticker"] = dict(ticker) if ticker else None
        else:
            result["ticker"] = None

        for interval in ("1D", "1h", "1m"):
            item = {"candle_rows": 0, "candle_range": {"first": None, "last": None}}
            if "candles" in tables:
                row = connection.execute(
                    """SELECT COUNT(*) AS n, MIN(time) AS first, MAX(time) AS last
                       FROM candles WHERE source='yahoo' AND symbol=? AND interval=?""",
                    (symbol.upper(), interval),
                ).fetchone()
                item["candle_rows"] = row["n"]
                item["candle_range"] = {
                    "first": utc_time(row["first"]),
                    "last": utc_time(row["last"]),
                }

            if "series" in tables:
                row = connection.execute(
                    "SELECT provider,revision,status FROM series WHERE source='yahoo' AND symbol=? AND interval=?",
                    (symbol.upper(), interval),
                ).fetchone()
                item["series"] = dict(row) if row else None

            if "archives" in tables:
                row = connection.execute(
                    """SELECT COUNT(*) AS objects, COALESCE(SUM(row_count),0) AS rows,
                              MIN(start) AS first, MAX(end) AS last
                       FROM archives WHERE source='yahoo' AND symbol=? AND interval=?
                         AND status='published'""",
                    (symbol.upper(), interval),
                ).fetchone()
                item["published_archives"] = {
                    "objects": row["objects"],
                    "rows": row["rows"],
                    "first": utc_time(row["first"]),
                    "last": utc_time(row["last"]),
                }

            if "jobs" in tables:
                jobs = connection.execute(
                    """SELECT kind,status,attempts,retry_at,updated_at,error FROM jobs
                       WHERE source='yahoo' AND symbol=? AND interval=?
                       ORDER BY updated_at DESC LIMIT 5""",
                    (symbol.upper(), interval),
                ).fetchall()
                item["recent_jobs"] = [
                    {
                        "kind": job["kind"],
                        "status": job["status"],
                        "attempts": job["attempts"],
                        "retry_at": utc_time(job["retry_at"]),
                        "updated_at": utc_time(job["updated_at"]),
                        "error": clean_error(job["error"]),
                    }
                    for job in jobs
                ]

            if "source_checks" in tables:
                columns = table_columns(connection, "source_checks")
                fields = [
                    field
                    for field in (
                        "outcome",
                        "provider",
                        "attempted_at_ns",
                        "successful_at_ns",
                        "error",
                    )
                    if field in columns
                ]
                if fields:
                    row = connection.execute(
                        f"SELECT {','.join(fields)} FROM source_checks WHERE source='yahoo' AND symbol=? AND interval=?",
                        (symbol.upper(), interval),
                    ).fetchone()
                    check = dict(row) if row else None
                    if check:
                        for field in ("attempted_at_ns", "successful_at_ns"):
                            if check.get(field) is not None:
                                check[field] = utc_time(check[field])
                        check["error"] = clean_error(check.get("error"))
                    item["last_source_check"] = check

            if "quality" in tables:
                findings = connection.execute(
                    """SELECT kind,detail,resolved FROM quality
                       WHERE source='yahoo' AND symbol=? AND interval=? AND resolved=0
                       ORDER BY last_seen DESC LIMIT 5""",
                    (symbol.upper(), interval),
                ).fetchall()
                item["open_quality_findings"] = [
                    {"kind": row["kind"], "detail": clean_error(row["detail"])}
                    for row in findings
                ]
            result["intervals"][interval] = item
    except sqlite3.Error as exc:
        result["error"] = clean_error(exc)
    finally:
        connection.close()
    return result


def inspect_rust_contract(symbol):
    root = REPO_ROOT / "legacy/rust/aipriceaction"
    result = {"source_present": root.exists(), "runtime_environment_available": False}
    if not root.exists():
        return result
    try:
        ticker_file = json.loads((root / "global_tickers.json").read_text())
        result["symbol_in_global_ticker_file"] = any(
            str(item.get("symbol", "")).upper() == symbol.upper()
            for item in ticker_file.get("data", [])
        )
    except (OSError, ValueError, AttributeError):
        result["symbol_in_global_ticker_file"] = None
    try:
        bootstrap = (root / "src/workers/yahoo_bootstrap.rs").read_text()
        result["bootstrap_intervals"] = [
            interval for interval in ("1D", "1h", "1m") if f'"{interval}"' in bootstrap
        ]
        result["bootstrap_includes_all_native_intervals"] = all(
            interval in result["bootstrap_intervals"] for interval in ("1D", "1h", "1m")
        )
    except OSError:
        result["bootstrap_intervals"] = None
    try:
        cli = (root / "src/cli.rs").read_text()
        result["workers_gated_by_yahoo_workers_env"] = "YAHOO_WORKERS" in cli
        result["old_runtime_worker_setting"] = "unavailable from this checkout"
    except OSError:
        result["workers_gated_by_yahoo_workers_env"] = None
    return result


def fetch_api(base_url, symbol, interval, timeout):
    query = urlencode(
        {"symbol": symbol, "interval": interval, "mode": "yahoo", "format": "csv", "limit": 10000}
    )
    url = base_url.rstrip("/") + "/tickers?" + query
    request = Request(url, headers={"Accept": "text/csv", "User-Agent": "AIPA-intraday-diagnostic/1.0"})
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            rows = list(csv.DictReader(io.StringIO(body)))
            return {
                "url": url,
                "http_status": response.status,
                "rows": len(rows),
                "range": row_dates(rows),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": None,
            }
    except HTTPError as exc:
        return {
            "url": url,
            "http_status": exc.code,
            "rows": None,
            "range": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": clean_error(exc.read().decode("utf-8", "replace")),
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "url": url,
            "http_status": None,
            "rows": None,
            "range": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": clean_error(exc),
        }


async def fetch_upstream(settings, symbol, interval):
    providers = Providers(settings)
    started = time.perf_counter()
    try:
        page = await providers.page("yahoo", symbol, interval, count=30)
        return {
            "provider": page.provider,
            "rows": len(page.rows),
            "range": candle_dates(page.rows),
            "no_data": page.no_data,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": None,
            "provider_attempts": dict(providers.provider_attempt_counts),
        }
    except Exception as exc:  # report adapter/provider failures as diagnostic evidence
        return {
            "provider": "yahoo",
            "rows": None,
            "range": None,
            "no_data": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": clean_error(exc),
            "provider_attempts": dict(providers.provider_attempt_counts),
        }
    finally:
        await providers.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPCX")
    parser.add_argument("--api-url", default="https://api.aipriceaction.com")
    parser.add_argument("--database", type=Path, default=API_ROOT / "data/aipriceaction.sqlite3")
    parser.add_argument("--watchlist", type=Path, default=API_ROOT / "watchlist.json")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--skip-production", action="store_true")
    parser.add_argument("--skip-upstream", action="store_true")
    args = parser.parse_args()
    symbol = args.symbol.upper()
    settings = Settings.from_env()

    report = {
        "symbol": symbol,
        "read_only": True,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "python_ingest_configuration": load_ingest_config(
            args.watchlist, settings.catalog_dir, settings.ingest_universe, symbol
        ),
        "local_sqlite": inspect_sqlite(args.database, symbol),
        "archived_rust_contract": inspect_rust_contract(symbol),
        "production_api": {},
        "yahoo_upstream_via_python_adapter": {},
    }

    if not args.skip_production:
        for interval in ("1D", "1h", "1m"):
            report["production_api"][interval] = fetch_api(
                args.api_url, symbol, interval, args.timeout
            )

    if not args.skip_upstream:
        for interval in ("1h", "1m"):
            report["yahoo_upstream_via_python_adapter"][interval] = await fetch_upstream(
                settings, symbol, interval
            )

    ingest_config = report["python_ingest_configuration"]
    if not ingest_config.get("scheduled_for_ingestion"):
        report["assessment"] = (
            "The symbol is outside the active Python ingest universe, so its worker will not ingest it."
        )
    else:
        report["assessment"] = (
            "Python schedules all three native intervals for this Yahoo symbol. Compare "
            "SQLite jobs/source checks with upstream and production results to locate any "
            "remaining data gap."
        )

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
