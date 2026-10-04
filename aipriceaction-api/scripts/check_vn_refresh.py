"""Rehearse configured native VN refreshes on an isolated populated SQLite copy.

Capture real provider responses. Never publish the candidate or process repair
jobs. Frozen snapshots and both disputed index hourly series stay excluded.
"""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import COLUMNS, Repository
from aipriceaction_api.workers import Worker
from scripts.stage_yahoo_daily_history import RecordingTransport


def vn_digest(path):
    digest = hashlib.sha256()
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as con:
        for row in con.execute(
            "SELECT * FROM candles WHERE source='vn' ORDER BY source,symbol,interval,time"
        ):
            digest.update(json.dumps(row, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


async def run(args):
    original = Settings.from_env()
    args.output.mkdir(parents=True, exist_ok=False)
    before, candidate = args.output / "before.sqlite3", args.output / "candidate.sqlite3"
    main = Repository(original.database)
    main.backup(before)
    Repository(before).backup(candidate)
    settings = replace(
        original,
        database=candidate,
        allow_direct=args.allow_direct,
        proxies=(),
    )
    repo = Repository(candidate)
    transport = RecordingTransport(args.output)
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "main_vn_before_sha256": vn_digest(before),
        "results": [],
        "excluded": [],
        "captures": [],
        "canonical_publication": False,
    }
    report_path = args.output / "report.json"

    def checkpoint():
        report["captures"] = transport.captures
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    entries = json.loads(settings.watchlist.read_text())["vn"]
    configured = {
        entry if isinstance(entry, str) else entry["symbol"]: (
            ["1D", "1h", "1m"]
            if isinstance(entry, str)
            else entry.get("intervals", ["1D", "1h", "1m"])
        )
        for entry in entries
    }
    checkpoint()
    for iv in ("1D", "1h", "1m"):
        selected = []
        for symbol, intervals in configured.items():
            state = repo.state("vn", symbol, iv)
            reason = (
                "interval not configured"
                if iv not in intervals
                else "index hourly replacement remains unapproved"
                if iv == "1h" and symbol in {"VNINDEX", "VN30"}
                else "no ready native state"
                if not state or state["status"] != "ready" or state["provider"] not in Providers.VN
                else None
            )
            if reason:
                report["excluded"].append({"symbol": symbol, "interval": iv, "reason": reason})
            else:
                selected.append(symbol)
        if not selected:
            continue
        worker = Worker(repo, settings, Providers(settings, transport=transport))
        # refresh closes its clients; each interval receives a new transport.
        try:
            results = await worker.refresh("vn", selected, iv)
            report["results"].extend(results)
            print(
                json.dumps(
                    {"interval": iv, "outcomes": dict(Counter(r["outcome"] for r in results))}
                ),
                flush=True,
            )
        finally:
            checkpoint()
        transport = RecordingTransport(args.output)
        # Preserve previous captures when replacing the closed transport.
        transport.captures = list(report["captures"])

    with repo.connect() as con:
        con.execute("ATTACH DATABASE ? AS original", (str(before.resolve()),))
        different = " OR ".join(f"a.{field} IS NOT b.{field}" for field in COLUMNS)
        protected = con.execute(
            f"SELECT COUNT(*) FROM original.candles b LEFT JOIN candles a "
            f"USING(source,symbol,interval,time) WHERE b.source<>'vn' "
            f"AND (a.time IS NULL OR {different})"
        ).fetchone()[0]
        assert protected == 0, "Unrelated candle versions changed"
        for table in ("archives", "snapshot_adoptions", "legacy_imports"):
            for left, right in ((table, f"original.{table}"), (f"original.{table}", table)):
                assert not con.execute(
                    f"SELECT * FROM {left} EXCEPT SELECT * FROM {right} LIMIT 1"
                ).fetchone(), f"Protected {table} changed"
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        report["vn_changed_ohlcv"] = con.execute(
            "SELECT COUNT(*) FROM original.candles b JOIN candles a "
            "USING(source,symbol,interval,time) WHERE b.source='vn' AND ("
            + " OR ".join(f"a.{f} IS NOT b.{f}" for f in ("open", "high", "low", "close", "volume"))
            + ")"
        ).fetchone()[0]
        report["candidate_records"] = con.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    report.update(
        finished_at=datetime.now(UTC).isoformat(),
        main_vn_after_sha256=vn_digest(original.database),
        protected_nonvn_versions_exact=True,
        protected_archives_and_certificates_exact=True,
        outcomes=dict(Counter(r["outcome"] for r in report["results"])),
    )
    assert report["main_vn_before_sha256"] == report["main_vn_after_sha256"], (
        "Canonical VN data changed"
    )
    report["passed"] = bool(report["results"]) and all(
        r["outcome"] == "succeeded" for r in report["results"]
    )
    checkpoint()
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("captures", "results", "excluded")}
        ),
        flush=True,
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-direct", action="store_true")
    asyncio.run(run(parser.parse_args()))
