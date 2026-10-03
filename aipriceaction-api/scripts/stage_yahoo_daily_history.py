"""Build isolated, coherent Yahoo daily candidates; never publish to the main index."""

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import cutoff, date_bounds
from aipriceaction_api.history import History
from aipriceaction_api.importing import json_rows
from aipriceaction_api.providers import Providers, adjustment_changes
from aipriceaction_api.storage import Repository

try:
    from scripts.check_crypto_daily_history import compare
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from check_crypto_daily_history import compare

SYMBOLS = ("AAPL", "MSFT", "NVDA", "SPY", "GC=F")


def freeze(root, label, raw):
    digest = hashlib.sha256(raw).hexdigest()
    path = root / f"{label}-{digest}.json"
    path.write_bytes(raw)
    return {"path": str(path), "sha256": digest, "bytes": len(raw)}


class RecordingTransport(httpx.AsyncHTTPTransport):
    def __init__(self, root):
        super().__init__()
        self.root, self.captures = root, []

    async def handle_async_request(self, request):
        response = await super().handle_async_request(request)
        raw = await response.aread()
        self.captures.append(
            freeze(self.root, "native", raw)
            | {"url": str(request.url), "status": response.status_code}
        )
        return response


async def run(args):
    main_settings = Settings.from_env()
    if main_settings.s3_endpoint != "http://127.0.0.1:9100":
        raise ValueError("This rehearsal requires local RustFS")
    first, end = date_bounds(args.start_date), date_bounds(args.end_date, end=True)
    today = date_bounds(datetime.now(UTC).strftime("%Y-%m-%d"))
    if first > end or end >= today:
        raise ValueError("Choose an ordered range ending before the current UTC day")
    args.output.mkdir(parents=True, exist_ok=False)
    prefix = main_settings.s3_prefix + "/candidates/yahoo-daily-" + uuid.uuid4().hex
    settings = replace(
        main_settings,
        database=args.output / "sqlite.db",
        cache_dir=args.output / "cache",
        s3_prefix=prefix,
        proxies=(),
    )
    candidate = Repository(settings.database)
    candidate.initialize()
    archived = Archive(candidate, settings)
    main = Repository(main_settings.database)
    history = History(main, Archive(main, main_settings), main_settings)
    transport = RecordingTransport(args.output)
    providers = Providers(settings, transport=transport)
    report = {
        "main_database": str(main.path),
        "candidate_database": str(candidate.path),
        "prefix": prefix,
        "start": first,
        "end": end,
        "main_publication": False,
        "symbols": [],
        "passed": False,
    }
    report_path = args.output / "report.json"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            for symbol in args.symbol or SYMBOLS[:4]:
                entry = {"symbol": symbol, "passed": False}
                report["symbols"].append(entry)
                report_path.write_text(json.dumps(report, indent=2) + "\n")
                state = main.state("yahoo", symbol, "1D")
                if not state or state["status"] != "ready" or state["provider"] != "yahoo":
                    raise ValueError("Expected a ready native Yahoo daily series")
                old = history.read("yahoo", symbol, "1D")
                if not old or old[0].time < first or old[-1].time > end:
                    raise ValueError("Requested candidate range would omit served dates")
                entry["original_state"] = state
                entry["original_snapshot"] = freeze(
                    args.output,
                    symbol + "-original",
                    json.dumps([r.record() for r in old], sort_keys=True).encode(),
                )
                transport.captures.clear()
                page = await providers.yahoo_page(symbol, "1D", end + 1, 20000, start=first)
                rows = [r for r in page.rows if first <= r.time <= end]
                if not rows or len(page.rows) >= 20000 or rows[-1].time != old[-1].time:
                    raise ValueError("Empty, truncated or stale native snapshot")
                entry["native_captures"] = transport.captures.copy()
                difference = compare(old, rows)
                if difference["missing"]:
                    raise ValueError("Native snapshot would drop served dates")
                entry["retained_changes"] = difference
                entry["adjustment_signals"] = adjustment_changes(old, rows, end + 1)
                response = await client.get(
                    "https://api.aipriceaction.com/tickers",
                    params={
                        "symbol": symbol,
                        "mode": "yahoo",
                        "interval": "1D",
                        "start_date": args.start_date,
                        "end_date": args.end_date,
                        "format": "json",
                        "limit": 10000,
                        "ma": "false",
                        "redis": "false",
                        "snap": "false",
                        "cache": "false",
                    },
                )
                response.raise_for_status()
                entry["public_capture"] = freeze(args.output, symbol + "-public", response.content)
                public = json_rows(response.text, "yahoo", symbol, "1D")
                if not public or len(public) >= 10000:
                    raise ValueError("Empty or potentially truncated public snapshot")
                entry["public_changes"] = compare(public, rows)
                if entry["public_changes"]["missing"]:
                    raise ValueError("Native snapshot would omit public history dates")
                revision = "yahoo-daily-history-" + uuid.uuid4().hex
                rows = [replace(r, revision=revision, updated_at=time.time_ns()) for r in rows]
                floor = cutoff(settings.daily_years)
                hot = [r for r in rows if r.time >= floor]
                candidate.put(hot)
                groups = {}
                for row in rows:
                    if row.time < floor:
                        groups.setdefault(datetime.fromtimestamp(row.time, UTC).year, []).append(
                            row
                        )
                objects = []
                for group in groups.values():
                    obj = archived.prepare(group)
                    candidate.publish_archive(obj, require_current=True)
                    objects.append(obj)
                actual = History(candidate, archived, settings).read("yahoo", symbol, "1D")
                verified = compare(rows, actual)
                if len(actual) != len(rows) or verified["missing"] or verified["changed"]:
                    raise ValueError("Candidate readback differs from native snapshot")
                if (
                    main.state("yahoo", symbol, "1D") != state
                    or history.read("yahoo", symbol, "1D") != old
                ):
                    raise ValueError("Main series changed during rehearsal")
                entry.update(
                    revision=revision,
                    total_rows=len(rows),
                    hot_rows=len(hot),
                    cold_rows=sum(o["row_count"] for o in objects),
                    cold_objects=len(objects),
                    original_dates_preserved=True,
                    public_dates_preserved=True,
                    first=rows[0].time,
                    last=rows[-1].time,
                    passed=True,
                )
                report_path.write_text(json.dumps(report, indent=2) + "\n")
                print(symbol, len(rows), "verified candidate candles", flush=True)
        archived.publish_metadata()
        report["passed"] = all(entry["passed"] for entry in report["symbols"])
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(report_path, flush=True)
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        if isinstance(exc, ValueError):
            report["error"] = str(exc)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        raise
    finally:
        await providers.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", choices=SYMBOLS, action="append")
    parser.add_argument("--start-date", default="1980-01-01")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
