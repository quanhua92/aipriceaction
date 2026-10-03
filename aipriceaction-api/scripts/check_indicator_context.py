"""Read-only comparison of indicator context in local and live legacy queries.

Preserve original responses. Separate candle differences from EMA seed decay;
this diagnostic does not mutate data or certify provider adjustment parity.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aipriceaction_api.config import Settings
from aipriceaction_api.storage import Repository

FIELDS = ("time", "open", "high", "low", "close", "volume")
PERIODS = (10, 20, 50, 100, 200)


def compare_context(tail, complete, ema):
    indexed = {row["time"]: row for row in complete}
    shared = [(row, indexed[row["time"]]) for row in tail if row["time"] in indexed]
    candle_changes = sum(any(a.get(k) != b.get(k) for k in FIELDS) for a, b in shared)
    result = {
        "tail_rows": len(tail),
        "complete_rows": len(complete),
        "shared_rows": len(shared),
        "missing_tail_dates": [r["time"] for r in tail if r["time"] not in indexed],
        "changed_shared_candles": candle_changes,
        "matches_complete_tail_dates": [r["time"] for r in tail]
        == [r["time"] for r in complete[-len(tail) :]],
        "indicators": {},
    }
    for period in PERIODS:
        field = f"ma{period}"
        deltas = [
            a[field] - b[field]
            for a, b in shared
            if a.get(field) is not None and b.get(field) is not None
        ]
        observed = {
            "comparable_rows": len(deltas),
            "missing_value_disagreements": sum(
                (a.get(field) is None) != (b.get(field) is None) for a, b in shared
            ),
            "max_absolute_delta": max(map(abs, deltas), default=None),
        }
        # Identical closes propagate an initial EMA discrepancy by (1-alpha).
        # Require every tail date and indicator to be present before checking.
        if (
            ema
            and not candle_changes
            and result["matches_complete_tail_dates"]
            and len(shared) == len(tail) == len(deltas) > 1
        ):
            factor = 1 - 2 / (period + 1)
            residual = max(
                abs(b - a * factor) for a, b in zip(deltas[:-1], deltas[1:], strict=True)
            )
            scale = max(abs(row[field]) for row in tail)
            tolerance = max(1e-8, scale * 1e-12)
            observed.update(
                seed_decay_max_residual=residual,
                seed_decay_tolerance=tolerance,
                follows_seed_decay=residual <= tolerance,
            )
        result["indicators"][field] = observed
    return result


def check(args):
    if urlsplit(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use a loopback replacement API")
    if args.report.exists() or args.report.with_suffix("").exists():
        raise ValueError("Use a new evidence report path")
    start = datetime.strptime(args.start_date, "%Y-%m-%d")
    end = datetime.strptime(args.end_date, "%Y-%m-%d")
    if not 0 <= (end - start).days <= 365:
        raise ValueError("Choose a historical range of at most one year")
    captures = args.report.with_suffix("")
    captures.mkdir(parents=True)
    repo = Repository(Settings.from_env().database)
    before = repo.epoch()
    collected = {}
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "main_publication": False,
        "source": args.source,
        "symbol": args.symbol,
        "results": [],
        "captures": [],
    }
    with httpx.Client(timeout=45) as client:
        for origin, base in (("local", args.url), ("legacy", "https://api.aipriceaction.com")):
            for ema in (False, True):
                exports = {}
                for shape in ("tail", "complete"):
                    params = {
                        "symbol": args.symbol,
                        "mode": "yahoo" if args.source == "sjc" else args.source,
                        "interval": "1D",
                        "end_date": args.end_date,
                        "limit": 20 if shape == "tail" else 10000,
                        "ma": "true",
                        "ema": str(ema).lower(),
                        "format": "json",
                        "cache": "false",
                        **({"start_date": args.start_date} if shape == "complete" else {}),
                        **({"redis": "false", "snap": "false"} if origin == "legacy" else {}),
                    }
                    response = client.get(base.rstrip("/") + "/tickers", params=params)
                    digest = hashlib.sha256(response.content).hexdigest()
                    path = captures / f"{origin}-{ema}-{shape}-{digest}.json"
                    path.write_bytes(response.content)
                    report["captures"].append(
                        {
                            "path": str(path),
                            "url": str(response.url),
                            "status": response.status_code,
                            "sha256": digest,
                        }
                    )
                    response.raise_for_status()
                    payload = response.json()
                    assert set(payload) == {args.symbol}, "Unexpected response symbol"
                    rows = payload[args.symbol]
                    assert isinstance(rows, list) and 0 < len(rows) < 10000
                    times = [row["time"] for row in rows]
                    assert times == sorted(set(times)), "Unordered or duplicate timestamps"
                    exports[shape] = rows
                    collected[origin, ema, shape] = rows
                report["results"].append(
                    {
                        "origin": origin,
                        "ema": ema,
                        **compare_context(exports["tail"], exports["complete"], ema),
                    }
                )
    report["cross_origin"] = [
        {
            "ema": ema,
            "shape": shape,
            **compare_context(collected["local", ema, shape], collected["legacy", ema, shape], ema),
        }
        for ema in (False, True)
        for shape in ("tail", "complete")
    ]
    report["main_epoch_unchanged"] = repo.epoch() == before
    assert report["main_epoch_unchanged"], "Publication changed during the comparison"
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "captures"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3001")
    parser.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start-date", default="2022-01-01")
    parser.add_argument("--end-date", default="2022-12-31")
    parser.add_argument("--report", type=Path, required=True)
    check(parser.parse_args())
