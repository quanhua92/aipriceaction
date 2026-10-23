# Production migration runbook

This guide covers a move from the retired Rust API to the Python API. Verify
the live production state before choosing a cutover date.

## Before deployment

The repository's Docker Compose file is for local development. It starts a
local RustFS service and uses local defaults. It is not a production
configuration. Do not copy `.env.example` over a production `.env`.

Prepare a production deployment that uses the existing data directory and the
approved writable S3-compatible endpoint, bucket, credentials, and prefix.
Keep the current Parquet prefix until its objects and manifests have been
copied and verified under `parquet/`. Public CSV files stay at the bucket root
under `ohlcv/`; Parquet is stored separately under `parquet/`.

Choose one data path:

- **Start empty:** initialize production storage, load the approved ticker
  catalog, and let the worker build history. Longer lookbacks may be incomplete
  until ingestion catches up.
- **Transfer local data:** move SQLite and its matching Parquet objects and
  manifests together. Verify checksums and history reads before cutover. Do not
  copy SQLite alone when it points to archive objects.

Keep one verified rollback copy. Use temporary directories for rehearsal and
restore copies, and remove them when checks finish. Follow the storage rules in
root [`AGENTS.md`](../AGENTS.md).

## Cutover steps

1. Record the Python commit or image, Compose configuration, SQLite schema,
   S3 endpoint/bucket/prefix, catalog snapshot, and provider order.
2. Set production secrets, CORS origins, provider access, storage settings,
   retention, and data mounts. Keep existing production `.env` values unless
   each change is intentional.
3. Initialize an empty store or rehearse the selected data transfer. Check
   SQLite integrity, archive checksums, manifests, and representative reads.
4. Start the Python API and worker beside the current service on an isolated
   port. Confirm fresh rows for VN, crypto, Yahoo, and SJC after a restart.
5. Test the real web client, all supported routes and query options, SDK/CLI
   reads, Notes flows, authentication, and failure responses. Check data
   freshness by market and interval; `/health` alone is not enough.
6. Switch ingress to Python only after the tests pass. Watch errors, latency,
   ingestion, and data freshness during the observation period.
7. Retire the old service only after acceptance. Keep the verified rollback
   copy until the observation period ends.

## Rollback

Use the old service only if it passes live health and data-freshness checks. If
it does not, route traffic to the agreed recovery or maintenance path instead.
Keep Python's database, archives, and receipts available for diagnosis. Do not
merge Python data into the old database while the authoritative store is
unclear. Record the affected versions and time window.

## Remaining checks

See [`TODO.md`](TODO.md) for open gates and [`VALIDATION.md`](VALIDATION.md) for
local test evidence. Local measurements do not prove production readiness.
