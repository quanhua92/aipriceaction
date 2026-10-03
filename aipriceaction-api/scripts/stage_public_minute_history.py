"""Stage a complete public minute snapshot in an isolated database.

Preserve original responses and all previously served timestamps. Empty API
ranges are observations of unavailability, not evidence of a trading calendar.
This command never publishes to the main database or licenses native updates.
"""

import argparse
import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import date_bounds
from aipriceaction_api.history import History
from aipriceaction_api.migration import LegacyImporter, atomic_write, encode
from aipriceaction_api.storage import Repository

try:
    from scripts.check_crypto_daily_history import compare
    from scripts.stage_yahoo_daily_history import RecordingTransport, freeze
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from check_crypto_daily_history import compare
    from stage_yahoo_daily_history import RecordingTransport, freeze


async def run(args):
    first, end = date_bounds(args.start_date), date_bounds(args.end_date, end=True)
    today = date_bounds(datetime.now(UTC).strftime("%Y-%m-%d"))
    if first > end or end >= today or end - first >= 366 * 86400:
        raise ValueError("Use at most 366 ordered days ending before the current UTC day")
    args.output.mkdir(parents=True, exist_ok=True)
    main_settings = Settings.from_env()
    main = Repository(main_settings.database)
    state = main.state(args.source, args.symbol, "1m")
    if not state or state["provider"] != "legacy-api" or state["status"] != "ready":
        raise ValueError("This staging command requires a ready frozen public minute snapshot")
    original = History(main, Archive(main, main_settings), main_settings).read(
        args.source, args.symbol, "1m"
    )
    if not original or original[0].time < first or original[-1].time > end:
        raise ValueError("Requested range would omit previously served history")
    settings = replace(
        main_settings,
        database=args.output / "sqlite.db",
        archive_backend="filesystem",
        object_dir=args.output / "objects",
        cache_dir=args.output / "cache",
    )
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    report_path = args.output / "report.json"
    report = {
        "main_publication": False,
        "passed": False,
        "source": args.source,
        "symbol": args.symbol,
        "revision": args.revision,
        "start": first,
        "end": end,
        "original_state": state,
        "original_snapshot": freeze(
            args.output, "original", encode([r.record() for r in original])
        ),
    }
    if report_path.exists():
        previous = json.loads(report_path.read_text())
        for key in (
            "source",
            "symbol",
            "revision",
            "start",
            "end",
            "original_state",
            "original_snapshot",
        ):
            if previous[key] != report[key]:
                raise ValueError(
                    "Staging inputs changed; preserve this directory and choose a new one"
                )
    atomic_write(report_path, encode(report))
    transport = RecordingTransport(args.output)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=60) as client:
            report["import"] = await LegacyImporter(repo, archive, client=client).run(
                "https://api.aipriceaction.com",
                args.source,
                args.symbol,
                "1m",
                start=args.start_date,
                end=args.end_date,
                provider="legacy-api",
                revision=args.revision,
                from_api=True,
                api_format="json",
                api_read_backend="database",
                api_batch_days=6,
            )
        rows = repo.read(args.source, args.symbol, "1m")
        report["comparison"] = compare(original, rows)
        report["rows"] = len(rows)
        report["first"] = rows[0].time if rows else None
        report["last"] = rows[-1].time if rows else None
        report["new_dates"] = len({row.time for row in rows} - {row.time for row in original})
        report["quote_events"] = sum(row.time % 60 != 0 for row in rows)
        report["quality_findings"] = repo.findings()
        report["passed"] = bool(rows) and not report["comparison"]["missing"]
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        report["captures"] = transport.captures
        atomic_write(report_path, encode(report))
    print(
        json.dumps(
            {
                "report": str(report_path),
                "passed": report["passed"],
                "rows": report["rows"],
                "new_dates": report["new_dates"],
                "missing": len(report["comparison"]["missing"]),
                "changed": len(report["comparison"]["changed"]),
                "quote_events": report["quote_events"],
                "empty_ranges": len(report["import"]["empty_files"]),
            }
        ),
        flush=True,
    )
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("vn", "yahoo", "crypto"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.symbol = args.symbol.upper()
    raise SystemExit(0 if asyncio.run(run(args)) else 1)
