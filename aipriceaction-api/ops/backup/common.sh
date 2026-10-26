#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_OPS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$BACKUP_OPS_DIR/../.." && pwd)"
STATE_ROOT="${AIPA_BACKUP_HOME:-${HOME:?HOME must be set}/.aipriceaction/sqlite-backup}"
CONFIG_DIR="$STATE_ROOT/config"
RUNS_DIR="$STATE_ROOT/runs"
WORK_ROOT="$STATE_ROOT/work"
PASSWORD_FILE="$CONFIG_DIR/restic-password"
CONFIG_FILE="$CONFIG_DIR/backup.env"
RUN_ID=""
RUN_DIR=""
LOG_FILE=""
WORK_DIR=""
RUN_KIND=""
RUN_STAGE="starting"
RUN_RESULT="failed"
RUN_STARTED_AT=""
WORK_CLEANUP_RESULT="not-created"

begin_run() {
    RUN_KIND="$1"
    RUN_ID="${RUN_KIND}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    RUN_DIR="$RUNS_DIR/$RUN_ID"
    mkdir -p "$CONFIG_DIR" "$RUNS_DIR" "$WORK_ROOT" "$STATE_ROOT/locks"
    chmod 700 "$STATE_ROOT" "$CONFIG_DIR" "$RUNS_DIR" "$WORK_ROOT" "$STATE_ROOT/locks"
    mkdir -m 700 "$RUN_DIR"
    LOG_FILE="$RUN_DIR/run.log"
    umask 077
    exec > >(tee -a "$LOG_FILE") 2>&1
    RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    exec 9>"$STATE_ROOT/locks/all-jobs.lock"
    if ! flock -n 9; then
        RUN_STAGE="lock"
        RUN_RESULT="skipped-overlap"
        echo "Another SQLite backup operation holds the global lock; this run is skipped."
        return 1
    fi

    echo "run_id=$RUN_ID"
    echo "kind=$RUN_KIND"
    echo "started_at_utc=$RUN_STARTED_AT"
    echo "project_dir=$PROJECT_DIR"
    echo "state_root=$STATE_ROOT"
    return 0
}

load_backup_config() {
    if [[ ! -r "$CONFIG_FILE" ]]; then
        echo "Missing $CONFIG_FILE; run ops/backup/install-systemd.sh first." >&2
        return 1
    fi
    local key value
    while IFS='=' read -r key value; do
        case "$key" in
            RESTIC_REPOSITORY) export RESTIC_REPOSITORY="$value" ;;
            RESTIC_HOST) export RESTIC_HOST="$value" ;;
            RESTIC_TAG) export RESTIC_TAG="$value" ;;
            RESTIC_KEEP_DAILY) export RESTIC_KEEP_DAILY="$value" ;;
            RESTIC_KEEP_WEEKLY) export RESTIC_KEEP_WEEKLY="$value" ;;
            RESTIC_KEEP_MONTHLY) export RESTIC_KEEP_MONTHLY="$value" ;;
        esac
    done < "$CONFIG_FILE"
    if [[ -z "${RESTIC_REPOSITORY:-}" || -z "${RESTIC_HOST:-}" || -z "${RESTIC_TAG:-}" ]]; then
        echo "Backup configuration is missing a required Restic setting." >&2
        return 1
    fi
    if [[ ! -s "$PASSWORD_FILE" ]]; then
        echo "Missing Restic repository password at $PASSWORD_FILE." >&2
        return 1
    fi
}

make_work_dir() {
    WORK_DIR="$(mktemp -d "$WORK_ROOT/${RUN_ID}.XXXXXX")"
    chmod 700 "$WORK_DIR"
    WORK_CLEANUP_RESULT="pending"
}

compose() {
    (cd "$PROJECT_DIR" && docker compose --profile backup "$@")
}

run_restic() {
    local work_mount="${1:-}"
    if [[ $# -gt 0 ]]; then
        shift
    fi
    local -a run_args=(run --rm --no-deps --volume "$PASSWORD_FILE:/run/secrets/restic-password:ro")
    if [[ -n "$work_mount" ]]; then
        if [[ "$work_mount" == /backup ]]; then
            run_args+=(--volume "$WORK_DIR:$work_mount:ro")
        else
            run_args+=(--volume "$WORK_DIR:$work_mount")
        fi
    fi
    compose "${run_args[@]}" backup-restic -o s3.bucket-lookup=path "$@"
}

finish_run() {
    local rc=$?
    trap - EXIT
    set +e
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        rm -rf -- "$WORK_DIR"
        if [[ -e "$WORK_DIR" ]]; then
            WORK_CLEANUP_RESULT="failed"
            [[ $rc -ne 0 ]] || rc=1
        else
            WORK_CLEANUP_RESULT="removed"
        fi
    fi
    if [[ $rc -eq 0 && "$RUN_RESULT" == "failed" ]]; then
        RUN_RESULT="success"
    elif [[ $rc -ne 0 && "$RUN_RESULT" != "skipped-overlap" ]]; then
        RUN_RESULT="failed"
    fi
    local ended_at
    ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    AIPA_RUN_ID="$RUN_ID" \
    AIPA_RUN_KIND="$RUN_KIND" \
    AIPA_RUN_RESULT="$RUN_RESULT" \
    AIPA_RUN_STAGE="$RUN_STAGE" \
    AIPA_RUN_STARTED_AT="$RUN_STARTED_AT" \
    AIPA_RUN_ENDED_AT="$ended_at" \
    AIPA_RUN_EXIT_CODE="$rc" \
    AIPA_RUN_WORK_CLEANUP="$WORK_CLEANUP_RESULT" \
    AIPA_RUN_SOURCE_DB_BYTES="${SOURCE_DB_BYTES:-}" \
    AIPA_RUN_SNAPSHOT_BYTES="${SNAPSHOT_BYTES:-}" \
    python3 "$BACKUP_OPS_DIR/write_run_record.py" "$RUN_DIR" || {
        echo "Could not write the run summary JSON." >&2
        [[ $rc -ne 0 ]] || rc=1
    }
    echo "result=$RUN_RESULT stage=$RUN_STAGE exit_code=$rc work_cleanup=$WORK_CLEANUP_RESULT"
    exit "$rc"
}

cleanup_stale_workdirs() {
    # All job types use one lock, so any remaining matching directory is from a
    # process that was killed before its EXIT cleanup ran. This removes only
    # this job's known temporary namespace, never retained run reports.
    local candidate
    shopt -s nullglob
    for candidate in "$WORK_ROOT"/*; do
        [[ -d "$candidate" && "${candidate##*/}" == *-*T*Z-*.* ]] || continue
        echo "Removing stale temporary work directory: ${candidate##*/}"
        rm -rf -- "$candidate"
    done
    shopt -u nullglob
}

check_free_space() {
    local required_bytes="$1"
    local available_kib available_bytes
    available_kib="$(df -Pk "$STATE_ROOT" | awk 'END {print $4}')"
    available_bytes=$((available_kib * 1024))
    echo "available_bytes=$available_bytes required_bytes=$required_bytes"
    if (( available_bytes < required_bytes )); then
        echo "Insufficient free space for a safe temporary SQLite copy." >&2
        return 1
    fi
}

trap finish_run EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
