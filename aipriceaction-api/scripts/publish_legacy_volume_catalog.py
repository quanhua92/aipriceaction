"""Preview or publish an audited volume-only batch to local RustFS, one manifest."""

import argparse
import hashlib
import json
from pathlib import Path

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.storage import Repository
from aipriceaction_api.vci_volume import proof_from_capture, validate_volume_proof
from aipriceaction_api.vci_volume_projection import project_legacy_volume
from aipriceaction_api.volume_corrections import publish_corrections
from scripts.artifact_budget import ArtifactBudget
from scripts.check_legacy_volume_projection import checksum


def prepare(repo, audit, catalog, captures):
    if audit["proofs_sha256"] != hashlib.sha256(catalog.read_bytes()).hexdigest():
        raise DataError("Projection audit and proof catalog differ")
    proofs = json.loads(catalog.read_text())
    indexed = {(proof["symbol"], validate_volume_proof(proof).time): proof for proof in proofs}
    if len(indexed) != len(proofs):
        raise DataError("Duplicate candidate proof targets")
    selected = [row for row in audit["series"] if row["status"] == "projection_available"]
    keys = [(row["symbol"], row["time"]) for row in selected]
    if not 1 <= len(keys) <= 100 or len(set(keys)) != len(keys):
        raise DataError("Choose a nonempty bounded audit without repeated targets")
    wanted = {indexed[key]["source_capture_sha256"] for key in keys}
    paths = {}
    for path in sorted(captures.rglob("native-*.json")):
        digest = path.stem.removeprefix("native-")
        if digest in wanted and digest not in paths:
            paths[digest] = path
    if set(paths) != wanted:
        raise DataError("Required native correction captures are missing")
    candidates, preview = [], []
    for entry, key in zip(selected, keys, strict=True):
        proof = indexed[key]
        raw = paths[proof["source_capture_sha256"]].read_bytes()
        if (
            hashlib.sha256(raw).hexdigest() != proof["source_capture_sha256"]
            or proof_from_capture(raw, proof["daily_witnesses"], key[1], proof["verified_at_ns"])
            != proof
        ):
            raise DataError("Audited correction native capture changed")
        rows = repo.read("vn", key[0], "1m", proof["day"], proof["day"] + 86399)
        staged = project_legacy_volume(rows, proof)
        if (
            checksum(rows) != entry["original_checksum"]
            or checksum(staged) != entry["candidate_checksum"]
        ):
            raise DataError("Audited correction snapshot changed")
        preview.append(
            {
                "symbol": key[0],
                "time": key[1],
                "rows": len(rows),
                "original_checksum": entry["original_checksum"],
                "candidate_checksum": entry["candidate_checksum"],
                "capture": str(paths[proof["source_capture_sha256"]]),
                "candidate_day_volume": sum(row.volume for row in staged),
            }
        )
        candidates.append((proof, raw))
    return candidates, preview


def run(args):
    settings = Settings.from_env()
    if settings.archive_backend != "s3" or settings.s3_endpoint != "http://127.0.0.1:9100":
        raise DataError("Batch publication requires the local RustFS endpoint")
    audit = json.loads(args.audit.read_text())
    repo = Repository(settings.database)
    candidates, preview = prepare(repo, audit, args.proofs, args.captures)
    args.output.mkdir(parents=True, exist_ok=False)
    budget = ArtifactBudget(args.output, 256 * 1024)
    report = {
        "canonical_publication": False,
        "raw_snapshot_mutated": False,
        "audit_sha256": hashlib.sha256(args.audit.read_bytes()).hexdigest(),
        "proofs_sha256": audit["proofs_sha256"],
        "targets": preview,
    }

    def checkpoint():
        budget.write(args.output / "report.json", (json.dumps(report, indent=2) + "\n").encode())

    checkpoint()
    if args.execute:
        try:
            records = publish_corrections(repo, Archive(repo, settings), candidates)
            report.update(
                canonical_publication=True,
                receipt_ids=[row["id"] for row in records],
                manifest_publications=1,
            )
        except DataError as exc:
            report["error"] = str(exc)
            # A pointer failure can happen after the atomic local commit.
            found = [
                row
                for row in repo.volume_corrections()
                if (row["symbol"], row["time"])
                in {(entry["symbol"], entry["time"]) for entry in preview}
            ]
            report["locally_present_receipt_ids"] = [row["id"] for row in found]
            checkpoint()
            raise
        checkpoint()
    print(
        json.dumps(
            {"targets": len(preview), "canonical_publication": report["canonical_publication"]}
        ),
        flush=True,
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    run(parser.parse_args())
