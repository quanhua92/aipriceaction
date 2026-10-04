"""Replay narrowly scoped, corroborated VCI minute-volume correction evidence.

A proof licenses one volume substitution, not prices, missing observations,
provider adoption, or a change to an entire series' adjustment basis.
"""

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime

from .domain import Candle, DataError, completed_vn_sessions


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def proof_from_capture(raw, daily_witnesses, stamp, verified_at_ns, *, allow_multiple=False):
    """Extract one entire observed day from a frozen native response."""
    try:
        payload = json.loads(raw)
        payload = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(payload, list) or len(payload) != 1:
            raise DataError("Invalid VCI volume capture envelope")
        body = payload[0]
        arrays = [body[key] for key in ("t", "o", "h", "l", "c", "v", "accumulatedVolume")]
        if any(type(a) is not list for a in arrays) or len({len(a) for a in arrays}) != 1:
            raise DataError("Invalid VCI volume capture arrays")

        def integer(value):
            number = float(value)
            if isinstance(value, bool) or not number.is_integer():
                raise ValueError("Expected an integral value")
            return int(number)

        day = stamp // 86400 * 86400
        rows = []
        for t, o, h, low, c, v, cumulative in zip(*arrays, strict=True):
            t = integer(t)
            if t // 86400 * 86400 != day:
                continue
            row = Candle(
                "vn",
                body["symbol"],
                "1m",
                t,
                float(o),
                float(h),
                float(low),
                float(c),
                integer(v),
                "vci",
                "captured",
            ).validate()
            rows.append(asdict(row) | {"cumulative_volume": integer(cumulative)})
        rows.sort(key=lambda row: row["time"])
        proof = {
            "kind": "vci_cumulative_volume",
            "schema_version": 1,
            "symbol": body["symbol"],
            "day": day,
            "verified_at_ns": verified_at_ns,
            "source_capture_sha256": hashlib.sha256(raw).hexdigest(),
            "source_rows": rows,
            "source_rows_checksum": digest(rows),
            "daily_witnesses": daily_witnesses,
        }
        contradictions = [
            row["time"]
            for previous, row in zip(rows, rows[1:], strict=False)
            if row["time"] == previous["time"] + 60
            and row["cumulative_volume"] - previous["cumulative_volume"] != row["volume"]
        ]
        if allow_multiple and len(contradictions) > 1:
            proof.update(schema_version=2, target_time=stamp, correction_times=contradictions)
        corrected = validate_volume_proof(proof)
        if corrected.time != stamp:
            raise DataError("VCI volume capture contradicts a different requested minute")
        return proof
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DataError("Invalid VCI volume capture values") from exc


def validate_volume_proof(proof):
    """Return the single corrected candle after replaying every source observation."""
    try:
        if (
            proof["kind"] != "vci_cumulative_volume"
            or type(proof["schema_version"]) is not int
            or proof["schema_version"] not in (1, 2)
            or type(proof["day"]) is not int
            or proof["day"] % 86400
            or type(proof["verified_at_ns"]) is not int
            or proof["verified_at_ns"] <= 0
            or proof["day"]
            >= completed_vn_sessions(
                datetime.fromtimestamp(proof["verified_at_ns"] / 1_000_000_000, UTC)
            )
            or type(proof["source_rows"]) is not list
            or not 2 <= len(proof["source_rows"]) <= 500
            or type(proof["daily_witnesses"]) is not list
            or len(proof["daily_witnesses"]) != 2
            or proof["source_rows_checksum"] != digest(proof["source_rows"])
            or type(proof["source_capture_sha256"]) is not str
            or len(proof["source_capture_sha256"]) != 64
            or set(proof["source_capture_sha256"]) - set("0123456789abcdef")
        ):
            raise DataError("Invalid VCI volume proof envelope")
        rows, cumulative = [], []
        for raw in proof["source_rows"]:
            record = dict(raw)
            total = record.pop("cumulative_volume")
            row = Candle(**record).validate()
            if (
                (row.source, row.symbol, row.interval, row.provider)
                != ("vn", proof["symbol"], "1m", "vci")
                or row.time // 86400 * 86400 != proof["day"]
                or row.time % 60
                or type(total) is not int
                or total < 0
            ):
                raise DataError("Invalid VCI volume proof source identity or cumulative volume")
            rows.append(row)
            cumulative.append(total)
        times = [r.time for r in rows]
        if times != sorted(set(times)) or cumulative[0] != rows[0].volume:
            raise DataError("VCI volume proof lacks a unique complete cumulative prefix")
        corrections = []
        for index in range(1, len(rows)):
            increment = cumulative[index] - cumulative[index - 1]
            if increment < 0:
                raise DataError("VCI cumulative volume decreases inside the proof day")
            if rows[index].time == rows[index - 1].time + 60:
                if increment != rows[index].volume:
                    corrections.append(replace(rows[index], volume=increment))
            elif increment != rows[index].volume:
                # A larger gap may hide trades; never allocate their volume to
                # the next observed bar just to force a matching day total.
                raise DataError("VCI volume proof cannot allocate volume across a minute gap")
        if proof["schema_version"] == 1 and len(corrections) != 1:
            raise DataError(
                "VCI volume proof requires exactly one consecutive-minute contradiction"
            )
        if proof["schema_version"] == 2:
            declared = proof["correction_times"]
            if (
                type(declared) is not list
                or not 2 <= len(declared) <= 10
                or any(type(t) is not int for t in declared)
                or declared != sorted(set(declared))
                or declared != [r.time for r in corrections]
                or type(proof["target_time"]) is not int
                or proof["target_time"] not in declared
            ):
                raise DataError(
                    "VCI multi-correction proof must declare every contradiction and one exact target"
                )
            corrected = next(r for r in corrections if r.time == proof["target_time"])
        else:
            corrected = corrections[0]
        corrected_by_time = {r.time: r for r in corrections}
        total = sum(corrected_by_time.get(r.time, r).volume for r in rows)
        if total != cumulative[-1] or any(r.volume <= 0 for r in corrections):
            raise DataError("VCI corrected minute volumes do not reconcile with the session total")
        witnesses = [Candle(**raw).validate() for raw in proof["daily_witnesses"]]
        if {r.provider for r in witnesses} != {"vndirect", "dnse"} or any(
            (r.source, r.symbol, r.interval, r.time, r.volume)
            != ("vn", proof["symbol"], "1D", proof["day"], total)
            for r in witnesses
        ):
            raise DataError("VCI volume correction lacks matching VNDirect/DNSE daily witnesses")
        return corrected
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise DataError("Invalid VCI volume proof values") from exc


def apply_volume_proof(row, proof):
    """Correct an exact captured source candle; preserve all other fields."""
    corrected = validate_volume_proof(proof)
    raw = next(r for r in proof["source_rows"] if r["time"] == corrected.time)
    if (row.source, row.symbol, row.interval, row.provider, row.time) != (
        "vn",
        proof["symbol"],
        "1m",
        "vci",
        corrected.time,
    ) or any(
        getattr(row, field) != raw[field] for field in ("open", "high", "low", "close", "volume")
    ):
        raise DataError("VCI volume proof does not match the exact original source candle")
    return replace(row, volume=corrected.volume).validate()
