"""Verify complete staged minute history through a loopback replacement API.

Check every timestamp and OHLCV field, including derived minute intervals.
This proves parity with the supplied snapshot, not trading-calendar coverage.
"""

import argparse
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aipriceaction_api.archive import Archive
from aipriceaction_api.calculations import aggregate
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import parse_time
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository

FIELDS = ("open", "high", "low", "close", "volume")


def check(args):
    if urlsplit(args.api_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use a loopback replacement API for this rehearsal")
    settings = replace(
        Settings(),
        database=args.candidate / "sqlite.db",
        archive_backend="filesystem",
        object_dir=args.candidate / "objects",
        cache_dir=args.candidate / "verification-cache",
    )
    if not settings.database.is_file():
        raise ValueError("Candidate database does not exist")
    repo = Repository(settings.database)
    original = History(repo, Archive(repo, settings), settings).read(args.source, args.symbol, "1m")
    if not original:
        raise ValueError("Candidate minute history is empty")
    report = {
        "passed": False,
        "candidate": str(args.candidate),
        "source": args.source,
        "symbol": args.symbol,
        "calendar_coverage_proven": False,
        "intervals": [],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(base_url=args.api_url, timeout=60) as client:
            for iv in ("1m", "15m", "30m"):
                expected = original if iv == "1m" else aggregate(original, iv, args.source)
                by_time = {row.time: row for row in expected}
                assert len(by_time) == len(expected), "Duplicate candidate timestamps"
                first = original[0].time // 86400 * 86400
                last = original[-1].time
                seen, captures = set(), []
                while first <= last:
                    end = min(first + 6 * 86400 - 1, last // 86400 * 86400 + 86399)
                    selected = {
                        stamp: row for stamp, row in by_time.items() if first <= stamp <= end
                    }
                    if len(selected) >= 10000:
                        raise ValueError("Snapshot exceeds bounded API export capacity")
                    response = client.get(
                        "/tickers",
                        params={
                            "symbol": args.symbol,
                            "mode": "yahoo" if args.source == "sjc" else args.source,
                            "interval": iv,
                            "start_date": datetime.fromtimestamp(first, UTC).date().isoformat(),
                            "end_date": datetime.fromtimestamp(end, UTC).date().isoformat(),
                            "limit": 10000,
                            "ma": "false",
                            "format": "json",
                        },
                    )
                    response.raise_for_status()
                    rows = response.json().get(args.symbol, [])
                    stamps = [parse_time(row["time"]) for row in rows]
                    assert stamps == sorted(selected), (iv, first, "Timestamp mismatch")
                    for stamp, row in zip(stamps, rows, strict=True):
                        assert row["symbol"] == args.symbol
                        assert all(
                            row[field] == getattr(selected[stamp], field) for field in FIELDS
                        ), (
                            iv,
                            stamp,
                            "OHLCV mismatch",
                        )
                    assert not seen.intersection(stamps), "Overlapping export batches"
                    seen.update(stamps)
                    captures.append(
                        {
                            "url": str(response.url),
                            "rows": len(rows),
                            "sha256": hashlib.sha256(response.content).hexdigest(),
                        }
                    )
                    first = end + 1
                assert seen == set(by_time), "Incomplete full-history verification"
                report["intervals"].append(
                    {"interval": iv, "rows": len(seen), "captures": captures, "exact": True}
                )
        report["quote_events"] = sum(row.time % 60 != 0 for row in original)
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": True, "report": str(args.report), "rows": len(original)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:3001")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.symbol = args.symbol.upper()
    check(args)
