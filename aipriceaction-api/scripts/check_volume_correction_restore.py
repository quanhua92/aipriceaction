"""Verify a local receipt, API aggregates and temporary cold restoration."""

import argparse
import json
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.archive import Archive
from aipriceaction_api.calculations import aggregate
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository
from aipriceaction_api.volume_corrections import validate_record


def verify_http(base_url, record, original, staged):
    if base_url != "http://127.0.0.1:3001":
        raise DataError("Verification requires the existing loopback API")
    day = datetime.fromtimestamp(record["time"], UTC).strftime("%Y-%m-%d")
    fields = ("open", "high", "low", "close", "volume")
    report = {}
    with httpx.Client(timeout=30) as client:
        for interval in ("1m", "15m"):
            response = client.get(
                base_url + "/tickers",
                params={
                    "symbol": record["symbol"],
                    "mode": "vn",
                    "interval": interval,
                    "start_date": day,
                    "end_date": day,
                    "ma": "false",
                    "cache": "false",
                    "snap": "false",
                },
            )
            response.raise_for_status()
            returned = response.json()[record["symbol"]]
            expected = staged if interval == "1m" else aggregate(staged, interval, "vn")
            before = original if interval == "1m" else aggregate(original, interval, "vn")
            if len(returned) != len(expected) or any(
                parse_time(raw["time"]) != row.time
                or any(raw[field] != getattr(row, field) for field in fields)
                for raw, row in zip(returned, expected, strict=True)
            ):
                raise DataError("HTTP OHLCV differs from the verified correction projection")
            report[interval] = {
                "rows": len(returned),
                "volume_total": sum(row["volume"] for row in returned),
                "changed_volume_rows": sum(
                    a.volume != b.volume for a, b in zip(before, expected, strict=True)
                ),
                "prices_unchanged": all(
                    all(getattr(a, field) == getattr(b, field) for field in fields[:-1])
                    for a, b in zip(before, expected, strict=True)
                ),
            }
    return report


def run(args):
    settings = Settings.from_env()
    if settings.s3_endpoint != "http://127.0.0.1:9100":
        raise DataError("Restore verification requires local RustFS")
    repo = Repository(settings.database)
    records = [record for record in repo.volume_corrections() if record["id"] == args.receipt]
    if len(records) != 1:
        raise DataError("Choose one existing correction receipt")
    record = records[0]
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
        raise DataError("Raw local snapshot changed after correction publication")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "receipt_id": args.receipt,
        "raw_snapshot_unchanged": True,
        "http": verify_http(args.base_url, record, original, staged),
        "remote_writes": False,
    }
    with tempfile.TemporaryDirectory(prefix="volume-restore-", dir=args.output) as temporary:
        root = Path(temporary)
        isolated = replace(settings, database=root / "index.sqlite3", cache_dir=root / "cache")
        restored = Repository(isolated.database)
        restored.initialize()
        archive = Archive(restored, isolated)
        report["restored_archive_objects"] = archive.restore_index()
        if record not in restored.volume_corrections():
            raise DataError("Receipt missing from the restored manifest")
        # Hot SQLite recovery is separate from archive-index recovery. For this
        # scoped rehearsal, mount the receipt's verified original-day object
        # only in the temporary index; no remote object/index is published.
        archive.repo.publish_archive(json.loads(record["evidence"])["original_archive"])
        cold = History(restored, archive, isolated).read(
            record["source"],
            record["symbol"],
            record["interval"],
            original[0].time,
            original[-1].time,
        )
        if cold != staged:
            raise DataError("Restored cold history differs from the verified volume projection")
        report["cold_rows"] = len(cold)
        report["cold_volume_total"] = sum(row.volume for row in cold)
        report["scope"] = (
            "Full manifest receipt replay; scoped cold read from the original-day evidence object, not full hot SQLite recovery"
        )
    report["temporary_storage_removed"] = not root.exists()
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:3001")
    run(parser.parse_args())
