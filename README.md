# AIPriceAction

AIPriceAction provides market data, technical indicators, analysis endpoints,
and Python/CLI clients for Vietnamese stocks, crypto, and global assets.

## Runtime status

The supported API implementation for this migration is the Python service in
[`aipriceaction-api/`](aipriceaction-api/README.md). The former Rust API project
has been moved to [`legacy/rust/`](legacy/rust/README.md) and is deprecated.
The last recorded production baseline (2026-10-20) still showed the Rust
service; this repository does not claim that production has been switched.
See the [migration runbook](aipriceaction-api/MIGRATION.md) for deployment
gates and rollback steps.

## Current data and storage

The Python service ingests from VPS, VNDirect, and DNSE for Vietnam; Binance
for crypto; Yahoo Finance for global symbols; and SJC's daily quote service for
gold. VCI is an opt-in fallback for older Vietnamese one-minute history only.
Recent and operational records live in SQLite. Verified historical data is
stored as checksummed Parquet objects in an S3-compatible archive and read via
DuckDB. Redis is optional; it is not the source of truth. Local Docker Compose
uses RustFS as its S3-compatible object store. See
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) and
[`DATA_FLOW.md`](DATA_FLOW.md).

Compose binds RustFS host ports to loopback by default. If LAN access is
required, set a strong non-default access key and secret and protect those
ports with the host firewall before changing `RUSTFS_BIND_ADDRESS`.

## Run the local API

From the repository root:

```sh
cd aipriceaction-api
# If .env does not exist, copy .env.example and set a non-empty REFRESH_SECRET.
docker compose up -d --build
curl --fail http://localhost:8899/health
```

The API listens on host port `8899`; its packaged explorer is at
<http://localhost:8899/explorer>. Compose keeps SQLite under `aipriceaction-api/data`
and stores archive objects in the RustFS volume. Keep `.env`, SQLite files,
archive objects, and operational receipts local to the deployment; do not commit
secrets or database copies.

The main chart website is a separate Vite app. The local launcher serves a
temporary copy and proxies `/aipriceaction-api` to Python without editing the
sibling web repository:

```sh
cd aipriceaction-api
.venv/bin/python scripts/serve_local_web.py --api http://127.0.0.1:8899 --port 5173
```

## Measured local performance

On 2026-10-21, the exact 19-request home-page batch completed through the local
Vite proxy in `550.51 ms` with cache bypass enabled. All 19 requests returned
HTTP 200; p95 and maximum request latency were `517.23 ms`. This is a local
measurement, not a production service-level objective. The report summary and
reproduction command are in [`aipriceaction-api/VALIDATION.md`](aipriceaction-api/VALIDATION.md).

## Repository map

| Path | Purpose |
| --- | --- |
| `aipriceaction-api/` | Python FastAPI service, SQLite history loader, archive, workers, Compose deployment |
| `aipriceaction-terminal/` | `aipa` CLI and Textual TUI |
| `sdk/aipriceaction-python/` | Python OHLCV SDK and analysis helpers |
| `skills/` | Agent skills and market-analysis workflows |
| `legacy/rust/aipriceaction/` | Deprecated Rust API source; retained for reference |
| `legacy/` | Historical docs and disabled Rust image workflow |

## Documentation

- [Current architecture and features](FEATURES.md)
- [Data flow](DATA_FLOW.md)
- [Data sources and storage](docs/DATA_SOURCES.md)
- [API endpoints and query contract](aipriceaction-api/CONTRACT.md)
- [Local API operations](aipriceaction-api/README.md)
- [Production migration and rollback](aipriceaction-api/MIGRATION.md)
- [Latest validation evidence](aipriceaction-api/VALIDATION.md)
- [CLI and market workflow instructions](AGENTS.md)
