"""A bounded, replayable bridge from a frozen daily API snapshot to live updates."""

import asyncio
import hashlib
import json
import math
import time
from datetime import UTC, datetime

from .adoption import checksum
from .domain import Candle, DataError, completed_vn_sessions


def archive_checksum(objects):
    return hashlib.sha256(
        json.dumps(objects, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_daily_adoption(record):
    evidence = json.loads(record["evidence"])
    if (
        record["source"] != "vn"
        or record["interval"] != "1D"
        or record["snapshot_provider"] != "legacy-api"
        or record["provider"] not in {"vps", "vndirect", "dnse"}
        or not record["symbol"]
        or not record["revision"]
        or evidence["kind"] != "exact_daily_snapshot_overlap"
        or type(evidence["verified_at_ns"]) is not int
        or evidence["verified_at_ns"] <= 0
        or type(evidence["snapshot"]) is not list
        or not 40 <= len(evidence["snapshot"]) <= 2000
        or type(evidence["provider_candles"]) is not list
        or len(evidence["provider_candles"]) != 40
        or type(evidence["archives"]) is not list
    ):
        raise DataError("Invalid daily snapshot adoption evidence")
    rows = [Candle(**raw).validate() for raw in evidence["snapshot"]]
    incoming = [Candle(**raw).validate() for raw in evidence["provider_candles"]]
    completed = completed_vn_sessions(
        datetime.fromtimestamp(evidence["verified_at_ns"] / 1_000_000_000, UTC)
    )
    expected = [r for r in rows if r.time < completed][-40:]
    if (
        evidence["completed_before"] != completed
        or evidence["matched_rows"] != 40
        or evidence["completed_sessions"] != 40
        or evidence["snapshot_rows"] != len(rows)
        or [r.time for r in rows] != sorted({r.time for r in rows})
        or [r.time for r in incoming] != [r.time for r in expected]
        or evidence["snapshot_start"] != rows[0].time
        or evidence["snapshot_end"] != rows[-1].time
        or rows[-1].time > evidence["verified_at_ns"] // 1_000_000_000
        or evidence["overlap_start"] != incoming[0].time
        or evidence["overlap_end"] != incoming[-1].time
        or evidence["snapshot_checksum"] != checksum(rows)
        or evidence["overlap_checksum"] != checksum(incoming)
        or evidence["archive_checksum"] != archive_checksum(evidence["archives"])
    ):
        raise DataError("Daily snapshot coverage or checksum differs")
    for row in rows:
        if (
            (row.source, row.symbol, row.interval, row.provider, row.revision)
            != ("vn", record["symbol"], "1D", "legacy-api", record["revision"])
            or row.time % 86400
            or not 0 < row.updated_at <= evidence["verified_at_ns"]
        ):
            raise DataError("Invalid original daily snapshot")
    for current, original in zip(incoming, expected, strict=True):
        if (
            (current.source, current.symbol, current.interval, current.provider)
            != ("vn", record["symbol"], "1D", record["provider"])
            or current.volume != original.volume
            or any(
                not math.isclose(getattr(current, k), getattr(original, k), rel_tol=0, abs_tol=1e-8)
                for k in ("open", "high", "low", "close")
            )
        ):
            raise DataError("Provider disagrees with completed daily snapshot OHLCV")
    for obj in evidence["archives"]:
        if (obj["source"], obj["symbol"], obj["interval"], obj["provider"], obj["revision"]) != (
            "vn",
            record["symbol"],
            "1D",
            "legacy-api",
            record["revision"],
        ) or obj["status"] != "published":
            raise DataError("Daily snapshot archives have an incompatible basis")


async def adopt_daily_snapshot(repo, providers, symbol, provider, execute=False):
    if provider not in providers.settings.vn_providers:
        raise DataError("Daily adoption requires a configured VN provider", 400)
    state = repo.state("vn", symbol, "1D")
    if not state or state["status"] != "ready" or state["provider"] != "legacy-api":
        raise DataError("Daily adoption requires a ready legacy-api snapshot", 400)
    rows = repo.read("vn", symbol, "1D")
    objects = repo.archives("vn", symbol, "1D")
    completed = completed_vn_sessions()
    expected = [r for r in rows if r.time < completed][-40:]
    if len(expected) != 40:
        raise DataError("Daily adoption requires 40 completed snapshot candles")
    page = await asyncio.wait_for(
        providers.page("vn", symbol, "1D", before=completed, count=40, provider=provider),
        timeout=90,
    )
    if page.provider != provider:
        raise DataError("Provider identity changed during daily verification")
    evidence = {
        "kind": "exact_daily_snapshot_overlap",
        "matched_rows": 40,
        "completed_sessions": 40,
        "completed_before": completed,
        "snapshot_rows": len(rows),
        "snapshot_start": rows[0].time,
        "snapshot_end": rows[-1].time,
        "snapshot_checksum": checksum(rows),
        "overlap_start": page.rows[0].time if page.rows else None,
        "overlap_end": page.rows[-1].time if page.rows else None,
        "overlap_checksum": checksum(page.rows),
        "archive_checksum": archive_checksum(objects),
        "verified_at_ns": time.time_ns(),
        "snapshot": [r.record() for r in rows],
        "provider_candles": [r.record() for r in page.rows],
        "archives": objects,
        "scope": "Forty exact completed daily candles through the snapshot tail permit append; original history remains frozen, without inferred scaling or lifetime adjustment-policy claims",
    }
    record = dict(
        source="vn",
        symbol=symbol,
        interval="1D",
        revision=state["revision"],
        snapshot_provider="legacy-api",
        provider=provider,
        evidence=json.dumps(evidence, sort_keys=True),
    )
    repo.validate_adoption(record)
    if execute:
        with repo.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT * FROM series WHERE source='vn' AND symbol=? AND interval='1D'", (symbol,)
            ).fetchone()
            published = con.execute(
                "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1D' ORDER BY time",
                (symbol,),
            ).fetchall()
            if (
                not current
                or dict(current) != state
                or checksum([Candle(**dict(r)) for r in published]) != evidence["snapshot_checksum"]
                or archive_checksum(repo.archives("vn", symbol, "1D"))
                != evidence["archive_checksum"]
            ):
                raise DataError("Daily snapshot changed during adoption verification")
            now = int(time.time())
            if (
                con.execute(
                    "SELECT 1 FROM live_leases WHERE source='vn' AND symbol=? AND interval='1D' AND until>?",
                    (symbol, now),
                ).fetchone()
                or con.execute(
                    "SELECT 1 FROM jobs WHERE source='vn' AND symbol=? AND interval='1D' AND status IN ('pending','running') AND lease_until>?",
                    (symbol, now),
                ).fetchone()
            ):
                raise DataError("A daily worker or recovery job is active")
            con.execute(
                "INSERT INTO snapshot_adoptions VALUES (?,?,?,?,?,?,?)", tuple(record.values())
            )
            con.execute(
                "UPDATE series SET provider=? WHERE source='vn' AND symbol=? AND interval='1D'",
                (provider, symbol),
            )
            for job in con.execute(
                "SELECT id FROM jobs WHERE source='vn' AND symbol=? AND interval='1D' AND kind IN ('bootstrap','repair') AND status NOT IN ('complete','cancelled')",
                (symbol,),
            ).fetchall():
                con.execute("DELETE FROM staging WHERE job_id=?", (job["id"],))
                con.execute(
                    "UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=0 WHERE id=?",
                    (job["id"],),
                )
            con.execute("UPDATE tickers SET next_1d=0 WHERE source='vn' AND symbol=?", (symbol,))
            repo.bump(con)
    return {
        "dry_run": not execute,
        "provider": provider,
        "revision": state["revision"],
        "evidence": evidence,
    }
