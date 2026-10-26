# Production SQLite backups

The production user's cron entries run one-shot Docker Compose jobs. SQLite's
Online Backup API makes a consistent snapshot of the shared
`/data/aipriceaction.sqlite3` file while the API and workers stay online. The
snapshot helper uses the same root container owner as the API because the
production database and WAL are root-owned; it runs no schema initialization or
SQL writes. The host staging directory remains private to `admin`. Restic
encrypts and deduplicates the snapshot into the S3 repository prefix:

```text
backups/aipriceaction-prod/sqlite-restic/
```

The first run sends the full current database. Later runs read the snapshot and
upload only new content chunks. They still create and scan a temporary local
snapshot about as large as the SQLite database. A large rewrite, vacuum, or
backfill can make one run upload substantially more than usual. The exact
network amount is recorded in each run's Restic summary; do not estimate it
before the first production runs.

## Host files and run records

The installer creates this private directory under the service user's home:

```text
~/.aipriceaction/sqlite-backup/
├── config/
│   ├── backup.env          # repository prefix and retention settings
│   └── restic-password     # generated once, mode 0600
├── locks/
└── runs/
    └── <job>-<UTC-time>-<pid>/
        ├── run.log
        ├── run.json          # result, stage, sizes, duration, Restic summary
        └── Restic/SQLite operation logs
```

Full SQLite snapshots and restored drill copies live temporarily under `work/`
and are removed on success or failure. The scripts also remove only their own
stale temporary directories after an unclean host shutdown. Run reports and logs
are retained. The Restic password is not written to logs or S3; store an
off-host copy in a password manager. Without it, the encrypted repository
cannot be restored.

## Schedule

- Daily backup at 03:35 in the server's `Asia/Ho_Chi_Minh` timezone.
- Monthly full restore and `PRAGMA integrity_check` on day 2 at 04:00 in that
  timezone. Day 2 avoids overlapping the first-of-month backup.
- Weekly retention runs after the Sunday backup succeeds: keep 7 daily, 4
  weekly, and 12 monthly snapshots, then prune unreferenced chunks.
- All jobs share a nonblocking lock. A concurrent cron or manual run is
  recorded as `skipped-overlap` and cannot read or delete another job's files.

The restore drill downloads one complete database once per month. It checks
Restic's restored file, then validates SQLite integrity, required market tables,
schema version, and candle/series counts. It never opens the live DB for writing
or replaces it.

## Install and operate

On production, as the unprivileged Docker Compose owner, from this directory:

```sh
ops/backup/install-cron.sh
ops/backup/backup.sh
ops/backup/drill.sh
crontab -l
```

The installer requires the production Compose configuration to resolve to
an HTTPS S3 endpoint with write credentials. It refuses to change an existing
Restic repository prefix automatically. It builds a separate API backup image,
so installing the backup command does not replace the image used by running API
containers. It pulls the version-pinned `restic/restic:0.19.1` image and adds
only its marked lines to the Compose owner's crontab. It does not restart the
API, workers, Nginx, or database. Success output stays in the run folder; failed
jobs send a short cron mail if local mail delivery is configured.
The encrypted Restic password is created once in the private config directory;
the install output never prints it. The S3 key used by Restic must allow list,
read, write, and delete under the backup prefix because weekly pruning removes
unreferenced chunks. If the S3 provider supports prefix-scoped keys, use one
restricted to this repository prefix.

Inspect a particular run:

```sh
ls -1t ~/.aipriceaction/sqlite-backup/runs
cat ~/.aipriceaction/sqlite-backup/runs/<run-id>/run.json
tail -100 ~/.aipriceaction/sqlite-backup/runs/<run-id>/run.log
```

If production recovery is needed, restore to a **new** path, verify it, and
perform a separately reviewed cutover. Never point a restore command at the
live SQLite file. The repository's `aipa-api restore` command also refuses an
existing destination and checks SQLite integrity before creating the target.

## Adversarial checks built into the procedure

1. **Concurrent writes and WAL activity:** snapshot through SQLite's online
   backup API; never `cp` the live database. One backup step avoids SQLite
   restarting from page zero after each page batch when another process writes.
   In production WAL mode, API readers and worker writes can continue during the
   snapshot read lock. The snapshot is a consistent point-in-time image.
2. **Disk exhaustion:** before snapshot/restore, require free space of at least
   the live DB size plus 20%. If that space is unavailable, fail before copying.
3. **Interrupted upload:** do not prune unless the backup completed and a
   matching tagged snapshot is visible in S3. Restic can resume/deduplicate on
   the next run.
4. **Wrong bucket, missing scheme, or bad credentials:** derive the repository
   from Compose's resolved API S3 configuration; require HTTPS and nonempty
   S3 credentials. A failed initial connection leaves the service failed and
   records the reason rather than reporting a successful backup.
5. **Public bucket exposure:** only Restic ciphertext is uploaded. Keep its
   password outside the bucket and outside the server. Bucket deletion or
   account compromise can still destroy both archives and backups because this
   plan intentionally uses the same S3 bucket.
6. **Overlapping jobs and cleanup:** use one `flock` lock for all backup and
   restore work. Each run writes logs/JSON under `runs/`; temporary SQLite
   files and SQLite sidecars are removed by the exit handler.
7. **Bad restore point:** the monthly drill selects the latest snapshot tagged
   for this production host, restores it to a fresh temporary directory, and
   runs full `integrity_check`. The restored database is opened immutable and
   read-only because Restic stores the single SQLite snapshot file, not WAL
   sidecars. Failed drills retain logs but remove the full restored database.

## Phase commits

1. **Point-in-time snapshot and verifier.** The `backup` command uses SQLite's
   Online Backup API without schema initialization/migrations or SQL writes.
   Its source handle is read/write only so SQLite can coordinate the root-owned
   WAL lock file. The verifier supports quick checks and full restore drills.
   Commit: `290814a`.
2. **Automated encrypted storage and operations.** Add the separate Compose
   backup profile, Restic backup/drill scripts, run reports, and an initial
   systemd installer. Commit: `a657b08`.
3. **Rootless scheduling for gc.** Replace system units with an idempotent
   installer for the Compose owner's crontab because `admin` has no
   passwordless sudo. Keep logs and generated config under the admin home.
   Commit: `79673a5`.
4. **Root-owned WAL support.** Run the one-shot snapshot container as root to
   coordinate locks on the existing root-owned SQLite/WAL files. Keep the host
   staging directory private, and give the Restic process read access only to
   the temporary snapshot. Commit: `2ca186f`.
5. **Single-step snapshot under production writes.** A live rehearsal showed
   512-page batches repeatedly restarting under worker writes. Switch to one
   SQLite backup step and add a WAL regression test. Commit: `efbb900`.
6. **Run status clarity.** Store the final stage separately from the failure
   stage; only failed runs name a `failed_stage`. Add tests for both outcomes.
   Commit: `f1b3ec3`.
7. **WAL restore verification.** A production drill confirmed that a restored
   WAL-mode database cannot be opened read-only without its `-wal` and `-shm`
   files. Use SQLite immutable read-only mode and cover this case in tests.
   This is the next code commit after `f1b3ec3`.
8. **Production acceptance.** Complete the backup and restore drill on `gc`;
   retain run logs and JSON, and delete only temporary database copies.
