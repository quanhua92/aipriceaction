"""Recover corrupt legacy daily snapshots from the pinned, verified provider.

Only timestamps come from the invalid CSV. All published OHLCV comes from valid
provider replies with exact date coverage and a completed retained-price check.
Checksummed original bytes and verification evidence remain in object storage.
"""

import asyncio
import csv
import io
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from .domain import DataError, parse_time
from .migration import digest, encode
from .session_exclusions import validate_placeholder, verified_exclusions


def daily_times(raw, year):
    if len(raw) > 64 * 1024 * 1024:
        raise DataError("Legacy recovery input exceeds its byte budget", 400)
    try:
        first, last = parse_time(f"{year}-01-01"), parse_time(f"{year + 1}-01-01")
        dates = []
        for number, row in enumerate(csv.reader(io.StringIO(raw.decode("utf-8-sig"))), 1):
            if number > 366:
                raise DataError("Recovery year exceeds its daily row budget", 400)
            if len(row) != 6:
                raise DataError("Recovery requires the original six-column daily CSV", 400)
            stamp = parse_time(row[0])
            if stamp % 86400 or not first <= stamp < last:
                raise DataError(f"Invalid recovery timestamp in row {number}", 400)
            dates.append(stamp)
    except (csv.Error, UnicodeError, ValueError, OverflowError) as exc:
        raise DataError("Invalid legacy daily recovery dates", 400) from exc
    if not dates or len(set(dates)) != len(dates):
        raise DataError("Recovery requires nonempty, unique original timestamps", 400)
    return set(dates)


async def recover_daily(worker, path, symbol, year, max_pages=4, exclude_verified_sessions=False):
    if not 1 <= max_pages <= 12:
        raise DataError("Recovery page budget must be between one and 12", 400)
    if worker.archive is None:
        raise DataError("Recovery requires configured archive storage", 400)
    repo, archive = worker.repo, worker.archive
    symbol = symbol.upper()
    entries = worker.configuration or worker.load_watchlist()
    entry = next((e for e in entries if e["source"] == "vn" and e["symbol"] == symbol), None)
    if entry is None:
        raise DataError("Recovery requires a configured VN ticker", 400)
    state = repo.state("vn", symbol, "1D")
    if not state or state["status"] != "ready":
        raise DataError("Recovery requires a ready retained daily series")
    if state["provider"] not in ("vps", "vndirect", "dnse"):
        raise DataError("Recovery requires a pinned selected VN provider")
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise DataError("Legacy recovery input exceeds its byte budget", 400)
    raw = path.read_bytes()
    original = daily_times(raw, year)
    exclusion = verified_exclusions(symbol, year, raw) if exclude_verified_sessions else None
    if entry.get("history_start") and min(original) < parse_time(entry["history_start"]):
        raise DataError("Recovery snapshot predates the configured listing/history start", 400)
    floor = worker.floor(entry, "1D")
    eligible = {stamp for stamp in original if stamp < floor}
    excluded = {parse_time(day) for day in exclusion["dates"]} if exclusion else set()
    if excluded - eligible:
        raise DataError("Session exclusion would affect retained data", 400)
    expected = eligible - excluded
    if not expected:
        raise DataError("Recovery is for archived history outside local retention", 400)
    overlaps = repo.archives("vn", symbol, "1D", min(expected), max(expected))
    old_id = None
    if overlaps:
        if len(overlaps) != 1:
            raise DataError("Overlapping archive fragments require scoped reconciliation")
        old = await asyncio.to_thread(archive.read, overlaps[0])
        if {r.time for r in old} not in (eligible, expected):
            raise DataError("Overlapping archive coverage differs from recovery input")
        old_id = overlaps[0]["id"]
    before, collected = max(expected) + 86400, {}
    for _ in range(max_pages):
        count = min(500, max(1, len(expected) - len(collected)))
        page = await worker.providers.page(
            "vn", symbol, "1D", before, count=count, provider=state["provider"]
        )
        if page.provider != state["provider"] or not page.rows or page.rows[0].time >= before:
            raise DataError("Recovery provider is unavailable, changed, or non-advancing")
        for row in page.rows:
            row.validate()
            if (row.source, row.symbol, row.interval, row.provider) != (
                "vn",
                symbol,
                "1D",
                state["provider"],
            ):
                raise DataError("Recovery provider returned a different series")
            if row.time in excluded:
                validate_placeholder([row.open, row.high, row.low, row.close, row.volume])
                continue
            if min(expected) <= row.time <= max(expected):
                collected[row.time] = replace(row, revision=state["revision"])
        before = page.rows[0].time
        if before <= min(expected):
            break
    if set(collected) != expected:
        raise DataError("Recovery timestamp coverage differs from the original snapshot")
    head = await worker.verify_archive_head(
        {"source": "vn", "symbol": symbol, "interval": "1D"}, state
    )
    rows = sorted(collected.values(), key=lambda r: r.time)
    candidate = await asyncio.to_thread(archive.prepare, rows)
    checksum = digest(raw)
    raw_key = f"{archive.settings.s3_prefix}/evidence/legacy-daily/{checksum}.csv"
    proof = {
        "kind": "legacy_daily_timestamp_recovery",
        "source": "vn",
        "symbol": symbol,
        "interval": "1D",
        "year": year,
        "original_checksum": checksum,
        "original_rows": len(original),
        "retained_floor": floor,
        "original_timestamps_checksum": digest(encode(sorted(expected))),
        "rows": len(rows),
        "provider": state["provider"],
        "revision": state["revision"],
        "archive_id": candidate["id"],
        "archive": candidate,
        "raw_key": raw_key,
        "head": head,
    }
    if exclusion:
        proof["session_exclusion"] = exclusion
    proof_data = encode(proof)
    proof_key = f"{archive.settings.s3_prefix}/evidence/legacy-daily/{digest(proof_data)}.json"
    with tempfile.TemporaryDirectory(dir=archive.settings.cache_dir) as temporary:
        raw_cache = Path(temporary) / "original.csv"
        proof_cache = Path(temporary) / "proof.json"
        raw_cache.write_bytes(raw)
        proof_cache.write_bytes(proof_data)
        await asyncio.to_thread(archive.store.put, raw_key, raw_cache)
        await asyncio.to_thread(archive.store.put, proof_key, proof_cache)
        if await asyncio.to_thread(archive.store.read, raw_key) != raw:
            raise DataError("Original recovery snapshot verification failed")
        if await asyncio.to_thread(archive.store.read, proof_key) != proof_data:
            raise DataError("Recovery evidence verification failed")
    obj = await asyncio.to_thread(archive.publish, rows, False, old_id, require_current=True)
    if obj["id"] != candidate["id"]:
        raise DataError("Recovery publication differs from verified candidate")
    result = proof | {"proof_key": proof_key, "proof_checksum": digest(proof_data)}
    identifier = digest(encode([symbol, year, checksum, obj["id"]]))
    with repo.connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO legacy_imports VALUES (?,?,?,?,?,?,?,?)",
            (
                identifier,
                "vn",
                symbol,
                "1D",
                str(year),
                checksum,
                json.dumps(result),
                int(time.time()),
            ),
        )
        con.execute(
            """UPDATE quality SET resolved=1 WHERE source='vn' AND symbol=? AND interval='1D'
            AND kind='migration_daily_history'
            AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,'$.year')=?""",
            (symbol, year),
        )
        repo.resolve_verified_history_gaps(
            con,
            "vn",
            symbol,
            "1D",
            parse_time(f"{year}-01-01"),
            parse_time(f"{year + 1}-01-01") - 1,
        )
    repo.retire_archive_jobs("vn", symbol, "1D")
    repo.mark_archive_repairs("vn", symbol, "1D")
    await asyncio.to_thread(archive.publish_metadata)
    return result


def validate_recovery(archive, record):
    """Verify immutable original/evidence/data before restoring its receipt."""
    try:
        if (
            set(record)
            != {
                "id",
                "source",
                "symbol",
                "interval",
                "period",
                "input_checksum",
                "result",
                "completed_at",
            }
            or type(record["completed_at"]) is not int
            or record["completed_at"] <= 0
        ):
            raise ValueError("Invalid recovery receipt")
        result = json.loads(record["result"])
        data = archive.store.read(result["proof_key"])
        proof = json.loads(data)
        if (
            digest(data) != result["proof_checksum"]
            or proof
            != {k: v for k, v in result.items() if k not in ("proof_key", "proof_checksum")}
            or proof["kind"] != "legacy_daily_timestamp_recovery"
            or proof["source"] != record["source"]
            or record["source"] != "vn"
            or proof["symbol"] != record["symbol"]
            or proof["interval"] != record["interval"]
            or record["interval"] != "1D"
            or str(proof["year"]) != record["period"]
            or proof["original_checksum"] != record["input_checksum"]
            or proof["provider"] not in ("vps", "vndirect", "dnse")
            or proof["head"]["kind"] != "completed_retained_ohlc"
            or type(proof["head"]["matched_rows"]) is not int
            or proof["head"]["matched_rows"] < 1
            or proof["head"]["first"] > proof["head"]["last"]
            or proof["head"]["last"] >= proof["head"]["verified_at_ns"] // 1_000_000_000
        ):
            raise ValueError("Recovery evidence differs")
        raw = archive.store.read(proof["raw_key"])
        original = daily_times(raw, proof["year"])
        expected = {stamp for stamp in original if stamp < proof["retained_floor"]}
        if "session_exclusion" in proof:
            exclusion = verified_exclusions(record["symbol"], proof["year"], raw)
            excluded = {parse_time(day) for day in exclusion["dates"]}
            if proof["session_exclusion"] != exclusion or excluded - expected:
                raise ValueError("Session exclusion differs from reviewed evidence")
            expected -= excluded
        obj = proof["archive"]
        rows = archive.read(obj, refresh=True)
        if (
            digest(raw) != proof["original_checksum"]
            or len(original) != proof["original_rows"]
            or digest(encode(sorted(expected))) != proof["original_timestamps_checksum"]
            or obj["id"] != proof["archive_id"]
            or obj["checksum"] != obj["id"]
            or (obj["source"], obj["symbol"], obj["interval"], obj["provider"], obj["revision"])
            != ("vn", record["symbol"], "1D", proof["provider"], proof["revision"])
            or len(rows) != proof["rows"]
            or len(rows) != obj["row_count"]
            or {r.time for r in rows} != expected
            or record["id"]
            != digest(encode([record["symbol"], proof["year"], digest(raw), obj["id"]]))
        ):
            raise ValueError("Recovered data differs")
    except (KeyError, TypeError, ValueError, DataError) as exc:
        raise DataError("Invalid legacy daily recovery evidence") from exc
