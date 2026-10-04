"""Stream every stored candle through the domain rules in a read-only snapshot.

This verifies structural OHLCV validity, not market truth or calendar coverage.
Keeps bounded error samples and per-series counts; never copies SQLite or candles.
"""

import argparse
import json
import math
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError


def audit(database, sample_limit=20):
    if not 0 <= sample_limit <= 1000:
        raise ValueError("Use an error sample limit between 0 and 1000")
    counts = Counter()
    violations = Counter()
    samples = []
    checked = 0
    started = time.monotonic()
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        epoch = con.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0]
        integrity = [row[0] for row in con.execute("PRAGMA quick_check")]
        for row in con.execute("SELECT * FROM candles ORDER BY source,symbol,interval,time"):
            checked += 1
            counts[row["source"], row["symbol"], row["interval"]] += 1
            try:
                Candle(**dict(row)).validate()
                if type(row["time"]) is not int or type(row["volume"]) is not int:
                    raise DataError("Timestamp and volume must be integers")
            except (DataError, ValueError, TypeError, OverflowError) as exc:
                reason = str(exc)
                violations[reason] += 1
                if len(samples) < sample_limit:
                    # JSON cannot encode non-finite numbers. Keep the exact
                    # representation visible as text in invalid-row witnesses.
                    values = {
                        key: repr(value)
                        if isinstance(value, float) and not math.isfinite(value)
                        else value
                        for key, value in dict(row).items()
                    }
                    samples.append({"reason": reason, "row": values})
            if checked % 1_000_000 == 0:
                print(json.dumps({"checked_rows": checked}), flush=True)
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "canonical_publication": False,
        "database_epoch": epoch,
        "sqlite_quick_check": integrity,
        "checked_rows": checked,
        "invalid_rows": sum(violations.values()),
        "violations": dict(violations),
        "samples": samples,
        "samples_truncated": sum(violations.values()) > len(samples),
        "series": [
            {"source": source, "symbol": symbol, "interval": interval, "rows": count}
            for (source, symbol, interval), count in sorted(counts.items())
        ],
        "structural_validity_passed": integrity == ["ok"] and not violations,
        "perfect_data_proven": False,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "limitations": [
            "Every current SQLite candle is checked; cold archive and staging rows are excluded.",
            "Valid ranges and timestamps do not prove correct prices, volumes or complete sessions.",
            "SJC quote-derived and Yahoo settlement/legacy quote exceptions follow domain rules.",
            "Concurrent workers may advance after the read-only snapshot; no global frozen state is claimed.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sample-limit", type=int, default=20)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Choose a new report path")
    result = audit(Settings.from_env().database, args.sample_limit)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x") as out:
        out.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("checked_rows", "invalid_rows", "structural_validity_passed")
            }
        )
    )
