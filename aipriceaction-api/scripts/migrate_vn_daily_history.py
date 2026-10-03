"""Bounded selected-universe daily migration and archive reconciliation.

Run as the single archival writer. Imports preserve the legacy snapshot under
an explicit revision. Reconciliation re-fetches exact timestamps from the current
daily provider; unavailable or different coverage remains pending. No inferred
adjustment factor, legacy object overwrite, or recent candle replacement occurs.
"""

import argparse
import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.migration import LegacyImporter, atomic_write, legacy_files
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


async def run(args):
    settings = replace(Settings.from_env(), allow_direct=args.allow_direct)
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    worker = Worker(repo, settings, archive=archive)
    try:
        entries = [e for e in worker.load_watchlist() if e["source"] == "vn"]
        selected = {s.upper() for s in args.symbol or []}
        if selected - {e["symbol"] for e in entries}:
            raise ValueError("Migration symbols must belong to the configured watchlist")
        if args.max_symbols is not None and args.max_symbols < 1:
            raise ValueError("Maximum symbols must be positive")
        if not 0 <= args.reconcile_pages <= 100:
            raise ValueError("Reconciliation pages per symbol must be between zero and 100")
        years = sorted({int(y) for y in args.years.split(",")})
        legacy_files("vn", "FPT", "1D", years=years)  # Validate before network/migration.
        if not args.revision.strip():
            raise ValueError("Snapshot revision must be explicit and nonempty")
        entries = [e for e in entries if not selected or e["symbol"] in selected]
        if args.max_symbols is not None:
            entries = entries[: args.max_symbols]
        report = {"years": years, "revision": args.revision, "symbols": []}
        next_request = 0

        async def throttle(_):
            nonlocal next_request
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, next_request - loop.time()))
            next_request = loop.time() + 60 / settings.requests_per_minute

        async with httpx.AsyncClient(
            timeout=60, follow_redirects=True, event_hooks={"request": [throttle]}
        ) as client:
            importer = LegacyImporter(repo, archive, client=client)
            for entry in entries:
                symbol = entry["symbol"]
                result = {"symbol": symbol}
                floor = worker.floor(entry, "1D")
                state = repo.state("vn", symbol, "1D")
                if not state or state["status"] != "ready":
                    result["error"] = "A ready recent daily series is required"
                    report["symbols"].append(result)
                    continue
                coherent = [
                    obj
                    for obj in repo.archives("vn", symbol, "1D")
                    if obj["status"] == "published"
                    and obj["provider"] == state["provider"]
                    and obj["revision"] == state["revision"]
                ]
                # Preserve previously verified years on repeated wider imports.
                # A skipped existing year is recorded, not proof of full coverage.
                existing_years = {
                    datetime.fromtimestamp(obj["start"], UTC).year for obj in coherent
                }
                listing_year = (
                    datetime.fromisoformat(entry["history_start"]).year
                    if entry.get("history_start")
                    else None
                )
                requested = [
                    y
                    for y in years
                    if y not in existing_years and (listing_year is None or y >= listing_year)
                ]
                result["skipped_existing_coherent_years"] = sorted(set(years) & existing_years)
                result["skipped_pre_listing_years"] = [
                    y for y in years if listing_year is not None and y < listing_year
                ]
                print(json.dumps({"symbol": symbol, "status": "started"}), flush=True)
                try:
                    result["imports"] = []
                    # One corrupt year must not prevent migration of the
                    # independently valid later years for the same ticker.
                    for year in requested:
                        try:
                            imported = await importer.run(
                                "https://s3.aipriceaction.com",
                                "vn",
                                symbol,
                                "1D",
                                years=[year],
                                provider="legacy",
                                revision=args.revision,
                                recent_floor=floor,
                                older_only=True,
                            )
                            result["imports"].append({"year": year, "result": imported})
                        except (DataError, httpx.HTTPError) as exc:
                            failure = {"year": year, "error": str(exc)[:500]}
                            result["imports"].append(failure)
                            repo.finding(
                                "vn", symbol, "1D", "migration_daily_history", json.dumps(failure)
                            )
                    result["marked_for_reconciliation"] = repo.mark_archive_repairs(
                        "vn", symbol, "1D"
                    )
                    result["reconciliation_pages"] = []
                    for _ in range(args.reconcile_pages):
                        pending = [
                            obj
                            for obj in repo.archives("vn", symbol, "1D")
                            if obj["status"] == "pending_repair"
                        ]
                        if not pending:
                            break
                        result["reconciliation_pages"].append(
                            await worker.archive_repair("vn", symbol, "1D")
                        )
                    result["pending_objects"] = sum(
                        obj["status"] == "pending_repair"
                        for obj in repo.archives("vn", symbol, "1D")
                    )
                    result["published_objects"] = sum(
                        obj["status"] == "published" for obj in repo.archives("vn", symbol, "1D")
                    )
                except (DataError, httpx.HTTPError) as exc:
                    result["error"] = str(exc)[:500]
                    repo.finding("vn", symbol, "1D", "migration_daily_history", result["error"])
                report["symbols"].append(result)
                atomic_write(args.report, (json.dumps(report, indent=2) + "\n").encode())
                print(json.dumps(result), flush=True)
        atomic_write(args.report, (json.dumps(report, indent=2) + "\n").encode())
        return report
    finally:
        await worker.providers.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", required=True, help="Comma-separated explicit calendar years")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--reconcile-pages", type=int, default=5)
    parser.add_argument("--allow-direct", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("data/migration-vn-daily-history.json"))
    asyncio.run(run(parser.parse_args()))
