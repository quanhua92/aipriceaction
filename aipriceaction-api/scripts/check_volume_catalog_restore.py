"""Verify a local receipt batch through HTTP and a temporary cold archive index."""

import argparse
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository
from aipriceaction_api.volume_corrections import validate_record
from scripts.artifact_budget import ArtifactBudget
from scripts.check_volume_correction_restore import verify_http


def run(args):
    settings = Settings.from_env()
    if settings.archive_backend != "s3" or settings.s3_endpoint != "http://127.0.0.1:9100":
        raise DataError("Batch restoration verification requires local RustFS")
    activation = json.loads(args.activation.read_text())
    ids = activation.get("receipt_ids", [])
    if (
        not activation["canonical_publication"]
        or not 1 <= len(ids) <= 100
        or len(set(ids)) != len(ids)
    ):
        raise DataError("Choose a completed bounded receipt batch")
    repo = Repository(settings.database)
    records = {row["id"]: row for row in repo.volume_corrections() if row["id"] in ids}
    if set(records) != set(ids):
        raise DataError("Published batch is missing local receipts")
    checked = []
    for ident in ids:
        record = records[ident]
        original, staged = validate_record(record)
        if (
            repo.read(
                record["source"],
                record["symbol"],
                record["interval"],
                original[0].time,
                original[-1].time,
            )
            != original
        ):
            raise DataError("Raw batch snapshot changed after publication")
        checked.append((record, original, staged))
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "remote_writes": False,
        "raw_snapshots_unchanged": True,
        "series": [],
        "completed": False,
    }
    budget = ArtifactBudget(args.output, 256 * 1024)

    def checkpoint():
        budget.write(args.output / "report.json", (json.dumps(report, indent=2) + "\n").encode())

    checkpoint()
    with tempfile.TemporaryDirectory(prefix="volume-batch-restore-", dir=args.output) as temporary:
        root = Path(temporary)
        isolated = replace(settings, database=root / "index.sqlite3", cache_dir=root / "cache")
        restored = Repository(isolated.database)
        restored.initialize()
        archive = Archive(restored, isolated)
        report["restored_archive_objects"] = archive.restore_index()
        restored_records = {row["id"]: row for row in restored.volume_corrections()}
        for record, original, staged in checked:
            if restored_records.get(record["id"]) != record:
                raise DataError("Batch receipt differs from restored manifest")
            # Mount only the immutable evidence days in this temporary index;
            # this does not claim a full hot SQLite restoration.
            archive.repo.publish_archive(json.loads(record["evidence"])["original_archive"])
            cold = History(restored, archive, isolated).read(
                record["source"],
                record["symbol"],
                record["interval"],
                original[0].time,
                original[-1].time,
            )
            if cold != staged:
                raise DataError("Batch cold OHLCV differs from its verified projection")
            http = verify_http(args.base_url, record, original, staged)
            report["series"].append(
                {
                    "id": record["id"],
                    "symbol": record["symbol"],
                    "time": record["time"],
                    "cold_rows": len(cold),
                    "cold_volume_total": sum(row.volume for row in cold),
                    "http": http,
                }
            )
            checkpoint()
    report.update(
        completed=True,
        temporary_storage_removed=not root.exists(),
        scope="Full manifest receipt replay and scoped cold evidence-day reads; not full hot SQLite recovery",
    )
    checkpoint()
    print(
        json.dumps(
            {
                "completed": report["completed"],
                "targets": len(checked),
                "temporary_storage_removed": report["temporary_storage_removed"],
            }
        ),
        flush=True,
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:3001")
    run(parser.parse_args())
