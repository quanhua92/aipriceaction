"""Read-only dated VN index hourly probes with complete raw response captures."""

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, parse_time
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository

try:
    from scripts.stage_yahoo_daily_history import RecordingTransport
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from stage_yahoo_daily_history import RecordingTransport


def dates(rows):
    return {
        "rows": len(rows),
        "observed_dates": len({row.time // 86400 for row in rows}),
        "first": rows[0].time if rows else None,
        "last": rows[-1].time if rows else None,
        "months": dict(
            sorted(
                Counter(
                    datetime.fromtimestamp(row.time, UTC).strftime("%Y-%m") for row in rows
                ).items()
            )
        ),
    }


async def run(args):
    settings = replace(Settings.from_env(), allow_direct=args.allow_direct)
    repo = Repository(settings.database)
    epoch = repo.epoch()
    originals = {symbol: repo.read("vn", symbol, "1h") for symbol in ("VNINDEX", "VN30")}
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "main_data_unchanged": False,
        "local": {symbol: dates(rows) for symbol, rows in originals.items()},
        "probes": [],
    }
    transport = RecordingTransport(args.output)
    providers = Providers(settings, transport=transport)
    try:
        for symbol, current in originals.items():
            existing = {row.time: row for row in current}
            for before in args.before:
                for provider in settings.vn_providers:
                    result = {"symbol": symbol, "provider": provider, "before": before}
                    first_capture = len(transport.captures)
                    try:
                        page = await providers.page(
                            "vn",
                            symbol,
                            "1h",
                            before=parse_time(before),
                            count=args.count,
                            provider=provider,
                        )
                        result.update(dates(page.rows), cursor=page.cursor)
                        result["absent_locally"] = [
                            row.time for row in page.rows if row.time not in existing
                        ]
                        result["changed_locally"] = [
                            row.time
                            for row in page.rows
                            if row.time in existing
                            and any(
                                getattr(row, key) != getattr(existing[row.time], key)
                                for key in ("open", "high", "low", "close", "volume")
                            )
                        ]
                    except DataError as exc:
                        result["error"] = str(exc)
                    result["captures"] = transport.captures[first_capture:]
                    report["probes"].append(result)
                    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                    print(
                        json.dumps(
                            {
                                key: result[key]
                                for key in (
                                    "symbol",
                                    "provider",
                                    "before",
                                    "rows",
                                    "observed_dates",
                                    "error",
                                )
                                if key in result
                            }
                        ),
                        flush=True,
                    )
            if args.walk_dnse_start:
                floor = parse_time(args.walk_dnse_start)
                boundary = parse_time(args.before[0])
                walk = {
                    "symbol": symbol,
                    "provider": "dnse",
                    "floor": floor,
                    "pages": [],
                    "complete": False,
                }
                retained = {}
                report.setdefault("native_walks", []).append(walk)
                for _ in range(12):
                    first_capture = len(transport.captures)
                    try:
                        page = await providers.page(
                            "vn",
                            symbol,
                            "1h",
                            before=boundary,
                            count=500,
                            provider="dnse",
                            start=floor,
                        )
                        if page.cursor is None or page.cursor >= boundary:
                            raise DataError("Native history ended or did not move backwards")
                        for row in page.rows:
                            if not floor <= row.time < boundary:
                                raise DataError("Native history outside requested window")
                            retained[row.time] = row
                        walk["pages"].append(
                            {
                                "before": boundary,
                                "cursor": page.cursor,
                                **dates(page.rows),
                                "captures": transport.captures[first_capture:],
                            }
                        )
                        boundary = page.cursor
                        if boundary <= floor:
                            walk["complete"] = True
                            break
                    except DataError as exc:
                        walk["error"] = str(exc)
                        walk["failed_captures"] = transport.captures[first_capture:]
                        break
                rows = [retained[stamp] for stamp in sorted(retained)]
                walk.update(dates(rows))
                walk["local_absent_native"] = [
                    row.time for row in current if row.time not in retained
                ]
                walk["changed_shared"] = [
                    row.time
                    for row in current
                    if row.time in retained
                    and any(
                        getattr(row, key) != getattr(retained[row.time], key)
                        for key in ("open", "high", "low", "close", "volume")
                    )
                ]
                path = args.output / (symbol + "-native-window.json")
                path.write_text(json.dumps([row.record() for row in rows], indent=2) + "\n")
                walk["candidate"] = str(path)
                (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                print(
                    json.dumps(
                        {
                            "symbol": symbol,
                            "native_walk_complete": walk["complete"],
                            "rows": len(rows),
                            "error": walk.get("error"),
                        }
                    ),
                    flush=True,
                )
    finally:
        await providers.close()
        if repo.epoch() != epoch or any(
            repo.read("vn", symbol, "1h") != rows for symbol, rows in originals.items()
        ):
            raise AssertionError("Main index records changed during the read-only probe")
        report["main_data_unchanged"] = True
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before", action="append", required=True, help="Exclusive YYYY-MM-DD boundary"
    )
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-direct", action="store_true")
    parser.add_argument(
        "--walk-dnse-start", help="Optionally walk one bounded native window without publishing"
    )
    args = parser.parse_args()
    if not 1 <= args.count <= 1000:
        parser.error("Choose a bounded count between 1 and 1000")
    for boundary in args.before:
        parse_time(boundary)
    if args.walk_dnse_start and (
        len(args.before) != 1 or parse_time(args.walk_dnse_start) >= parse_time(args.before[0])
    ):
        parser.error("Native walking requires one boundary after its start")
    asyncio.run(run(args))
