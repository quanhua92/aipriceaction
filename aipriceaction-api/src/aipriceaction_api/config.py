import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
ROOT = (
    Path(
        os.environ.get(
            "AIPA_API_HOME",
            str(
                PROJECT
                if (PROJECT / "pyproject.toml").exists()
                else Path.home() / ".local/share/aipriceaction-api"
            ),
        )
    )
    .expanduser()
    .resolve()
)

RUST_DEFAULT_CORS_ORIGINS = (
    "https://aipriceaction.com",
    "https://api.aipriceaction.com",
)
_RUST_I64 = re.compile(r"[+-]?[0-9]+\Z", re.ASCII)
_RUST_U64 = re.compile(r"\+?[0-9]+\Z", re.ASCII)


def parse_rust_i64_or_default(value: str, default: int) -> int:
    """Match Rust's `str::parse::<i64>().unwrap_or(default)` environment parsing."""
    if _RUST_I64.fullmatch(value) is None:
        return default
    try:
        parsed = int(value, 10)
    except ValueError:
        return default
    return parsed if -(1 << 63) <= parsed < (1 << 63) else default


def parse_rust_u64_or_default(value: str, default: int, *, positive: bool = False) -> int:
    """Match Rust's `parse::<u64>().unwrap_or(default)` and optional positive filter."""
    if _RUST_U64.fullmatch(value) is None:
        return default
    try:
        parsed = int(value, 10)
    except ValueError:
        return default
    if parsed >= 1 << 64 or positive and parsed == 0:
        return default
    return parsed


def trim_rust_whitespace(value: str) -> str:
    # Rust str::trim follows Unicode White_Space; Python additionally treats
    # U+001C..U+001F as whitespace, while Rust leaves those invalid header bytes intact.
    start, end = 0, len(value)
    while start < end and value[start].isspace() and value[start] not in "\x1c\x1d\x1e\x1f":
        start += 1
    while start < end and value[end - 1].isspace() and value[end - 1] not in "\x1c\x1d\x1e\x1f":
        end -= 1
    return value[start:end]


@dataclass(frozen=True)
class Settings:
    database: Path = ROOT / "data/aipriceaction.sqlite3"
    cache_dir: Path = ROOT / "data/archive-cache"
    object_dir: Path = ROOT / "data/objects"
    # Rust defaults to a path relative to the server working directory.
    uploads_dir: Path | str = Path("uploads")
    uploads_max_bytes: int = 512 * 1024 * 1024
    uploads_max_sessions: int = 10_000
    # Filesystem backend is useful for offline operation/tests. CLI init --s3
    # explicitly creates the local RustFS bucket; constructing the app never does.
    archive_backend: str = "s3"
    s3_endpoint: str | None = "http://127.0.0.1:9100"
    s3_bucket: str = "aipriceaction-api-local"
    # Keep Parquet in its own bucket keyspace. The
    # Rust-compatible public CSV catalog and candles live at the bucket root.
    s3_prefix: str = "parquet"
    s3_region: str = "us-east-1"
    s3_access_key: str | None = "aipa-local"
    s3_secret_key: str | None = "aipa-local-development-only"
    s3_path_style: bool = True
    # Public CSV compatibility writes reuse the same bucket/client credentials
    # as Parquet and run automatically after successful worker ingestion.
    public_export_enabled: bool = True
    daily_years: int = 3
    hourly_years: int = 3
    minute_years: int = 1
    max_limit: int = 40
    single_limit: int = 10_000
    max_symbols: int = 500
    # Match the default Rust Redis retention capacities for dated EMA reads.
    redis_daily_max_size: int = 5_000
    redis_hourly_max_size: int = 30_000
    redis_minute_max_size: int = 20_000
    # Optional connection to the same Redis cache used by the retiring Rust
    # server. Without it, local reads keep using the deterministic SQLite tail
    # emulation used by tests and offline development.
    redis_url: str | None = None
    cache_bytes: int = 512 * 1024 * 1024
    # Match Rust's in-process /tickers response cache lifetime.
    cache_ttl: float = 5
    sync_tokens: tuple[str, ...] = ()
    # None means the variable is unset. An explicitly empty value matches Rust's
    # std::env::var behavior and is a configured empty refresh key.
    refresh_secret: str | None = None
    cors_origins: tuple[str, ...] = RUST_DEFAULT_CORS_ORIGINS
    vn_providers: tuple[str, ...] = ("vps", "vndirect", "dnse")
    vci_history_fallback: bool = False
    vci_volume_proofs: Path | None = None
    proxies: tuple[str, ...] = ()
    allow_direct: bool = False
    worker_concurrency: int = 3
    requests_per_minute: int = 30
    watchlist: Path = (
        ROOT / "watchlist.json"
        if (ROOT / "watchlist.json").exists()
        else Path(__file__).parent / "data/watchlist.json"
    )
    ingest_universe: str = "catalog"
    # Optional ordered export from Rust's `tickers` table. It is used only to
    # preserve duplicate Yahoo/SJC identity ordering in merged API reads.
    rust_ticker_identity_order: Path | None = None
    catalog_dir: Path = field(default_factory=lambda: Path(__file__).parent / "data")
    public_dir: Path = field(default_factory=lambda: Path(__file__).parent / "public")
    company_info: Path | None = None
    archive_max_rows: int = 5_000_000
    body_limit: int = 5 * 1024 * 1024
    # Kept configurable on constructed Settings for short timeout tests. The
    # production environment does not override this: Rust fixes it at 180s.
    query_timeout: int = 180
    request_concurrency: int = 64
    read_concurrency: int = 8
    market_read_concurrency: int = 8

    @classmethod
    def from_env(cls):
        # Load only this project's .env, never the legacy/production environment file.
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
        base = cls()
        values = {}
        names = {
            "database": "SQLITE_PATH",
            "cache_dir": "ARCHIVE_CACHE_PATH",
            "object_dir": "ARCHIVE_OBJECT_PATH",
            "uploads_dir": "UPLOADS_PATH",
            "uploads_max_bytes": "UPLOADS_MAX_BYTES",
            "uploads_max_sessions": "UPLOADS_MAX_SESSIONS",
            "archive_backend": "ARCHIVE_BACKEND",
            "s3_endpoint": "S3_ENDPOINT",
            "s3_bucket": "S3_BUCKET",
            "s3_prefix": "S3_PREFIX",
            "s3_region": "AWS_DEFAULT_REGION",
            "s3_access_key": "AWS_ACCESS_KEY_ID",
            "s3_secret_key": "AWS_SECRET_ACCESS_KEY",
            "s3_path_style": "S3_FORCE_PATH_STYLE",
            "public_export_enabled": "PUBLIC_EXPORT_ENABLED",
            "daily_years": "DAILY_RETENTION_YEARS",
            "hourly_years": "HOURLY_RETENTION_YEARS",
            "minute_years": "MINUTE_RETENTION_YEARS",
            "max_limit": "API_MAX_LIMIT",
            "redis_daily_max_size": "REDIS_DAILY_MAX_SIZE",
            "redis_hourly_max_size": "REDIS_HOURLY_MAX_SIZE",
            "redis_minute_max_size": "REDIS_MINUTE_MAX_SIZE",
            "redis_url": "REDIS_URL",
            "cache_bytes": "ARCHIVE_CACHE_BYTES",
            "sync_tokens": "SYNC_TOKEN",
            "refresh_secret": "REFRESH_SECRET",
            "cors_origins": "CORS_ORIGINS",
            "vn_providers": "VN_PROVIDERS",
            "vci_history_fallback": "VCI_HISTORY_FALLBACK",
            "vci_volume_proofs": "VCI_VOLUME_PROOFS",
            "proxies": "HTTP_PROXIES",
            "allow_direct": "ALLOW_DIRECT",
            "worker_concurrency": "WORKER_CONCURRENCY",
            "requests_per_minute": "PROVIDER_RPM",
            "watchlist": "WATCHLIST_PATH",
            "ingest_universe": "INGEST_UNIVERSE",
            "rust_ticker_identity_order": "RUST_TICKER_IDENTITY_ORDER_PATH",
            "company_info": "COMPANY_INFO_PATH",
            "read_concurrency": "API_READ_CONCURRENCY",
            "market_read_concurrency": "API_MARKET_READ_CONCURRENCY",
            "archive_max_rows": "ARCHIVE_MAX_ROWS",
            "request_concurrency": "API_REQUEST_CONCURRENCY",
        }
        for attr, env in names.items():
            if env not in os.environ:
                continue
            raw = os.environ[env]
            default = getattr(base, attr)
            if attr == "cors_origins":
                try:
                    raw.encode("utf-8")
                except UnicodeEncodeError:
                    # Rust std::env::var treats non-UTF-8 values as absent and
                    # build_cors_layer falls back to these two production origins.
                    values[attr] = RUST_DEFAULT_CORS_ORIGINS
                else:
                    values[attr] = tuple(trim_rust_whitespace(x) for x in raw.split(","))
            elif attr in ("sync_tokens", "refresh_secret"):
                try:
                    raw.encode("utf-8")
                except UnicodeEncodeError:
                    # Rust std::env::var treats a non-UTF-8 value as unset.
                    values[attr] = () if attr == "sync_tokens" else None
                else:
                    if attr == "sync_tokens":
                        values[attr] = tuple(
                            normalized
                            for item in raw.split(",")
                            if (normalized := trim_rust_whitespace(item))
                        )
                    else:
                        values[attr] = raw
            elif isinstance(default, tuple):
                values[attr] = tuple(
                    normalized
                    for item in raw.split(",")
                    if (normalized := trim_rust_whitespace(item))
                )
            elif isinstance(default, bool):
                values[attr] = raw.lower() in ("1", "true", "yes")
            elif attr == "max_limit":
                values[attr] = parse_rust_i64_or_default(raw, default)
            elif attr in ("uploads_max_bytes", "uploads_max_sessions"):
                values[attr] = parse_rust_u64_or_default(raw, default, positive=True)
            elif attr in ("redis_daily_max_size", "redis_hourly_max_size", "redis_minute_max_size"):
                values[attr] = parse_rust_u64_or_default(raw, default)
            elif isinstance(default, int):
                values[attr] = int(raw)
            elif attr == "redis_url":
                try:
                    raw.encode("utf-8")
                except UnicodeEncodeError:
                    # Rust std::env::var treats non-UTF-8 REDIS_URL as absent.
                    values[attr] = None
                else:
                    values[attr] = raw or None
            elif attr == "uploads_dir":
                # Rust preserves relative paths and distinguishes an explicit
                # empty PathBuf from an unset UPLOADS_PATH.
                values[attr] = raw if raw == "" else Path(raw)
            elif isinstance(default, Path) or attr in (
                "company_info",
                "vci_volume_proofs",
                "rust_ticker_identity_order",
            ):
                path = Path(raw).expanduser() if raw else None
                values[attr] = (
                    (path if path.is_absolute() else ROOT / path).resolve() if path else None
                )
            else:
                values[attr] = (
                    raw or None
                    if attr in ("s3_endpoint", "s3_access_key", "s3_secret_key")
                    else raw
                )
        result = cls(**values)
        if result.archive_backend not in ("s3", "filesystem"):
            raise ValueError("ARCHIVE_BACKEND must be s3 or filesystem")
        if result.ingest_universe not in ("watchlist", "catalog"):
            raise ValueError("INGEST_UNIVERSE must be watchlist or catalog")
        if not result.vn_providers or set(result.vn_providers) - {"vps", "vndirect", "dnse"}:
            raise ValueError("VN_PROVIDERS must contain only vps, vndirect, dnse")
        for attr in (
            "daily_years",
            "hourly_years",
            "minute_years",
            "single_limit",
            "worker_concurrency",
            "requests_per_minute",
            "cache_bytes",
            "uploads_max_bytes",
            "uploads_max_sessions",
            "read_concurrency",
            "market_read_concurrency",
            "archive_max_rows",
            "query_timeout",
            "request_concurrency",
        ):
            if getattr(result, attr) < 1:
                raise ValueError(f"{attr} must be positive")
        return result
