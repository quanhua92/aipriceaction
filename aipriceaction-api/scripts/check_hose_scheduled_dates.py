"""Read-only index date audit against a source-backed announced HOSE calendar."""

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError


def dates_between(first, last):
    while first <= last:
        yield first
        first += timedelta(days=1)


def verify_source(source, path):
    raw = path.read_bytes()
    kind = source.get("format", "pdf")
    valid_format = (kind == "pdf" and raw.startswith(b"%PDF")) or (
        kind == "jpeg" and raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9")
    )
    if (
        not valid_format
        or len(raw) != source["bytes"]
        or hashlib.sha256(raw).hexdigest() != source["sha256"]
    ):
        label = "PDF" if kind == "pdf" else "image"
        raise DataError(f"Announced calendar source {label} changed")


def load_calendar(path, source_pdf, amendment_files=()):
    calendar = json.loads(path.read_text())
    if (
        calendar.get("schema") != 1
        or calendar.get("exchange") != "HOSE"
        or type(calendar.get("year")) is not int
        or not 2000 <= calendar["year"] <= 2100
        or calendar.get("weekdays") != [0, 1, 2, 3, 4]
        or calendar.get("symbols") != ["VNINDEX", "VN30"]
    ):
        raise DataError("Unsupported announced calendar scope")
    amendments = calendar.get("amendments", [])
    if len(amendment_files) != len(amendments):
        raise DataError("Supply every declared calendar amendment source in order")
    declarations = [calendar, *amendments]
    for declaration, source_path in zip(declarations, [source_pdf, *amendment_files], strict=True):
        verify_source(declaration["source"], source_path)
    closed = set()
    for declaration in declarations:
        for first, last in declaration["closed_ranges"]:
            first, last = date.fromisoformat(first), date.fromisoformat(last)
            if first > last or first.year != calendar["year"] or last.year != calendar["year"]:
                raise DataError("Calendar closure outside declared year")
            for day in dates_between(first, last):
                if day in closed:
                    raise DataError("Duplicate announced calendar closure")
                closed.add(day)
        for value in declaration["explicit_closed_weekends"]:
            day = date.fromisoformat(value)
            if day.year != calendar["year"] or day.weekday() < 5 or day in closed:
                raise DataError("Invalid explicit non-trading weekend")
            closed.add(day)
    return calendar, closed


def compare_dates(calendar, closed, first, last, timestamps):
    if first > last or first.year != calendar["year"] or last.year != calendar["year"]:
        raise DataError("Audit window outside verified calendar year")
    expected = {
        day for day in dates_between(first, last) if day.weekday() < 5 and day not in closed
    }
    observed = {datetime.fromtimestamp(stamp, UTC).date() for stamp in timestamps}
    if any(day < first or day > last for day in observed):
        raise DataError("Observed date outside requested audit window")
    invalid_times = sorted(stamp for stamp in timestamps if stamp % 86400)
    missing, unexpected = sorted(expected - observed), sorted(observed - expected)
    return {
        "scheduled_dates": len(expected),
        "observed_dates": len(observed),
        "missing_scheduled_dates": [day.isoformat() for day in missing],
        "unexpected_dates": [day.isoformat() for day in unexpected],
        "non_midnight_daily_timestamps": invalid_times,
        "scheduled_date_coverage_passed": not missing and not unexpected and not invalid_times,
        "ohlcv_accuracy_proven": False,
        "actual_session_completeness_proven": False,
    }


def run(args):
    calendar, closed = load_calendar(
        args.calendar, args.source_pdf, getattr(args, "amendment_file", [])
    )
    first, last = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    # Validate the entire request before opening SQLite; unknown years never fall
    # back to observed provider dates or a generic public-holiday package.
    compare_dates(calendar, closed, first, last, [])
    symbols = args.symbol or calendar["symbols"]
    interval = getattr(args, "interval", "1D")
    if interval not in {"1D", "1h", "1m"}:
        raise DataError("Use a native stored interval for scheduled date checks")
    if len(set(symbols)) != len(symbols) or any(s not in calendar["symbols"] for s in symbols):
        raise DataError("Use unique indices explicitly licensed by this HOSE calendar")
    if args.output.exists():
        raise DataError("Preserve the previous audit; choose a new output path")
    start = int(datetime.combine(first, datetime.min.time(), UTC).timestamp())
    before = int(datetime.combine(last + timedelta(days=1), datetime.min.time(), UTC).timestamp())
    report = {
        "read_only": True,
        "canonical_publication": False,
        "source": calendar["source"],
        "amendment_sources": [a["source"] for a in calendar.get("amendments", [])],
        "calendar_sha256": hashlib.sha256(args.calendar.read_bytes()).hexdigest(),
        "exchange": calendar["exchange"],
        "start_date": first.isoformat(),
        "end_date": last.isoformat(),
        "interval": interval,
        "scope": f"Local SQLite {interval} date partitions versus independently announced scheduled HOSE dates",
        "limitations": calendar["limitations"],
        "series": [],
    }
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.execute("BEGIN")
        for symbol in symbols:
            timestamps = [
                row[0]
                for row in con.execute(
                    "SELECT time FROM candles WHERE source='vn' AND symbol=? AND interval=? AND time>=? AND time<? ORDER BY time",
                    (symbol, interval, start, before),
                )
            ]
            compared = compare_dates(
                calendar,
                closed,
                first,
                last,
                timestamps
                if interval == "1D"
                else sorted({t // 86400 * 86400 for t in timestamps}),
            )
            if interval != "1D":
                # An observed intraday date means at least one candle exists;
                # neither these date bins nor the schedule establish exact
                # minute/hour coverage, auction labels or session semantics.
                del compared["non_midnight_daily_timestamps"]
                compared["intraday_timestamp_completeness_proven"] = False
            report["series"].append(
                {"symbol": symbol, "observed_rows": len(timestamps), **compared}
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "interval": interval,
                "series": [
                    {
                        "symbol": entry["symbol"],
                        "scheduled_dates": entry["scheduled_dates"],
                        "observed_dates": entry["observed_dates"],
                        "missing_scheduled_dates": len(entry["missing_scheduled_dates"]),
                        "unexpected_dates": len(entry["unexpected_dates"]),
                        "scheduled_date_coverage_passed": entry["scheduled_date_coverage_passed"],
                    }
                    for entry in report["series"]
                ],
            }
        ),
        flush=True,
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument(
        "--source-file", "--source-pdf", dest="source_pdf", type=Path, required=True
    )
    parser.add_argument("--amendment-file", type=Path, action="append", default=[])
    parser.add_argument("--database", type=Path, default=Settings.from_env().database)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--interval", choices=("1D", "1h", "1m"), default="1D")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
