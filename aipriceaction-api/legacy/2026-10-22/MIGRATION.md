# Migration from Rust to the Python API

Status: preparation runbook; production has not been changed. Run commands from
`aipriceaction-api/` unless another directory is specified. Replace hostnames,
paths and bucket names with the deployment's actual values before execution.

## 1. Make sure the Docker web uses Python

The replacement stack is `docker-compose.yml` in this directory: Python API,
Python worker, initialization and RustFS. Do not deploy the Compose files under
`../../../legacy/rust/aipriceaction/`; those run the legacy Rust server and Postgres/Redis stack.
This workspace already has initialized local data. Its Compose initializer also
fetches the ticker catalog from `CATALOG_BASE_URL` (defaulting to production), so
do not rerun initialization against the possibly stale production catalog.
For a new empty data directory, select and verify a trusted catalog snapshot or
catalog source before initializing.

```sh
# Ensure the ignored aipriceaction-api/.env contains:
# AIPA_API_PORT=8899
# AIPA_API_SSR_PORT=3003
# RUSTFS_API_PORT=9200
# RUSTFS_CONSOLE_PORT=9201
# Python listens on port 8899 inside and outside the API container.
# Port 3000 is used by a local Node service.
# SSH occupies RustFS host ports 9100–9103; these overrides leave the
# internal S3 endpoint at http://rustfs:9000 unchanged.
docker compose up -d --wait rustfs
docker compose build api worker
docker compose up -d --no-deps api worker
curl --fail http://localhost:8899/explorer -o /tmp/aipa-explorer.html
curl --fail -I http://localhost:8899/public/js/utils.js
curl --fail -D - 'http://localhost:8899/tickers?symbol=FPT&interval=1D&limit=5'
```

The API and worker share `/data/archive-cache`. Cache reads use stable `flock`
files under `.locks/` so eviction cannot remove an object while another process
is reading it. Deploy both services from the same image and release, with the
same cache mount and UID; recreate them together when changing this locking
code. An older worker does not honor the new locks and must not overlap an
upgraded API.

Redis is optional in this Compose file and `REDIS_URL` defaults to blank. Without
it, the API reads durable SQLite/S3 history and the worker does not publish its
updates to a shared cache. To exercise Rust-compatible cache behavior, configure
the same `REDIS_URL` for the API and worker. Keep a candidate Redis isolated
during side-by-side production validation unless sharing Rust's cache is
intentional; Redis is an ephemeral read cache, not the canonical data migration
source. At cutover, retain the cache endpoint only if it is part of the resolved
production configuration, and verify its ticker identity order and contents.

Open <http://localhost:8899/explorer>. This web UI is already packaged inside the
Python image; no separate web container is required. Its API base is
`window.location.origin`, so its requests go to Python on the same host/port.
Check Network in the browser: `/tickers`, `/health`, `/analysis/*` and static
assets must succeed. For single-source `/tickers` requests, a cold database
read should have the compatibility tag `database`; warm in-memory responses
use `cache`, and usable Redis snapshot reads use `redis-snap`. Rust and Python
intentionally omit `x-data-source` on a `mode=all` in-memory response-cache hit.

This explorer is not the main chart website hosted at `aipriceaction.com`.
The main website must also be tested against Python before retiring Rust.
The local web source is `/Users/quan/data-workspace/aipriceaction-web`.
The current Vite launcher serves a temporary copy on port 5173, bound to
`0.0.0.0`; an equivalent second instance is also running on port 5174. Reuse
the requested `:5173` instance while it is up. If it is stopped, start one
without modifying the web repository (choose another free port only if 5173 is
occupied):

```sh
.venv/bin/python scripts/serve_local_web.py --api http://127.0.0.1:8899 --port 5173
```

Open <http://192.168.1.13:5173/> from another LAN device or
<http://localhost:5173/> locally. It copies source to a
temporary folder, reuses installed dependencies, keeps Vite cache in the
temporary copy, and sets `VITE_API_PROXY_TARGET` to the supplied Python API URL.
This workspace
uses host port 8899 to avoid collisions with ports 3001 and 3002. It excludes web `.env`
files and proxies `/aipriceaction-api/*` to the Python Docker API. Stop with
Ctrl-C; the temporary source/cache is removed. An existing browser API override
can supersede the proxy for normal API-client calls; clear that override in the
site's debug settings if set. Notes upload helpers select their browser base
separately and use `/aipriceaction-api` in development, so test Notes uploads
through that proxy independently. This runs Vite on the host; the API remains
in Docker. Production hosting still
requires the actual web deployment configuration. If it uses `https://api.aipriceaction.com`, keeping
that URL and switching the API reverse proxy avoids a frontend URL change.
For local chart testing, use the existing Playwright routing rehearsal below.
Check the exact web origin is allowed by `CORS_ORIGINS` for direct browser calls.

The current Notes SSR helper hardcodes `http://127.0.0.1:3000`, but this
workspace's local Node web service occupies port 3000 and Compose maps the API
SSR port to 3003. Browser Notes uploads can use the Vite proxy, while server-side
markdown reads cannot reach Python with this mapping. Keep SSR verification open;
resolving it requires an integration-side origin/proxy change, and the sibling
web repository remains read-only in this migration work.

Do not call this step complete from an HTTP HTML check alone. Actually open the
explorer and main charts; inspect console errors, requests and rendered data.

## 2. Make sure required behavior works

### Observed baseline, 2026-10-20

| Check | Legacy production | Local Python |
| --- | --- | --- |
| `/tickers`, default query | HTTP 200, empty object `{}` | HTTP 200, 1,559 top-level entries |
| Data-source header | `postgres` | `sqlite+s3` |
| Last reported daily ingestion | 2026-10-04 | 2026-10-20 |
| `/explorer` | Not checked | HTTP 200 |

These observations show a production data-serving/freshness problem, not a
complete HTTP outage. They do not establish its cause. Default ticker queries
can differ in universe/selection: compare explicit symbols and dates too.

Local health reported pending archive repairs and queued work. VN daily,
hourly and minute coverage reached October 20; crypto and Yahoo daily coverage
ended October 8. Current Compose runs the worker with `--source vn` only.
An empty `history_gaps` field does not certify completeness. Local history is
not identical to all legacy Postgres history.

Latest targeted LAN smoke on 2026-10-20: health, crypto EMA CSV and Yahoo EMA
CSV returned 200 through Vite; VN EMA CSV returned 503 with
`Historical adjustment repair pending for VTH 1D`. The three quarantined VTH
archive objects are intact but belong to an older revision. Keep this data gate
open; do not suppress the response or relabel/delete the rows. Consider the
targeted archive-repair command only after mount ownership and provider access
are resolved.

Home-page endpoint parity was checked against the legacy Rust query types and
handlers. The listed `/tickers`, `/tickers/group`, `/tickers/name`, `/health`
and `/analysis/rrg` parameter names are accepted by Python; the CSV header
columns match. `mode=yahoo` also includes SJC. This establishes query/format
compatibility by code inspection, not production readiness. A 19-URL LAN-proxy
sweep returned HTTP 200 for every listed request; market-wide CSV calls took
19.44–22.68 seconds and duplicate RRG calls took 25.99 seconds during that
concurrent sweep. Later standalone RRG requests took 2.37–2.50 seconds, and
health took 7.21–9.56 seconds. The web client has a 30-second request timeout,
so repeat cold/warm and concurrent measurements through a rendered home page
before cutover. The proxy path rewrites `/aipriceaction-api` to the local Python
service on port 8899 for this workspace.

The web backtest caller currently sends comma-joined symbols. Both Rust and
Python expand a comma-joined value into symbols unless that complete value is
itself a known ticker. This preserves literal comma-containing tickers, but
means a client cannot disambiguate a literal ticker such as `A,B` from two
symbols `A` and `B` with that encoding. Repeated `symbol` query keys avoid the
ambiguity; no web change is required for ordinary comma-separated ticker lists.
The web repository remains untouched in this migration preparation.

### Required gates before cutover

- [ ] Record the exact Git commit/image deployed and resolved Compose configuration.
- [ ] Run the repository checks below; investigate failures rather than waive them.
- [ ] Test main VN, crypto and global charts against Python, including indicators,
  date selection, weekly/monthly aggregation, CSV and empty/invalid queries.
- [ ] Test analysis endpoints, ticker groups/names/info and actual SDK/CLI clients.
- [ ] Test authenticated sync/refresh if used; preserve private sync records.
- [ ] Verify the candidate's exact production CORS origins, refresh secret and
  sync bearer token; exercise authorized and rejected requests before routing.
- [ ] Before retiring Rust, export its ticker identities as ordered
  `id,source,symbol` rows (`ORDER BY id`) and preserve the mapping with the
  migration evidence, then configure `RUST_TICKER_IDENTITY_ORDER_PATH` to the
  export. Python validates and applies the mapping for merged Yahoo/SJC native
  and aggregate queries. Synthetic source-order tests pass; the actual
  production export and 2W/1M shared HTTP comparisons remain required.
- [x] Mount Notes upload, retrieval and session deletion in both Rust and Python;
  the shared isolated HTTP differential passes all three flows. Before cutover,
  repeat them through the candidate public API origin using an isolated session.
- [ ] Compare selected historical OHLCV timestamps and values with retained
  verified evidence. Record intentional adjustment/retention differences.
- [ ] Verify recent session freshness separately for each supported market and
  interval. Fix or explicitly defer stale markets before advertising support.
- [ ] Resolve or explain each pending archive repair and verify referenced objects.
- [ ] Confirm the ingestion worker survives restart and resumes queued work.
- [ ] Set production secrets, CORS, provider access and storage permissions.
- [ ] Keep a verified rollback path and complete the data restore rehearsal.

```sh
uv sync
uv run pytest
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv build

# Optional browser dependency: install Playwright/Chromium separately if absent.
uv run python scripts/check_web_client.py --market vn --symbol FPT --report /tmp/aipa-web-vn.json
uv run python scripts/check_web_client.py --market crypto --symbol BTCUSDT --report /tmp/aipa-web-crypto.json
uv run python scripts/check_web_client.py --market global --symbol AAPL --report /tmp/aipa-web-global.json
```

On 2026-10-21, the repository-wide format check reports six untouched Python
files that already differ from Ruff formatting. The changed API, calculation,
history, transport and endpoint-test files pass targeted format checks. Resolve
the six baseline files separately if a clean repository-wide format gate is
required; do not treat that baseline as endpoint parity evidence.

Use each script's `--help` to select explicit intervals/date bounds; defaults
are smoke tests, not evidence of complete historical coverage. Store small
reports and checksums. Any full SQLite rehearsal copy must live inside a
`tempfile.TemporaryDirectory`, be removed even after failure, and never become
an archived test database. Check local and RustFS sizes before/after large runs.
Clean only exact temporary S3 prefixes, including versions/delete markers.

## 3. Move local data to production, preferably without rebuilding history

Recommended: promote a coherent local SQLite snapshot plus its S3 archive and
operational evidence. This avoids fetching the same provider history again.
It is a transfer, not a conversion from SQLite to Postgres.

### Inventory and prepare

1. On the local host, inspect the actual API/worker bind mounts using
   `docker inspect`; do not assume `./data` is the live directory. On this
   machine, `/Users/quan/data-workspace/...` resolves on macOS to the workspace
   under `/Volumes/data`, but Colima shares `/Volumes/data` via virtiofs and does
   not resolve that symlink inside the VM. The current Compose bind therefore
   points at a separate VM-local ext4 SQLite file that is newer and larger than
   the workspace database. Keep both intact. Do not restart services onto a
   changed path, repair data or transfer either file until their contents and
   ownership are reconciled. For future runs, configure a canonical Colima-shared
   path and verify host, VM and container file identity before starting writers.
2. Record SQLite/data size, Docker RustFS volume usage, object counts, bucket,
   prefix, checksums, catalog snapshot and active proof/correction references.
   Inventory persistent Notes state under `/data/uploads` separately: session
   IDs, file counts, byte totals and checksums. Do not print session secrets or
   Markdown contents in the inventory report. Record explicitly when there are
   no sessions; `aipa-api backup` does not include uploads.
3. On production, provision disk for SQLite, cache and the complete archive.
   Use a dedicated new bucket/prefix, never an unrelated canonical destination.
4. Deploy the same code revision on both hosts. Do not start the destination
   API/worker against an empty database and then overwrite it while running.
5. Save the existing proxy configuration and an operational rollback of the old
   Postgres/archive/private records using its established backup procedure.
6. While Rust PostgreSQL is still available, export its ticker identity order
   with the Rust CLI/admin connection:

   ```sql
   SELECT id, source, ticker AS symbol FROM tickers ORDER BY id;
   ```

   Keep the export beside the catalog snapshot, checksum it, and configure
   `RUST_TICKER_IDENTITY_ORDER_PATH` to its location (for example,
   `/data/rust-ticker-identities.csv`). Python validates and uses this map when
   Yahoo and SJC rows share a ticker name. Rehearse the actual export against
   reversed-ID native, weekly, biweekly and monthly fixtures before claiming
   exact duplicate-source ordering parity.

### Freeze and transfer

1. Choose a quiet window. Stop the local worker and any other writers, including
   host scripts or refresh requests. Stop the local API to prevent writes through
   authenticated routes. Keep RustFS running for the object transfer.

   ```sh
   docker compose stop worker api
   ```

2. Create one operational SQLite before-image with the CLI's online backup API,
   using the same environment, live mount and image. For example, a one-off
   container can write into the live data mount:

   ```sh
   docker compose run --rm --no-deps api aipa-api backup /data/cutover.sqlite3
   ```

   Destination must not already exist. Never copy only the live `.sqlite3` while
   writers are running; committed data may still be in WAL. Preserve the catalog
   snapshot and every file referenced by active proofs/receipts. Include required
   private sync state; do not regenerate it from public OHLCV. Keep upload
   session metadata and Markdown files with their exact session IDs and paths.

3. Transfer all referenced archive objects, manifests and immutable evidence to
   production S3/RustFS, preserving object keys and bytes. Use an S3-aware copy
   tool with separate source/destination credentials; compare key/size/checksum
   inventories after copying. Do not use destructive sync/delete flags. Raw Docker
   volume copying is not a substitute for a verified object transfer.
4. Transfer `cutover.sqlite3`, catalog snapshot and required operational evidence
   securely to production; copy `/data/uploads` when it contains retained Notes
   sessions, then compare SHA-256 checksums for both. Keep secrets out of Git.
5. On production with its API/worker stopped, restore the transferred backup to
   the new configured database path with `aipa-api restore ... --destination ...`.
   The destination must be new. Use the CLI from the same image and mount the
   input backup read-only. Set ownership for the runtime container.
6. Configure production bucket/prefix to match the copied keys. Verify archive
   manifests/receipts against destination objects before starting a writer.
   `restore-index` can reconstruct an archive index; it does not restore recent
   SQLite candles or private sync records, and is not a substitute for transfer.
7. Start the candidate API privately and repeat section 2 against the transferred
   dataset, including queries that read S3 outside SQLite retention.
8. Resume the local services if needed after the coherent transfer completes.
   Only the production instance may write the production database/archive.

The transfer will have a freshness gap from the freeze time. Let the production
worker catch up and confirm freshness before public routing changes. Preserve
one archival writer per database/prefix; do not run competing hosts against it.

### Empty-start fallback

If transfer cannot be verified, start a fresh dedicated SQLite/S3 deployment and
bootstrap it while the old backend remains available. This is simpler to copy,
but takes provider time and does not guarantee recovery of all old history,
private sync records, Notes uploads or adjustment evidence. Decide whether
Notes sessions are required; if none exist, record that explicitly. Do not
switch traffic merely because `/health` returns HTTP 200.

1. Initialize a new bucket/data directory with the production credentials.
2. Capture/sync the ticker catalog while the existing catalog API is available.
3. Start ingestion; monitor coverage and errors for every required market.
4. Import required unavailable history from retained verified exports/archives
   using the migration workflows in README.md. Keep original captures/receipts.
5. Complete the same acceptance gates before switching production.

If the required historical data cannot be rebuilt, postpone cutover or agree on
an explicit reduced coverage contract. Do not silently discard it.

## 4. Detailed deployment and cutover sequence

1. Prepare a production-specific Compose override and private `.env`. Use durable
   absolute `AIPA_DATA_DIR`, unique S3 credentials, authentication tokens and
   exact CORS origins. Publish only the reverse proxy publicly; bind the API and
   RustFS management/API ports to loopback or private networking as appropriate.
   Current local Compose exposes them on `0.0.0.0` and has development defaults;
   do not deploy those defaults unchanged.
2. Keep the Python API on internal port 8899. Configure the existing Caddy/proxy
   upstream for the candidate but leave public traffic on Rust during validation.
   For a host proxy the candidate may be `127.0.0.1:8899`; for a container proxy
   use a reachable service/network address, not that container's own loopback.
3. Resolve initialization before switching: Compose currently runs
   `init --s3 --sync-catalog` using `CATALOG_BASE_URL=https://api.aipriceaction.com`.
   If production points to this API and it is down during startup, that creates
   a startup dependency on itself. For a seeded deployment, use an override with
   initialization command `["aipa-api", "init", "--s3"]` and preserve the verified
   catalog snapshot at `/data/catalog-snapshot.json`. Confirm all required groups
   resolve from that snapshot. Schedule future catalog updates deliberately.
4. Choose market support. Existing worker command updates VN only. If all markets
   are required, use a single worker without `--source vn`:
   `["aipa-api", "worker", "--archive-daily"]`. Rehearse this workload and verify
   freshness/provider limits before adoption; do not assume non-VN history or
   SJC coverage is ready because a ticker exists.
5. Validate the resolved configuration locally with `docker compose ... config
   --quiet`, build a pinned image and deploy that exact image on production.
   Use `docker compose ... up -d --no-deps api` for private candidate startup after
   storage initialization/restoration. Start the worker once storage is verified.
   Here `...` means the actual base and production override `-f` arguments.
6. Complete sections 1–3 against the candidate. Keep compact before/after reports,
   explicit symbol/date comparisons and checksums. Verify public frontend CORS
   and chart behavior through the candidate route.
7. Freeze any legacy write endpoints and finalize private-state migration if used.
   Save the current reverse-proxy config. Change only the API upstream to Python,
   validate the proxy configuration, then reload it using the deployment's normal
   procedure. Keep the public domain/TLS unchanged.
8. Verify public `/health`, explicit `/tickers` queries, `/explorer`, main charts,
   CSV/indicators, analysis and authenticated features. Confirm the expected
   logical `x-data-source` tag on single-source `/tickers` requests (`database`
   for a cold database read, `cache` for an in-memory hit, or `redis-snap` for a
   Redis snapshot). `mode=all` response-cache hits intentionally omit this
   header. Also verify current completed-session data. Watch proxy/API/worker
   logs for errors, latency, provider failures, disk growth and archive repairs.
9. If any required gate fails, restore the previous proxy upstream immediately.
   Reconcile any post-switch private writes before a rollback; old and new private
   state do not synchronize automatically. Retain the candidate for diagnosis.
   The old server's observed data issue means rollback restores the prior service,
   not necessarily healthy/fresh data; communicate that limitation.
10. After acceptance, stop all legacy Rust API replicas and Rust ingestion workers.
    Disable their systemd/orchestrator restart policy so they stay stopped.
    Check the proxy has no old upstreams. Retain Postgres, Redis and legacy archive
    data for rollback until all consumers/history are verified. Stop shared
    dependencies only after confirming no other service uses them. Never run
    `docker compose down -v` as part of this migration.
11. Document the final image, host, mounts, bucket/prefix, worker scope, backup and
    recovery procedure. Remove temporary restore/verification databases, sidecars
    and exact rehearsal object prefixes. Preserve the necessary operational
    rollback backup, active evidence and small verification reports.

## Completion record

- [ ] Docker explorer and main chart website verified against Python.
- [ ] Required regression checks passed with reports attached.
- [ ] Local SQLite + S3 transfer verified, or empty bootstrap coverage accepted.
- [ ] Catalog initialization works without the old public API.
- [ ] Required markets have ongoing ingestion and acceptable freshness.
- [ ] Public API routes exclusively to Python; TLS/domain unchanged.
- [ ] Old Rust API/workers stopped and automatic restart disabled.
- [ ] Rollback backup retained; temporary verification storage cleaned.
