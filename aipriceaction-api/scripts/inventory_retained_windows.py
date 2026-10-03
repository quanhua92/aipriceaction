"""Inventory selected local windows, archive bounds and incomplete jobs.

Read-only observations: a ready series or early timestamp does not prove a
complete trading calendar. This command never initializes or updates SQLite.
"""

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import cutoff, parse_time


def inventory(settings):
    watchlist = json.loads(settings.watchlist.read_text())
    selected = {}
    for source, items in watchlist.items():
        for item in items:
            entry = {"symbol": item} if isinstance(item, str) else item
            selected[source, entry["symbol"].upper()] = entry
    years = {"1D": settings.daily_years, "1h": settings.hourly_years, "1m": settings.minute_years}
    report = {"read_only": True, "calendar_coverage_proven": False, "series": []}
    with sqlite3.connect(settings.database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        report["database_epoch"] = con.execute(
            "SELECT value FROM meta WHERE key='epoch'"
        ).fetchone()[0]
        states = [
            dict(row) for row in con.execute("SELECT * FROM series ORDER BY source,symbol,interval")
        ]
        bounds = {
            (row["source"], row["symbol"], row["interval"]): dict(row)
            for row in con.execute(
                "SELECT source,symbol,interval,COUNT(*) AS rows,MIN(time) AS first,MAX(time) AS last FROM candles GROUP BY source,symbol,interval"
            )
        }
        jobs = [
            dict(row)
            for row in con.execute(
                "SELECT * FROM jobs WHERE status IN ('pending','running') ORDER BY source,symbol,interval,id"
            )
        ]
        archives = [
            dict(row)
            for row in con.execute(
                "SELECT * FROM archives WHERE status IN ('published','pending_repair') ORDER BY source,symbol,interval,start,id"
            )
        ]
        for state in states:
            source, symbol, iv = state["source"], state["symbol"], state["interval"]
            entry = selected.get((source, symbol))
            if entry is None:
                continue
            floor = cutoff(years[iv])
            if entry.get("history_start"):
                floor = max(floor, parse_time(entry["history_start"]))
            current = bounds.get((source, symbol, iv), {"rows": 0, "first": None, "last": None})
            identity = (source, symbol, iv)
            pending = [
                job for job in jobs if (job["source"], job["symbol"], job["interval"]) == identity
            ]
            objects = [
                obj
                for obj in archives
                if (obj["source"], obj["symbol"], obj["interval"]) == identity
            ]
            staging = con.execute(
                "SELECT COUNT(*) AS rows,MIN(time) AS first,MAX(time) AS last FROM staging WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, iv),
            ).fetchone()
            report["series"].append(
                {
                    **state,
                    "configured_ingestion": iv
                    in entry.get(
                        "intervals", ["1D"] if source in ("sjc", "yahoo") else ["1D", "1h", "1m"]
                    ),
                    "retention_floor": floor,
                    "local_rows": current["rows"],
                    "local_first": current["first"],
                    "local_last": current["last"],
                    "local_starts_after_retention_date": current["first"] is None
                    or current["first"] // 86400 > floor // 86400,
                    "archive_objects": len(objects),
                    "archive_first": min((obj["start"] for obj in objects), default=None),
                    "archive_last": max((obj["end"] for obj in objects), default=None),
                    "staged": dict(staging),
                    "incomplete_jobs": pending,
                }
            )
    report["captured_at"] = datetime.now(UTC).isoformat()
    report["selected_tickers"] = len(selected)
    report["published_series"] = len(report["series"])
    report["series_with_pending_jobs"] = sum(
        bool(row["incomplete_jobs"]) for row in report["series"]
    )
    configured = {
        (source, symbol, iv)
        for (source, symbol), entry in selected.items()
        for iv in entry.get(
            "intervals", ["1D"] if source in ("sjc", "yahoo") else ["1D", "1h", "1m"]
        )
    }
    present = {(row["source"], row["symbol"], row["interval"]) for row in report["series"]}
    report["configured_series"] = len(configured)
    report["missing_configured_series"] = [
        {"source": source, "symbol": symbol, "interval": iv}
        for source, symbol, iv in sorted(configured - present)
    ]
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = inventory(Settings.from_env())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.report.exists():
        raise ValueError("Preserve the previous inventory; use a new report path")
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("selected_tickers", "published_series", "series_with_pending_jobs")
            }
        )
    )
