"""Capture public/native hourly candles before replacing frozen hourly history.

Read-only. Reports timestamp and value differences separately; a successful
comparison establishes only the captured window, not a historical handoff.
"""

import argparse
import asyncio
import json
import math
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import date_bounds
from aipriceaction_api.history import History
from aipriceaction_api.importing import json_rows
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository

try:
    from scripts.check_crypto_daily_history import compare, save_response
    from scripts.stage_yahoo_daily_history import RecordingTransport
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from check_crypto_daily_history import compare, save_response
    from stage_yahoo_daily_history import RecordingTransport


def native_comparison(reference, actual):
    """Retain exact differences and separately apply the existing handoff tolerance."""
    result = compare(reference, actual)
    result["material_changed"] = []
    for change in result["changed"]:
        fields = {
            field: values
            for field, values in change["fields"].items()
            if field == "volume" or not math.isclose(values[0], values[1], rel_tol=0, abs_tol=1e-8)
        }
        if fields:
            result["material_changed"].append({"time": change["time"], "fields": fields})
    by_time = {row.time: row for row in reference}
    result["missing_flat_zero_volume_observations"] = [
        stamp
        for stamp in result["missing"]
        if by_time[stamp].volume == 0
        and by_time[stamp].open == by_time[stamp].high == by_time[stamp].low == by_time[stamp].close
    ]
    result["price_abs_tolerance"] = 1e-8
    result["volume_exact"] = True
    return result


async def run(args):
    first, end = date_bounds(args.start_date), date_bounds(args.end_date, end=True)
    if first > end or end >= date_bounds(datetime.now(UTC).strftime("%Y-%m-%d")):
        raise ValueError("Choose an ordered range ending before the current UTC day")
    if end - first >= 31 * 86400:
        raise ValueError("Use at most 31 days for this source comparison")
    args.output.mkdir(parents=True, exist_ok=False)
    settings = Settings.from_env()
    repo = Repository(settings.database)
    history = History(repo, Archive(repo, settings), settings)
    transport = RecordingTransport(args.output)
    providers = Providers(replace(settings, proxies=()), transport=transport)
    report = {
        "read_only": True,
        "start": first,
        "end": end,
        "symbols": [],
        "canonical_epoch_before": repo.epoch(),
    }
    report_path = args.output / "report.json"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            for symbol in args.symbol:
                transport.captures.clear()
                entry = {"symbol": symbol, "passed": False}
                report["symbols"].append(entry)
                report_path.write_text(json.dumps(report, indent=2) + "\n")
                try:
                    response = await client.get(
                        "https://api.aipriceaction.com/tickers",
                        params={
                            "symbol": symbol,
                            "mode": "yahoo",
                            "interval": "1h",
                            "start_date": args.start_date,
                            "end_date": args.end_date,
                            "limit": 10000,
                            "ma": "false",
                            "format": "json",
                            "cache": "false",
                            "redis": "false",
                            "snap": "false",
                        },
                    )
                    entry["public_capture"] = save_response(
                        args.output, symbol + "-public", response
                    )
                    public = json_rows(response.text, "yahoo", symbol, "1h", "legacy-api", "audit")
                    if not public or len(public) >= 10000:
                        raise ValueError("Empty or potentially truncated public snapshot")
                    if any(not first <= row.time <= end for row in public):
                        raise ValueError("Public snapshot outside requested bounds")
                    transport.captures.clear()
                    native = await providers.yahoo_page(symbol, "1h", end + 1, 10000, start=first)
                    entry["native_captures"] = transport.captures.copy()
                    offsets = {}
                    for capture in transport.captures:
                        payload = json.loads(Path(capture["path"]).read_text())
                        for result in payload.get("chart", {}).get("result") or []:
                            for stamp in result.get("timestamp") or []:
                                offset = str(int(stamp) % 3600)
                                offsets[offset] = offsets.get(offset, 0) + 1
                    entry["raw_timestamp_offsets_seconds"] = offsets
                    if not native.rows or len(native.rows) >= 10000:
                        raise ValueError("Empty or potentially truncated native snapshot")
                    entry["native"] = native_comparison(public, native.rows)
                    floored = [replace(row, time=row.time // 3600 * 3600) for row in native.rows]
                    if len({row.time for row in floored}) != len(floored):
                        raise ValueError("Hour normalization would merge source candles")
                    entry["hour_floor"] = native_comparison(public, floored)
                    local = history.read("yahoo", symbol, "1h", first, end)
                    entry["local"] = compare(public, local)
                    minute = history.aggregated(
                        "yahoo", symbol, "1h", first, end, 10000, native="1m"
                    )
                    entry["minute_aggregation"] = compare(public, minute)
                    entry["passed"] = not (
                        entry["hour_floor"]["missing"] or entry["hour_floor"]["material_changed"]
                    )
                except Exception as exc:
                    entry["error"] = str(exc)
                    entry["native_captures"] = transport.captures.copy()
                report_path.write_text(json.dumps(report, indent=2) + "\n")
                print(
                    json.dumps(
                        {
                            "symbol": symbol,
                            "passed": entry["passed"],
                            "error": entry.get("error"),
                            "comparisons": {
                                key: {
                                    "missing": len(entry[key]["missing"]),
                                    "changed": len(entry[key]["changed"]),
                                    **(
                                        {"material_changed": len(entry[key]["material_changed"])}
                                        if "material_changed" in entry[key]
                                        else {}
                                    ),
                                }
                                for key in ("native", "hour_floor", "local", "minute_aggregation")
                                if key in entry
                            },
                        }
                    ),
                    flush=True,
                )
    finally:
        await providers.close()
        report["canonical_epoch_after"] = repo.epoch()
        report["canonical_unchanged"] = (
            report["canonical_epoch_before"] == report["canonical_epoch_after"]
        )
        report["passed"] = (
            bool(report["symbols"])
            and all(row["passed"] for row in report["symbols"])
            and report["canonical_unchanged"]
        )
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(0 if asyncio.run(run(parser.parse_args())) else 1)
