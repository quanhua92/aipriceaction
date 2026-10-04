"""Run bounded real crypto scheduler cycles and verify populated before/after data.

Updates the configured database through the ordinary worker. Captures native
responses and checks consistent temporary backups; does not run archive maintenance.
"""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from aipriceaction_api.archive import sha256
from aipriceaction_api.config import Settings
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


async def run(args):
    with TemporaryDirectory(prefix="aipa-crypto-worker-") as temporary:
        return await _run(args, Path(temporary))


async def _run(args, databases):
    settings = Settings.from_env()
    repo = Repository(settings.database)
    args.output.mkdir(parents=True, exist_ok=False)
    before = databases / "before.sqlite3"
    repo.backup(before)
    report = {
        "passed": False,
        "started_at": datetime.now(UTC).isoformat(),
        "captures": [],
        "attempts": [],
        "cycles": args.cycles,
        "archive_daily": args.archive_daily,
    }
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")

    class CapturedProviders(Providers):
        async def request(self, provider, url, params=None, referer=None, data=None, vn=False):
            if provider != "binance" or url != "https://api.binance.com/api/v3/klines":
                raise ValueError("Expected only native Binance candle requests")
            payload = await super().request(provider, url, params, referer, data, vn)
            raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
            digest = hashlib.sha256(raw).hexdigest()
            path = args.output / f"binance-{digest}.json"
            path.write_bytes(raw)
            report["captures"].append({"path": str(path), "sha256": digest, "params": params})
            return payload

    class RecordedWorker(Worker):
        async def sync(self, entry, iv):
            result = await super().sync(entry, iv)
            with self.repo.connect() as con:
                row = con.execute(
                    "SELECT * FROM source_checks WHERE source=? AND symbol=? AND interval=?",
                    (entry["source"], entry["symbol"], iv),
                ).fetchone()
            report["attempts"].append({"rows": result, "check": dict(row) if row else None})
            print(json.dumps(report["attempts"][-1]), flush=True)
            return result

    try:
        await RecordedWorker(repo, settings, CapturedProviders(settings)).run(
            cycles=args.cycles, source="crypto", archive_daily=args.archive_daily
        )
    finally:
        repo.backup(databases / "after.sqlite3")
        report["finished_at"] = datetime.now(UTC).isoformat()
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    verify(args.output, databases)


def verify(output, databases=None):
    report_path = output / "report.json"
    report = json.loads(report_path.read_text())
    databases = databases or output
    before, after = databases / "before.sqlite3", databases / "after.sqlite3"
    if any(not a["check"] or a["check"]["outcome"] != "succeeded" for a in report["attempts"]):
        raise AssertionError("A scheduled update failed or has no recorded source check")
    with sqlite3.connect(after) as con:
        con.row_factory = sqlite3.Row
        con.execute("ATTACH DATABASE ? AS original", (str(before.resolve()),))
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise AssertionError("Populated SQLite integrity check failed")
        fields = ("open", "high", "low", "close", "volume", "provider", "revision", "updated_at")
        changed = " OR ".join(f"a.{field} IS NOT b.{field}" for field in fields)
        protected = con.execute(
            f"""SELECT COUNT(*) FROM original.candles b LEFT JOIN candles a
            USING(source,symbol,interval,time) WHERE b.source<>'crypto'
            AND (a.time IS NULL OR {changed})"""
        ).fetchone()[0]
        if protected:
            raise AssertionError("Noncrypto candles or versions changed")
        completed_changed = con.execute(
            """SELECT COUNT(*) FROM original.candles b
            JOIN original.source_checks s USING(source,symbol,interval)
            LEFT JOIN candles a USING(source,symbol,interval,time)
            WHERE b.source='crypto' AND b.time<s.completed_before AND
            (a.time IS NULL OR a.open IS NOT b.open OR a.high IS NOT b.high
            OR a.low IS NOT b.low OR a.close IS NOT b.close OR a.volume IS NOT b.volume
            OR a.provider IS NOT b.provider OR a.revision IS NOT b.revision)"""
        ).fetchone()[0]
        if completed_changed:
            raise AssertionError("Previously completed crypto records changed")
        verified_capture_rows = 0
        for capture in report["captures"]:
            raw = Path(capture["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != capture["sha256"]:
                raise AssertionError("Captured native response checksum changed")
            params = capture["params"]
            iv = {"1d": "1D", "1h": "1h", "1m": "1m"}[params["interval"]]
            # Exclude the final, potentially still forming candle in each page.
            for row in json.loads(raw)[:-1]:
                stored = con.execute(
                    "SELECT open,high,low,close,volume,provider FROM candles WHERE source='crypto' AND symbol=? AND interval=? AND time=?",
                    (params["symbol"], iv, int(row[0]) // 1000),
                ).fetchone()
                expected = (*(float(value) for value in row[1:5]), int(float(row[5])), "binance")
                if not stored or tuple(stored) != expected:
                    raise AssertionError(
                        "Stored completed candle differs from captured Binance response"
                    )
                verified_capture_rows += 1
        for table in ("series", "archives", "snapshot_adoptions", "legacy_imports", "jobs"):
            for left, right in ((table, f"original.{table}"), (f"original.{table}", table)):
                if con.execute(
                    f"SELECT * FROM {left} EXCEPT SELECT * FROM {right} LIMIT 1"
                ).fetchone():
                    raise AssertionError(f"Unexpected {table} change")
        report["series"] = []
        for state in con.execute("SELECT * FROM series WHERE source='crypto'"):
            identity = (state["source"], state["symbol"], state["interval"])
            first, last, count = con.execute(
                "SELECT MIN(time),MAX(time),COUNT(*) FROM candles WHERE source=? AND symbol=? AND interval=?",
                identity,
            ).fetchone()
            step = {"1D": 86400, "1h": 3600, "1m": 60}[state["interval"]]
            if count != (last - first) // step + 1:
                raise AssertionError("Stored crypto series has missing timestamps")
            successes = {
                a["check"]["attempted_at_ns"]
                for a in report["attempts"]
                if a["check"]
                and tuple(a["check"][k] for k in ("source", "symbol", "interval")) == identity
                and a["check"]["outcome"] == "succeeded"
            }
            column = {"1D": "next_1d", "1h": "next_1h", "1m": "next_1m"}[state["interval"]]
            initial_due = con.execute(
                f"SELECT {column} FROM original.tickers WHERE source=? AND symbol=?",
                identity[:2],
            ).fetchone()[0]
            due_at_start = initial_due <= datetime.fromisoformat(report["started_at"]).timestamp()
            if due_at_start and not successes or state["interval"] == "1m" and len(successes) < 2:
                raise AssertionError("Scheduler did not complete initial/repeated selected updates")
            report["series"].append(
                {
                    **dict(state),
                    "first": first,
                    "last": last,
                    "rows": count,
                    "successful_scheduled_attempts": len(successes),
                    "due_at_start": due_at_start,
                    "original_next_due_at": initial_due,
                }
            )
        report["records"] = con.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        report["verified_completed_capture_rows"] = verified_capture_rows
    report.update(
        passed=True,
        protected_noncrypto_unchanged=True,
        completed_crypto_unchanged=True,
        before_sha256=sha256(before),
        after_sha256=sha256(after),
    )
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"passed": True, "records": report["records"], "attempts": len(report["attempts"])}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=75)
    parser.add_argument(
        "--archive-daily", action="store_true", help="Exercise worker daily retention maintenance"
    )
    parser.add_argument(
        "--verify-only", action="store_true", help="Recheck saved backups and report; no worker run"
    )
    args = parser.parse_args()
    if args.cycles < 2:
        parser.error("Use at least two cycles; repeated minute updates usually require 75 or more")
    if args.verify_only:
        verify(args.output)
    else:
        asyncio.run(run(args))
