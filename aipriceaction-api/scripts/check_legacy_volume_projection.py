"""Rehearse one legacy volume projection with temporary Parquet storage only."""

import argparse
import hashlib
import json
import sqlite3
import tempfile
from dataclasses import asdict
from pathlib import Path

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.storage import Repository
from aipriceaction_api.vci_volume import validate_volume_proof
from aipriceaction_api.vci_volume_projection import project_legacy_volume


def checksum(rows):
    return hashlib.sha256(
        json.dumps([asdict(row) for row in rows], sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def rehearse(rows, proof, output):
    staged = project_legacy_volume(rows, proof)
    with tempfile.TemporaryDirectory(prefix="volume-rehearsal-", dir=output) as temporary:
        root = Path(temporary)
        settings = Settings(
            database=root / "fixture.sqlite3",
            archive_backend="filesystem",
            object_dir=root / "objects",
            cache_dir=root / "cache",
        )
        repo = Repository(settings.database)
        repo.initialize()
        archive = Archive(repo, settings)
        original = archive.prepare(rows)
        candidate = archive.prepare(staged)
        if (
            archive.read(original, refresh=True) != rows
            or archive.read(candidate, refresh=True) != staged
        ):
            raise DataError("Projection archive round trip changed candle fields")
        # Re-read the original after staging: it remains immutable and restores
        # the exact original prices, volume, revision and timestamps.
        if archive.read(original, refresh=True) != rows:
            raise DataError("Original projection rollback snapshot changed")
        changed = [(a, b) for a, b in zip(rows, staged, strict=True) if a != b]
        if len(changed) != 1:
            raise DataError("Projection must change exactly one candle")
        before, after = changed[0]
        result = {
            "canonical_publication": False,
            "rows": len(rows),
            "original_checksum": checksum(rows),
            "candidate_checksum": checksum(staged),
            "original_archive_checksum": original["checksum"],
            "candidate_archive_checksum": candidate["checksum"],
            "rollback_round_trip_verified": True,
            "original": asdict(before),
            "candidate": asdict(after),
        }
    result["temporary_storage_removed"] = not root.exists()
    return result


def run(args):
    proof = json.loads(args.proof.read_text())
    target = validate_volume_proof(proof)
    settings = Settings.from_env()
    with sqlite3.connect(settings.database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        rows = [
            Candle(**dict(row))
            for row in con.execute(
                "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1m' AND time>=? AND time<? ORDER BY time",
                (target.symbol, proof["day"], proof["day"] + 86400),
            )
        ]
    args.output.mkdir(parents=True, exist_ok=False)
    result = rehearse(rows, proof, args.output)
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--proof", type=Path, required=True, help="One verified proof, not a catalog"
    )
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
