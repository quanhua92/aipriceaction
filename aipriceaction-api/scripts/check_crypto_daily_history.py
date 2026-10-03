"""Capture old public/native crypto history and verify it against retained data.

Read-only: preserve original HTTP bodies and report discrepancies before any
archive publication. A successful report proves the observed snapshot, not
future provider immutability or availability before a symbol's first candle.
"""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, date_bounds
from aipriceaction_api.importing import json_rows
from aipriceaction_api.storage import Repository

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
FIELDS = ("open", "high", "low", "close", "volume")


def compare(reference, actual):
    reference = {row.time: row for row in reference}
    actual = {row.time: row for row in actual}
    missing = sorted(reference.keys() - actual.keys())
    changes = [
        {
            "time": stamp,
            "fields": {
                field: [getattr(reference[stamp], field), getattr(actual[stamp], field)]
                for field in FIELDS
                if getattr(reference[stamp], field) != getattr(actual[stamp], field)
            },
        }
        for stamp in sorted(reference.keys() & actual.keys())
        if any(getattr(reference[stamp], k) != getattr(actual[stamp], k) for k in FIELDS)
    ]
    return {"reference_rows": len(reference), "missing": missing, "changed": changes}


def save_response(root, name, response):
    response.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    path = root / f"{name}-{digest}.json"
    path.write_bytes(response.content)
    return {"path": str(path), "sha256": digest, "bytes": len(response.content)}


async def run(args):
    settings = Settings.from_env()
    repo = Repository(settings.database)
    start = date_bounds(args.start_date)
    end = date_bounds(args.end_date, end=True)
    if start > end or end >= date_bounds(datetime.now(UTC).strftime("%Y-%m-%d")):
        raise ValueError("Use an ordered range ending before the current UTC day")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"read_only": True, "start": start, "end": end, "symbols": [], "passed": False}
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    async with httpx.AsyncClient(timeout=60) as client:
        for symbol in SYMBOLS:
            entry = {"symbol": symbol, "passed": False, "captures": []}
            report["symbols"].append(entry)
            try:
                state = repo.state("crypto", symbol, "1D")
                if not state or state["provider"] != "binance" or state["status"] != "ready":
                    raise ValueError("Expected a ready retained Binance daily series")
                retained = repo.read("crypto", symbol, "1D", start, end)
                if not retained:
                    raise ValueError("No retained daily overlap")
                entry["state"] = state
                public = await client.get(
                    "https://api.aipriceaction.com/tickers",
                    params={
                        "symbol": symbol,
                        "mode": "crypto",
                        "interval": "1D",
                        "start_date": args.start_date,
                        "end_date": args.end_date,
                        "limit": 10000,
                        "format": "json",
                        "ma": "false",
                        "redis": "false",
                        "snap": "false",
                        "cache": "false",
                    },
                )
                entry["captures"].append(save_response(args.output, symbol + "-public", public))
                exported = json_rows(public.text, "crypto", symbol, "1D", "legacy-api", "audit")
                if not exported or len(exported) >= 10000:
                    raise ValueError("Empty or possibly truncated public snapshot")
                if any(not start <= row.time <= end for row in exported):
                    raise ValueError("Public snapshot outside requested bounds")
                native = {}
                before = end + 1
                for page in range(10):
                    response = await client.get(
                        "https://api.binance.com/api/v3/klines",
                        params={
                            "symbol": symbol,
                            "interval": "1d",
                            "endTime": before * 1000 - 1,
                            "limit": 1000,
                        },
                    )
                    entry["captures"].append(
                        save_response(args.output, f"{symbol}-native-{page}", response)
                    )
                    payload = response.json()
                    if not isinstance(payload, list):
                        raise ValueError("Invalid Binance response")
                    if not payload:
                        break
                    rows = [
                        Candle(
                            "crypto",
                            symbol,
                            "1D",
                            int(raw[0]) // 1000,
                            *[float(raw[i]) for i in range(1, 5)],
                            int(float(raw[5])),
                            "binance",
                            state["revision"],
                        )
                        for raw in payload
                    ]
                    for row in rows:
                        row.validate()
                        if row.time >= before:
                            raise ValueError("Native pagination failed to move backwards")
                        if start <= row.time <= end:
                            if row.time in native:
                                raise ValueError("Duplicate native timestamp")
                            native[row.time] = row
                    before = min(row.time for row in rows)
                    if before <= start or len(rows) < 1000:
                        break
                else:
                    raise ValueError("Native pagination budget exhausted")
                native_rows = sorted(native.values(), key=lambda row: row.time)
                if not native_rows:
                    raise ValueError("Empty native snapshot")
                gaps = [
                    [left.time, right.time]
                    for left, right in zip(native_rows, native_rows[1:], strict=False)
                    if right.time - left.time != 86400
                ]
                entry.update(
                    public_rows=len(exported),
                    native_rows=len(native_rows),
                    native_first=native_rows[0].time,
                    native_last=native_rows[-1].time,
                    continuity_gaps=gaps,
                    retained_vs_native=compare(retained, native_rows),
                    public_vs_native=compare(exported, native_rows),
                    native_missing_from_public=sorted(native.keys() - {r.time for r in exported}),
                )
                entry["native_basis_verified"] = (
                    not gaps
                    and native_rows[-1].time == end // 86400 * 86400
                    and not entry["retained_vs_native"]["missing"]
                    and not entry["retained_vs_native"]["changed"]
                    and not entry["public_vs_native"]["missing"]
                    and not entry["native_missing_from_public"]
                    and all(
                        set(change["fields"]) == {"volume"}
                        for change in entry["public_vs_native"]["changed"]
                    )
                    and repo.state("crypto", symbol, "1D") == state
                    and repo.read("crypto", symbol, "1D", start, end) == retained
                )
                checks = (entry["retained_vs_native"], entry["public_vs_native"])
                entry["passed"] = (
                    not gaps
                    and not entry["native_missing_from_public"]
                    and all(not c["missing"] and not c["changed"] for c in checks)
                    and repo.state("crypto", symbol, "1D") == state
                    and repo.read("crypto", symbol, "1D", start, end) == retained
                )
            except Exception as exc:
                # Network errors can contain proxy credentials; report only type.
                entry["error_type"] = type(exc).__name__
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            print(symbol, "PASS" if entry["passed"] else "FAIL", flush=True)
    report["passed"] = all(entry["passed"] for entry in report["symbols"])
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(report_path, flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2017-08-01")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
