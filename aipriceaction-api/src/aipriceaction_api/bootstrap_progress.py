"""Append verified older VN hourly bootstrap rows without declaring completion."""

import asyncio
import hashlib
import math
import tempfile
import time
import uuid
from pathlib import Path

from .domain import Candle, DataError, completed_vn_sessions, cutoff
from .migration import encode
from .storage import COLUMNS


def snapshot(con, symbol, job_id):
    current = [
        Candle(**dict(row))
        for row in con.execute(
            "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1h' ORDER BY time",
            (symbol,),
        )
    ]
    staged = [
        Candle(**{key: row[key] for key in COLUMNS})
        for row in con.execute("SELECT * FROM staging WHERE job_id=? ORDER BY time", (job_id,))
    ]
    return current, staged


async def publish_bootstrap_progress(repo, providers, archive, symbol, execute=False):
    with repo.connect() as con:
        con.execute("BEGIN")
        jobs = con.execute(
            "SELECT * FROM jobs WHERE source='vn' AND symbol=? AND interval='1h' AND kind='bootstrap' AND status IN ('pending','running')",
            (symbol,),
        ).fetchall()
        if len(jobs) != 1:
            raise DataError("Progress publication requires one incomplete hourly bootstrap")
        job = dict(jobs[0])
        state_row = con.execute(
            "SELECT * FROM series WHERE source='vn' AND symbol=? AND interval='1h'", (symbol,)
        ).fetchone()
        state = dict(state_row) if state_row else None
        current, staged = snapshot(con, symbol, job["id"])
    if (
        not state
        or state["status"] != "ready"
        or state["provider"] not in providers.settings.vn_providers
        or (state["provider"], state["revision"]) != (job["provider"], job["revision"])
        or job["lease_until"] > int(time.time())
        or not current
        or not staged
    ):
        raise DataError("Progress requires an idle bootstrap on the current native hourly revision")
    identity = ("vn", symbol, "1h", state["provider"], state["revision"])
    for row in current + staged:
        row.validate()
        if (row.source, row.symbol, row.interval, row.provider, row.revision) != identity:
            raise DataError("Bootstrap progress contains mixed series identities")
    existing = {row.time: row for row in current}
    fields = ("open", "high", "low", "close", "volume")
    for row in staged:
        previous = existing.get(row.time)
        if previous and any(getattr(row, key) != getattr(previous, key) for key in fields):
            raise DataError("Staged bootstrap conflicts with published OHLCV")
    floor = max(job["floor"], cutoff(providers.settings.hourly_years))
    older = [row for row in staged if floor <= row.time < current[0].time]
    if not older:
        raise DataError("No retained older bootstrap rows to publish")
    completed = completed_vn_sessions()
    page = await asyncio.wait_for(
        providers.page("vn", symbol, "1h", count=200, provider=state["provider"]), timeout=90
    )
    if page.provider != state["provider"]:
        raise DataError("Bootstrap verification provider changed")
    incoming = [row for row in page.rows if row.time < completed and row.time <= current[-1].time]
    for row in incoming:
        row.validate()
        previous = existing.get(row.time)
        if (
            (row.source, row.symbol, row.interval, row.provider) != identity[:4]
            or previous is None
            or any(
                not math.isclose(getattr(row, key), getattr(previous, key), rel_tol=0, abs_tol=1e-8)
                for key in fields[:4]
            )
            or row.volume != previous.volume
        ):
            raise DataError("Fresh native provider disagrees with published bootstrap tail")
    published_completed = [row.time for row in current if row.time < completed]
    if (
        len(incoming) < 100
        or len({row.time // 86400 for row in incoming}) < 5
        or not published_completed
        or incoming[-1].time != published_completed[-1]
        or [row.time for row in incoming] != sorted({row.time for row in incoming})
    ):
        raise DataError(
            "Progress requires 100 exact hourly bars across five dates through the tail"
        )
    report = {
        "dry_run": not execute,
        "published": False,
        "source": "vn",
        "symbol": symbol,
        "interval": "1h",
        "job_id": job["id"],
        "state": state,
        "old_rows": len(current),
        "append_rows": len(older),
        "effective_floor": floor,
        "matched_rows": len(incoming),
        "observed_date_partitions": len({row.time // 86400 for row in incoming}),
        "native_proof": [row.record() for row in incoming],
        "complete_retention_proven": False,
        "job_remains_incomplete": True,
    }
    if not execute:
        return report
    owner = uuid.uuid4().hex
    if not repo.live_claim("vn", symbol, "1h", owner, lease=300):
        raise DataError("Another hourly update is active")
    try:
        report["beforeimage"] = archive.prepare(current)
        report["replacement_image"] = archive.prepare(older + current)
        raw = encode(report)
        digest = hashlib.sha256(raw).hexdigest()
        key = archive.settings.s3_prefix + "/evidence/bootstrap-progress/" + digest + ".json"
        with tempfile.TemporaryDirectory(dir=archive.settings.cache_dir) as tmp:
            path = Path(tmp) / "intent.json"
            path.write_bytes(raw)
            archive.store.put(key, path)
            if archive.store.read(key) != raw:
                raise DataError("Bootstrap progress evidence readback failed")
        report["intent_object_key"] = key
        with repo.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            actual_job = con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
            actual_state = con.execute(
                "SELECT * FROM series WHERE source='vn' AND symbol=? AND interval='1h'", (symbol,)
            ).fetchone()
            lease = con.execute(
                "SELECT owner,until FROM live_leases WHERE source='vn' AND symbol=? AND interval='1h'",
                (symbol,),
            ).fetchone()
            if (
                not actual_job
                or dict(actual_job) != job
                or not actual_state
                or dict(actual_state) != state
                or not lease
                or lease["owner"] != owner
                or lease["until"] <= int(time.time())
                or snapshot(con, symbol, job["id"]) != (current, staged)
            ):
                raise DataError("Bootstrap progress changed during verification")
            con.executemany(
                f"INSERT INTO candles({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
                [tuple(row.record()[key] for key in COLUMNS) for row in older],
            )
            repo.bump(con)
        report["published"] = True
        return report
    finally:
        repo.live_release("vn", symbol, "1h", owner)
