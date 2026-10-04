"""Flag local stock dates for review against matching source-backed VN schedules."""

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from scripts.check_hose_scheduled_dates import dates_between, load_reference_calendar
from scripts.stock_transfer_evidence import load_transfers


def shared_schedule(hose, hnx, first, last):
    calendars = [hose, hnx]
    if [c[0]["exchange"] for c in calendars] != ["HOSE", "HNX"]:
        raise DataError("Supply both HOSE and HNX in declared order")
    if first > last or any(
        first.year != c[0]["year"] or last.year != c[0]["year"] for c in calendars
    ):
        raise DataError("Stock review window outside both verified calendar years")
    schedules = [
        {d for d in dates_between(first, last) if d.weekday() < 5 and d not in closed}
        for _, closed in calendars
    ]
    if schedules[0] != schedules[1]:
        raise DataError("Exchange schedules differ; establish historical venue before review")
    return schedules[0]


def run(args):
    if args.output.exists():
        raise DataError("Preserve the previous review; choose a new output path")
    if (
        not args.symbol
        or len(set(args.symbol)) != len(args.symbol)
        or any(not re.fullmatch(r"[A-Z]{3}", s) for s in args.symbol)
    ):
        raise DataError("Supply unique three-letter Vietnamese stock symbols")
    if args.interval not in {"1D", "1h", "1m"}:
        raise DataError("Use a native stored interval for stock date review")
    hose = load_reference_calendar(args.hose_calendar, args.hose_source, args.hose_amendment)
    hnx = load_reference_calendar(args.hnx_calendar, args.hnx_source, args.hnx_amendment)
    first, last = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    expected = shared_schedule(hose, hnx, first, last)
    transfers, transfer_dates = load_transfers(
        getattr(args, "transfers", None), getattr(args, "transfer_source", [])
    )
    start = int(datetime.combine(first, datetime.min.time(), UTC).timestamp())
    before = int(datetime.combine(last + timedelta(days=1), datetime.min.time(), UTC).timestamp())
    report = {
        "read_only": True,
        "canonical_publication": False,
        "scope": "Local SQLite stock date candidates against equal announced HOSE/HNX weekday schedules",
        "start_date": first.isoformat(),
        "end_date": last.isoformat(),
        "interval": args.interval,
        "scheduled_dates": len(expected),
        "calendar_evidence": [
            {
                "exchange": calendar["exchange"],
                "declaration_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source": calendar["source"],
                "amendment_sources": [a["source"] for a in calendar.get("amendments", [])],
            }
            for (calendar, _), path in zip(
                [hose, hnx], [args.hose_calendar, args.hnx_calendar], strict=True
            )
        ],
        "limitations": [
            "Absent dates are review candidates, not verified missing stock sessions.",
            "Listings, transfers outside explicit reviewed evidence, suspensions and no-trade dates are unverified.",
            "SQLite absence does not establish absence from merged SQLite/S3 API history.",
            "Date presence does not certify OHLCV values or complete intraday timestamps.",
            "Equal announced weekday schedules do not establish actual exchange operations.",
        ],
        "ohlcv_accuracy_proven": False,
        "actual_session_completeness_proven": False,
        "series": [],
    }
    if transfers is not None:
        report["transfer_evidence"] = {
            "declaration_sha256": hashlib.sha256(args.transfers.read_bytes()).hexdigest(),
            "events": transfers["events"],
            "limitations": transfers["limitations"],
        }
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.execute("BEGIN")
        for symbol in args.symbol:
            timestamps = [
                row[0]
                for row in con.execute(
                    "SELECT time FROM candles WHERE source='vn' AND symbol=? AND interval=? AND time>=? AND time<? ORDER BY time",
                    (symbol, args.interval, start, before),
                )
            ]
            observed = {datetime.fromtimestamp(t, UTC).date() for t in timestamps}
            entry = {
                "symbol": symbol,
                "observed_rows": len(timestamps),
                "observed_dates": len(observed),
                "absent_shared_schedule_dates": [
                    d.isoformat() for d in sorted(expected - observed)
                ],
                "unexpected_dates": [d.isoformat() for d in sorted(observed - expected)],
            }
            if transfers is not None:
                absent = expected - observed
                reviewed = transfer_dates.get(symbol, set())
                entry["explained_transfer_dates"] = [
                    d.isoformat() for d in sorted(absent & reviewed)
                ]
                entry["remaining_absent_dates"] = [d.isoformat() for d in sorted(absent - reviewed)]
                entry["observed_transfer_dates"] = [
                    d.isoformat() for d in sorted(observed & reviewed)
                ]
            if args.interval == "1D":
                entry["non_midnight_daily_timestamps"] = [t for t in timestamps if t % 86400]
            report["series"].append(entry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also guards another process creating the report mid-read.
    with args.output.open("x") as file:
        file.write(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "series": len(report["series"]),
                "scheduled_dates": len(expected),
                "output": str(args.output),
            }
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for exchange in ("hose", "hnx"):
        parser.add_argument(f"--{exchange}-calendar", type=Path, required=True)
        parser.add_argument(f"--{exchange}-source", type=Path, required=True)
        parser.add_argument(f"--{exchange}-amendment", type=Path, action="append", default=[])
    parser.add_argument("--database", type=Path, default=Settings.from_env().database)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--interval", choices=("1D", "1h", "1m"), default="1D")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--transfers", type=Path)
    parser.add_argument("--transfer-source", type=Path, action="append", default=[])
    run(parser.parse_args())
