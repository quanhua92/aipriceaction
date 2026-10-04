"""Check a candidate proof catalog against frozen SQLite days; never publish."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.vci_volume import validate_volume_proof
from aipriceaction_api.vci_volume_projection import project_legacy_volume
from aipriceaction_api.volume_corrections import apply_records
from scripts.artifact_budget import ArtifactBudget
from scripts.check_legacy_volume_projection import checksum


def audit(con, proofs, first, before):
    if type(proofs) is not list or len(proofs) > 100 or first >= before:
        raise DataError("Use a bounded catalog and ordered audit window")
    targets = [validate_volume_proof(proof) for proof in proofs]
    if len({(row.symbol, row.time) for row in targets}) != len(targets):
        raise DataError("Duplicate volume projection proof targets")
    result = []
    for proof, target in zip(proofs, targets, strict=True):
        entry = {
            "symbol": target.symbol,
            "time": target.time,
            "proof_sha256": hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest(),
            "corrected_volume": target.volume,
        }
        result.append(entry)
        if not first <= target.time < before:
            entry["status"] = "outside_requested_window"
            continue
        rows = [
            Candle(**dict(row))
            for row in con.execute(
                "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1m' AND time>=? AND time<? ORDER BY time",
                (target.symbol, proof["day"], proof["day"] + 86400),
            )
        ]
        entry["sqlite_rows"] = len(rows)
        entry["native_source_rows"] = len(proof["source_rows"])
        entry["original_day_volume"] = sum(row.volume for row in rows)
        try:
            staged = project_legacy_volume(rows, proof)
            state = con.execute(
                "SELECT * FROM series WHERE source='vn' AND symbol=? AND interval='1m'",
                (target.symbol,),
            ).fetchone()
            if not state or state["status"] != "ready" or state["revision"] != rows[0].revision:
                raise DataError(
                    "Volume projection series basis is not ready or differs from snapshot"
                )
            receipts = [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM volume_corrections WHERE source='vn' AND symbol=? AND interval='1m' AND time>=? AND time<? ORDER BY time,id",
                    (target.symbol, proof["day"], proof["day"] + 86400),
                )
            ]
            served = apply_records(rows, receipts)
            if served != rows and served != staged:
                raise DataError("Existing volume receipts differ from this candidate projection")
            entry.update(
                status="already_applied" if served == staged else "projection_available",
                original_checksum=checksum(rows),
                candidate_checksum=checksum(staged),
                original_volume=next(row.volume for row in rows if row.time == target.time),
                candidate_day_volume=sum(row.volume for row in staged),
                revision=rows[0].revision,
            )
        except DataError as exc:
            entry.update(status="projection_blocked", error=str(exc))
    return result


def run(args):
    settings = Settings.from_env()
    proofs = json.loads(args.proofs.read_text())
    first, before = date_bounds(args.start_date), date_bounds(args.end_date, end=True) + 1
    with sqlite3.connect(settings.database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        series = audit(con, proofs, first, before)
    result = {
        "read_only": True,
        "canonical_publication": False,
        "remote_requests": False,
        "proofs_sha256": hashlib.sha256(args.proofs.read_bytes()).hexdigest(),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "publication_license": False,
        "counts": dict(Counter(row["status"] for row in series)),
        "series": series,
        "limitations": [
            "Projection requires the complete source-day timestamps and original volumes in one frozen legacy revision.",
            "Only the existing single cumulative-volume correction is supported; peer/multiple corrections stay explicit refusals.",
            "Available projection preserves legacy prices and provenance; source capture replay, immutable archive evidence and recovery must precede publication.",
        ],
    }
    args.output.mkdir(parents=True, exist_ok=False)
    ArtifactBudget(args.output, 2 * 1024 * 1024).write(
        args.output / "report.json", (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result["counts"]), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proofs", type=Path, required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
