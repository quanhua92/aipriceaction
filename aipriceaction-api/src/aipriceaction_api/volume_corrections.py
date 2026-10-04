"""Replayable volume corrections over immutable frozen legacy candles."""

import hashlib
import json
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path

from .domain import Candle, DataError
from .vci_volume import proof_from_capture, validate_volume_proof
from .vci_volume_projection import project_legacy_volume


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def make_record(rows, proof, original, capture_key):
    staged = project_legacy_volume(rows, proof)
    target = validate_volume_proof(proof)
    record = {
        "source": "vn",
        "symbol": target.symbol,
        "interval": "1m",
        "time": target.time,
        "revision": rows[0].revision,
        "evidence": encode(
            {
                "kind": "legacy_volume_projection",
                "schema_version": 1,
                "proof": proof,
                "original_rows": [asdict(row) for row in rows],
                "original_archive": original,
                "capture_key": capture_key,
                "candidate_checksum": digest(encode([asdict(row) for row in staged])),
            }
        ).decode(),
    }
    return {"id": digest(encode(record)), **record}


def validate_record(record, archive=None):
    try:
        if type(record) is not dict or set(record) != {
            "id",
            "source",
            "symbol",
            "interval",
            "time",
            "revision",
            "evidence",
        }:
            raise ValueError("Invalid receipt shape")
        if (
            record["id"] != digest(encode({k: v for k, v in record.items() if k != "id"}))
            or len(record["evidence"].encode()) > 2 * 1024 * 1024
        ):
            raise ValueError("Invalid receipt identity or size")
        evidence = json.loads(record["evidence"])
        if (
            evidence["kind"] != "legacy_volume_projection"
            or evidence["schema_version"] != 1
            or type(evidence["original_rows"]) is not list
            or not 2 <= len(evidence["original_rows"]) <= 500
        ):
            raise ValueError("Invalid projection evidence")
        original = [Candle(**row) for row in evidence["original_rows"]]
        staged = project_legacy_volume(original, evidence["proof"])
        target = validate_volume_proof(evidence["proof"])
        if (
            record["source"],
            record["symbol"],
            record["interval"],
            record["time"],
            record["revision"],
        ) != ("vn", target.symbol, "1m", target.time, original[0].revision) or evidence[
            "candidate_checksum"
        ] != digest(encode([asdict(row) for row in staged])):
            raise ValueError("Projection identity or checksum differs")
        if archive is not None:
            if archive.read(evidence["original_archive"], refresh=True) != original:
                raise DataError("Volume correction original archive differs")
            raw = archive.store.read(evidence["capture_key"])
            proof = evidence["proof"]
            if (
                digest(raw) != proof["source_capture_sha256"]
                or proof_from_capture(
                    raw, proof["daily_witnesses"], target.time, proof["verified_at_ns"]
                )
                != proof
            ):
                raise DataError("Volume correction native capture differs")
        return original, staged
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DataError("Invalid volume correction receipt") from exc


def apply_records(rows, records):
    result = {row.time: row for row in rows}
    for record in records:
        row = result.get(record["time"])
        if row is None or row.revision != record["revision"]:
            continue
        original, staged = validate_record(record)
        before = next(value for value in original if value.time == row.time)
        after = next(value for value in staged if value.time == row.time)
        if row != before:
            raise DataError("Stored candle changed since its volume correction proof")
        result[row.time] = after
    return [result[row.time] for row in rows]


def publish_correction(repo, archive, proof, raw):
    return publish_corrections(repo, archive, [(proof, raw)])[0]


def publish_corrections(repo, archive, candidates):
    """Commit checked receipts atomically, then publish one recoverable manifest."""
    candidates = list(candidates)
    if not 1 <= len(candidates) <= 100:
        raise DataError("Use a volume correction batch between 1 and 100 targets")
    targets = [validate_volume_proof(proof) for proof, _ in candidates]
    if len({(row.symbol, row.time) for row in targets}) != len(targets):
        raise DataError("Volume correction batch repeats a target")
    if len(
        {(row.symbol, proof["day"]) for row, (proof, _) in zip(targets, candidates, strict=True)}
    ) != len(targets):
        raise DataError("Volume correction batch repeats a source day")
    for target, (proof, raw) in zip(targets, candidates, strict=True):
        if digest(raw) != proof["source_capture_sha256"]:
            raise DataError("Volume correction capture checksum differs")
        if (
            proof_from_capture(raw, proof["daily_witnesses"], target.time, proof["verified_at_ns"])
            != proof
        ):
            raise DataError("Volume correction native capture differs")
    owner = uuid.uuid4().hex
    if not repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", owner, lease=3600):
        raise DataError("Another archive writer is active")
    claimed, prepared, records, pending = [], [], [], []
    try:
        for symbol in sorted({row.symbol for row in targets}):
            if not repo.live_claim("vn", symbol, "1m", owner, lease=3600):
                raise DataError("Volume correction series is busy")
            claimed.append(symbol)
        # Check every day before creating any new evidence objects.
        for target, (proof, raw) in zip(targets, candidates, strict=True):
            rows = repo.read("vn", target.symbol, "1m", proof["day"], proof["day"] + 86400 - 1)
            staged = project_legacy_volume(rows, proof)
            served = apply_records(
                rows,
                repo.volume_corrections(
                    "vn", target.symbol, "1m", proof["day"], proof["day"] + 86399
                ),
            )
            if served != rows and served != staged:
                raise DataError("Existing volume corrections differ from this candidate day")
            existing = [
                record
                for record in repo.volume_corrections(
                    "vn", target.symbol, "1m", target.time, target.time
                )
                if record["revision"] == rows[0].revision
            ]
            record = existing[0] if existing else None
            if record is not None:
                original_rows, _ = validate_record(record, archive)
                if json.loads(record["evidence"])["proof"] != proof or original_rows != rows:
                    raise DataError("Conflicting existing volume correction")
            prepared.append((proof, raw, rows, record))
        for proof, raw, rows, record in prepared:
            if record is None:
                original = archive.prepare(rows)
                key = f"{archive.settings.s3_prefix}/evidence/volume-corrections/{digest(raw)}.json"
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "capture.json"
                    path.write_bytes(raw)
                    archive.store.put(key, path)
                record = make_record(rows, proof, original, key)
                validate_record(record, archive)
                pending.append((record, rows))
            records.append(record)
        repo.record_volume_corrections(pending)
        # A pointer failure leaves the entire verified batch locally visible;
        # retry republishes these same receipts, with original candles immutable.
        archive.manifest(repo.archives())
        return records
    finally:
        for symbol in claimed:
            repo.live_release("vn", symbol, "1m", owner)
        repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", owner)
