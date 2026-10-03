"""Bounded, resumable selected-universe migration from public legacy API exports.

Run as the single archival writer. Explicit dates/revision freeze the capture
identity across restarts. Existing independent series are preserved. A provider
handoff requires the existing exact-match certificate; disagreement is reported.
"""

import argparse
import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from aipriceaction_api.adoption import adopt_snapshot
from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, parse_time
from aipriceaction_api.migration import LegacyImporter, atomic_write
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


async def run(args):
    settings = replace(Settings.from_env(), allow_direct=args.allow_direct)
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    worker = Worker(repo, settings, archive=archive)
    entries = [e for e in worker.load_watchlist() if e["source"] == "vn"]
    await worker.providers.close()
    selected = {s.upper() for s in args.symbol or []}
    if selected - {e["symbol"] for e in entries}:
        raise ValueError("Migration symbols must belong to the configured watchlist")
    if args.max_symbols is not None and args.max_symbols < 1:
        raise ValueError("Maximum symbols must be positive")
    entries = [e for e in entries if not selected or e["symbol"] in selected]
    report = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "revision": args.revision,
        "symbols": [],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
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
        providers = Providers(settings)
        attempted = 0
        try:
            for entry in entries:
                symbol = entry["symbol"]
                state = repo.state("vn", symbol, "1m")
                if state and (
                    state["provider"] != "legacy-api" or state["revision"] != args.revision
                ):
                    report["symbols"].append({"symbol": symbol, "skipped_existing": True})
                    continue
                if args.max_symbols is not None and attempted >= args.max_symbols:
                    break
                attempted += 1
                result = {"symbol": symbol}
                print(json.dumps({"symbol": symbol, "status": "started"}), flush=True)
                floor = max(parse_time(args.start_date), worker.floor(entry, "1m"))
                try:
                    result["recent"] = await importer.run(
                        "https://api.aipriceaction.com",
                        "vn",
                        symbol,
                        "1m",
                        start=args.start_date,
                        end=args.end_date,
                        provider="legacy-api",
                        revision=args.revision,
                        recent_floor=floor,
                        from_api=True,
                        api_batch_days=31,
                    )
                    # A separate bounded prior month preserves indicator lookback.
                    start = datetime.fromtimestamp(floor, UTC)
                    warmup = start.replace(day=1) - timedelta(days=1)
                    warmup = warmup.replace(day=1)
                    if warmup < start:
                        result["warmup"] = await importer.run(
                            "https://api.aipriceaction.com",
                            "vn",
                            symbol,
                            "1m",
                            start=warmup.date().isoformat(),
                            end=(start - timedelta(days=1)).date().isoformat(),
                            provider="legacy-api",
                            revision=args.revision,
                            recent_floor=floor,
                            older_only=True,
                            from_api=True,
                            api_batch_days=31,
                        )
                    with repo.connect() as con:
                        daily = {
                            r[0]
                            for r in con.execute(
                                "SELECT time FROM candles WHERE source='vn' AND symbol=? AND interval='1D' AND time>=? AND time<=?",
                                (symbol, floor, parse_time(args.end_date)),
                            )
                        }
                        minutes = {
                            r[0]
                            for r in con.execute(
                                "SELECT DISTINCT time/86400*86400 FROM candles WHERE source='vn' AND symbol=? AND interval='1m'",
                                (symbol,),
                            )
                        }
                    result["observed_daily_dates_without_minutes"] = [
                        datetime.fromtimestamp(t, UTC).date().isoformat()
                        for t in sorted(daily - minutes)
                    ]
                    if daily - minutes:
                        repo.finding(
                            "vn",
                            symbol,
                            "1m",
                            "migration_minute_dates",
                            json.dumps(result["observed_daily_dates_without_minutes"]),
                        )
                    result["provider_checks"] = []
                    for provider in settings.vn_providers:
                        try:
                            result["adoption"] = await adopt_snapshot(
                                repo,
                                providers,
                                symbol,
                                provider,
                                args.adopt,
                            )
                            break
                        except DataError as exc:
                            result["provider_checks"].append(
                                {"provider": provider, "error": str(exc)}
                            )
                    if "adoption" not in result:
                        raise DataError(
                            "No selected provider verified: "
                            + json.dumps(result["provider_checks"])
                        )
                    if args.adopt:
                        with repo.connect() as con:
                            con.execute(
                                "UPDATE quality SET resolved=1 WHERE source='vn' AND symbol=? AND interval='1m' AND kind IN ('migration_handoff','snapshot_adoption_disagreement')",
                                (symbol,),
                            )
                except (DataError, httpx.HTTPError) as exc:
                    result["error"] = str(exc)[:500]
                    repo.finding("vn", symbol, "1m", "migration_handoff", result["error"])
                report["symbols"].append(result)
                atomic_write(args.report, (json.dumps(report, indent=2) + "\n").encode())
                print(
                    json.dumps(
                        {
                            "symbol": symbol,
                            "imported": result.get("recent", {}).get("imported"),
                            "missing_observed_dates": len(
                                result.get("observed_daily_dates_without_minutes", [])
                            ),
                            "error": result.get("error"),
                            "adoption": result.get("adoption"),
                        }
                    ),
                    flush=True,
                )
        finally:
            await providers.close()
            if args.adopt and any(r.get("adoption") for r in report["symbols"]):
                # The final successful adoption can follow the last warm-up
                # upload. Publish its certificate before ending this writer.
                await asyncio.to_thread(archive.manifest, repo.archives())
            atomic_write(args.report, (json.dumps(report, indent=2) + "\n").encode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument(
        "--adopt",
        action="store_true",
        help="Execute only an exactly verified selected-provider handoff",
    )
    parser.add_argument("--allow-direct", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("data/migration-vn-minutes.json"))
    asyncio.run(run(parser.parse_args()))
