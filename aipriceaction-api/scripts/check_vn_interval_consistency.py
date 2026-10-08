"""Audit the latest locally served VN session across native intervals.

This is a read-only operational check.  It deliberately uses the canonical
SQLite rows that back recent API requests and does not contact providers,
create database copies, or write evidence files.
"""

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import INDEXES, cutoff

FIELDS = ("open", "high", "low", "close", "volume")
INTERVALS = ("1D", "1h", "1m")
REGULAR_SESSION_WINDOWS = (
    (2 * 3600 + 15 * 60, 4 * 3600 + 30 * 60),
    (6 * 3600, 7 * 3600 + 45 * 60),
)


def day(ts):
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def clock(ts):
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M:%S")


def aggregate(rows):
    if not rows:
        return None
    return {
        "open": rows[0]["open"],
        "high": max(row["high"] for row in rows),
        "low": min(row["low"] for row in rows),
        "close": rows[-1]["close"],
        "volume": sum(row["volume"] for row in rows),
    }


def differences(expected, observed):
    if observed is None:
        return list(FIELDS)
    return [
        name
        for name in FIELDS
        if not math.isclose(expected[name], observed[name], rel_tol=1e-12, abs_tol=1e-9)
    ]


def compare_hours(minutes, hours):
    minute_groups = defaultdict(list)
    for row in minutes:
        minute_groups[row["time"] // 3600 * 3600].append(row)
    native = {row["time"]: row for row in hours}
    issues = []
    for timestamp in sorted(set(minute_groups) | set(native)):
        if timestamp not in native:
            issues.append({"time": clock(timestamp), "kind": "native_hour_missing"})
        elif timestamp not in minute_groups:
            issues.append({"time": clock(timestamp), "kind": "minutes_missing"})
        else:
            fields = differences(dict(native[timestamp]), aggregate(minute_groups[timestamp]))
            if fields:
                issues.append({"time": clock(timestamp), "kind": "ohlcv", "fields": fields})
    return issues


def effective_hours(minutes, hours, active_hourly=True):
    """Mirror the API's conservative recent minute overlay."""
    minute_groups = defaultdict(list)
    for row in minutes:
        minute_groups[row["time"] // 3600 * 3600].append(row)
    derived = {
        timestamp: {"time": timestamp, **aggregate(rows)}
        for timestamp, rows in minute_groups.items()
    }
    if not active_hourly:
        return [derived[timestamp] for timestamp in sorted(derived)]
    native = {row["time"]: dict(row) for row in hours}
    if not native:
        return []
    # Spell out the OHLC comparison rather than allowing a volume difference
    # to block the exact substitution the API is meant to make.
    overlaps = {
        timestamp
        for timestamp, row in derived.items()
        if timestamp in native
        and all(
            math.isclose(native[timestamp][name], row[name], rel_tol=1e-12, abs_tol=1e-9)
            for name in FIELDS[:-1]
        )
    }
    latest_native = max(native)
    anchored = bool(overlaps)
    for timestamp, row in derived.items():
        if timestamp in overlaps or (timestamp > latest_native and anchored):
            native[timestamp] = row
    return [native[timestamp] for timestamp in sorted(native)]


def audit(database, symbols=()):
    database = Path(database)
    minute_cutoff = cutoff(Settings.from_env().minute_years)
    selected = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        if selected:
            placeholders = ",".join("?" for _ in selected)
            universe = [
                row[0]
                for row in con.execute(
                    f"SELECT symbol FROM tickers WHERE source='vn' AND symbol IN ({placeholders}) "
                    "ORDER BY symbol",
                    selected,
                )
            ]
        else:
            # Pending catalog entries are expected during bootstrap. Audit only
            # symbols whose recent API storage has at least one native series.
            universe = [
                row[0]
                for row in con.execute(
                    "SELECT DISTINCT symbol FROM candles WHERE source='vn' ORDER BY symbol"
                )
            ]

        records = []
        totals = Counter()
        for symbol in universe:
            latest = {
                row["interval"]: row["last"]
                for row in con.execute(
                    "SELECT interval,MAX(time) last FROM candles "
                    "WHERE source='vn' AND symbol=? GROUP BY interval",
                    (symbol,),
                )
            }
            native_dates = {iv: day(latest[iv]) for iv in INTERVALS if iv in latest}
            missing_native = [iv for iv in INTERVALS if iv not in latest]
            record = {
                "symbol": symbol,
                "native_latest_dates": native_dates,
                "missing_native_intervals": missing_native,
            }
            totals["symbols"] += 1
            totals["native_interval_gaps"] += bool(missing_native)

            if "1m" not in latest:
                record["latest_dates"] = native_dates
                record["missing_served_intervals"] = missing_native
                record["latest_dates_aligned"] = False
                totals["missing_served_intervals"] += bool(missing_native)
                records.append(record)
                continue
            session_day = latest["1m"] // 86400 * 86400
            recent_start = max(minute_cutoff, session_day - 14 * 86400)
            recent_minutes = con.execute(
                "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1m' "
                "AND time>=? AND time<? ORDER BY time",
                (symbol, recent_start, session_day + 86400),
            ).fetchall()
            recent_hours = con.execute(
                "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1h' "
                "AND time>=? AND time<? ORDER BY time",
                (symbol, recent_start, session_day + 86400),
            ).fetchall()
            active_hourly = con.execute(
                "SELECT 1 FROM series WHERE source='vn' AND symbol=? AND interval='1h'",
                (symbol,),
            ).fetchone() is not None
            served_hours = effective_hours(recent_minutes, recent_hours, active_hourly)
            served = dict(latest)
            if served_hours:
                served["1h"] = served_hours[-1]["time"]
            elif not active_hourly:
                served.pop("1h", None)
            served_dates = {iv: day(served[iv]) for iv in INTERVALS if iv in served}
            missing_served = [iv for iv in INTERVALS if iv not in served]
            aligned = not missing_served and len(set(served_dates.values())) == 1
            record["latest_dates"] = served_dates
            record["missing_served_intervals"] = missing_served
            record["latest_dates_aligned"] = aligned
            totals["missing_served_intervals"] += bool(missing_served)
            totals["latest_date_mismatches"] += not missing_served and not aligned
            minute_rows = con.execute(
                "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1m' "
                "AND time>=? AND time<? ORDER BY time",
                (symbol, session_day, session_day + 86400),
            ).fetchall()
            record["minute_session"] = {
                "date": day(session_day),
                "first": clock(minute_rows[0]["time"]),
                "last": clock(minute_rows[-1]["time"]),
                "rows": len(minute_rows),
            }
            outside = [
                row
                for row in minute_rows
                if not any(
                    lower <= row["time"] - session_day <= upper
                    for lower, upper in REGULAR_SESSION_WINDOWS
                )
            ]
            record["minute_session"]["outside_regular_session"] = len(outside)
            if symbol not in INDEXES:
                # Sparse tickers need not trade at either auction boundary.
                # Only observations outside the two legal session windows are
                # a timestamp defect.
                boundary_ok = not outside
                record["minute_session"]["regular_boundary"] = boundary_ok
                totals["stock_session_boundary_mismatches"] += not boundary_ok
            else:
                totals["index_session_extensions"] += bool(outside)

            daily = con.execute(
                "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1D' AND time=?",
                (symbol, session_day),
            ).fetchone()
            hours = con.execute(
                "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1h' "
                "AND time>=? AND time<? ORDER BY time",
                (symbol, session_day, session_day + 86400),
            ).fetchall()
            hourly_issues = compare_hours(minute_rows, hours) if hours else []
            record["native_hourly_vs_minutes"] = hourly_issues
            totals["native_hourly_minute_bucket_mismatches"] += bool(hourly_issues)
            if daily is not None:
                expected = dict(daily)
                minute_diff = differences(expected, aggregate(minute_rows))
                hour_rows = [
                    row for row in served_hours if session_day <= row["time"] < session_day + 86400
                ]
                hour_diff = differences(expected, aggregate(hour_rows)) if hour_rows else None
                record["daily_vs_minutes"] = minute_diff
                record["daily_vs_hours"] = hour_diff
                record["hour_basis"] = (
                    "recent-minute-overlay"
                    if hour_rows
                    else "native-missing-latest-session"
                )
                totals["daily_minute_ohlcv_mismatches"] += bool(minute_diff)
                totals["daily_hour_ohlcv_mismatches"] += hour_diff is not None and bool(hour_diff)
            records.append(record)

    return {"database": str(database), "summary": dict(totals), "series": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Settings.from_env().database)
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on missing served intervals, date/boundary drift, or OHLCV disagreement",
    )
    args = parser.parse_args()
    report = audit(args.database, args.symbol)
    print(json.dumps(report, indent=2))
    failures = sum(
        value
        for key, value in report["summary"].items()
        if key != "symbols" and ("mismatch" in key or key == "missing_served_intervals")
    )
    if args.strict and failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
