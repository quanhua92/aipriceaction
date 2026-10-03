"""Import explicit hourly legacy snapshots for configured global tickers.

This preserves imported prices/provenance and records unavailable years. It does
not enable ongoing Yahoo updates or claim complete trading-session coverage.
Run as the single archival writer; no legacy objects are modified.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, cutoff
from aipriceaction_api.migration import LegacyImporter, atomic_write, encode
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


async def migrate(args):
    settings = Settings.from_env()
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    entries = Worker(repo, settings).load_watchlist()
    symbols = [e["symbol"] for e in entries if e["source"] == "yahoo"]
    if args.symbol:
        requested = {s.upper() for s in args.symbol}
        if requested - set(symbols):
            raise DataError("Only configured global tickers may be imported", 400)
        symbols = [s for s in symbols if s in requested]
    if args.max_symbols is not None:
        if args.max_symbols < 1:
            raise DataError("Symbol budget must be positive", 400)
        symbols = symbols[: args.max_symbols]
    years = [int(y) for y in args.years.split(",")]
    report = {"revision": args.revision, "years": years, "tickers": []}
    importer = LegacyImporter(repo, archive)
    for symbol in symbols:
        result = {"symbol": symbol, "years": []}
        report["tickers"].append(result)
        state = repo.state("yahoo", symbol, "1h")
        if state and (state["provider"], state["revision"]) != ("legacy-s3", args.revision):
            result["skipped"] = "Existing independent hourly series is preserved"
        else:
            for year in years:
                try:
                    imported = await importer.run(
                        "https://s3.aipriceaction.com",
                        "yahoo",
                        symbol,
                        "1h",
                        years=[year],
                        provider="legacy-s3",
                        revision=args.revision,
                        recent_floor=cutoff(settings.hourly_years),
                    )
                    result["years"].append({"year": year, "result": imported})
                except (DataError, httpx.HTTPError) as exc:
                    failure = {"year": year, "error": str(exc)}
                    result["years"].append(failure)
                    repo.finding(
                        "yahoo", symbol, "1h", "migration_hourly_history", json.dumps(failure)
                    )
                report["checked_at"] = datetime.now(UTC).isoformat()
                atomic_write(args.report, encode(report))
        print(json.dumps(result), flush=True)
        report["checked_at"] = datetime.now(UTC).isoformat()
        atomic_write(args.report, encode(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--report", type=Path, default=Path("data/migration-global-hourly.json"))
    asyncio.run(migrate(parser.parse_args()))
