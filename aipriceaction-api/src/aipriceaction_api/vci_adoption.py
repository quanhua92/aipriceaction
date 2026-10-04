"""License same-provider VCI refresh after a frozen native snapshot is verified.

This exact overlap proof does not establish historical market truth or license
switching providers, revisions, or arbitrary corrections.
"""

import asyncio
import json
import time
from datetime import UTC, datetime

from .adoption import checksum
from .domain import Candle, DataError, completed_vn_sessions
from .vci_volume import validate_volume_proof

KIND = "native_vci_snapshot_overlap"


def validate_native_adoption(record):
    try:
        evidence = json.loads(record["evidence"])
        if (
            (record["source"], record["interval"], record["provider"], record["snapshot_provider"])
            != ("vn", "1m", "vci", "vci")
            or not record["symbol"]
            or not record["revision"]
            or evidence["kind"] != KIND
            or type(evidence["verified_at_ns"]) is not int
            or evidence["verified_at_ns"] <= 0
            or type(evidence["native_overlap"]) is not list
            or type(evidence["snapshot_overlap"]) is not list
            or not 1000 <= len(evidence["native_overlap"]) <= 2000
            or len(evidence["snapshot_overlap"]) != len(evidence["native_overlap"])
        ):
            raise DataError("Invalid native VCI handoff envelope")
        native = [Candle(**r).validate() for r in evidence["native_overlap"]]
        saved = [Candle(**r).validate() for r in evidence["snapshot_overlap"]]
        completed = completed_vn_sessions(
            datetime.fromtimestamp(evidence["verified_at_ns"] / 1_000_000_000, UTC)
        )
        times = [r.time for r in native]
        if (
            evidence["completed_before"] != completed
            or times != sorted(set(times))
            or times[-1] >= completed
            or evidence["matched_rows"] != len(native)
            or evidence["completed_sessions"] != len({t // 86400 for t in times})
            or evidence["completed_sessions"] < 5
            or type(evidence["snapshot_rows"]) is not int
            or evidence["snapshot_rows"] < len(native)
            or not evidence["snapshot_start"]
            <= times[0]
            <= times[-1]
            <= evidence["snapshot_end"]
            <= evidence["verified_at_ns"] // 1_000_000_000
            or (evidence["overlap_start"], evidence["overlap_end"]) != (times[0], times[-1])
            or checksum(native) != evidence["native_checksum"]
            or checksum(saved) != evidence["overlap_checksum"]
            or type(evidence["snapshot_checksum"]) is not str
            or len(evidence["snapshot_checksum"]) != 64
            or set(evidence["snapshot_checksum"]) - set("0123456789abcdef")
        ):
            raise DataError("Invalid native VCI handoff coverage or checksums")
        for current, previous in zip(native, saved, strict=True):
            if (
                (current.source, current.symbol, current.interval, current.provider)
                != ("vn", record["symbol"], "1m", "vci")
                or (
                    previous.source,
                    previous.symbol,
                    previous.interval,
                    previous.provider,
                    previous.revision,
                )
                != ("vn", record["symbol"], "1m", "vci", record["revision"])
                or not 0 < previous.updated_at <= evidence["verified_at_ns"]
                or current.time % 60
                or datetime.fromtimestamp(current.time, UTC).weekday() >= 5
                or any(
                    getattr(current, k) != getattr(previous, k)
                    for k in ("time", "open", "high", "low", "close", "volume")
                )
            ):
                raise DataError("Native VCI handoff lacks exact completed source candles")
        volume_proofs = evidence.get("volume_proofs", [])
        if type(volume_proofs) is not list or len(volume_proofs) > 20:
            raise DataError("Invalid native VCI volume proof list")
        seen = set()
        incoming = {r.time: r for r in native}
        for proof in volume_proofs:
            corrected = validate_volume_proof(proof)
            current = incoming.get(corrected.time)
            if (
                corrected.time in seen
                or current is None
                or corrected.symbol != record["symbol"]
                or any(
                    getattr(current, key) != getattr(corrected, key)
                    for key in ("open", "high", "low", "close", "volume")
                )
            ):
                raise DataError("Native VCI volume proof does not match its normalized witness")
            seen.add(corrected.time)
    except (KeyError, ValueError, TypeError, OverflowError, OSError) as exc:
        raise DataError("Invalid native VCI handoff values") from exc


async def adopt_native_snapshot(repo, providers, symbol, *, execute=False):
    if not providers.settings.vci_history_fallback:
        raise DataError("Native VCI handoff requires explicit fallback enablement", 400)
    state = repo.state("vn", symbol, "1m")
    rows = repo.read("vn", symbol, "1m")
    if not state or state["status"] != "ready" or state["provider"] != "vci" or not rows:
        raise DataError("Native VCI handoff requires a ready populated VCI minute snapshot", 400)
    if {(r.provider, r.revision) for r in rows} != {("vci", state["revision"])}:
        raise DataError("Native VCI handoff requires a single native snapshot revision")
    completed = completed_vn_sessions()
    published_completed = [r for r in rows if r.time < completed]
    if len(published_completed) < 1000:
        raise DataError("Native VCI snapshot has insufficient completed observations")
    page = await asyncio.wait_for(
        providers.page("vn", symbol, "1m", completed, count=2000, provider="vci"), timeout=90
    )
    if page.provider != "vci" or len(page.rows) < 1000:
        raise DataError("Native VCI handoff has insufficient source overlap")
    original = {r.time: r for r in rows}
    if any(r.time not in original for r in page.rows) or page.rows[-1].time != max(
        r.time for r in published_completed
    ):
        raise DataError("Native VCI handoff must match the complete published tail")
    saved = [original[r.time] for r in page.rows]
    # Reject missing observations within the source overlap, not just different
    # values at the timestamps the provider happened to return.
    if [r.time for r in rows if page.rows[0].time <= r.time <= page.rows[-1].time] != [
        r.time for r in page.rows
    ]:
        raise DataError("Native VCI handoff omits a published overlap observation")
    evidence = {
        "kind": KIND,
        "verified_at_ns": time.time_ns(),
        "completed_before": completed,
        "snapshot_rows": len(rows),
        "snapshot_start": rows[0].time,
        "snapshot_end": rows[-1].time,
        "snapshot_checksum": checksum(rows),
        "matched_rows": len(saved),
        "completed_sessions": len({r.time // 86400 for r in page.rows}),
        "overlap_start": saved[0].time,
        "overlap_end": saved[-1].time,
        "overlap_checksum": checksum(saved),
        "native_checksum": checksum(page.rows),
        "native_overlap": [r.record() for r in page.rows],
        "snapshot_overlap": [r.record() for r in saved],
        "volume_proofs": list(page.volume_proofs),
    }
    record = {
        "source": "vn",
        "symbol": symbol,
        "interval": "1m",
        "provider": "vci",
        "snapshot_provider": "vci",
        "revision": state["revision"],
        "evidence": json.dumps(evidence),
    }
    validate_native_adoption(record)
    if execute:
        with repo.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT * FROM series WHERE source='vn' AND symbol=? AND interval='1m'", (symbol,)
            ).fetchone()
            current_rows = [
                Candle(**dict(r))
                for r in con.execute(
                    "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1m' ORDER BY time",
                    (symbol,),
                )
            ]
            if (
                not current
                or dict(current) != state
                or checksum(current_rows) != evidence["snapshot_checksum"]
            ):
                raise DataError("Native VCI snapshot changed during handoff verification")
            if (
                con.execute(
                    "SELECT 1 FROM live_leases WHERE source='vn' AND symbol=? AND interval='1m' AND until>?",
                    (symbol, int(time.time())),
                ).fetchone()
                or con.execute(
                    "SELECT 1 FROM jobs WHERE source='vn' AND symbol=? AND interval='1m' AND status='running' AND lease_until>?",
                    (symbol, int(time.time())),
                ).fetchone()
            ):
                raise DataError("Native VCI handoff cannot race an active worker")
            con.execute(
                "INSERT OR REPLACE INTO snapshot_adoptions VALUES (?,?,?,?,?,?,?)",
                tuple(
                    record[k]
                    for k in (
                        "source",
                        "symbol",
                        "interval",
                        "revision",
                        "snapshot_provider",
                        "provider",
                        "evidence",
                    )
                ),
            )
            con.execute(
                "UPDATE quality SET resolved=1 WHERE source='vn' AND symbol=? AND interval='1m' AND kind IN ('vci_verification_pending','provider_handoff_pending')",
                (symbol,),
            )
            repo.bump(con)
        repo.record_volume_proofs(page.volume_proofs)
    return {
        "dry_run": not execute,
        "symbol": symbol,
        "revision": state["revision"],
        "evidence": evidence,
    }
