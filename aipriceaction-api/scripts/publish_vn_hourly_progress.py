"""Preview or publish guarded VN hourly progress, retaining jobs and backups.

Only configured watchlist series with older staging are attempted. Each series
uses the existing provider/snapshot guards; a refusal leaves that series alone.
Execution is restricted to local RustFS and takes populated before/after backups.
"""

import argparse
import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.archive import Archive
from aipriceaction_api.bootstrap_progress import publish_bootstrap_progress
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import COLUMNS, Repository

try:
    from scripts.stage_yahoo_daily_history import RecordingTransport
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from stage_yahoo_daily_history import RecordingTransport


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def run(args):
    settings = replace(Settings.from_env(), allow_direct=args.allow_direct)
    if args.execute and settings.s3_endpoint != "http://127.0.0.1:9100":
        raise ValueError("Batch execution requires local RustFS")
    selected = {
        entry["symbol"] if isinstance(entry, dict) else entry
        for entry in json.loads(settings.watchlist.read_text())["vn"]
    }
    requested = {symbol.upper() for symbol in args.symbol or []}
    if requested - selected:
        raise ValueError("Symbols must belong to the configured VN watchlist")
    repo = Repository(settings.database)
    archive = Archive(repo, settings)
    with repo.connect() as con:
        candidates = [
            row[0]
            for row in con.execute(
                "SELECT DISTINCT j.symbol FROM jobs j JOIN staging s ON s.job_id=j.id "
                "WHERE j.source='vn' AND j.interval='1h' AND j.kind='bootstrap' "
                "AND j.status IN ('pending','running') AND s.time < "
                "(SELECT MIN(c.time) FROM candles c WHERE c.source=j.source "
                "AND c.symbol=j.symbol AND c.interval=j.interval) ORDER BY j.symbol"
            )
            if row[0] in selected and (not requested or row[0] in requested)
        ]
    args.output.mkdir(parents=True, exist_ok=False)
    report_path = args.output / "report.json"
    before, after = args.output / "before.sqlite3", args.output / "after.sqlite3"
    report = {
        "dry_run": not args.execute,
        "candidates": candidates,
        "symbols": [],
        "complete_retention_proven": False,
        "preservation_verified": False,
    }

    def checkpoint():
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    checkpoint()
    if args.execute:
        repo.backup(before)
        report["before_backup"] = str(before)
        checkpoint()
    transport = RecordingTransport(args.output)
    providers = Providers(settings, transport=transport)
    try:
        for symbol in candidates:
            try:
                result = await publish_bootstrap_progress(
                    repo, providers, archive, symbol, execute=args.execute
                )
                report["symbols"].append(result)
                print(
                    json.dumps(
                        {
                            key: result[key]
                            for key in ("symbol", "published", "append_rows", "matched_rows")
                        }
                    ),
                    flush=True,
                )
            except DataError as exc:
                report["symbols"].append({"symbol": symbol, "published": False, "error": str(exc)})
                print(json.dumps({"symbol": symbol, "refused": str(exc)}), flush=True)
            checkpoint()
    finally:
        await providers.close()
        report["captures"] = transport.captures
        checkpoint()
    if args.execute:
        published = [result for result in report["symbols"] if result["published"]]
        for result in published:
            rows = repo.read("vn", result["symbol"], "1h")
            if rows != archive.read(result["replacement_image"], refresh=True):
                raise AssertionError("Published series differs from its verified replacement image")
        with repo.connect() as con:
            con.execute("BEGIN")
            con.execute("ATTACH DATABASE ? AS before", (str(before.resolve()),))
            old_count = con.execute("SELECT COUNT(*) FROM before.candles").fetchone()[0]
            new_count = con.execute("SELECT COUNT(*) FROM main.candles").fetchone()[0]
            expected = sum(result["append_rows"] for result in published)
            if new_count != old_count + expected:
                raise AssertionError("Unexpected candle count change")
            match = " AND ".join(
                f"a.{key}=b.{key}" for key in ("source", "symbol", "interval", "time")
            )
            differs = " OR ".join(f"a.{key} IS NOT b.{key}" for key in COLUMNS)
            if con.execute(
                f"SELECT 1 FROM before.candles a LEFT JOIN main.candles b ON {match} "
                f"WHERE {differs} LIMIT 1"
            ).fetchone():
                raise AssertionError("An original candle or capture version changed")
            for table in (
                "tickers",
                "jobs",
                "staging",
                "series",
                "sync_kv",
                "legacy_imports",
                "snapshot_adoptions",
                "source_checks",
                "archives",
                "quality",
            ):
                for left, right in (("main", "before"), ("before", "main")):
                    if con.execute(
                        f"SELECT 1 FROM (SELECT * FROM {left}.{table} "
                        f"EXCEPT SELECT * FROM {right}.{table}) LIMIT 1"
                    ).fetchone():
                        raise AssertionError(f"Operational records changed: {table}")
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise AssertionError("Main SQLite integrity check failed")
        repo.backup(after)
        copied = Repository(after)
        with copied.connect() as con:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise AssertionError("Backup SQLite integrity check failed")
            if con.execute("SELECT COUNT(*) FROM candles").fetchone()[0] != new_count:
                raise AssertionError("Backup candle count differs")
        for result in published:
            if copied.read("vn", result["symbol"], "1h") != repo.read("vn", result["symbol"], "1h"):
                raise AssertionError("Backup selected records differ")
        report.update(
            preservation_verified=True,
            original_candles_exact=old_count,
            appended_rows=expected,
            total_candles=new_count,
            published_symbols=len(published),
            after_backup=str(after),
            after_backup_bytes=after.stat().st_size,
            after_backup_sha256=checksum(after),
            backup_quick_check="ok",
        )
        checkpoint()
        evidence = []
        for capture in transport.captures:
            path = Path(capture["path"])
            if checksum(path) != capture["sha256"]:
                raise AssertionError("Native capture checksum changed")
            key = settings.s3_prefix + "/evidence/bootstrap-progress/" + capture["sha256"] + ".json"
            archive.store.put(key, path)
            if archive.store.read(key) != path.read_bytes():
                raise AssertionError("Native capture readback differs")
            evidence.append(key)
        report["raw_evidence_keys"] = evidence
        checkpoint()
        key = (
            settings.s3_prefix
            + "/evidence/bootstrap-progress/batch-"
            + checksum(report_path)
            + ".json"
        )
        archive.store.put(key, report_path)
        if archive.store.read(key) != report_path.read_bytes():
            raise AssertionError("Batch receipt readback differs")
    print(
        json.dumps(
            {
                "report": str(report_path),
                "candidates": len(candidates),
                "preservation_verified": report["preservation_verified"],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-direct", action="store_true")
    asyncio.run(run(parser.parse_args()))
