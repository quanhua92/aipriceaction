"""Prepare explicit field-scoped witnesses in an isolated daily evidence bundle."""

import argparse
import json
from pathlib import Path

from aipriceaction_api.domain import DataError, date_bounds
from scripts.vn_daily_volume_evidence import (
    captured,
    project_vndirect_volumes,
    volume_only_witnesses,
)


def run(args):
    if not args.symbol or len(set(args.symbol)) != len(args.symbol):
        raise DataError("Choose unique explicit stock symbols")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"main_publication": False, "volume_only": []}
    for path in sorted(args.daily.glob("*/*-1D.json")):
        record = json.loads(path.read_text())
        symbol, feed = path.name[:-8], path.parent.name
        if feed == "vndirect" and symbol in args.symbol:
            if "Invalid OHLC range" not in record.get("error", ""):
                raise DataError("Require an explicitly rejected native OHLC range")
            first, end = (
                date_bounds(record["start_date"]),
                date_bounds(record["end_date"], True) + 1,
            )
            record["volume_only_evidence"] = project_vndirect_volumes(
                captured(record, {}),
                symbol,
                first,
                end,
                min(10000, max(100, (end - first) // 86400 + 1)),
            )
            rows = volume_only_witnesses(record, feed, symbol)
            report["volume_only"].append(
                {
                    "symbol": symbol,
                    "dates": len(rows),
                    "ohlc_rejections": record["volume_only_evidence"]["ohlc_rejections"],
                }
            )
        target = args.output / feed / path.name
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(record, indent=2) + "\n")
    if {r["symbol"] for r in report["volume_only"]} != set(args.symbol):
        raise DataError("Missing selected native source records")
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", action="append", required=True)
    run(parser.parse_args())
