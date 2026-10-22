# Python API

This is the supported FastAPI service for AIPriceAction. It serves market data
and analysis from recent SQLite history plus verified Parquet archives. A
separate worker fetches provider data; the API process does not fetch market
history on demand. The deprecated Rust API is archived under
[`../legacy/rust/`](../legacy/rust/README.md).

For the whole-repository map, start at the [root README](../README.md).

## Start locally

### Prerequisites

- Docker Compose (Docker Desktop or Colima on macOS).
- Internet access for catalog initialization and market-data providers.
- For the chart website only: Python 3.13+ with `uv`, Node.js, and a separate
  checkout of `aipriceaction-web` with its Node dependencies installed.

With Colima, the repository's data directory must be shared with the Docker VM.
If this checkout is under `/Volumes` and Compose cannot mount `data/`, use a
shared path under `/Users` as noted in `.env.example`.

### Start the API and worker

From the repository root:

```sh
cd aipriceaction-api
cp .env.example .env  # only on first setup; keep an existing .env
```

Set a non-empty `REFRESH_SECRET` in `.env`. Generate one with
`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` and paste it
into the `REFRESH_SECRET` line. For Vietnamese provider ingestion, either set
`HTTP_PROXIES` to a working proxy or explicitly set `ALLOW_DIRECT=true` for
your local environment. The sample leaves direct requests disabled, so without
one of these choices Vietnamese provider requests will fail.

Then start and check the stack:

```sh
docker compose up -d --build
docker compose ps
curl --fail http://localhost:8899/health
```

Compose starts the API, one ingestion worker, a one-time initializer, and
RustFS. The API uses host port `8899`; the packaged API explorer is at
<http://localhost:8899/explorer>. Host port `8899` binds on all interfaces, so
LAN devices can reach it when the host firewall allows. The Compose worker
intentionally ingests the full catalog, regardless of the `INGEST_UNIVERSE`
value in `.env` (that setting applies only to direct `aipa-api` commands).
RustFS host ports `9100` and `9101` bind to loopback by default.

The initializer refreshes ticker catalog **metadata** from
`CATALOG_BASE_URL` (default `https://api.aipriceaction.com`). It does not copy
old Rust/PostgreSQL price history. The worker fetches candles from configured
providers, so initial coverage depends on internet access and can take time.
`/health` confirms service health; it does not guarantee every symbol has fresh
data. Check startup or ingestion details with:

```sh
docker compose logs --tail=100 initialize api worker
```

By default, SQLite files are stored under `aipriceaction-api/data` on the host
(`AIPA_DATA_DIR` can select another directory). RustFS archives use a named
Docker volume. `docker compose down` stops the containers and preserves both.
`docker compose down -v` also removes the RustFS archive volume; do not use it
if you need those archive objects.

### Run the chart website

The chart website is **not** in this repository. Clone or use the existing
`aipriceaction-web` checkout, then install its Node dependencies using that
repository's instructions. The local launcher requires its Vite installation
at `node_modules/vite`; it makes a temporary copy of the web files and proxies
`/aipriceaction-api` to Python without editing the web checkout.

From `aipriceaction-api/`, install this API's local Python environment and
start the launcher. Replace the example path with the actual web checkout path:

```sh
uv sync --locked --group dev
WEB_DIR=/path/to/aipriceaction-web
uv run python scripts/serve_local_web.py \
  --web-dir "$WEB_DIR" \
  --api http://127.0.0.1:8899 \
  --port 5173
```

The API must already be healthy. The launcher listens on `0.0.0.0:5173`, so
the page is available at <http://localhost:5173>; other devices can use the
host's LAN address if its firewall allows port `5173`. Press Ctrl+C to stop the
web server; its temporary copy is removed automatically.

### Stop the local stack

```sh
cd aipriceaction-api
docker compose down
```

This preserves `data/` and the RustFS Docker volume. Keep `.env`, SQLite files,
archive objects, and operational receipts local to the deployment; do not commit
secrets or populated database copies.

## Development checks

From `aipriceaction-api/`:

```sh
uv sync --locked --group dev
uv run pytest
uv run ruff check src tests
```

The main API implementation is in `src/aipriceaction_api/app.py`; request
history reads are shared through `data_loading.py` and `history.py`. API tests
are under `tests/`. The route-family summary is in [`CONTRACT.md`](CONTRACT.md).

## Data and performance

The API uses VPS, VNDirect, and DNSE for Vietnam; Binance for crypto; Yahoo
Finance for global symbols; and SJC quote data for gold. VCI is an opt-in
fallback for older Vietnamese one-minute history only. `mode=yahoo` includes
stored `yahoo` and `sjc` records. Full details are in
[`../docs/DATA_SOURCES.md`](../docs/DATA_SOURCES.md).

History reads use a shared SQLite multi-ticker loader and NumPy-backed
calculations. Recent data comes from SQLite; older verified history comes from
Parquet archives read through DuckDB. Redis can be enabled for compatible
snapshots, but it is not required. The measured local 19-request homepage batch
completed in `550.51 ms` (2026-10-21). That run bypassed ticker-response,
Redis, and snapshot caches; the archive object cache remained enabled. See
[`VALIDATION.md`](VALIDATION.md) for the measurement scope and reproduction
command.

The API and terminal share one S3 bucket. Rust-compatible CSV objects stay at
the bucket root (`ohlcv/...` and `meta/tickers.json`); Python Parquet objects use
the sibling `parquet/` prefix. Public CSV export is enabled by default and runs
after successful ingestion. The bucket is intended to be public, which also
exposes the Parquet objects and manifests. See
[`ops/s3/README.md`](ops/s3/README.md) for the key layout and backfill commands.

## API and operations

- [`CONTRACT.md`](CONTRACT.md): current method, route families, and query shape.
- [`MIGRATION.md`](MIGRATION.md): production cutover, data transfer options, acceptance, and rollback.
- [`TODO.md`](TODO.md): current migration gates.
- [`VALIDATION.md`](VALIDATION.md): benchmark and test evidence.
- [`calendars/README.md`](calendars/README.md): exchange calendar data policy.
- [`ops/systemd/README.md`](ops/systemd/README.md): prepared Linux systemd example.
- [`ops/s3/README.md`](ops/s3/README.md): one-bucket CSV and Parquet layout.
- [`legacy/README.md`](legacy/README.md): historical implementation and evidence.

The API does not use the old Rust/PostgreSQL service as its OHLCV source. The
Compose initializer may read that public service's ticker catalog metadata;
this does not transfer price history.
