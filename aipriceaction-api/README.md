# Python API

FastAPI market-data service with SQLite for recent and operational data,
checksummed Parquet archives in S3-compatible storage, DuckDB archive reads,
and optional Redis compatibility. This is the supported API implementation;
the former Rust server is archived under [`../legacy/rust/`](../legacy/rust/README.md).
Production cutover is not recorded as complete; see [`MIGRATION.md`](MIGRATION.md).

## Start locally

```sh
# From aipriceaction-api/
# Keep an existing .env. If missing, copy .env.example and set REFRESH_SECRET.
docker compose up -d --build
curl --fail http://localhost:8899/health
```

The API uses port `8899` inside and outside the container. Compose starts the
API, one ingestion worker, an initializer, and RustFS. SQLite is mounted from
`./data` by default; archive objects live in the RustFS volume. `REDIS_URL` is
empty by default. Set a non-empty `REFRESH_SECRET` before starting; configure
`SYNC_TOKEN` only if private sync routes are needed. Review `.env.example` and
`docker-compose.yml` before exposing the service publicly. RustFS host ports
bind to loopback by default; only expose them on a LAN with non-default
credentials and a firewall rule.

The packaged API explorer is at `http://localhost:8899/explorer`. The main chart
website is a separate Vite app; its local launcher runs a temporary copy and
proxies `/aipriceaction-api` to this API without editing the web repository:

```sh
.venv/bin/python scripts/serve_local_web.py --api http://127.0.0.1:8899 --port 5173
```

## Data and performance

The API uses VPS, VNDirect, and DNSE for Vietnam; Binance for crypto; Yahoo
Finance for global symbols; and SJC quote data for gold. VCI is an opt-in
fallback for older Vietnamese one-minute history only. `mode=yahoo` includes
stored `yahoo` and `sjc` records. Full details are in
[`../docs/DATA_SOURCES.md`](../docs/DATA_SOURCES.md).

History reads use a shared SQLite multi-ticker loader and NumPy-backed
calculations. Recent data comes from SQLite; older verified history comes from
Parquet archives read through DuckDB. Redis can be enabled for compatible
snapshots, but is not required. The measured local 19-request homepage batch
completed in `550.51 ms` with cache bypass enabled (2026-10-21); see
[`VALIDATION.md`](VALIDATION.md) for scope, failures, and exact measurements.

## API and operations

- [`CONTRACT.md`](CONTRACT.md): current method/route families and query shape.
- [`MIGRATION.md`](MIGRATION.md): production cutover, optional data transfer,
  empty-start path, acceptance, and rollback.
- [`TODO.md`](TODO.md): current migration gates.
- [`VALIDATION.md`](VALIDATION.md): latest benchmark and test evidence.
- [`calendars/README.md`](calendars/README.md): exchange calendar data policy.
- [`ops/systemd/README.md`](ops/systemd/README.md): prepared Linux systemd example.
- [`legacy/README.md`](legacy/README.md): historical implementation and evidence.

The API does not source OHLCV from the old Rust/PostgreSQL service. Catalog
initialization may still use the configured catalog endpoint; this does not
transfer price history.
