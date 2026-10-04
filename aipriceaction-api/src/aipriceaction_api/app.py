import asyncio
import hmac
import logging
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from .analysis import Analysis
from .archive import Archive
from .catalog import Catalog
from .config import Settings
from .domain import SOURCES, DataError, date_bounds, interval, mode
from .history import History
from .responses import csv_response, legacy_rows
from .storage import Repository

log = logging.getLogger(__name__)


class TickersQuery(BaseModel):
    symbol: list[str] | None = None
    interval: str = "1D"
    start_date: str | None = None
    end_date: str | None = None
    limit: int | None = Field(None, ge=1)
    legacy: bool = False
    format: str = "json"
    cache: bool = True
    mode: str = "vn"
    redis: bool = True
    ma: bool = True
    ema: bool = False
    snap: bool = True


class SyncPost(BaseModel):
    secret: str
    value: Any


class RefreshPost(BaseModel):
    interval: str
    mode: str = "vn"
    key: str | None = None


def selected_sources(value):
    return SOURCES if value == "all" else ("yahoo", "sjc") if value == "yahoo" else (value,)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    repo = Repository(settings.database)
    archive = Archive(repo, settings)
    history = History(repo, archive, settings)
    catalog = Catalog(settings)
    analysis = Analysis(history, catalog)
    responses = OrderedDict()
    started = time.monotonic()
    executor = None

    async def background(func, *args):
        return await asyncio.get_running_loop().run_in_executor(executor, partial(func, *args))

    @asynccontextmanager
    async def lifespan(app):
        nonlocal executor
        executor = ThreadPoolExecutor(
            max_workers=settings.read_concurrency, thread_name_prefix="aipa-read"
        )
        try:
            await background(repo.initialize)
            await background(catalog.initialize, repo)
            yield
        finally:
            responses.clear()
            executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="AIPriceAction API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.repo = repo
    app.state.archive = archive
    app.state.history = history
    app.state.catalog = catalog
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def limits_and_headers(request: Request, call_next):
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"error": "Invalid content-length"}, 400)
        if size > settings.body_limit:
            return JSONResponse({"error": "Request body exceeds limit"}, 413)
        # Bound actual bytes even for chunked/misreported requests; don't buffer
        # an unbounded request body before checking it.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > settings.body_limit:
                return JSONResponse({"error": "Request body exceeds limit"}, 413)
        request._body = bytes(body)
        try:
            async with asyncio.timeout(settings.query_timeout):
                response = await call_next(request)
        except TimeoutError:
            response = JSONResponse({"error": "Request timed out"}, 504)
        response.headers["x-frame-options"] = "SAMEORIGIN"
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-xss-protection"] = "1; mode=block"
        if request.url.path.startswith("/public/"):
            suffix = request.url.path.rsplit(".", 1)[-1]
            response.headers["cache-control"] = {
                "js": "max-age=300, public",
                "css": "max-age=3600, public",
                "html": "no-cache, no-store, must-revalidate",
            }.get(suffix, "max-age=86400, public")
        return response

    @app.exception_handler(DataError)
    async def data_error(request, exc):
        payload = {"error": str(exc)}
        if request.url.path.startswith("/sync/"):
            payload["success"] = False
        return JSONResponse(payload, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Don't expose inputs, bearer tokens, or sync secrets in errors/logs.
        body = any(error["loc"][0] == "body" for error in exc.errors())
        payload = {"error": exc.errors()[0]["msg"] if exc.errors() else "Invalid request"}
        if request.url.path.startswith("/sync/"):
            payload["success"] = False
        return JSONResponse(payload, 422 if body else 400)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": exc.detail}, exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        # Log exception class and path, not SQL/boto credentials or query strings.
        log.error("request failed path=%s kind=%s", request.url.path, type(exc).__name__)
        payload = {"error": "Internal server error"}
        if request.url.path.startswith("/sync/"):
            payload["success"] = False
        return JSONResponse(payload, 500)

    @app.get("/tickers")
    async def tickers(params: Annotated[TickersQuery, Query()]):
        iv = interval(params.interval)
        source_mode = mode(params.mode)
        start, end = date_bounds(params.start_date), date_bounds(params.end_date, True)
        if start is not None and end is not None and end < start:
            raise DataError("end_date must be >= start_date", 400)
        single = params.symbol is not None and len(params.symbol) == 1
        limit = min(
            params.limit or (settings.single_limit if params.start_date else 252)
            if single
            else params.limit or 1,
            settings.single_limit if single else settings.max_limit,
        )
        if params.symbol is not None and len(params.symbol) > settings.max_symbols:
            raise DataError("Too many symbols", 400)
        epoch = await background(repo.epoch)
        key = (
            epoch,
            source_mode,
            iv,
            tuple(sorted(params.symbol)) if params.symbol is not None else None,
            start,
            end,
            limit,
            params.ma,
            params.ema,
        )
        cached = responses.get(key) if params.cache else None
        if cached and cached[0] > time.monotonic():
            data = cached[1]
            tag = "in-memory"
            responses.move_to_end(key)
        else:

            def load():
                data = {}
                sources = selected_sources(source_mode)
                if params.symbol is None:
                    with repo.connect() as con:
                        available = con.execute(
                            "SELECT DISTINCT source,symbol FROM series UNION SELECT DISTINCT source,symbol FROM archives WHERE status IN ('published','pending_repair')"
                        ).fetchall()
                    tickers = [
                        (r["source"], r["symbol"]) for r in available if r["source"] in sources
                    ]
                elif source_mode == "all":
                    known = {(t["source"], t["symbol"]) for t in repo.tickers()}
                    tickers = [
                        (src, sym)
                        for src in sources
                        for sym in params.symbol
                        if (src, sym) in known
                    ]
                else:
                    tickers = [(src, sym) for src in sources for sym in params.symbol]
                for src, sym in sorted(set(tickers)):
                    if not sym:
                        continue
                    rows = history.query(src, sym, iv, start, end, limit, params.ma, params.ema)
                    if rows:
                        data[sym] = rows
                return data

            data = await background(load)
            tag = "sqlite+s3" if data else "empty"
            if params.cache:
                for stale in [k for k, value in responses.items() if value[0] <= time.monotonic()]:
                    responses.pop(stale)
                rows_count = sum(len(rows) for rows in data.values())
                responses[key] = (time.monotonic() + settings.cache_ttl, data, rows_count)
                while (
                    len(responses) > 500
                    or sum(v[2] for v in responses.values()) > settings.response_cache_rows
                ):
                    responses.popitem(last=False)
        view = legacy_rows(data, source_mode) if params.legacy else data
        headers = {"x-data-source": tag}
        if params.format.lower() == "csv":
            return Response(csv_response(view), media_type="text/csv", headers=headers)
        return JSONResponse(view, headers=headers)

    @app.get("/health")
    async def health():
        status = await background(repo.status)
        now = datetime.now(UTC)
        data = {
            "total_tickers_count": status["tickers"],
            "active_tickers_count": status["active_tickers"],
            "is_trading_hours": now.weekday() < 5 and 2 <= now.hour < 8,
            "trading_hours_timezone": "Asia/Ho_Chi_Minh",
            "uptime_secs": int(time.monotonic() - started),
            "current_system_time": now.isoformat(),
        }
        for label, iv in (("daily", "1D"), ("hourly", "1h"), ("minute", "1m")):
            data[f"{label}_records_count"] = status["records"].get(iv, 0)
            stamp = status["last_sync"].get(iv)
            data[f"{label}_last_sync"] = (
                datetime.fromtimestamp(stamp / 1e9, UTC).isoformat() if stamp else None
            )
        data.update(
            {
                name: 0
                for name in (
                    "crypto_last_sync",
                    "daily_iteration_count",
                    "slow_iteration_count",
                    "crypto_iteration_count",
                    "memory_usage_bytes",
                    "memory_usage_mb",
                    "memory_limit_mb",
                    "memory_usage_percent",
                    "disk_cache_entries",
                    "disk_cache_size_bytes",
                    "disk_cache_size_mb",
                    "disk_cache_limit_mb",
                    "disk_cache_usage_percent",
                )
            }
        )
        data["storage"] = {
            "engine": "sqlite",
            "archive_objects": status["archives"],
            "pending_archive_repairs": status["pending_archives"],
            "pending_jobs": len(status["jobs"]),
            "history_gaps": status["history_gaps"],
            "coverage": [
                {
                    "source": row["source"],
                    "interval": row["interval"],
                    "rows": row["rows"],
                    "first_candle_at": datetime.fromtimestamp(row["first"], UTC).isoformat(),
                    "latest_candle_at": datetime.fromtimestamp(row["last"], UTC).isoformat(),
                    "last_ingest_at": datetime.fromtimestamp(
                        row["last_ingest_ns"] / 1e9, UTC
                    ).isoformat(),
                }
                for row in status["coverage"]
            ],
            "series": [
                {
                    **{
                        name: row[name]
                        for name in (
                            "source",
                            "symbol",
                            "interval",
                            "provider",
                            "revision",
                            "status",
                            "rows",
                            "verification_current",
                            "latest_verification",
                            "completed_rows",
                            "provisional_rows",
                            "archive_objects",
                            "archive_rows",
                            "pending_archive_repairs",
                        )
                    },
                    "enabled": bool(row["enabled"]),
                    "last_check_outcome": row["outcome"],
                    "last_check_error": row["error"],
                    "checked_provider": row["checked_provider"],
                    "checked_revision": row["checked_revision"],
                    **{
                        name: datetime.fromtimestamp(row[column] / divisor, UTC).isoformat()
                        if row[column] is not None
                        else None
                        for name, column, divisor in (
                            ("first_candle_at", "first", 1),
                            ("latest_candle_at", "last", 1),
                            ("last_ingest_at", "last_ingest_ns", 1e9),
                            ("first_archived_candle_at", "archive_first", 1),
                            ("latest_archived_candle_at", "archive_last", 1),
                            ("last_update_attempt_at", "attempted_at_ns", 1e9),
                            ("last_provider_success_at", "successful_at_ns", 1e9),
                            ("completed_before_at_check", "completed_before", 1),
                            ("completed_checked_start", "completed_start", 1),
                            ("completed_checked_end", "completed_end", 1),
                        )
                    },
                }
                for row in status["series"]
            ],
        }
        return data

    @app.get("/tickers/group")
    async def groups(mode_value: Annotated[str, Query(alias="mode")] = "vn"):
        return await background(catalog.groups, mode(mode_value))

    @app.get("/tickers/name")
    async def names(mode_value: Annotated[str, Query(alias="mode")] = "vn"):
        return await background(catalog.names, mode(mode_value), repo)

    @app.get("/tickers/info")
    async def info(ticker: str | None = None):
        rows = await background(lambda: catalog.info)
        if ticker is None:
            return rows
        for row in rows:
            if row["ticker"].upper() == ticker.upper():
                return row
        raise DataError(f"Ticker '{ticker}' not found", 404)

    @app.post("/tickers/refresh")
    async def refresh(body: RefreshPost):
        if not settings.refresh_secret:
            raise DataError(
                "Refresh endpoint is disabled. Set REFRESH_SECRET environment variable.", 403
            )
        if body.key is None or not hmac.compare_digest(
            body.key.encode(), settings.refresh_secret.encode()
        ):
            raise DataError("Invalid or missing key", 401)
        value = mode(body.mode)
        sources = ("vn", "crypto", "yahoo") if value == "all" else (value,)
        return await background(repo.refresh, sources, body.interval)

    def authorize(request, key):
        if not settings.sync_tokens:
            raise DataError("Sync endpoint is disabled. Set SYNC_TOKEN environment variable.", 403)
        provided = request.headers.get("authorization", "")
        if not any(
            hmac.compare_digest(provided.encode(), ("Bearer " + token).encode())
            for token in settings.sync_tokens
        ):
            raise DataError("Invalid or missing authorization token.", 401)
        try:
            parsed = uuid.UUID(key)
            canonical = str(parsed)
            # Rust Uuid::parse_str accepts four exact shapes. Python also
            # strips malformed prefixes, braces and misplaced hyphens.
            if key.lower() not in (canonical, parsed.hex, "{" + canonical + "}") and not (
                key.startswith("urn:uuid:") and key[9:].lower() == canonical
            ):
                raise ValueError("Unsupported UUID shape")
        except ValueError as exc:
            raise DataError("Key must be a valid UUID", 400) from exc

    @app.get("/sync/{key}")
    async def sync_get(key: str, request: Request, secret: str):
        authorize(request, key)
        return await background(repo.sync, key, secret)

    @app.post("/sync/{key}")
    async def sync_post(key: str, body: SyncPost, request: Request):
        authorize(request, key)
        return await background(repo.sync, key, body.secret, body.value, True)

    @app.get("/analysis/top-performers")
    async def performers(
        mode_value: Annotated[str, Query(alias="mode")] = "vn",
        date: str | None = None,
        ema: bool = False,
        sort_by: str = "close_changed",
        direction: str = "desc",
        limit: int = 10,
        min_volume: int = 10000,
        sector: str | None = None,
        snap: bool = True,
    ):
        return await background(
            analysis.performers,
            mode(mode_value),
            date,
            ema,
            sort_by,
            direction,
            limit,
            min_volume,
            sector,
        )

    @app.get("/analysis/ma-scores-by-sector")
    async def sectors(
        mode_value: Annotated[str, Query(alias="mode")] = "vn",
        date: str | None = None,
        ema: bool = False,
        ma_period: int = 20,
        min_score: float = 0,
        above_threshold_only: bool = False,
        top_per_sector: int = 10,
        snap: bool = True,
    ):
        return await background(
            analysis.sectors,
            mode(mode_value),
            date,
            ema,
            ma_period,
            min_score,
            above_threshold_only,
            top_per_sector,
        )

    @app.get("/analysis/volume-profile")
    async def profile(
        symbol: str,
        mode_value: Annotated[str, Query(alias="mode")] = "vn",
        date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        bins: int = 50,
        value_area_pct: float = 70,
    ):
        return await background(
            analysis.profile,
            symbol,
            mode(mode_value),
            date,
            start_date,
            end_date,
            bins,
            value_area_pct,
        )

    @app.get("/analysis/rrg")
    async def rrg(
        mode_value: Annotated[str, Query(alias="mode")] = "vn",
        date: str | None = None,
        ema: bool = False,
        algorithm: str = "jdk",
        benchmark: str | None = None,
        period: int = 10,
        trails: int = 10,
        min_volume: int = 100000,
        snap: bool = True,
    ):
        return await background(
            analysis.rrg,
            mode(mode_value),
            date,
            ema,
            algorithm,
            benchmark,
            period,
            trails,
            min_volume,
        )

    @app.get("/explorer", include_in_schema=False)
    async def explorer():
        return FileResponse(settings.public_dir / "index.html")

    if settings.public_dir.exists():
        app.mount("/public", StaticFiles(directory=settings.public_dir), name="public")

    @app.exception_handler(404)
    async def not_found(request, exc):
        return JSONResponse({"error": "Not found"}, 404)

    # Wrap the entire application so errors and preflight responses get CORS too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "authorization", "user-agent"],
        expose_headers=["x-data-source"],
    )
    return app
