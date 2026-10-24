# Data sources and durable storage

This page describes the current Python API implementation. Provider availability
and row freshness vary by symbol, interval, and market session; health and
per-series status should be checked instead of assuming complete coverage.

## Providers

| Stored source | Upstream | Policy and limits |
| --- | --- | --- |
| `vn` | VPS, VNDirect, DNSE | Configured by `VN_PROVIDERS`; order varies by symbol/interval. VCI is not a preferred provider. |
| `vn` (optional fallback) | VCI | Disabled by default. Can be appended only for older `1m` history when `VCI_HISTORY_FALLBACK=true`; volume proof rules still apply. |
| `crypto` | Binance REST klines | Large one-minute history can bootstrap from Binance public monthly ZIPs after checksum and continuity checks. |
| `yahoo` | Yahoo Finance chart API | Daily values apply Yahoo's adjusted-close ratio; hourly requests use bounded range rules. |
| `sjc` | SJC daily HCMC gold quotes | Buy/sell quotes are represented as synthetic OHLC with placeholder volume; these are not traded candles. |

`mode=yahoo` is a compatibility mode that includes both `yahoo` and `sjc`;
`mode=all` includes all configured market sources. API responses preserve each
record's stored source identity.

## Storage path

- SQLite stores recent candles and operational state at
  `aipriceaction-api/data/aipriceaction.sqlite3` on the host and
  `/data/aipriceaction.sqlite3` in the Compose containers by default. Set
  `AIPA_DATA_DIR` to mount another host directory. The indexed identity is
  source, symbol, interval, and timestamp.
- Older verified history is stored as immutable Parquet objects under the
  configured S3 prefix, with checksums and JSON manifests.
- DuckDB reads archived Parquet. A bounded local file cache reduces repeat
  archive I/O.
- Local Docker Compose runs RustFS as the S3-compatible object store. RustFS is
  unrelated to the deprecated Rust API.
- Redis is optional and disabled by default in Compose. If configured, it serves
  compatibility snapshots/acceleration; it is not the durable source of truth.

The shared history loader reads bounded SQLite rows for recent history and
consults the archive for older ranges. Provider writes and archive publication
are separately validated; a missing row or an empty health field does not prove
that a range is complete.

## Catalog and migration boundaries

The worker defaults to `INGEST_UNIVERSE=catalog` and attempts every bundled
catalog symbol; set it to `watchlist` to restrict ingestion. This does not mean
every provider has complete history for every symbol. The Compose initializer
loads ticker metadata from the files bundled with the API; it does not fetch
price history from the old Rust server. Production cutover is still pending in
the last recorded baseline; see the
[migration runbook](../aipriceaction-api/MIGRATION.md).
