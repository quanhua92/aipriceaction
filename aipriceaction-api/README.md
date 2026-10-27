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
docker compose logs --tail=100 initialize api api2 nginx worker worker-hourly worker-minute worker-history
```

`/health` includes a `configuration` object with non-secret effective settings
such as ingestion universe, provider access mode, retention, and Parquet prefix.
It reports whether a proxy is configured, never its URL or credentials.

Compose starts two Python API replicas behind Nginx, three live interval
workers, one single-flight history worker, a one-time initializer, and local
RustFS. The daily worker handles current daily data. The hourly and minute
workers handle those intervals independently, so slow intraday requests cannot
block daily updates. The history worker crawls backward from each live retention
cutoff, up to ten years, publishes cold Parquet and Rust-compatible CSV, and
archives rows as they age out of SQLite. It keeps only job and archive metadata
in SQLite; cold candles stay in Parquet. It pins the active provider/revision
for each series and uses the normal proxy shuffle/retry behavior. If VN history
needs another provider, it queues a live repair and restarts the cold pass only
after the new series basis is active. It moves at most one retention group per
minute so the first archive pass cannot block cold-history pages. Each live worker defaults to 2 concurrent
tasks and 10 requests/minute per provider route; set
the matching `*_WORKER_CONCURRENCY` or `*_PROVIDER_RPM` variable to change it.
These six role-specific settings replace `WORKER_CONCURRENCY` and
`PROVIDER_RPM` for Compose workers; the old variables do not override them.
The history worker defaults to 5 requests/minute per route and one series page
at a time. Its request budget does not pause the live workers. Use
`aipa-api history-status` to inspect cold-history cursors and retries.
Follow provider, page, Parquet, and CSV progress with
`docker compose logs -f worker-history`. To inspect one ticker, run
`docker compose exec worker-history aipa-api history-status --source vn --symbol VCB --interval 1D`.
Failed cold jobs wait 30 minutes after the first failure and one hour after the
second and later failures. Ten failed attempts for the same provider and
revision mark the job `exhausted`; the worker will not retry it again on that
basis. Successful pages do not reset this total. A new provider or revision
basis starts a fresh pass. During upgrade, the last recorded retry streak is
carried forward; failures from before an earlier successful page cannot be
reconstructed. Old 30-day coverage-gap deadlines are shortened to the current
retry schedule, while retries already due remain due. A verified VN provider
handoff waits for live-series repair and does not spend a failure attempt.
`coverage_gap` means older candles remain unverified; `provider_limit` means the
provider's history boundary was reached.
Both API replicas and all workers share the same SQLite data directory. SQLite
is the source of truth; each API process keeps its own bounded in-memory caches.
The Docker image builds SQLite 3.54.1 from checksum-verified upstream source so
all API, worker, and backup containers include the WAL-reset corruption fix.
Before replacing a public candle CSV, the exporter checks that the replacement
contains every timestamp in the existing file. It refuses an incomplete or
unreadable replacement and logs the affected file for investigation.
Nginx owns host port `8899` and localhost port `3003`, and sends requests to
either healthy API replica. The initializer loads ticker groups and names from
the bundled `vn_tickers.json` directory; it does not call the production API.
RustFS uses ports `9100` and `9101` on loopback.

The workers use `INGEST_UNIVERSE` from `.env` (default `catalog`), which ingests
every symbol in the bundled catalog. Set it to `watchlist` to limit ingestion
to `watchlist.json`. Every selected Yahoo symbol is probed at daily, hourly,
and minute intervals without per-symbol interval entries; catalog mode selects
the full Yahoo directory. A Yahoo response that clearly has no candles is
recorded as no data and retried after 24 hours; it does not retry every worker
cycle. Crypto catalog entries use all three native intervals, SJC uses daily
only, and VN intraday coverage is promoted for liquid symbols. Other currently
listed VN stocks start with daily history; delisted stocks stay in metadata but
are not scheduled for ingestion. VN index symbols remain in the watchlist.
Initial history for the full catalog may take time to build and depends on
provider coverage and limits.

## Refresh the Vietnamese stock directory

From this directory, run `python3 scripts/refresh_vn_tickers.py`. It uses only
the Python standard library and writes all Vietcap `STOCK` entries to
`src/aipriceaction_api/data/vn_tickers.json`; it does not remove low-cap stocks.
The market-cap snapshot is estimated as the KBS quote price times total listed
shares. Stocks without a usable quote keep a null market cap. Use
`--dry-run` to fetch and summarize without writing the file.
Restart API processes after writing the file so their in-memory catalog reloads.

The saved file is the single Vietnamese ticker source for `/tickers/group`,
`/tickers/name`, `/tickers/info`, `/tickers/directory`, sector analysis, and
catalog initialization. The old Python `vn.csv` and `ticker_group.json` are
removed. Python does not read or update the archived Rust catalog files under
`../legacy/rust/aipriceaction/`.

The group, name, info, and directory endpoints use a shared default minimum
market cap of VND 387,175,000,000. In the catalog generated on Oct 24, 2026,
that cutoff kept 657 stocks and covered 99% of known market cap; the 235 rows
with unknown market cap were excluded. This is one fixed constant shared by
these endpoints. A later catalog refresh can change the coverage percentage.
Pass `min_market_cap_vnd=0` to include every stock with a known market cap. Add
`include_unknown_market_cap=true` to include rows without a market cap too.
`/tickers/info` also includes legacy company profiles and financial ratios
where available. These filters apply to metadata routes; `/tickers` continues
to query stored OHLCV data.

`/tickers/group` uses English ICB level-2 labels by default, such as `Banks`.
Pass `lang=vi` (or `lang=vn`) for Vietnamese labels such as `Ngân hàng`. The
directory and info rows include both labels in `icb_level2_name` and
`icb_level2_name_en`.

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
It pauses `worker-history` during the API migration so an old worker cannot
write retry state after the database schema changes, then resumes it on the new
image.

The SQLite database is stored under `data/` by default. Set `AIPA_DATA_DIR` to
use another host directory. `docker compose down` keeps the database and RustFS
data. `docker compose down -v` deletes the RustFS volume; use it only when you
intend to remove that archive.

Production SQLite backups and monthly restore drills are installed in the
Compose owner's user crontab. They do not start with `docker compose up`; see the
[backup operations guide](ops/backup/README.md).

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
