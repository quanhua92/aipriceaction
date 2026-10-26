#!/usr/bin/env bash
set -Eeuo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if ! begin_run backup; then
    exit 0
fi

RUN_STAGE=preflight
load_backup_config
cleanup_stale_workdirs
compose config -q

DB_BYTES="$(compose exec -T api python -c 'import os; print(os.path.getsize("/data/aipriceaction.sqlite3"))')"
[[ "$DB_BYTES" =~ ^[0-9]+$ && "$DB_BYTES" -gt 0 ]]
SOURCE_DB_BYTES="$DB_BYTES"
export SOURCE_DB_BYTES
export AIPA_RUN_SOURCE_DB_BYTES="$DB_BYTES"
REQUIRED_BYTES=$((DB_BYTES + DB_BYTES / 5))
check_free_space "$REQUIRED_BYTES"
echo "database_bytes=$DB_BYTES"

make_work_dir
RUN_STAGE=snapshot
compose run --rm --no-deps --volume "$WORK_DIR:/backup" sqlite-backup \
    | tee "$RUN_DIR/sqlite-backup.json"
SNAPSHOT="$WORK_DIR/aipriceaction.sqlite3"
[[ -s "$SNAPSHOT" ]]
SNAPSHOT_BYTES="$(stat -c %s "$SNAPSHOT")"
export AIPA_RUN_SNAPSHOT_BYTES="$SNAPSHOT_BYTES"
echo "snapshot_bytes=$SNAPSHOT_BYTES"

RUN_STAGE=restic-repository-check
if ! run_restic "" cat config >/dev/null 2>"$RUN_DIR/restic-config-check.log"; then
    echo "Restic repository config is absent or unreadable; trying init without deleting existing objects."
    if ! run_restic "" init >"$RUN_DIR/restic-init.log" 2>&1; then
        if ! run_restic "" cat config >/dev/null 2>&1; then
            echo "Restic repository could not be initialized or opened." >&2
            cat "$RUN_DIR/restic-init.log" >&2
            exit 1
        fi
        echo "Another run initialized the repository; continuing."
    fi
fi

RUN_STAGE=restic-backup
run_restic /backup backup --json --host "$RESTIC_HOST" --tag "$RESTIC_TAG" \
    /backup/aipriceaction.sqlite3 2>&1 | tee "$RUN_DIR/restic-backup.jsonl"

RUN_STAGE=snapshot-confirmation
run_restic "" snapshots --json --host "$RESTIC_HOST" --tag "$RESTIC_TAG" \
    >"$RUN_DIR/restic-snapshots.json"
python3 - "$RUN_DIR/restic-snapshots.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    snapshots = json.load(stream)
if not snapshots:
    raise SystemExit("Restic reports no snapshot after the backup")
latest = max(snapshots, key=lambda item: item["time"])
print(f"restic_snapshot_count={len(snapshots)} latest_snapshot={latest.get('short_id')}")
PY

if [[ "$(date +%u)" == 7 ]]; then
    RUN_STAGE=weekly-retention
    run_restic "" forget --host "$RESTIC_HOST" --tag "$RESTIC_TAG" \
        --keep-daily "${RESTIC_KEEP_DAILY:-7}" \
        --keep-weekly "${RESTIC_KEEP_WEEKLY:-4}" \
        --keep-monthly "${RESTIC_KEEP_MONTHLY:-12}" \
        --prune --json 2>&1 | tee "$RUN_DIR/restic-retention.jsonl"
fi

RUN_STAGE=complete
RUN_RESULT=success
echo "Backup completed; the temporary snapshot will be removed after this run."
