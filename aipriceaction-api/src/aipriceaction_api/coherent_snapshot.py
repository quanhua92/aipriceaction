"""Activate one frozen VN minute snapshot across hot and cold storage together.

This storage operation does not certify market accuracy or license live VCI
refresh. Callers must verify upstream evidence separately before using it.
"""

import hashlib
import json
import tempfile
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .adoption import checksum
from .domain import Candle, DataError
from .storage import COLUMNS


@dataclass
class Snapshot:
    state: dict
    hot: list[Candle]
    archives: list[dict]
    cold: dict[str, list[Candle]]


def capture(repo, archive, symbol):
    with repo.connect() as con:
        con.execute("BEGIN")
        state = con.execute(
            "SELECT * FROM series WHERE source='vn' AND symbol=? AND interval='1m'", (symbol,)
        ).fetchone()
        if not state or state["status"] != "ready":
            raise DataError("Coherent activation requires a readable existing VN minute series")
        hot = [
            Candle(**dict(r))
            for r in con.execute(
                "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1m' ORDER BY time",
                (symbol,),
            )
        ]
        objects = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM archives WHERE source='vn' AND symbol=? AND interval='1m' "
                "AND status IN ('published','pending_repair','historical_snapshot') ORDER BY id",
                (symbol,),
            )
        ]
    cold = {obj["id"]: archive.read(obj) for obj in objects}
    return Snapshot(dict(state), hot, objects, cold)


def publish(repo, archive, snapshot, replacement, floor, *, execute=False):
    """Prepare verified images, then atomically replace recent rows and archive pointers."""
    state = snapshot.state
    symbol = state["symbol"]
    rows = sorted(replacement, key=lambda row: row.time)
    if not rows or type(floor) is not int or floor % 86400:
        raise DataError("Coherent snapshot requires populated rows and a UTC retention floor")
    revision = rows[0].revision
    if revision == state["revision"] or len({r.time for r in rows}) != len(rows):
        raise DataError("Coherent replacement requires a new revision and unique timestamps")
    for row in rows:
        row.validate()
        if (row.source, row.symbol, row.interval, row.provider, row.revision) != (
            "vn",
            symbol,
            "1m",
            "vci",
            revision,
        ):
            raise DataError("Coherent replacement requires one pinned VCI minute revision")
    if set(snapshot.cold) != {obj["id"] for obj in snapshot.archives}:
        raise DataError("Original archive before-images are incomplete")
    observed = {r.time for r in snapshot.hot}
    for obj in snapshot.archives:
        previous = snapshot.cold[obj["id"]]
        if not previous or (len(previous), previous[0].time, previous[-1].time) != (
            obj["row_count"],
            obj["start"],
            obj["end"],
        ):
            raise DataError("Original archive before-image does not match its metadata")
        observed.update(r.time for r in previous)
    if observed - {r.time for r in rows}:
        raise DataError("Coherent snapshot drops an existing observed timestamp")
    hot = [r for r in rows if r.time >= floor]
    if not hot:
        raise DataError("Coherent snapshot has no retained minute observations")
    partitions, before_groups = defaultdict(list), defaultdict(list)
    for row in rows:
        if row.time < floor:
            partitions[datetime.fromtimestamp(row.time, UTC).strftime("%Y-%m")].append(row)
    for row in snapshot.hot:
        before_groups[(row.provider, row.revision)].append(row)
    result = {
        "symbol": symbol,
        "revision": revision,
        "published": False,
        "provider_handoff_licensed": False,
        "rows": len(rows),
        "hot_rows": len(hot),
        "cold_rows": len(rows) - len(hot),
        "floor": floor,
        "manifest_published": False,
        "preserved_observed_timestamps": len(observed),
        "original_state": state,
        "original_hot_checksum": checksum(snapshot.hot),
        "original_archives": snapshot.archives,
    }
    if not execute:
        return result
    owner = uuid.uuid4().hex
    if not repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", owner, lease=3600):
        raise DataError("Another archive writer is active")
    try:
        # prepare() uploads content-addressed objects and verifies complete
        # candle readback. No primary archive pointer is changed yet.
        result["before_hot_images"] = [archive.prepare(group) for group in before_groups.values()]
        objects = [archive.prepare(group) for group in partitions.values()]
        result["replacement_archives"] = objects
        result["replacement_hot_image"] = archive.prepare(hot)
        result["replacement_hot_checksum"] = checksum(hot)
        data = json.dumps(result, sort_keys=True, allow_nan=False).encode()
        key = (
            archive.settings.s3_prefix
            + "/evidence/coherent-snapshot/"
            + hashlib.sha256(data).hexdigest()
            + ".json"
        )
        with tempfile.TemporaryDirectory(dir=archive.settings.cache_dir) as tmp:
            path = Path(tmp) / "receipt.json"
            path.write_bytes(data)
            archive.store.put(key, path)
            if archive.store.read(key) != data:
                raise DataError("Coherent snapshot receipt readback differs")
        result["prepared_receipt_key"] = key
        with repo.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT * FROM series WHERE source='vn' AND symbol=? AND interval='1m'", (symbol,)
            ).fetchone()
            current_hot = [
                Candle(**dict(r))
                for r in con.execute(
                    "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1m' ORDER BY time",
                    (symbol,),
                )
            ]
            current_objects = [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM archives WHERE source='vn' AND symbol=? AND interval='1m' "
                    "AND status IN ('published','pending_repair','historical_snapshot') ORDER BY id",
                    (symbol,),
                )
            ]
            if (
                not current
                or dict(current) != state
                or current_hot != snapshot.hot
                or current_objects != snapshot.archives
            ):
                raise DataError(
                    "Original series or archive pointers changed during snapshot preparation"
                )
            now = int(time.time())
            if (
                con.execute(
                    "SELECT 1 FROM live_leases WHERE source='vn' AND symbol=? AND interval='1m' AND until>?",
                    (symbol, now),
                ).fetchone()
                or con.execute(
                    "SELECT 1 FROM jobs WHERE source='vn' AND symbol=? AND interval='1m' "
                    "AND status='running' AND lease_until>?",
                    (symbol, now),
                ).fetchone()
            ):
                raise DataError("A minute worker is active during snapshot activation")
            if con.execute(
                "SELECT 1 FROM quality WHERE source='vn' AND symbol=? AND interval='1m' "
                "AND kind='history_unavailable' AND resolved=0",
                (symbol,),
            ).fetchone():
                raise DataError(
                    "Coherent snapshot must not silently bypass unresolved history markers"
                )
            con.execute(
                "DELETE FROM candles WHERE source='vn' AND symbol=? AND interval='1m'", (symbol,)
            )
            con.executemany(
                f"INSERT INTO candles({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
                [tuple(row.record()[key] for key in COLUMNS) for row in hot],
            )
            con.execute(
                "UPDATE archives SET status='superseded' WHERE source='vn' AND symbol=? "
                "AND interval='1m' AND status IN ('published','pending_repair','historical_snapshot')",
                (symbol,),
            )
            for obj in objects:
                columns = tuple(obj)
                if con.execute(
                    "SELECT 1 FROM archives WHERE id=? OR object_key=?",
                    (obj["id"], obj["object_key"]),
                ).fetchone():
                    raise DataError("Coherent snapshot image already belongs to another activation")
                con.execute(
                    f"INSERT INTO archives({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    tuple(obj.values()),
                )
            con.execute(
                "UPDATE series SET provider='vci',revision=?,status='ready' WHERE source='vn' AND symbol=? AND interval='1m'",
                (revision, symbol),
            )
            con.execute(
                "UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=0,updated_at=? WHERE source='vn' AND symbol=? AND interval='1m' AND status NOT IN ('complete','cancelled')",
                (time.time_ns(), symbol),
            )
            con.execute(
                "UPDATE source_checks SET attempted_at_ns=?,outcome='handoff_required',error='Coherent snapshot activated; VCI provider handoff pending',successful_at_ns=NULL,provider='vci',revision=?,completed_before=NULL,completed_start=NULL,completed_end=NULL,completed_rows=NULL,provisional_rows=NULL WHERE source='vn' AND symbol=? AND interval='1m'",
                (time.time_ns(), revision, symbol),
            )
            result["published"] = True
            detail = json.dumps(result, sort_keys=True, allow_nan=False)
            con.execute(
                "INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen,resolved) VALUES ('vn',?,'1m','coherent_snapshot_activation',?,?,?,1)",
                (symbol, detail, now, now),
            )
            repo.bump(con)
        # If remote publication fails, local data and rollback images survive;
        # the receipt explicitly identifies the missing restore checkpoint.
        try:
            archive.manifest(repo.archives())
        except DataError as exc:
            result["manifest_error"] = str(exc)
        else:
            result["manifest_published"] = True
        return result
    finally:
        repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", owner)
