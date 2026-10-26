#!/usr/bin/env bash
set -Eeuo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if ! begin_run restore-drill; then
    exit 0
fi

RUN_STAGE=preflight
load_backup_config
cleanup_stale_workdirs
compose config -q
DB_BYTES="$(compose exec -T api python -c 'import os; print(os.path.getsize("/data/aipriceaction.sqlite3"))')"
[[ "$DB_BYTES" =~ ^[0-9]+$ && "$DB_BYTES" -gt 0 ]]
export SOURCE_DB_BYTES="$DB_BYTES"
REQUIRED_BYTES=$((DB_BYTES + DB_BYTES / 5))
check_free_space "$REQUIRED_BYTES"

make_work_dir
RUN_STAGE=restic-snapshot-list
run_restic "" snapshots --json --host "$RESTIC_HOST" --tag "$RESTIC_TAG" \
    >"$RUN_DIR/restic-snapshots.json"
python3 - "$RUN_DIR/restic-snapshots.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    snapshots = json.load(stream)
if not snapshots:
    raise SystemExit("No Restic snapshot is available for the restore drill")
latest = max(snapshots, key=lambda item: item["time"])
print(f"drill_snapshot={latest['short_id']} time={latest['time']}")
PY

RUN_STAGE=restic-restore
run_restic /restore restore latest --host "$RESTIC_HOST" --tag "$RESTIC_TAG" \
    --target /restore 2>&1 | tee "$RUN_DIR/restic-restore.log"
RESTORED_DB="$WORK_DIR/backup/aipriceaction.sqlite3"
[[ -s "$RESTORED_DB" ]]
RESTORED_BYTES="$(stat -c %s "$RESTORED_DB")"
echo "restored_bytes=$RESTORED_BYTES"

RUN_STAGE=sqlite-integrity-check
compose run --rm --no-deps --volume "$WORK_DIR:/restore:ro" sqlite-backup \
    aipa-api verify-backup /restore/backup/aipriceaction.sqlite3 --full \
    | tee "$RUN_DIR/verify.json"
RUN_STAGE=complete
RUN_RESULT=success
echo "Restore drill passed; the restored database and sidecars will be removed after this run."
