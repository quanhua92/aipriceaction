"""Read-only full structural audit of active cold OHLCV objects with temporary cache."""

import argparse
import json
import sqlite3
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError


def audit(settings):
    started = time.monotonic()
    with sqlite3.connect(settings.database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        epoch = con.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0]
        objects = [
            dict(row)
            for row in con.execute(
                "SELECT * FROM archives WHERE status IN "
                "('published','pending_repair','historical_snapshot') ORDER BY object_key"
            )
        ]
    checked_rows = 0
    results = []
    with TemporaryDirectory(prefix="aipa-archive-audit-") as temporary:
        archive = Archive(None, replace(settings, cache_dir=Path(temporary)))
        for index, obj in enumerate(objects, 1):
            record = {
                key: obj[key]
                for key in (
                    "id",
                    "source",
                    "symbol",
                    "interval",
                    "object_key",
                    "checksum",
                    "status",
                )
            }
            try:
                if obj["schema_version"] != 1:
                    raise DataError("Unsupported archive schema version")
                if not 0 < obj["row_count"] <= settings.archive_max_rows:
                    raise DataError("Archive row count outside configured budget")
                rows = archive.read(obj, refresh=True)
                if (
                    not rows
                    or len(rows) != obj["row_count"]
                    or rows[0].time != obj["start"]
                    or rows[-1].time != obj["end"]
                ):
                    raise DataError("Archive bounds or row count differ from index")
                previous = None
                for row in rows:
                    if type(row.time) is not int or type(row.volume) is not int:
                        raise DataError("Archive timestamp and volume must be integers")
                    if previous is not None and row.time <= previous:
                        raise DataError("Archive contains duplicate or unordered timestamps")
                    previous = row.time
                checked_rows += len(rows)
                record.update(passed=True, checked_rows=len(rows))
            except Exception as exc:
                record.update(
                    passed=False,
                    error=str(exc) if isinstance(exc, DataError) else type(exc).__name__,
                )
            results.append(record)
            if index % 100 == 0:
                print(
                    json.dumps({"checked_objects": index, "total_objects": len(objects)}),
                    flush=True,
                )
    errors = Counter(row["error"] for row in results if not row["passed"])
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "canonical_publication": False,
        "database_epoch": epoch,
        "checked_objects": len(objects),
        "verified_rows": checked_rows,
        "failed_objects": sum(errors.values()),
        "pending_repair_objects": sum(row["status"] == "pending_repair" for row in results),
        "errors": dict(errors),
        "objects": results,
        "structural_validity_passed": bool(objects) and not errors,
        "perfect_data_proven": False,
        "temporary_cache_removed": True,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "limitations": [
            "Scope is all active archive-index objects captured from one read-only SQLite snapshot.",
            "Checksums, identity, values, timestamps and index bounds do not prove market truth or session completeness.",
            "Superseded objects, rollback dependencies and manifest parity require separate checks.",
            "Pending-repair status remains an unresolved data-quality signal even if structure passes.",
            "Current SQLite candles and concurrent archive-index changes are outside this captured scope.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Choose a new report path")
    result = audit(Settings.from_env())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x") as out:
        out.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "checked_objects",
                    "verified_rows",
                    "failed_objects",
                    "structural_validity_passed",
                )
            }
        )
    )
