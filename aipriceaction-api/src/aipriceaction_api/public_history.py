"""Preserve validated public daily records and explicitly quarantine invalid dates."""

import csv
import hashlib
import io
import json
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path

from .domain import DataError, cutoff, parse_time
from .importing import csv_rows, json_rows
from .recovery import daily_times


def partition_public_year(raw, original, symbol, year, revision, captured_at):
    """Require exact original timestamps and OHLCV, including invalid records."""
    expected = daily_times(original, year)
    if (
        not revision
        or type(captured_at) is not int
        or not max(expected) * 1_000_000_000 <= captured_at <= time.time_ns()
    ):
        raise DataError("A separate revision and valid capture version are required", 400)
    try:
        payload = json.loads(raw)
        if type(payload) is not dict or set(payload) != {symbol}:
            raise ValueError("Expected one public symbol")
        records = payload[symbol]
        if type(records) is not list or len(records) != len(expected):
            raise ValueError("Public record count differs from the original year")
        csv_values = {
            parse_time(row[0]): tuple(float(value) for value in row[1:])
            for row in csv.reader(io.StringIO(original.decode("utf-8-sig")))
        }
        valid, invalid, seen = [], [], set()
        for record in records:
            if type(record) is not dict or any(
                record.get(key, symbol) != symbol for key in ("symbol", "ticker")
            ):
                raise ValueError("Unexpected public candle identity")
            stamp = parse_time(record["time"])
            if stamp not in expected or stamp in seen:
                raise ValueError("Missing, additional or duplicate public timestamp")
            seen.add(stamp)
            values = tuple(record[field] for field in ("open", "high", "low", "close", "volume"))
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
                raise ValueError("Public OHLCV must be numeric")
            if values != csv_values[stamp]:
                raise ValueError("Public OHLCV differs from the original year")
            try:
                row = json_rows(
                    json.dumps({symbol: [record]}), "vn", symbol, "1D", "legacy-api", revision
                )[0]
                valid.append(replace(row, updated_at=captured_at))
            except DataError as exc:
                invalid.append({"time": stamp, "record": record, "reason": str(exc)})
        if seen != expected or not valid or not invalid:
            raise ValueError("Partition requires both verified candles and explicit invalid dates")
        json.dumps(invalid, allow_nan=False)
        return sorted(valid, key=lambda row: row.time), invalid
    except (ValueError, KeyError, TypeError, UnicodeError, OverflowError) as exc:
        raise DataError(f"Public year cannot refine the original gap: {exc}", 400) from exc


def recover_public_year(
    repo, archive, public_path, original_path, symbol, year, revision, captured_at, execute=False
):
    public_path, original_path = Path(public_path), Path(original_path)
    if max(public_path.stat().st_size, original_path.stat().st_size) > 64 * 1024 * 1024:
        raise DataError("Public recovery input exceeds its byte budget", 400)
    raw, original = public_path.read_bytes(), original_path.read_bytes()
    rows, invalid = partition_public_year(raw, original, symbol, year, revision, captured_at)
    first, last = parse_time(f"{year}-01-01"), parse_time(f"{year + 1}-01-01") - 1
    if last >= cutoff(archive.settings.daily_years):
        raise DataError("Public year recovery must be wholly outside retention", 400)
    parents = [
        gap
        for gap in repo.history_gaps("vn", symbol, "1D", first, last)
        if gap["start"] == first and gap["end"] == last
    ]
    if len(parents) != 1:
        raise DataError("Recovery requires one unchanged full-year unavailable-history marker")
    parent = parents[0]
    if parent["evidence"].get("kind") != "legacy_daily_import":
        raise DataError("Only an invalid legacy daily import can be partitioned")
    try:
        csv_rows(original.decode("utf-8-sig"), "vn", symbol, "1D")
    except DataError as exc:
        if str(exc) != parent["reason"]:
            raise DataError("Original CSV failure differs from the stored observation") from exc
    else:
        raise DataError("Original CSV has no invalid record")
    archive.validate_historical_snapshot(rows)
    archive.validate_historical_capture(rows)
    state = repo.state("vn", symbol, "1D")
    if state and revision == state["revision"]:
        raise DataError("Public recovery cannot reuse the active revision")
    public_checksum, original_checksum = (
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(original).hexdigest(),
    )
    report = {
        "execute": execute,
        "symbol": symbol,
        "year": year,
        "valid_rows": len(rows),
        "invalid_rows": invalid,
        "public_checksum": public_checksum,
        "original_checksum": original_checksum,
    }
    if not execute:
        return report
    owner = uuid.uuid4().hex
    if not repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", owner, lease=3600):
        raise DataError("Another archive writer is active")
    try:
        archive.validate_historical_capture(rows)
        evidence_keys = []
        with tempfile.TemporaryDirectory() as temp:
            for content, checksum, suffix in (
                (raw, public_checksum, "json"),
                (original, original_checksum, "csv"),
            ):
                path = Path(temp) / f"original.{suffix}"
                path.write_bytes(content)
                key = f"{archive.settings.s3_prefix}/evidence/public-year/{checksum}.{suffix}"
                archive.store.put(key, path)
                if archive.store.read(key) != content:
                    raise DataError("Public year evidence readback failed")
                evidence_keys.append(key)
        obj = archive.prepare(rows) | {"status": "historical_snapshot"}
        gaps = [
            dict(
                source="vn",
                symbol=symbol,
                interval="1D",
                start=record["time"],
                end=record["time"] + 86399,
                reason=parent["reason"]
                if len(invalid) == 1
                else f"Invalid public daily candle: {record['reason']}",
                evidence={
                    "kind": "partitioned_public_year",
                    "original_gap": parent,
                    "public_checksum": public_checksum,
                    "original_checksum": original_checksum,
                    "evidence_keys": evidence_keys,
                    "invalid_record": record["record"],
                    "snapshot_id": obj["id"],
                },
            )
            for record in invalid
        ]
        # Keep the original native/mixed-frame gate. Only this verified frozen
        # revision can use the finer invalid-date markers below.
        gaps.insert(
            0,
            parent
            | {
                "evidence": {
                    "kind": "public_year_partition_basis",
                    "original_gap": parent,
                    "revision": revision,
                    "snapshot_id": obj["id"],
                }
            },
        )
        repo.publish_partitioned_history(obj, parent, gaps)
        archive.manifest(repo.archives())
        return report | {"object": obj, "remaining_gaps": gaps}
    finally:
        repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", owner)
