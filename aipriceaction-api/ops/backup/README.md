# Production SQLite backups

The production systemd timers run one-shot Docker Compose jobs. SQLite's Online
Backup API makes a consistent snapshot of the shared `/data/aipriceaction.sqlite3`
file while the API and workers stay online. Restic encrypts and deduplicates the
snapshot into the S3 repository prefix:

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
        ├── run.json
        └── Restic/SQLite operation logs
```

Full SQLite snapshots and restored drill copies live temporarily under `work/`
and are removed on success or failure. The scripts also remove only their own
stale temporary directories after an unclean host shutdown. Run reports and logs
are retained. The Restic password is not written to logs or S3; store an
off-host copy in a password manager. Without it, the encrypted repository
cannot be restored.

## Schedule

- Daily backup at 20:35 UTC (03:35 Vietnam time).
- Monthly full restore and `PRAGMA integrity_check` on the first day at 21:00
  UTC (04:00 Vietnam time).
- Weekly retention runs after the Sunday backup succeeds: keep 7 daily, 4
  weekly, and 12 monthly snapshots, then prune unreferenced chunks.
- All jobs share a nonblocking lock. A concurrent timer or manual run is
  recorded as `skipped-overlap` and cannot read or delete another job's files.

The restore drill downloads one complete database once per month. It checks
Restic's restored file, then validates SQLite integrity, required market tables,
schema version, and candle/series counts. It never opens the live DB for writing
or replaces it.

## Install and operate

On production, from this directory:

```sh
sudo -n ops/backup/install-systemd.sh
sudo systemctl start aipriceaction-sqlite-backup.service
sudo systemctl start aipriceaction-sqlite-drill.service
systemctl list-timers --all 'aipriceaction-sqlite-*'
```

The install script requires the production Compose configuration to resolve to
an HTTPS S3 endpoint with write credentials. It refuses to change an existing
Restic repository prefix automatically. It builds a separate API backup image,
so installing the backup command does not replace the image used by running API
containers. It pulls the version-pinned `restic/restic:0.19.1` image and installs
systemd units without restarting the API, workers, Nginx, or database.
The encrypted Restic password is created once in the private config directory;
the install output never prints it. The S3 key used by Restic must allow list,
read, write, and delete under the backup prefix because weekly pruning removes
unreferenced chunks. If the S3 provider supports prefix-scoped keys, use one
restricted to this repository prefix.

Inspect a particular run:

```sh
ls -1t ~/.aipriceaction/sqlite-backup/runs
cat ~/.aipriceaction/sqlite-backup/runs/<run-id>/run.json
sudo journalctl -u aipriceaction-sqlite-backup.service -n 100 --no-pager
```

If production recovery is needed, restore to a **new** path, verify it, and
perform a separately reviewed cutover. Never point a restore command at the
live SQLite file. The repository's `aipa-api restore` command also refuses an
existing destination and checks SQLite integrity before creating the target.

## Adversarial checks built into the procedure

1. **Concurrent writes and WAL activity:** snapshot through SQLite's online
   backup API; never `cp` the live database. Workers and API replicas continue
   operating, while SQLite takes short read locks as pages are copied.
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
   runs full `integrity_check`. Failed drills retain logs but remove the full
   restored database.

## Phase commits

1. **Read-only, point-in-time snapshot and verifier.** The `backup` command
   opens the live DB read-only and skips schema initialization/migrations. The
   verifier supports quick daily checks and full restore drills.
2. **Automated encrypted storage and operations.** Add a separate Compose
   backup profile, Restic scripts, run reports, systemd timers, and installer.
3. **Production install and acceptance.** Pull the committed change on `gc`,
   install timers, run one backup, run one restore drill, and retain only small
   run evidence.
