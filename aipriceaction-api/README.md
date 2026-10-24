# Python API

This service provides the AIPriceAction API and market-data worker. The API
reads recent candles from SQLite and older verified candles from Parquet in an
S3-compatible store. The worker fetches and validates new data.

The chart website lives in a separate repository. The retired Rust API is in
[`../legacy/rust/`](../legacy/rust/README.md).

## Run locally

You need Docker Compose and internet access. With Colima, make sure Docker can
mount this repository's `data/` directory.

For a new local setup only, create `.env` from the example:

```sh
cp .env.example .env
```

Set a non-empty `REFRESH_SECRET`. Provider requests shuffle configured routes
and retry through the pool on failure. VN requests include direct access by
default; set `ALLOW_DIRECT=false` to keep them proxy-only.
Logs record the upstream provider, sanitized route, attempt, and cumulative
attempt counts for that provider and route.

Start the services and check the API:

```sh
docker compose up -d --build
docker compose ps
curl --fail http://localhost:8899/health
docker compose logs --tail=100 initialize api api2 nginx worker worker-hourly worker-minute
```

`/health` includes a `configuration` object with non-secret effective settings
such as ingestion universe, provider access mode, retention, and Parquet prefix.
It reports whether a proxy is configured, never its URL or credentials.

Compose starts two Python API replicas behind Nginx, three interval workers, a
one-time initializer, and local RustFS. The daily worker handles daily data and
Parquet retention. The hourly and minute workers handle those intervals
independently, so slow intraday requests cannot block daily updates. Each worker
defaults to 2 concurrent tasks and 10 requests/minute per provider route; set
the matching `*_WORKER_CONCURRENCY` or `*_PROVIDER_RPM` variable to change it.
These six role-specific settings replace `WORKER_CONCURRENCY` and
`PROVIDER_RPM` for Compose workers; the old variables do not override them.
Both API replicas and all workers share the same SQLite data directory. SQLite
is the source of truth; each API process keeps its own bounded in-memory caches.
Nginx owns host port `8899` and localhost port `3003`, and sends requests to
either healthy API replica. The initializer loads ticker groups and names from
the files bundled in this repository; it does not call the production API for
a catalog snapshot. RustFS uses ports `9100` and `9101` on loopback.

The workers use `INGEST_UNIVERSE` from `.env` (default `catalog`), which ingests
every symbol in the bundled catalog. Set it to `watchlist` to limit ingestion
to `watchlist.json`. Every selected Yahoo symbol is probed at daily, hourly,
and minute intervals without per-symbol interval entries; catalog mode selects
the full Yahoo directory. A Yahoo response that clearly has no candles is
recorded as no data and retried after 24 hours; it does not retry every worker
cycle. Crypto catalog entries use all three native intervals, SJC uses daily
only, and VN intraday coverage follows the existing curated and liquid-symbol
selection. Initial history for the full catalog may take time to build and
depends on provider coverage and limits.

When a retained-series repair changes its revision, the same SQLite transaction
marks older Parquet objects for repair and queues them. The worker repairs one
object per few cycles, prepares a batch, and publishes one Parquet manifest for
that batch. It also refreshes the affected public daily and yearly CSV files.
If a provider cannot verify a full archive range and its overlap with the
current series, that object stays pending. The worker retries it; operators can
inspect `aipa-api status` and `aipa-api quality`, or resume the batch with
`aipa-api archive-repair`.

Compose passes `.env` values to the API and worker. It overrides filesystem
paths with container paths under `/data` and uses the RustFS service address
when `S3_ENDPOINT` is not set.

These are local development defaults. Before deploying, replace the values in
the top deployment section of `.env` with the production secret, S3 endpoint,
bucket, and write credentials. The remaining settings can stay at their
defaults unless the deployment needs an override. This Compose file is still
the local setup; see the [migration runbook](MIGRATION.md) for production
cutover steps.

For a rolling API update after the first Nginx deployment, run
`./scripts/deploy-api-rolling.sh` from this directory. It rebuilds the shared
image, replaces one API replica at a time, waits for its health check, reloads
Nginx to resolve the replacement container address, then updates the worker.

The SQLite database is stored under `data/` by default. Set `AIPA_DATA_DIR` to
use another host directory. `docker compose down` keeps the database and RustFS
data. `docker compose down -v` deletes the RustFS volume; use it only when you
intend to remove that archive.

## Run the chart website

The web source is a separate checkout. Start the API first, then run the local
proxy from this directory. Replace the example path with the web checkout path:

```sh
uv sync --locked --group dev
WEB_DIR=/path/to/aipriceaction-web
uv run python scripts/serve_local_web.py \
  --web-dir "$WEB_DIR" \
  --api http://127.0.0.1:8899 \
  --port 5173
```

The proxy listens on all interfaces at port `5173`. Open `http://localhost:5173`
on this machine, or use its LAN address from another device. The launcher uses
a temporary copy of the web files and does not edit the web checkout.

## Development checks

```sh
uv sync --locked --group dev
uv run pytest
uv run ruff check src tests
```

## Useful references

- [API routes and parameters](CONTRACT.md)
- [Production migration and rollback](MIGRATION.md)
- [Storage and provider notes](../docs/DATA_SOURCES.md)
- [Public CSV and Parquet layout](ops/s3/README.md)
- [Recorded local validation](VALIDATION.md)
- [Trading calendar checks](calendars/README.md)

The main implementation is under `src/aipriceaction_api/`. Tests are in
`tests/`.
