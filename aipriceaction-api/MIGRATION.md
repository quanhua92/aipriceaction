# Production migration: Rust API to Python API

**Status:** not cut over in the last recorded production baseline (2026-10-20).
The Rust API was still serving production, and its reported ticker data was
stale/empty. Treat those as dated observations; verify live production before
scheduling a change. The Rust implementation is deprecated and preserved under
[`../legacy/rust/`](../legacy/rust/README.md).

The local Compose configuration uses API port `8899`; confirm `/health` before using that instance. The
main website is a separate Vite app; local development uses a temporary copy
served on `5173` and a proxy to Python. No sibling web source is changed here.

## Choose the data path

**Start empty** when copying local data cannot be rehearsed cleanly or the local
catalog/history is not accepted as production data. Initialize the production
SQLite database and object archive, load the approved ticker catalog, start the
all-source worker, and let it ingest. This is the simplest cutover but the
service may need time to build history before longer lookbacks and analysis are
complete.

**Transfer local state** only when it materially reduces warm-up time and the
exact local database/archive is verified. Move the SQLite database and its
matching archive prefix together. Do not copy SQLite alone when its archive
index refers to Parquet objects. Preserve operational receipts, manifests,
checksums, and the ticker identity ordering needed by merged Yahoo/SJC queries.

For a transfer rehearsal, stop the local worker and API before making the
operational before-image. Run SQLite integrity checks; copy archive objects and
manifests to an isolated destination prefix; verify checksums and restore the
index; then test reads before switching traffic. Keep one verified rollback
before-image. Do not create extra full database copies for repeated checks, and
clean temporary rehearsal copies when finished. Follow root `AGENTS.md` for
storage lifecycle requirements.

## Cutover sequence

1. **Freeze the release.** Record the Python commit/image tag, Compose config,
   SQLite schema version, object-store endpoint/bucket/prefix, catalog snapshot,
   and configured provider order. Do not change the old Rust deployment yet.
2. **Prepare production config.** Set `REFRESH_SECRET`, optional `SYNC_TOKEN`,
   exact web CORS origins, S3 credentials/bucket/prefix, data mount, provider
   access/proxies, retention, and API port. Keep RustFS for local testing; use
   the approved production S3-compatible store in production.
3. **Select empty start or data transfer** using the rules above. For an empty
   start, verify the catalog source and first worker cycle. For transfer, verify
   SQLite, each required series, matching archives/manifests, and restore-index.
4. **Start Python side by side.** Start the API and all-source worker on an
   isolated port/origin. Confirm worker restart/resume behavior and that VN,
   crypto, Yahoo, and SJC receive fresh rows. Do not share the old Redis cache
   unless that topology is intentionally configured and validated.
5. **Run acceptance through the real web client.** Check the home-page 19-call
   matrix, charts, indicators, all modes, `group`/`name`, RRG, volume profile,
   Notes uploads/reads/deletes, SDK/CLI reads, health, and auth rejection/success.
   Confirm freshness by market and interval; compare selected OHLCV against
   verified source evidence. A fast health response alone is insufficient.
6. **Switch ingress.** Route the production API origin to Python. Keep the old
   Rust service available for rollback until the agreed observation period ends.
   Watch errors, latency, ingestion, and data freshness.
7. **Retire Rust.** After acceptance, stop the old Rust service, archive its
   deployment configuration, revoke unused credentials, and retain the
   operational rollback snapshot. The old GitHub image publisher is disabled
   and archived under `legacy/rust/workflows/`.

## Rollback

Before cutover, verify a rollback target directly: check its health and confirm
fresh, representative ticker data for the required markets, intervals, and
dates. The last recorded Rust production baseline was stale/empty, so it is not
a rollback target unless it has since been repaired and passes these checks.

If Python fails acceptance or freshness checks, route back to Rust only when
that live validation passed. Otherwise, route to the agreed maintenance or
known-good recovery path; do not send users to a stale Rust service. Preserve
Python SQLite, object archives, and receipts for diagnosis, and do not merge
them into the Rust database during rollback. Record the trigger, time window,
and exact image revisions. Keep writes paused or isolated while the authoritative
backend is unclear.

## Open gates

See [`TODO.md`](TODO.md) and [`VALIDATION.md`](VALIDATION.md) for current local
measurements and remaining checks. The 2026-10-21 local homepage benchmark does
not establish production readiness or production data parity.
