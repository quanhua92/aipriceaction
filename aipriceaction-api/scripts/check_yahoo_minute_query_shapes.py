"""Capture legacy-range and explicitly dated Yahoo intraday query shapes.

Read-only diagnostic: compare each response with retained candles without
licensing ingestion. The range profile is relative to Yahoo's current data;
it need not cover the explicit day. Rust extraction semantics are replayed
only for comparison, including null defaults and legacy timestamp labels.
"""

import argparse
import asyncio
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

try:
    from scripts.stage_yahoo_daily_history import freeze
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from stage_yahoo_daily_history import freeze
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import date_bounds
from aipriceaction_api.storage import Repository


async def run(args):
    root = args.output
    first = date_bounds(args.date)
    today = date_bounds(datetime.now(UTC).date().isoformat())
    if first >= today:
        raise ValueError("Explicit day must be before the current UTC day")
    root.mkdir(parents=True, exist_ok=False)
    repo = Repository(Settings.from_env().database)
    interval = args.interval
    step = {"1m": 60, "1h": 3600}[interval]
    saved = {r.time: r for r in repo.read("yahoo", args.symbol, interval)}
    if not saved:
        raise ValueError("Expected a retained Yahoo intraday snapshot")
    epoch = repo.epoch()
    before = int(datetime.now(UTC).timestamp())
    report = {
        "read_only": True,
        "profiles": [],
        "symbol": args.symbol,
        "interval": interval,
        "explicit_day": args.date,
        "native_handoff": False,
        "null_policy": "Replay Rust extraction: omit null close, default other null fields to zero, normalize legacy interval labels; diagnostic only",
        "canonical_epoch_before": epoch,
    }
    profiles = [
        (
            "legacy-range",
            {
                "range": "1d" if interval == "1m" else "5d",
                "interval": interval,
                "events": "div|split|capitalGains",
                "symbol": args.symbol.removesuffix(":US"),
            },
        ),
        (
            "dated-day",
            {
                "period1": first,
                "period2": first + 86400,
                "interval": interval,
                "events": "div,splits",
            },
        ),
        (
            "native-six-day" if interval == "1m" else "native-forty-day",
            {
                "period1": before - (6 if interval == "1m" else 40) * 86400,
                "period2": before,
                "interval": "1m" if interval == "1m" else "60m",
                "events": "div,splits",
            },
        ),
    ]
    path = root / "report.json"
    try:
        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": "Mozilla/5.0 AIPriceAction/0.1"}
        ) as client:
            for name, params in profiles:
                response = await client.get(
                    "https://query1.finance.yahoo.com/v8/finance/chart/"
                    + quote(args.symbol.removesuffix(":US"), safe=""),
                    params=params,
                )
                capture = freeze(root, name, response.content) | {
                    "url": str(response.url),
                    "status": response.status_code,
                }
                entry = {"profile": name, "capture": capture}
                report["profiles"].append(entry)
                if response.status_code != 200:
                    continue
                result = response.json()["chart"]["result"][0]
                q = result["indicators"]["quote"][0]
                stamps = result.get("timestamp") or []
                entry["outside_requested_bounds"] = (
                    [t for t in stamps if not params["period1"] <= t < params["period2"]]
                    if "period1" in params
                    else []
                )
                if any(
                    not isinstance(q.get(k), list) or len(q[k]) != len(stamps)
                    for k in ("open", "high", "low", "close", "volume")
                ):
                    raise ValueError(
                        "Inconsistent Yahoo arrays; legacy extraction would reject them"
                    )
                rows = []
                nulls = 0
                unaligned = []
                for i, t in enumerate(stamps):
                    if t % step:
                        unaligned.append(t)
                    values = {k: q[k][i] for k in ("open", "high", "low", "close", "volume")}
                    if values["close"] is None:
                        nulls += 1
                        continue
                    # Replay Rust crate's extraction and worker timestamp labels.
                    values = {k: v if v is not None else 0 for k, v in values.items()}
                    rows.append({"time": t // step * step, **values})
                missing = []
                changed = []
                material_changed = []
                for row in rows:
                    old = saved.get(row["time"])
                    if old is None:
                        missing.append(row["time"])
                    elif any(
                        getattr(old, k) != row[k]
                        for k in ("open", "high", "low", "close", "volume")
                    ):
                        changed.append(
                            {
                                "time": row["time"],
                                "fields": {
                                    k: [getattr(old, k), row[k]]
                                    for k in ("open", "high", "low", "close", "volume")
                                    if getattr(old, k) != row[k]
                                },
                            }
                        )
                        fields = {
                            k: values
                            for k, values in changed[-1]["fields"].items()
                            if k == "volume"
                            or not math.isclose(values[0], values[1], rel_tol=0, abs_tol=1e-8)
                        }
                        if fields:
                            material_changed.append({"time": row["time"], "fields": fields})
                entry.update(
                    rows=len(rows),
                    first=rows[0]["time"] if rows else None,
                    last=rows[-1]["time"] if rows else None,
                    null_close_rows=nulls,
                    unaligned_raw_timestamps=unaligned,
                    missing_from_public=missing,
                    changed_from_public=changed,
                    material_changed_from_public=material_changed,
                    normalized=freeze(
                        root, name + "-normalized", json.dumps(rows, sort_keys=True).encode()
                    ),
                )
        assert repo.epoch() == epoch
        assert {r.time: r for r in repo.read("yahoo", args.symbol, interval)} == saved
        report["retained_data_unchanged"] = True

    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        report["canonical_epoch_after"] = repo.epoch()
        path.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(path),
                "profiles": [
                    {k: r.get(k) for k in ("profile", "rows", "null_close_rows", "first", "last")}
                    | {
                        "missing": len(r.get("missing_from_public", [])),
                        "changed": len(r.get("changed_from_public", [])),
                        "material_changed": len(r.get("material_changed_from_public", [])),
                    }
                    for r in report["profiles"]
                ],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", choices=("1m", "1h"), default="1m")
    parser.add_argument(
        "--date", required=True, help="Completed UTC day for the explicitly dated profile"
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.symbol = args.symbol.upper()
    asyncio.run(run(args))
