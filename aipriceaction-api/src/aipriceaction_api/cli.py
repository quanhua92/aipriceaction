import argparse
import asyncio
import json
import logging
import signal
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from .adoption import adopt_snapshot
from .archive import Archive
from .bootstrap_progress import publish_bootstrap_progress
from .catalog import Catalog
from .config import Settings
from .daily_adoption import adopt_daily_snapshot
from .domain import DataError, cutoff, interval, parse_time
from .importing import import_bundle, import_csv, import_historical_snapshot
from .logging_config import configure_logging
from .migration import LegacyImporter
from .providers import Providers
from .public_history import recover_public_year
from .recovery import recover_daily
from .storage import Repository
from .workers import Worker, audit


def parser():
    p = argparse.ArgumentParser(prog="aipa-api", description="FastAPI/SQLite/S3 backend management")
    p.add_argument("--database", type=Path, help="Override SQLite file")
    p.add_argument("--archive-backend", choices=("filesystem", "s3"))
    p.add_argument(
        "--allow-direct", action="store_true", help="Explicitly allow direct VN provider requests"
    )
    p.add_argument(
        "--vci-history-fallback",
        action="store_true",
        help="Enable VCI as a last-resort older-minute source; verified publication is still required",
    )
    p.add_argument(
        "--vci-volume-proofs",
        type=Path,
        help="Explicit replayable VCI volume proof list; requires VCI fallback enablement",
    )
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init", help="Initialize SQLite metadata; optionally initialize archive bucket"
    )
    init.add_argument(
        "--s3", action="store_true", help="Create/access configured archive bucket explicitly"
    )
    init.add_argument(
        "--sync-catalog",
        action="store_true",
        help="Refresh VN, crypto and Yahoo ticker groups from the configured live API",
    )
    serve = commands.add_parser("serve", help="Run API only; workers are a separate process")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=3001)
    commands.add_parser("status")
    for name in ("bootstrap", "worker"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--once", action="store_true", help="Process one bounded cycle then exit")
        cmd.add_argument("--cycles", type=int, help="Process a bounded number of cycles")
        cmd.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"))
        cmd.add_argument("--symbol", action="append", help="Filter configured tickers; may repeat")
        cmd.add_argument("--interval", choices=("1D", "1h", "1m"))
        cmd.add_argument(
            "--archive-daily",
            action="store_true",
            help="Publish and verified-prune old rows once per UTC day within these filters",
        )
    refresh = commands.add_parser(
        "refresh", help="Recheck selected ready series once without processing historical jobs"
    )
    refresh.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"), required=True)
    refresh.add_argument("--interval", choices=("1D", "1h", "1m"), required=True)
    refresh.add_argument("--symbol", action="append", help="Filter configured tickers; may repeat")
    probe = commands.add_parser("probe", help="Read one upstream page without modifying candles")
    probe.add_argument("symbol")
    probe.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"), default="vn")
    probe.add_argument("--provider", choices=("vps", "vndirect", "dnse", "vci"))
    probe.add_argument("--interval", default="1D")
    probe.add_argument("--before")
    probe.add_argument("--count", type=int, default=20)
    imp = commands.add_parser("import-csv")
    imp.add_argument("path", type=Path)
    imp.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"), required=True)
    imp.add_argument("--symbol", required=True)
    imp.add_argument("--interval", default="1D")
    imp.add_argument("--provider", default="legacy")
    imp.add_argument("--revision", default="legacy-snapshot")
    imp.add_argument(
        "--split-retention",
        action="store_true",
        help="Write older rows to Parquet and recent rows to SQLite",
    )
    historical = commands.add_parser(
        "import-history", help="Plan/publish a frozen public export wholly outside retention"
    )
    historical.add_argument("path", type=Path)
    historical.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"), required=True)
    historical.add_argument("--symbol", required=True)
    historical.add_argument("--interval", default="1D")
    historical.add_argument("--format", choices=("json", "csv"), default="json")
    historical.add_argument("--revision", required=True, help="Separate captured public revision")
    historical.add_argument(
        "--captured-at", required=True, help="UTC timestamp of the public capture"
    )
    historical.add_argument("--execute", action="store_true", help="Default is a dry run")
    partition = commands.add_parser(
        "recover-public-year",
        help="Plan/publish valid public daily rows with explicit invalid-day gaps",
    )
    partition.add_argument("path", type=Path, help="Complete captured public JSON year")
    partition.add_argument(
        "--original", type=Path, required=True, help="Original invalid six-column CSV"
    )
    partition.add_argument("--symbol", required=True)
    partition.add_argument("--year", type=int, required=True)
    partition.add_argument("--revision", required=True)
    partition.add_argument("--captured-at", required=True)
    partition.add_argument("--execute", action="store_true", help="Default is a dry run")
    legacy = commands.add_parser(
        "import-legacy",
        help="Resume explicit yearly/daily CSV imports from the existing public archive",
    )
    legacy.add_argument(
        "--base-url", help="Default is the public S3 archive, or legacy API with --from-api"
    )
    legacy.add_argument(
        "--from-api",
        action="store_true",
        help="Read bounded public exports from the old HTTP API; never query PostgreSQL directly",
    )
    legacy.add_argument(
        "--api-format",
        choices=("csv", "json"),
        default="csv",
        help="JSON preserves full API price precision; use a new explicit snapshot revision",
    )
    legacy.add_argument(
        "--api-read-backend",
        choices=("default", "database"),
        default="default",
        help="With --from-api, database disables legacy Redis/snapshot reads; use a new revision and migration database",
    )
    legacy.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"), required=True)
    legacy.add_argument("--symbol", required=True)
    legacy.add_argument("--interval", default="1D")
    legacy.add_argument("--years", help="Comma-separated explicit years for daily/hourly files")
    legacy.add_argument("--start-date", help="First UTC date for minute files, inclusive")
    legacy.add_argument("--end-date", help="Last UTC date for minute files, inclusive")
    legacy.add_argument(
        "--api-batch-days",
        type=int,
        default=1,
        help="Minute API export batch size, 1 to 31 days; defaults to one day",
    )
    legacy.add_argument("--provider")
    legacy.add_argument("--revision", default="legacy-snapshot")
    legacy.add_argument(
        "--split-retention",
        action="store_true",
        help="Archive old partitions; import recent rows to SQLite",
    )
    legacy.add_argument(
        "--older-only",
        action="store_true",
        help="With --split-retention, preserve live recent rows and import only older history",
    )
    legacy.add_argument(
        "--dry-run",
        action="store_true",
        help="HEAD explicit source objects and report availability without importing",
    )
    commands.add_parser("quality")
    progress = commands.add_parser(
        "publish-bootstrap-progress",
        help="Append verified older VN hourly bootstrap rows; leave completion and repair work pending",
    )
    progress.add_argument("--symbol", required=True)
    progress.add_argument("--execute", action="store_true")
    adopt = commands.add_parser(
        "adopt-snapshot",
        help="Verify exact completed candle overlap before enabling a provider on a legacy API snapshot",
    )
    adopt.add_argument("--symbol", required=True)
    adopt.add_argument("--source", choices=("vn", "yahoo"), default="vn")
    adopt.add_argument("--interval", choices=("1m", "1h", "1D"), default="1m")
    adopt.add_argument(
        "--provider", choices=("vps", "vndirect", "dnse", "vci", "yahoo"), required=True
    )
    adopt.add_argument(
        "--yahoo-hourly-range",
        choices=("5d",),
        help="Verify and pin the legacy five-day Yahoo hourly request policy",
    )
    adopt.add_argument(
        "--complete-sessions",
        action="store_true",
        help="Verify every snapshot timestamp in five completed sessions against minute and daily OHLCV; preserve replayable evidence",
    )
    adopt.add_argument(
        "--corroborate-provider",
        choices=("vps", "vndirect", "dnse"),
        help="With complete sessions, require a second provider for every corrected candle and preserve 15-minute/daily OHLCV",
    )
    adopt.add_argument(
        "--execute",
        action="store_true",
        help="Default verifies and reports without changing the snapshot",
    )
    commands.add_parser("audit")
    native_vci = commands.add_parser(
        "adopt-vci-snapshot",
        help="Verify exact completed overlap before refreshing one native VCI minute snapshot",
    )
    native_vci.add_argument("symbol")
    native_vci.add_argument(
        "--execute", action="store_true", help="Default verifies without changing the snapshot"
    )
    reconcile = commands.add_parser(
        "reconcile", help="Queue retained-window repair; no immediate historical wipe"
    )
    reconcile.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"), required=True)
    reconcile.add_argument("--symbol", required=True)
    reconcile.add_argument("--interval", default="1D")
    reconcile.add_argument("--provider", choices=("vps", "vndirect", "dnse"))
    reconcile.add_argument(
        "--archives-only",
        action="store_true",
        help="Queue incompatible old partitions against the current ready provider without rebuilding recent data",
    )
    archive = commands.add_parser(
        "archive", help="Plan/publish old partitions; prune only after verification"
    )
    archive.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"))
    archive.add_argument("--symbol", action="append", help="Filter stored tickers; may repeat")
    archive.add_argument("--interval", choices=("1D", "1h", "1m"))
    archive.add_argument("--execute", action="store_true", help="Default is a dry run")
    archive.add_argument(
        "--prune", action="store_true", help="Remove exactly verified exported row versions"
    )
    compact = commands.add_parser(
        "compact-archives", help="Merge bounded fragments without provider downloads or deletion"
    )
    compact.add_argument("--execute", action="store_true", help="Default is a dry run")
    compact.add_argument("--max-groups", type=int, default=20)
    repair = commands.add_parser(
        "archive-repair", help="Process one lower-priority affected archive page"
    )
    repair.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"))
    repair.add_argument("--symbol")
    repair.add_argument("--interval")
    repair.add_argument(
        "--restart",
        action="store_true",
        help="Restart failed staging; requires source, symbol, and native interval",
    )
    recover = commands.add_parser(
        "recover-legacy-daily",
        help="Recover corrupt original CSV dates from the pinned provider; preserve evidence",
    )
    recover.add_argument("file", type=Path)
    recover.add_argument("--symbol", required=True)
    recover.add_argument("--year", type=int, required=True)
    recover.add_argument("--max-pages", type=int, default=4)
    recover.add_argument(
        "--exclude-verified-sessions",
        action="store_true",
        help="Apply reviewed ticker/year closure exceptions, preserving original placeholders and evidence",
    )
    commands.add_parser("restore-index", help="Verify S3 objects and rebuild SQLite archive index")
    commands.add_parser(
        "publish-index",
        help="Publish current archive metadata and retry failed manifests without downloading or pruning candles",
    )
    backup = commands.add_parser("backup")
    backup.add_argument("path", type=Path)
    restore = commands.add_parser("restore", help="Restore backup into a NEW database file")
    restore.add_argument("path", type=Path)
    restore.add_argument("--destination", type=Path, required=True)
    return p


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def native_interval(value):
    result = interval(value)
    if result not in ("1D", "1h", "1m"):
        raise DataError("Operational commands require native 1D, 1h, or 1m", 400)
    return result


def restore_backup(path, destination, runtime_database):
    if destination.exists() or destination.resolve() == runtime_database.resolve():
        raise DataError("Restore destination must be a NEW database path", 400)
    created = False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            if source.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise DataError("Backup integrity check failed")
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Claim the new path exclusively so a concurrent creator cannot be
            # overwritten between validation and SQLite's destination open.
            with destination.open("xb"):
                created = True
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target)
    except Exception as exc:
        if created:
            destination.unlink(missing_ok=True)
        if isinstance(exc, sqlite3.Error):
            raise DataError("Backup could not be read or restored") from exc
        raise


async def execute(args, settings):
    if args.command == "serve":
        import uvicorn

        from .app import create_app

        # The application middleware logs safe paths without query-string secrets.
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(settings),
                host=args.host,
                port=args.port,
                access_log=False,
                log_config=None,
            )
        )
        await server.serve()
        return
    if args.command == "restore":
        restore_backup(args.path, args.destination, settings.database)
        emit({"restored": str(args.destination)})
        return
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    if args.command == "init":
        catalog_result = None
        if args.sync_catalog:
            from .catalog_sync import sync_catalog

            catalog_result = await sync_catalog(
                settings.catalog_base_url, settings.catalog_snapshot
            )
        Catalog(settings).initialize(repo)
        Worker(repo, settings).load_watchlist()
        if args.s3:
            await asyncio.to_thread(archive.store.initialize)
        emit(
            {
                "database": str(settings.database),
                "archive_initialized": args.s3,
                "catalog": catalog_result,
            }
        )
    elif args.command == "status":
        emit(repo.status())
    elif args.command in ("bootstrap", "worker"):
        worker = Worker(repo, settings, archive=archive)
        if args.cycles is not None and args.cycles < 1:
            raise DataError("Worker cycles must be positive", 400)
        await worker.run(
            args.once,
            args.cycles,
            args.source,
            [s.upper() for s in args.symbol] if args.symbol else None,
            args.interval,
            args.archive_daily,
        )
        if args.once or args.cycles:
            status = repo.status()
            emit(
                {
                    "records": status["records"],
                    "pending_jobs": len(status["jobs"]),
                    "quality_findings": len(repo.findings()),
                }
            )
    elif args.command == "refresh":
        worker = Worker(repo, settings, archive=archive)
        emit(
            {
                "series": await worker.refresh(
                    args.source,
                    [s.upper() for s in args.symbol] if args.symbol else None,
                    args.interval,
                )
            }
        )
    elif args.command == "probe":
        if not 1 <= args.count <= 1000:
            raise DataError("Probe count must be between 1 and 1000", 400)
        providers = Providers(settings)
        try:
            page = await providers.page(
                args.source,
                args.symbol.upper(),
                native_interval(args.interval),
                parse_time(args.before) if args.before else None,
                args.count,
                args.provider,
            )
            emit(
                {
                    "provider": page.provider,
                    "no_data": page.no_data,
                    "rows": [r.record() for r in page.rows],
                    "next_before": page.rows[0].time if page.rows else None,
                    "volume_proofs": list(page.volume_proofs),
                }
            )
        finally:
            await providers.close()
    elif args.command == "import-csv":
        iv = native_interval(args.interval)
        if args.split_retention:
            years = {
                "1D": settings.daily_years,
                "1h": settings.hourly_years,
                "1m": settings.minute_years,
            }[iv]
            emit(
                await asyncio.to_thread(
                    import_bundle,
                    repo,
                    archive,
                    args.path,
                    args.source,
                    args.symbol.upper(),
                    iv,
                    args.provider,
                    args.revision,
                    cutoff(years),
                )
            )
        else:
            emit(
                {
                    "imported": import_csv(
                        repo,
                        args.path,
                        args.source,
                        args.symbol.upper(),
                        iv,
                        args.provider,
                        args.revision,
                    )
                }
            )
    elif args.command == "import-history":
        emit(
            await asyncio.to_thread(
                import_historical_snapshot,
                repo,
                archive,
                args.path,
                args.source,
                args.symbol.upper(),
                native_interval(args.interval),
                args.revision,
                parse_time(args.captured_at),
                args.execute,
                args.format,
            )
        )
    elif args.command == "recover-public-year":
        emit(
            await asyncio.to_thread(
                recover_public_year,
                repo,
                archive,
                args.path,
                args.original,
                args.symbol.upper(),
                args.year,
                args.revision,
                parse_time(args.captured_at) * 1_000_000_000,
                args.execute,
            )
        )
    elif args.command == "import-legacy":
        iv = native_interval(args.interval)
        years = [int(y) for y in args.years.split(",")] if args.years else None
        floor = (
            cutoff(
                {
                    "1D": settings.daily_years,
                    "1h": settings.hourly_years,
                    "1m": settings.minute_years,
                }[iv]
            )
            if args.split_retention
            else None
        )
        emit(
            await LegacyImporter(repo, archive).run(
                args.base_url
                or (
                    "https://api.aipriceaction.com"
                    if args.from_api
                    else "https://s3.aipriceaction.com"
                ),
                args.source,
                args.symbol.upper(),
                iv,
                years=years,
                start=args.start_date,
                end=args.end_date,
                provider=args.provider or ("legacy-api" if args.from_api else "legacy"),
                revision=args.revision,
                recent_floor=floor,
                older_only=args.older_only,
                dry_run=args.dry_run,
                from_api=args.from_api,
                api_batch_days=args.api_batch_days,
                api_format=args.api_format,
                api_read_backend=args.api_read_backend,
            )
        )
    elif args.command == "publish-bootstrap-progress":
        providers = Providers(settings)
        try:
            emit(
                await publish_bootstrap_progress(
                    repo, providers, archive, args.symbol.upper(), args.execute
                )
            )
        finally:
            await providers.close()
    elif args.command == "adopt-vci-snapshot":
        from .vci_adoption import adopt_native_snapshot

        providers = Providers(settings)
        try:
            result = await adopt_native_snapshot(
                repo, providers, args.symbol.upper(), execute=args.execute
            )
            if args.execute:
                archive.publish_metadata()
            emit(result)
        finally:
            await providers.close()
    elif args.command == "adopt-snapshot":
        if args.interval == "1D" and (
            args.source != "vn"
            or args.complete_sessions
            or args.corroborate_provider
            or args.yahoo_hourly_range
        ):
            raise DataError(
                "Daily adoption supports VN exact overlap without minute correction options", 400
            )
        providers = Providers(settings)
        try:
            if args.interval == "1D":
                emit(
                    await adopt_daily_snapshot(
                        repo, providers, args.symbol.upper(), args.provider, args.execute
                    )
                )
                return
            emit(
                await adopt_snapshot(
                    repo,
                    providers,
                    args.symbol.upper(),
                    args.provider,
                    args.execute,
                    complete_sessions=args.complete_sessions,
                    corroborate=args.corroborate_provider,
                    source=args.source,
                    iv=args.interval,
                    yahoo_hourly_range=args.yahoo_hourly_range,
                )
            )
        finally:
            await providers.close()
    elif args.command in ("quality", "audit"):
        emit(repo.findings() if args.command == "quality" else audit(repo))
    elif args.command == "reconcile":
        iv = native_interval(args.interval)
        if args.archives_only:
            if args.provider:
                raise DataError(
                    "Archive-only reconciliation pins the current provider; omit --provider", 400
                )
            emit(
                {
                    "pending_archives": repo.mark_archive_repairs(
                        args.source, args.symbol.upper(), iv
                    )
                }
            )
            return
        years = {
            "1D": settings.daily_years,
            "1h": settings.hourly_years,
            "1m": settings.minute_years,
        }[iv]
        worker = Worker(repo, settings)
        try:
            entries = worker.load_watchlist()
            entry = next(
                (
                    r
                    for r in entries
                    if r["source"] == args.source and r["symbol"] == args.symbol.upper()
                ),
                None,
            )
            floor = worker.floor(entry, iv) if entry else cutoff(years)
        finally:
            await worker.providers.close()
        emit(
            {
                "job": repo.queue(
                    args.source, args.symbol.upper(), iv, "repair", floor, args.provider
                )
            }
        )
    elif args.command == "archive":
        groups = archive.eligible(
            args.source,
            [symbol.upper() for symbol in args.symbol] if args.symbol else None,
            args.interval,
        )
        if not args.execute:
            emit(
                {
                    "dry_run": True,
                    "partitions": [
                        {
                            "source": rows[0].source,
                            "symbol": rows[0].symbol,
                            "interval": rows[0].interval,
                            "start": rows[0].time,
                            "end": rows[-1].time,
                            "rows": len(rows),
                        }
                        for rows in groups
                    ],
                }
            )
        else:
            emit(
                {
                    "published": [
                        await asyncio.to_thread(archive.publish, rows, args.prune)
                        for rows in groups
                    ]
                }
            )
    elif args.command == "compact-archives":
        if not 1 <= args.max_groups <= 100:
            raise DataError("Compaction group budget must be between one and 100", 400)
        groups = archive.compaction_groups()[: args.max_groups]
        if not args.execute:
            emit(
                {
                    "dry_run": True,
                    "groups": [
                        {
                            "source": group[0]["source"],
                            "symbol": group[0]["symbol"],
                            "interval": group[0]["interval"],
                            "fragments": len(group),
                            "input_rows": sum(o["row_count"] for o in group),
                        }
                        for group in groups
                    ],
                }
            )
        else:
            objects = [await asyncio.to_thread(archive.compact, group) for group in groups]
            # A failed manifest after a verified local replacement is resumable
            # even if there are no longer any fragmented groups to compact.
            await asyncio.to_thread(archive.publish_metadata)
            emit({"compacted": objects})
    elif args.command == "archive-repair":
        worker = Worker(repo, settings, archive=archive)
        try:
            emit(
                {
                    "processed": await worker.archive_repair(
                        args.source,
                        args.symbol.upper() if args.symbol else None,
                        native_interval(args.interval) if args.interval else None,
                        args.restart,
                    )
                }
            )
        finally:
            await worker.providers.close()
    elif args.command == "recover-legacy-daily":
        worker = Worker(repo, settings, archive=archive)
        try:
            emit(
                await recover_daily(
                    worker,
                    args.file,
                    args.symbol,
                    args.year,
                    args.max_pages,
                    args.exclude_verified_sessions,
                )
            )
        finally:
            await worker.providers.close()
    elif args.command == "restore-index":
        emit({"restored_objects": await asyncio.to_thread(archive.restore_index)})
    elif args.command == "publish-index":
        await asyncio.to_thread(archive.publish_metadata)
        emit({"published_index": True})
    elif args.command == "backup":
        repo.backup(args.path)
        emit({"backup": str(args.path)})


async def execute_with_shutdown(args, settings, *, restore_signal=True):
    if args.command not in ("worker", "bootstrap"):
        return await execute(args, settings)
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    stopping = False

    def stop():
        nonlocal stopping
        if not stopping:
            stopping = True
            logging.getLogger(__name__).info("Worker shutdown requested")
            task.cancel()

    previous = signal.getsignal(signal.SIGTERM)

    def handle_signal(*_):
        if not loop.is_closed():
            loop.call_soon_threadsafe(stop)

    signal.signal(signal.SIGTERM, handle_signal)
    try:
        await execute(args, settings)
    except asyncio.CancelledError:
        if not stopping:
            raise
    finally:
        if restore_signal:
            signal.signal(signal.SIGTERM, previous)


def main(argv=None):
    args = parser().parse_args(argv)
    configure_logging()
    handles_shutdown = args.command in ("worker", "bootstrap")
    previous = signal.getsignal(signal.SIGTERM) if handles_shutdown else None
    try:
        settings = Settings.from_env()
        overrides = {}
        if args.database:
            overrides["database"] = args.database.resolve()
        if args.archive_backend:
            overrides["archive_backend"] = args.archive_backend
        if args.allow_direct:
            overrides["allow_direct"] = True
        if args.vci_history_fallback:
            overrides["vci_history_fallback"] = True
        if args.vci_volume_proofs:
            overrides["vci_volume_proofs"] = args.vci_volume_proofs.resolve()
        asyncio.run(
            execute_with_shutdown(args, replace(settings, **overrides), restore_signal=False)
        )
    except KeyboardInterrupt:
        pass
    except (DataError, ValueError, OSError) as exc:
        emit({"error": str(exc)})
        return 1
    finally:
        if handles_shutdown:
            signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":
    sys.exit(main())
