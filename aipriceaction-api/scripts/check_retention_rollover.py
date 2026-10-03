"""Rehearse a dated retention rollover on an isolated populated database copy.

Writes a unique S3 rehearsal prefix and new local files only. The original
database and its archive manifest pointer are never published or pruned.
"""

import argparse
import json
import sqlite3
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from aipriceaction_api.archive import Archive, sha256
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import cutoff
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository


def run(args):
    settings = Settings.from_env()
    original = Repository(settings.database)
    epoch = original.epoch()
    simulated = datetime.strptime(args.cutoff_date, "%Y-%m-%d").replace(tzinfo=UTC)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "passed": False,
        "simulated_utc_date": args.cutoff_date,
        "source": args.source,
        "canonical_epoch_before": epoch,
        "canonical_pruned": False,
        "partitions": [],
    }
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    before_path = args.output / "before.sqlite3"
    original.backup(before_path)
    before = Repository(before_path)
    candidate_path = args.output / "candidate.sqlite3"
    before.backup(candidate_path)
    isolated = replace(
        settings,
        database=candidate_path,
        cache_dir=args.output / "candidate-cache",
        s3_prefix=f"{settings.s3_prefix}/rehearsals/rollover-{uuid.uuid4().hex}",
        object_dir=args.output / "objects",
    )
    candidate = Repository(candidate_path)
    archive = Archive(candidate, isolated)
    report["archive_prefix"] = isolated.s3_prefix
    with patch(
        "aipriceaction_api.archive.cutoff", lambda years, now=None: cutoff(years, simulated)
    ):
        groups = archive.eligible(args.source)
    if not groups:
        raise ValueError("No expired populated partitions at the selected simulated cutoff")
    removed = 0
    for rows in groups:
        first = rows[0]
        identity = (first.source, first.symbol, first.interval)
        years = {
            "1D": settings.daily_years,
            "1h": settings.hourly_years,
            "1m": settings.minute_years,
        }
        floor = cutoff(years[first.interval], simulated)
        expected = before.read(*identity, rows[0].time, floor)
        obj = archive.publish(rows, prune=True, require_current=True)
        if archive.read(obj, refresh=True) != rows:
            raise AssertionError("Published object differs from original row versions")
        if candidate.read(*identity, rows[0].time, rows[-1].time):
            raise AssertionError("Verified expired rows remain in candidate SQLite")
        if History(candidate, archive, isolated).read(*identity, rows[0].time, floor) != expected:
            raise AssertionError("Boundary history changed after rollover")
        removed += len(rows)
        report["partitions"].append({**obj, "boundary_rows_verified": len(expected)})
        print(f"Verified {first.symbol} {first.interval}: {len(rows)} expired rows", flush=True)

    # Check every surviving field/version and every missing key against the
    # consistent before-image, including markets outside the requested scope.
    with sqlite3.connect(candidate_path) as con:
        con.execute("ATTACH DATABASE ? AS original", (str(before_path),))
        key = " AND ".join(f"a.{k}=b.{k}" for k in ("source", "symbol", "interval", "time"))
        fields = ("open", "high", "low", "close", "volume", "provider", "revision", "updated_at")
        changed = " OR ".join(f"a.{k} IS NOT b.{k}" for k in fields)
        changed_rows = con.execute(
            f"SELECT COUNT(*) FROM candles a JOIN original.candles b ON {key} WHERE {changed}"
        ).fetchone()[0]
        extras = con.execute(
            f"SELECT COUNT(*) FROM candles a LEFT JOIN original.candles b ON {key} WHERE b.time IS NULL"
        ).fetchone()[0]
        missing = con.execute(
            f"SELECT COUNT(*) FROM original.candles b LEFT JOIN candles a ON {key} WHERE a.time IS NULL"
        ).fetchone()[0]
        if changed_rows or extras or missing != removed:
            raise AssertionError("Rollover changed rows outside its verified expired selection")
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise AssertionError("Candidate SQLite integrity check failed")
    restored_settings = replace(
        isolated,
        database=args.output / "restored.sqlite3",
        cache_dir=args.output / "restored-cache",
    )
    restored = Repository(restored_settings.database)
    restored.initialize()
    restored_archive = Archive(restored, restored_settings)
    report["restored_objects"] = restored_archive.restore_index()
    for rows in groups:
        first = rows[0]
        actual = History(restored, restored_archive, restored_settings).read(
            first.source, first.symbol, first.interval, rows[0].time, rows[-1].time
        )
        if actual != rows:
            raise AssertionError("Manifest restoration changed expired row versions")
    if original.epoch() != epoch:
        raise AssertionError("Canonical database changed during isolated rehearsal")
    after_path = args.output / "after.sqlite3"
    candidate.backup(after_path)
    report.update(
        passed=True,
        expired_rows=removed,
        changed_surviving_rows=changed_rows,
        unexpected_new_rows=extras,
        canonical_epoch_after=original.epoch(),
        before_sha256=sha256(before_path),
        after_sha256=sha256(after_path),
    )
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": True, "partitions": len(groups), "expired_rows": removed}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=("vn", "crypto", "yahoo", "sjc"))
    parser.add_argument(
        "--cutoff-date", required=True, help="Simulated UTC rollover date, YYYY-MM-DD"
    )
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args())
