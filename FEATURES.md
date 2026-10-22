# Current features

This inventory covers the Python service and active clients. It replaces the
old Rust-centered feature inventory retained in
[`legacy/docs/root/FEATURES.md`](legacy/docs/root/FEATURES.md).

| Area | Current implementation |
| --- | --- |
| Market data API | FastAPI routes for candles, ticker metadata, health, sync, and analysis |
| OHLCV storage | SQLite hot history plus checksummed Parquet objects and JSON manifests in S3-compatible storage |
| Shared loading | Reusable SQLite multi-ticker loader with bounded reads and archive history support |
| Indicators | NumPy-based SMA/EMA and score calculations; NumPy-backed aggregation and analysis paths |
| Data ingestion | Configurable VN provider order, Binance, Yahoo Finance, SJC; optional VCI older-minute fallback |
| Archive | DuckDB reads indexed Parquet; local archive cache; immutable objects/manifests support restore and audit |
| Optional cache | Redis-compatible snapshots when configured; Redis is not required in local Compose |
| Analysis API | Top performers, MA scores by sector, volume profile, and relative rotation graphs |
| Web support | Packaged API explorer plus a separately served web app through the local Vite proxy |
| Clients | Python SDK, `aipa` CLI/TUI, and agent skills |

## HTTP endpoints

The full current method and route list is in the
[Python API contract](aipriceaction-api/CONTRACT.md). Main route families are:

- `GET /tickers` — single, multi-symbol, and market-wide OHLCV; date/interval,
  indicator, mode, and CSV options.
- `GET /tickers/group`, `/tickers/name`, `/tickers/info` — ticker metadata.
- `GET /health` — service, ingestion, storage, and market status.
- `POST /tickers/refresh` and `GET/POST /sync/{key}` — guarded operational APIs.
- `GET /analysis/top-performers`, `/analysis/ma-scores-by-sector`,
  `/analysis/volume-profile`, `/analysis/rrg` — calculated market views.
- Notes upload and retrieval routes, plus the packaged `/explorer` UI.

## Data and deployment boundaries

The Python service is the migration target and runs locally through Docker Compose
on host port `8899`. The production baseline recorded in the migration guide
still used the old Rust API; cutover remains an explicit operational step.
RustFS is the local S3-compatible archive service and remains active. The old
Rust API source and image publishing workflow are under `legacy/rust/`.
