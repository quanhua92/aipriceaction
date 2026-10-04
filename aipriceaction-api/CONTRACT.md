# Compatibility contract

References inspected: Rust handlers under `../aipriceaction/src/server/`,
the packaged explorer JavaScript under `../aipriceaction/public/js/`, the existing
JavaScript integration suite, and `../sdk/aipriceaction-python/src/aipriceaction/client.py`.
The external production web application's source is outside this repository.

| Route | Preserved interface |
| --- | --- |
| `GET /tickers` | Repeated `symbol`; `interval`, `mode`, dates, limit, MA/EMA, JSON/CSV, legacy price option, cache/snapshot inputs |
| `GET /health` | Existing ticker/candle counts, sync timestamps, timezone, trading-hours and compatibility metric fields; added storage summary |
| `GET /tickers/group` | Mode-dependent group-to-symbol mapping; Yahoo/all includes SJC commodities |
| `GET /tickers/name` | Mode-dependent symbol-to-name mapping |
| `GET /tickers/info` | Existing metadata/fundamental snapshot; optional ticker |
| `POST /tickers/refresh` | Secret authorization, source/interval selection, schedule response |
| `GET/POST /sync/{key}` | UUID key, bearer token, secret hash, JSON value, creation/update timestamps, disabled/auth errors |
| `GET /analysis/top-performers` | Date, mode, sort/direction, limit, volume; daily performers/worst performers and optional hourly envelope |
| `GET /analysis/ma-scores-by-sector` | Date, mode, EMA, period, threshold, filtering and per-sector limits |
| `GET /analysis/volume-profile` | Symbol, minute dates/range, source, bins, value-area percentage; original profile envelope |
| `GET /analysis/rrg` | JDK/mascore, dates, source, EMA, benchmark, period, trails, volume threshold |
| `/explorer`, `/public/...` | Existing explorer/static files and caching/security headers |

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

Differences are explicit: Redis/snapshot implementation switches are accepted
but use the new storage; diagnostic header values change. Health metrics tied
to the former runtime retain compatible fields without inventing unavailable
measurements. Historical analysis honors supplied dates using actual available
history, and the maintained universe changes as configured. Full production
history, current sync records, and provider adjustment policies still require
migration and validation before a complete drop-in cutover claim.

The Python SDK also reads public S3 directly: `meta/tickers.json`, hashes,
fundamental metadata, per-day CSV, and
`ohlcv/{source}/{ticker}/yearly/{ticker}-{interval}-{year}.csv`. Those existing
URLs are preserved. Parquet is an internal API archive format under a new
prefix; the existing SDK does not need to read it directly.
