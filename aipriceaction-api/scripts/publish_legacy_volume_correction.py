"""Preview or explicitly publish one replayable local legacy-volume correction."""

import argparse
import json
from pathlib import Path

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.storage import Repository
from aipriceaction_api.vci_volume import validate_volume_proof
from aipriceaction_api.vci_volume_projection import project_legacy_volume
from aipriceaction_api.volume_corrections import digest, publish_correction


def run(args):
    settings = Settings.from_env()
    if settings.archive_backend != "s3" or settings.s3_endpoint != "http://127.0.0.1:9100":
        raise DataError("Publication controls require the local RustFS endpoint")
    proof = json.loads(args.proof.read_text())
    raw = args.capture.read_bytes()
    target = validate_volume_proof(proof)
    if digest(raw) != proof["source_capture_sha256"]:
        raise DataError("Correction capture checksum differs")
    repo = Repository(settings.database)
    rows = repo.read("vn", target.symbol, "1m", proof["day"], proof["day"] + 86400 - 1)
    staged = project_legacy_volume(rows, proof)
    result = {
        "canonical_publication": bool(args.execute),
        "raw_snapshot_mutated": False,
        "symbol": target.symbol,
        "time": target.time,
        "rows": len(rows),
        "original_volume": next(row.volume for row in rows if row.time == target.time),
        "corrected_volume": target.volume,
        "projected_day_volume": sum(row.volume for row in staged),
    }
    args.output.mkdir(parents=True, exist_ok=False)
    if args.execute:
        repo.initialize()
        record = publish_correction(repo, Archive(repo, settings), proof, raw)
        result["receipt_id"] = record["id"]
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    run(parser.parse_args())
