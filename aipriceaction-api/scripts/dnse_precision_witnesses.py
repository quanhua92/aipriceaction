"""Explicit observed binary32 corroboration, without changing source values."""

import json
import math
import struct
from urllib.parse import parse_qs, urlsplit

from aipriceaction_api.domain import DataError, date_bounds
from scripts.vn_daily_volume_evidence import captured


def integer(value):
    if isinstance(value, bool) or not math.isfinite(float(value)) or not float(value).is_integer():
        raise DataError("Precision witness requires integral native fields")
    return int(float(value))


def binary32(value):
    if type(value) is not int or not 2**24 < value < 2**53:
        raise DataError("Precision witness requires an exact integer above binary32 unit precision")
    return int(struct.unpack("!f", struct.pack("!f", value))[0])


def daily_volumes(record, feed, symbol, artifacts):
    first, end = date_bounds(record["start_date"]), date_bounds(record["end_date"], True) + 1
    count = min(10000, max(100, (end - first) // 86400 + 1))
    raw = captured(record, artifacts)
    successes = [c for c in record["captures"] if c["status"] == 200]
    url = urlsplit(successes[-1].get("url", ""))
    host, path, resolution = (
        ("dchart-api.vndirect.com.vn", "/dchart/history", "D")
        if feed == "vndirect"
        else ("api.dnse.com.vn", "/chart-api/v2/ohlcs/stock", "1D")
    )
    if (url.scheme, url.netloc, url.path) != ("https", host, path) or parse_qs(url.query) != {
        "symbol": [symbol],
        "resolution": [resolution],
        "from": [str(end - count * 3 * 86400)],
        "to": [str(end - 1)],
        "countback": [str(count)],
    }:
        raise DataError("Precision witness native request/source binding differs")
    body = json.loads(raw)
    arrays = [body[k] for k in ("t", "o", "h", "l", "c", "v")]
    if (
        any(type(a) is not list for a in arrays)
        or len({len(a) for a in arrays}) != 1
        or not arrays[0]
        or body.get("s") not in (None, "ok")
    ):
        raise DataError("Precision witness native arrays are invalid")
    rows = {}
    for t, v in zip(body["t"], body["v"], strict=True):
        t, v = integer(t), integer(v)
        offsets = {0} if feed == "vndirect" else {0, 7200}
        if t % 86400 not in offsets or v < 0:
            raise DataError("Precision witness native timestamp or volume is unverified")
        day = t // 86400 * 86400
        if day in rows:
            raise DataError("Precision witness has duplicate daily observations")
        rows[day] = v
    selected = {t: rows[t] for t in sorted(rows) if t < end}
    selected = dict(list(selected.items())[-count:])
    selected = {t: v for t, v in selected.items() if t >= first}
    if selected != {r["time"]: r["volume"] for r in record["rows"]} or len(selected) != len(
        record["rows"]
    ):
        raise DataError("Precision witness daily values differ from immutable capture")
    return selected


def cumulative_totals(staged, groups, artifacts):
    observations = {}
    for page in staged["pages"]:
        body = json.loads(captured(page, artifacts))
        body = body.get("data") if isinstance(body, dict) else body
        if (
            not isinstance(body, list)
            or len(body) != 1
            or body[0].get("symbol") != staged["symbol"]
        ):
            raise DataError("Precision witness minute source identity differs")
        body = body[0]
        if len(body["t"]) != len(body["accumulatedVolume"]):
            raise DataError("Precision witness cumulative arrays differ")
        for t, v in zip(body["t"], body["accumulatedVolume"], strict=True):
            t, v = integer(t), integer(v)
            if v < 0 or t % 60 or t in observations and observations[t] != v:
                raise DataError("Precision witness cumulative observations conflict")
            observations[t] = v
    totals = {}
    for day, rows in groups.items():
        rows = sorted(rows, key=lambda r: r["time"])
        if (
            any(r["time"] not in observations for r in rows)
            or observations[rows[0]["time"]] != rows[0]["volume"]
        ):
            raise DataError("Precision witness lacks a complete observed cumulative prefix")
        totals[day] = observations[rows[-1]["time"]]
    return totals


def derive_precision_witnesses(staged, groups, records, artifacts=None):
    artifacts = artifacts if artifacts is not None else {}
    symbol = staged["symbol"]
    if not staged["complete"] or staged["main_publication"] or staged["provider"] != "vci":
        raise DataError("Precision witness requires a complete isolated native candidate")
    exact = daily_volumes(records["vndirect"], "vndirect", symbol, artifacts)
    reported = daily_volumes(records["dnse"], "dnse", symbol, artifacts)
    cumulative = cumulative_totals(staged, groups, artifacts)
    dates = []
    for day, rows in sorted(groups.items()):
        total = sum(r["volume"] for r in rows)
        if day not in reported or reported[day] == total:
            continue
        if exact.get(day) != total or cumulative[day] != total or binary32(total) != reported[day]:
            raise DataError(
                "Rounded witness lacks exact VNDirect/cumulative corroboration or binary32 equivalence"
            )
        dates.append(
            {
                "day": day,
                "exact_volume": total,
                "reported_volume": reported[day],
                "exact_peer": "vndirect",
                "cumulative_total": cumulative[day],
            }
        )
    if not dates:
        raise DataError("Precision witness requires an observed representation difference")
    return {
        "kind": "observed_dnse_binary32_volume",
        "symbol": symbol,
        "dates": dates,
        "scope": "rounded_corroboration_only",
        "provider_storage_type_verified": False,
    }


def precision_witnesses(staged, groups, records, artifacts=None):
    declared = records["dnse"].get("volume_precision_evidence")
    if declared is None:
        return []
    expected = derive_precision_witnesses(staged, groups, records, artifacts)
    if declared != expected:
        raise DataError("Precision witness declaration differs from current immutable sources")
    return expected["dates"]
