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

Set a non-empty `REFRESH_SECRET`. For Vietnamese provider requests, either set
`HTTP_PROXIES` or allow direct connections with `ALLOW_DIRECT=true`.

Start the services and check the API:

```sh
docker compose up -d --build
docker compose ps
curl --fail http://localhost:8899/health
docker compose logs --tail=100 initialize api worker
```

Compose starts the Python API, one worker, a one-time initializer, and local
RustFS. The initializer loads ticker groups and names from the files bundled in
this repository; it does not call the production API for a catalog snapshot.
The API listens on port `8899`; RustFS uses ports `9100` and `9101` on
loopback. The worker uses `INGEST_UNIVERSE` from `.env` (default `watchlist`).
Initial history is fetched from market-data providers and may take time to
build.

Compose passes `.env` values to the API and worker. It overrides filesystem
paths with container paths under `/data` and uses the RustFS service address
when `S3_ENDPOINT` is not set.

These are local development defaults. Before deploying, replace the values in
the top deployment section of `.env` with the production secret, S3 endpoint,
bucket, and write credentials. The remaining settings can stay at their
defaults unless the deployment needs an override. This Compose file is still
the local setup; see the [migration runbook](MIGRATION.md) for production
cutover steps.

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
