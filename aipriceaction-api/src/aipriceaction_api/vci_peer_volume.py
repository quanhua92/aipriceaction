"""Replay a single volume correction backed by full native minute peers."""

import hashlib
import json
import math
from dataclasses import asdict, replace
from datetime import UTC, datetime

from .domain import Candle, DataError, completed_vn_sessions


def peer_proof_from_capture(raw, daily, peers, stamp, verified_at_ns, witness_hashes):
    from .vci_volume import digest

    body = json.loads(raw)
    body = body.get("data") if isinstance(body, dict) else body
    if not isinstance(body, list) or len(body) != 1:
        raise DataError("Invalid peer-proof native envelope")
    body = body[0]
    arrays = [body[k] for k in ("t", "o", "h", "l", "c", "v", "accumulatedVolume")]
    if any(type(a) is not list for a in arrays) or len({len(a) for a in arrays}) != 1:
        raise DataError("Invalid peer-proof native arrays")
    day = stamp // 86400 * 86400
    rows = []
    for t, o, h, low, c, v, total in zip(*arrays, strict=True):
        if any(
            isinstance(n, bool) or not math.isfinite(float(n)) or not float(n).is_integer()
            for n in (t, v, total)
        ):
            raise DataError("Invalid peer-proof native integer fields")
        if int(float(t)) // 86400 * 86400 == day:
            row = Candle(
                "vn",
                body["symbol"],
                "1m",
                int(float(t)),
                float(o),
                float(h),
                float(low),
                float(c),
                int(float(v)),
                "vci",
                "captured",
            ).validate()
            rows.append(asdict(row) | {"cumulative_volume": int(float(total))})
    rows.sort(key=lambda r: r["time"])
    proof = {
        "kind": "vci_peer_minute_volume",
        "schema_version": 1,
        "symbol": body["symbol"],
        "day": day,
        "target_time": stamp,
        "verified_at_ns": verified_at_ns,
        "source_capture_sha256": hashlib.sha256(raw).hexdigest(),
        "source_rows": rows,
        "source_rows_checksum": digest(rows),
        "daily_witnesses": daily,
        "minute_witnesses": peers,
        "minute_witnesses_checksum": digest(peers),
        "witness_capture_sha256": witness_hashes,
    }
    validate_peer_volume_proof(proof)
    return proof


def validate_peer_volume_proof(proof):
    from .vci_volume import digest

    try:
        hashes = [proof["source_capture_sha256"], *proof["witness_capture_sha256"]]
        if (
            proof["kind"] != "vci_peer_minute_volume"
            or type(proof["schema_version"]) is not int
            or proof["schema_version"] != 1
            or type(proof["day"]) is not int
            or proof["day"] % 86400
            or type(proof["target_time"]) is not int
            or type(proof["verified_at_ns"]) is not int
            or proof["verified_at_ns"] <= 0
            or proof["day"]
            >= completed_vn_sessions(datetime.fromtimestamp(proof["verified_at_ns"] / 1e9, UTC))
            or type(proof["witness_capture_sha256"]) is not list
            or len(hashes) != 5
            or any(
                type(h) is not str or len(h) != 64 or set(h) - set("0123456789abcdef")
                for h in hashes
            )
            or type(proof["source_rows"]) is not list
            or not 2 <= len(proof["source_rows"]) <= 500
            or proof["source_rows_checksum"] != digest(proof["source_rows"])
            or proof["minute_witnesses_checksum"] != digest(proof["minute_witnesses"])
        ):
            raise DataError("Invalid native peer volume proof envelope")
        rows, totals = [], []
        for raw in proof["source_rows"]:
            record = dict(raw)
            total = record.pop("cumulative_volume")
            row = Candle(**record).validate()
            if (
                (row.source, row.symbol, row.interval, row.provider)
                != ("vn", proof["symbol"], "1m", "vci")
                or type(row.time) is not int
                or row.time % 60
                or row.time // 86400 * 86400 != proof["day"]
                or type(row.volume) is not int
                or type(total) is not int
                or total < 0
            ):
                raise DataError("Invalid native peer proof source identity")
            rows.append(row)
            totals.append(total)
        times = [r.time for r in rows]
        if times != sorted(set(times)) or totals[0] != rows[0].volume:
            raise DataError("Native peer proof lacks a complete unique cumulative prefix")
        witnesses = proof["minute_witnesses"]
        if type(witnesses) is not dict or set(witnesses) != {"vndirect", "dnse"}:
            raise DataError("Native peer proof needs both minute providers")
        peers = {}
        for feed, records in witnesses.items():
            if type(records) is not list or len(records) != len(rows):
                raise DataError("Native peer proof has incomplete minute witnesses")
            peer = [Candle(**r).validate() for r in records]
            if [r.time for r in peer] != times or any(
                (r.source, r.symbol, r.interval, r.provider) != ("vn", proof["symbol"], "1m", feed)
                or type(r.volume) is not int
                for r in peer
            ):
                raise DataError("Native peer proof minute identities differ")
            peers[feed] = peer
        changed = []
        for index, row in enumerate(rows):
            left, right = peers["vndirect"][index], peers["dnse"][index]
            if left.volume != right.volume:
                raise DataError("Native minute peer volumes disagree")
            if left.volume != row.volume:
                changed.append(index)
        if len(changed) != 1 or rows[changed[0]].time != proof["target_time"]:
            raise DataError("Native peer proof must license one exact existing target")
        index = changed[0]
        original = rows[index]
        peer = peers["vndirect"][index]
        if not all(
            math.isclose(getattr(original, f), getattr(peer, f), rel_tol=0, abs_tol=1e-8)
            for f in ("open", "high", "low", "close")
        ):
            raise DataError("Native peer correction target price basis differs")
        corrected = replace(original, volume=peer.volume)
        delta = corrected.volume - original.volume
        if delta <= 0 or index == 0 or index + 1 >= len(rows):
            raise DataError("Native peer proof requires a positive witnessed residual")
        for i in range(1, len(rows)):
            increment = totals[i] - totals[i - 1]
            if i == index + 1:
                if rows[i].time <= rows[i - 1].time + 60 or increment != rows[i].volume + delta:
                    raise DataError(
                        "Native peer residual does not match the observed following gap"
                    )
            elif increment != rows[i].volume:
                raise DataError("Native peer proof has another cumulative discrepancy")
        total = sum(r.volume for r in rows) + delta
        daily = [Candle(**r).validate() for r in proof["daily_witnesses"]]
        if (
            total != totals[-1]
            or len(daily) != 2
            or {r.provider for r in daily} != {"vndirect", "dnse"}
            or any(
                (r.source, r.symbol, r.interval, r.time, r.volume)
                != ("vn", proof["symbol"], "1D", proof["day"], total)
                for r in daily
            )
        ):
            raise DataError("Native peer correction lacks exact cumulative/daily corroboration")
        return corrected.validate()
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise DataError("Invalid native peer volume proof values") from exc
