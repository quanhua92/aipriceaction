import os
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


@dataclass(frozen=True)
class Settings:
    database: Path = ROOT / "data/aipriceaction.sqlite3"
    cache_dir: Path = ROOT / "data/archive-cache"
    object_dir: Path = ROOT / "data/objects"
    # Filesystem backend is useful for offline operation/tests. CLI init --s3
    # explicitly creates the local RustFS bucket; constructing the app never does.
    archive_backend: str = "s3"
    s3_endpoint: str | None = "http://127.0.0.1:9100"
    s3_bucket: str = "aipriceaction-api-local"
    s3_prefix: str = "archive-v2"
    s3_region: str = "us-east-1"
    s3_access_key: str | None = "aipa-local"
    s3_secret_key: str | None = "aipa-local-development-only"
    s3_path_style: bool = True
    daily_years: int = 3
    hourly_years: int = 3
    minute_years: int = 1
    max_limit: int = 40
    single_limit: int = 10_000
    max_symbols: int = 500
    cache_bytes: int = 512 * 1024 * 1024
    cache_ttl: float = 5
    response_cache_rows: int = 20_000
    sync_tokens: tuple[str, ...] = ()
    refresh_secret: str = ""
    cors_origins: tuple[str, ...] = (
        "https://aipriceaction.com",
        "https://api.aipriceaction.com",
        "http://localhost:5173",
    )
    vn_providers: tuple[str, ...] = ("vps", "vndirect", "dnse")
    proxies: tuple[str, ...] = ()
    allow_direct: bool = False
    worker_concurrency: int = 3
    requests_per_minute: int = 30
    watchlist: Path = (
        ROOT / "watchlist.json"
        if (ROOT / "watchlist.json").exists()
        else Path(__file__).parent / "data/watchlist.json"
    )
    catalog_dir: Path = field(default_factory=lambda: Path(__file__).parent / "data")
    public_dir: Path = field(default_factory=lambda: Path(__file__).parent / "public")
    company_info: Path | None = None
    archive_max_rows: int = 5_000_000
    body_limit: int = 5 * 1024 * 1024
    query_timeout: int = 180
    read_concurrency: int = 4

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
            "archive_backend": "ARCHIVE_BACKEND",
            "s3_endpoint": "S3_ENDPOINT",
            "s3_bucket": "S3_BUCKET",
            "s3_prefix": "S3_PREFIX",
            "s3_region": "AWS_DEFAULT_REGION",
            "s3_access_key": "AWS_ACCESS_KEY_ID",
            "s3_secret_key": "AWS_SECRET_ACCESS_KEY",
            "s3_path_style": "S3_FORCE_PATH_STYLE",
            "daily_years": "DAILY_RETENTION_YEARS",
            "hourly_years": "HOURLY_RETENTION_YEARS",
            "minute_years": "MINUTE_RETENTION_YEARS",
            "max_limit": "API_MAX_LIMIT",
            "single_limit": "API_SINGLE_TICKER_MAX_LIMIT",
            "cache_bytes": "ARCHIVE_CACHE_BYTES",
            "response_cache_rows": "API_RESPONSE_CACHE_ROWS",
            "sync_tokens": "SYNC_TOKEN",
            "refresh_secret": "REFRESH_SECRET",
            "cors_origins": "CORS_ORIGINS",
            "vn_providers": "VN_PROVIDERS",
            "proxies": "HTTP_PROXIES",
            "allow_direct": "ALLOW_DIRECT",
            "worker_concurrency": "WORKER_CONCURRENCY",
            "requests_per_minute": "PROVIDER_RPM",
            "watchlist": "WATCHLIST_PATH",
            "company_info": "COMPANY_INFO_PATH",
            "read_concurrency": "API_READ_CONCURRENCY",
            "archive_max_rows": "ARCHIVE_MAX_ROWS",
            "query_timeout": "API_QUERY_TIMEOUT",
        }
        for attr, env in names.items():
            if env not in os.environ:
                continue
            raw = os.environ[env]
            default = getattr(base, attr)
            if isinstance(default, tuple):
                values[attr] = tuple(x.strip() for x in raw.split(",") if x.strip())
            elif isinstance(default, bool):
                values[attr] = raw.lower() in ("1", "true", "yes")
            elif isinstance(default, int):
                values[attr] = int(raw)
            elif isinstance(default, Path) or attr == "company_info":
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
        if not result.vn_providers or set(result.vn_providers) - {"vps", "vndirect", "dnse"}:
            raise ValueError("VN_PROVIDERS must contain only vps, vndirect, dnse")
        for attr in (
            "daily_years",
            "hourly_years",
            "minute_years",
            "max_limit",
            "single_limit",
            "worker_concurrency",
            "requests_per_minute",
            "cache_bytes",
            "response_cache_rows",
            "read_concurrency",
            "archive_max_rows",
            "query_timeout",
        ):
            if getattr(result, attr) < 1:
                raise ValueError(f"{attr} must be positive")
        return result
