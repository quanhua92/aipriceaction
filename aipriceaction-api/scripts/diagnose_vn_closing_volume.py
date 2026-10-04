"""Replay native minute/hourly/daily controls without assigning residual trades."""

import argparse
import asyncio
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from scripts.vci_activation_inputs import replay_page, values
from scripts.vn_daily_volume_evidence import captured


def locate_residual(minutes, hourly, daily):
    if not minutes or not hourly or len(daily) != 2:
        raise DataError("Require observed native minute/hourly and two daily controls")
    symbol, day = minutes[0].symbol, minutes[0].time // 86400 * 86400
    groups = defaultdict(int)
    for row in minutes:
        row.validate()
        if (row.source, row.symbol, row.interval, row.provider) != (
            "vn",
            symbol,
            "1m",
            "vci",
        ) or row.time // 86400 * 86400 != day:
            raise DataError("Minute control source/day differs")
        groups[row.time // 3600 * 3600] += row.volume
    if (
        len({r.time for r in minutes}) != len(minutes)
        or {r.time for r in hourly} != set(groups)
        or len({r.time for r in hourly}) != len(hourly)
    ):
        raise DataError("Hourly observations do not cover the same unique minute buckets")
    for row in hourly:
        row.validate()
        if (row.source, row.symbol, row.interval, row.provider) != ("vn", symbol, "1h", "dnse"):
            raise DataError("Require native DNSE hourly observations")
    total = sum(r.volume for r in hourly)
    for row in daily:
        row.validate()
    if {r.provider for r in daily} != {"vndirect", "dnse"} or any(
        (r.source, r.symbol, r.interval, r.time, r.volume) != ("vn", symbol, "1D", day, total)
        for r in daily
    ):
        raise DataError("Native hourly total lacks matching native daily controls")
    buckets = [
        {
            "time": r.time,
            "minute_volume": groups[r.time],
            "native_hourly_volume": r.volume,
            "difference": r.volume - groups[r.time],
        }
        for r in sorted(hourly, key=lambda r: r.time)
    ]
    return {
        "symbol": symbol,
        "day": day,
        "minute_rows": len(minutes),
        "hourly_rows": len(hourly),
        "minute_total": sum(r.volume for r in minutes),
        "native_hourly_total": total,
        "buckets": buckets,
        "residual_buckets": [r for r in buckets if r["difference"]],
        "minute_attribution_verified": False,
        "publication_licensed": False,
    }


async def run(args):
    settings = replace(
        Settings.from_env(), vci_history_fallback=True, vci_volume_proofs=args.volume_proofs
    )
    controls = json.loads((args.minutes / "report.json").read_text())
    if controls["main_publication"]:
        raise DataError("Require isolated read-only controls")
    args.output.mkdir(parents=True, exist_ok=False)
    artifacts, report = {}, {"main_publication": False, "sessions": []}
    for root in args.hourly:
        path = root / "dnse" / f"{args.symbol}-1h.json"
        record = json.loads(path.read_text())
        if record["start_date"] != record["end_date"]:
            raise DataError("Choose one closed observed day per hourly control")
        date = record["start_date"]
        day = date_bounds(date)
        candidates = [
            r
            for r in controls["controls"]
            if (r["symbol"], r["date"], r["feed"]) == (args.symbol, date, "vci")
        ]
        minute = min(candidates, key=lambda r: r["count"])
        page = await replay_page(
            settings,
            captured(minute, artifacts),
            "vci",
            args.symbol,
            "1m",
            day + 86400,
            minute["count"],
            day,
        )
        if (
            values(page.rows)
            != [
                (r["time"], *(r[f] for f in ("open", "high", "low", "close", "volume")))
                for r in minute["rows"]
            ]
            or page.cursor is None
            or page.cursor >= day
        ):
            raise DataError("Minute controls differ from whole-day immutable source")
        hours = await replay_page(
            settings, captured(record, artifacts), "dnse", args.symbol, "1h", day + 86400, 100, day
        )
        if values(hours.rows) != [
            (r["time"], *(r[f] for f in ("open", "high", "low", "close", "volume")))
            for r in record["rows"]
        ]:
            raise DataError("Hourly controls differ from immutable source")
        daily = []
        for feed in ("vndirect", "dnse"):
            record = json.loads((args.daily / feed / f"{args.symbol}-1D.json").read_text())
            first, end = (
                date_bounds(record["start_date"]),
                date_bounds(record["end_date"], True) + 1,
            )
            native = await replay_page(
                settings,
                captured(record, artifacts),
                feed,
                args.symbol,
                "1D",
                end,
                min(10000, max(100, (end - first) // 86400 + 1)),
                first,
            )
            expected = [Candle("vn", args.symbol, "1D", provider=feed, **r) for r in record["rows"]]
            if native.rows != expected:
                raise DataError("Daily controls differ from immutable source")
            daily.append(next(r for r in native.rows if r.time == day))
        report["sessions"].append(locate_residual(page.rows, hours.rows, daily))
    report["source_artifacts"] = list(artifacts.values())
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["sessions"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--minutes", type=Path, required=True)
    parser.add_argument("--hourly", type=Path, action="append", required=True)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--volume-proofs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
