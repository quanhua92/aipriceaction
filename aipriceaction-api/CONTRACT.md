# Compatibility contract

References inspected: Rust handlers under `../aipriceaction/src/server/`,
the packaged explorer JavaScript under `../aipriceaction/public/js/`, the existing
JavaScript integration suite, and `../sdk/aipriceaction-python/src/aipriceaction/client.py`.
The sibling React web source was inspected read-only; its production hosting
configuration remains outside this repository.

| Route | Preserved interface |
| --- | --- |
| `GET /tickers` | Repeated `symbol`; `interval`, `mode`, dates, limit, MA/EMA, JSON/CSV, legacy price option, cache/snapshot inputs |
| `GET /health` | Existing ticker/candle counts, sync timestamps, timezone, trading-hours and compatibility metric fields; no Python-only `storage` field |
| `GET /tickers/group` | Mode-dependent group-to-symbol mapping; Yahoo/all includes SJC commodities |
| `GET /tickers/name` | Mode-dependent symbol-to-name mapping |
| `GET /tickers/info` | Existing metadata/fundamental snapshot; optional ticker |
| `POST /tickers/refresh` | Secret authorization, source/interval selection, schedule response |
| `GET/POST /sync/{key}` | UUID key, bearer token, secret hash, JSON value, creation/update timestamps, disabled/auth errors |
| `GET /analysis/top-performers` | Date, mode, sort/direction, limit, volume, sector, `ema`, `snap`; daily performers/worst performers. The packaged Explorer advertises `with_hour` and hourly results, but the active Rust handler ignores that query key and returns `hourly: null`; Python matches it. The Explorer's documented result shape also conflicts with Rust's response scaffold, and it leaves hour labels, timezone, ranking and indicator baselines unspecified. |
| `GET /analysis/ma-scores-by-sector` | Date, mode, EMA, period, threshold, filtering and per-sector limits |
| `GET /analysis/volume-profile` | Symbol, minute dates/range, source, bins, value-area percentage; original profile envelope |
| `GET /analysis/rrg` | JDK/mascore, dates, source, EMA, benchmark, period, trails, volume threshold |
| `POST /upload/markdown` | Notes upload of `content.md` using `session_id` and `secret`; response and storage quotas are preserved |
| `GET /uploads/{session_id}/markdown/{filename}` | Public Notes Markdown retrieval; Rust accepts optional `secret`, which has no effect for Markdown |
| `DELETE /uploads/{session_id}` | Notes session deletion using `secret` |
| `/explorer`, `/public/...` | Existing explorer/static files and caching/security headers |

The active Rust router is the route authority. Its legacy GitHub-proxy module is
not mounted by that router, so the explorer's deprecated `/raw/*` note and the
SDK's `getRawData()` helper do not describe a live API endpoint. FastAPI
preserves every business route above and serves the same explorer assets without
starting the Rust server. FastAPI's documentation routes are disabled so the
public route surface matches Rust; contract tests inspect the generated schema
through `app.openapi()` directly.
Legacy Rust image handlers for `/upload/image` and `/uploads/{session_id}/images/*`
are also unmounted and are not active endpoints.

Sync keys follow Rust's UUID parser: simple hexadecimal, standard hyphenated,
braced hyphenated, or `urn:uuid:` plus hyphenated. Hexadecimal case can vary;
valid forms share one canonical key and secret. Malformed prefixes, extra
braces and misplaced hyphens return the legacy HTTP 400 error after authorization.

Integer query values preserve Rust's signed/unsigned widths and ASCII decimal
syntax. Decimal-point, exponent, whitespace and non-ASCII numeral strings are
rejected. Zero values retain the handler's existing clamp or empty-list behavior;
RRG's signed minimum volume still accepts negative values. Volume-profile mode
matching is case-insensitive for `crypto`/`yahoo` and defaults other values to
Vietnam, as in its separate legacy string parser.

JSON body routes (`POST /tickers/refresh` and `POST /sync/{key}`) follow the
pinned Axum 0.8.9 / `mime` 0.3.17 media-type parser, including `application/*+json`
suffixes and its parameter edge cases. Rust-visible header bytes are enforced
before parsing. Runtime environment parsing also follows the Rust defaults:
`API_MAX_LIMIT` uses signed 64-bit parsing with a default of 40,
`API_SINGLE_TICKER_MAX_LIMIT` is ignored, upload limits use positive unsigned
64-bit values or their defaults, and Redis retention limits accept zero while
falling back on invalid unsigned values. An unset `REFRESH_SECRET` disables
refresh, while an explicitly empty value is configured and matches an empty
request key; Compose requires a non-empty value to avoid starting with that
empty key. An unset or empty `SYNC_TOKEN` disables sync.

Native intervals are `1D`, `1h`, and `1m`; aggregate intervals are `5m`, `15m`,
`30m`, `4h`, `1W`, `2W`, `1M`. Minute `1m` and monthly `1M` remain distinct.
Hourly aliases, daily aliases, and mode aliases are retained. Daily and
weekly/monthly dates use `YYYY-MM-DD`; intraday timestamps use
`YYYY-MM-DDTHH:MM:SS`. Results are chronological.

A single ticker defaults to 252 recent rows, or the larger bounded historical
limit when `start_date` is supplied. Multiple/all ticker requests default to
one row and retain the legacy smaller limit. Date ranges are inclusive; with a
start date, the first requested candles are selected. Latest requests select
the last candles. Earlier warm-up bars do not change the requested output size.

Candles retain OHLCV, symbol/time, optional moving averages/scores, close/volume
changes, and total-money-change fields. The legacy price option scales VN OHLC
by 1,000 while leaving indexes and MA values unchanged, following the original
behavior. CSV column ordering and precision follow the original handler.
MA calculations and aggregated history use a coherent adjustment revision.

The Python service uses SQLite and object storage for durable history and can
use Redis for Rust-compatible ticker/analysis snapshots and raw-history reads
when `REDIS_URL` is configured. The paired differential suite checks source
headers and Redis diagnostics for the covered cache branches. Health retains
Rust's compatibility fields without a Python-only storage summary; count
estimates can still differ between PostgreSQL and SQLite at runtime. Historical
analysis uses available history, and the maintained ticker universe depends on
the configured catalog. Production history, private sync records, deployment
configuration, and provider adjustment policies still require migration and
validation before a complete drop-in cutover claim.

The production Rust Compose file uses standalone Redis. The Python client
supports that centralized topology, including `redis`/`rediss` and Valkey
aliases, Rust-compatible database and credential parsing, and Fred's outer URL
whitespace handling. Redis Cluster and Sentinel URLs are not supported by the
Python client; do not configure those schemes for the Python deployment unless
topology-aware clients are added and verified. Rust's current Fred dependency
does not enable the Unix-socket feature, so Unix Redis URLs remain disabled in
both services.
The typed SDK also omits some server query fields (`redis`/`snap` on tickers,
`mode`/`snap` on several analysis methods, and `with_hour` on top-performers),
so those options are unavailable through its typed parameter objects even
though raw URL query parameters work.

The Python SDK also reads public S3 directly: `meta/tickers.json`, hashes,
fundamental metadata, per-day CSV, and
`ohlcv/{source}/{ticker}/yearly/{ticker}-{interval}-{year}.csv`. Those existing
URLs are preserved. Parquet is an internal API archive format under a new
prefix; the existing SDK does not need to read it directly.
