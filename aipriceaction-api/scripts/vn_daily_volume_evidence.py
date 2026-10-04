"""Field-scoped native volume witnesses; never synthesize or publish OHLC."""

import hashlib
import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from aipriceaction_api.domain import Candle, DataError, date_bounds


def captured(record, artifacts):
    successful = [c for c in record["captures"] if c["status"] == 200]
    if not successful:
        raise DataError("Source record lacks a successful immutable capture")
    for item in record["captures"]:
        raw = Path(item["path"]).read_bytes()
        if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise DataError("Source capture bytes changed since recording")
        artifacts[item["sha256"]] = item
    return Path(successful[-1]["path"]).read_bytes()


def project_vndirect_volumes(raw, symbol, first, before, count):
    """Validate exact daily timestamps/volumes independently of OHLC ranges."""
    try:
        body = json.loads(raw)
        fields = ("t", "o", "h", "l", "c", "v")
        if body.get("s") != "ok" or any(type(body.get(f)) is not list for f in fields):
            raise DataError("Invalid volume-only native envelope")
        arrays = [body[f] for f in fields]
        if len({len(a) for a in arrays}) != 1 or not arrays[0]:
            raise DataError("Invalid volume-only native arrays")
        rows = {}
        for stamp, o, h, low, close, volume in zip(*arrays, strict=True):
            if (
                type(stamp) is not int
                or stamp <= 0
                or stamp % 86400
                or isinstance(volume, bool)
                or not isinstance(volume, (int, float))
                or not math.isfinite(volume)
                or volume < 0
                or int(volume) != volume
                or stamp in rows
            ):
                raise DataError("Invalid or duplicate volume-only timestamp/volume")
            prices = (o, h, low, close)
            if any(
                isinstance(p, bool)
                or not isinstance(p, (int, float))
                or not math.isfinite(p)
                or p <= 0
                for p in prices
            ):
                raise DataError("Invalid volume-only native price fields")
            rows[stamp] = Candle(
                "vn", symbol, "1D", stamp, *(p * 1000 for p in prices), int(volume), "vndirect"
            )
        selected = [rows[t] for t in sorted(rows) if t < before][-count:]
        selected = [r for r in selected if r.time >= first]
        volumes, rejected = [], []
        for row in selected:
            volumes.append({"time": row.time, "volume": row.volume})
            try:
                row.validate()
            except DataError as exc:
                if str(exc) != "Invalid OHLC range":
                    raise
                rejected.append({"time": row.time, "error": str(exc)})
        if not volumes or not rejected:
            raise DataError("Volume-only evidence requires an observed OHLC range rejection")
        return {"kind": "vndirect_daily_volume_only", "rows": volumes, "ohlc_rejections": rejected}
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DataError("Invalid volume-only native values") from exc


def volume_only_witnesses(record, feed, symbol, artifacts=None):
    evidence = record.get("volume_only_evidence")
    if evidence is None:
        return None
    if (
        feed != "vndirect"
        or "Invalid OHLC range" not in record.get("error", "")
        or record.get("rows")
    ):
        raise DataError("Volume-only evidence must retain the rejected VNDirect OHLC record")
    first, end = date_bounds(record["start_date"]), date_bounds(record["end_date"], True) + 1
    count = min(10000, max(100, (end - first) // 86400 + 1))
    successful = [c for c in record["captures"] if c["status"] == 200]
    url = urlsplit(successful[-1].get("url", "")) if successful else urlsplit("")
    if (
        (url.scheme, url.netloc, url.path)
        != ("https", "dchart-api.vndirect.com.vn", "/dchart/history")
        or url.fragment
        or parse_qs(url.query)
        != {
            "symbol": [symbol],
            "resolution": ["D"],
            "to": [str(end - 1)],
            "from": [str(end - count * 3 * 86400)],
            "countback": [str(count)],
        }
    ):
        raise DataError("Volume-only capture does not match the native source/symbol/window")
    replayed = project_vndirect_volumes(
        captured(record, artifacts if artifacts is not None else {}),
        symbol,
        first,
        end,
        count,
    )
    if replayed != evidence:
        raise DataError("Volume-only witnesses differ from immutable native capture")
    return replayed["rows"]
