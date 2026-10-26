#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "Run this installer as the unprivileged Compose owner, not as root." >&2
    exit 1
fi

OPS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$OPS_DIR/../.." && pwd)"
STATE_ROOT="${HOME:?HOME must be set}/.aipriceaction/sqlite-backup"
CONFIG_DIR="$STATE_ROOT/config"
MARKER_BEGIN="# BEGIN aipriceaction-sqlite-backup managed"
MARKER_END="# END aipriceaction-sqlite-backup managed"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

[[ -f "$PROJECT_DIR/docker-compose.yml" && -f "$PROJECT_DIR/.env" ]] \
    || die "Compose project or production .env is missing."
command -v crontab >/dev/null || die "crontab is not installed."
systemctl is-active --quiet cron \
    || die "The cron daemon is not running; ask the host administrator to enable it."

mkdir -p "$HOME/.aipriceaction" "$STATE_ROOT" "$CONFIG_DIR" \
    "$STATE_ROOT/runs" "$STATE_ROOT/work" "$STATE_ROOT/locks"
chmod 700 "$HOME/.aipriceaction" "$STATE_ROOT" "$CONFIG_DIR" \
    "$STATE_ROOT/runs" "$STATE_ROOT/work" "$STATE_ROOT/locks"
INSTALL_LOG="$STATE_ROOT/install-$(date -u +%Y%m%dT%H%M%SZ).log"
umask 077
exec > >(tee -a "$INSTALL_LOG") 2>&1
echo "install_started_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "project_dir=$PROJECT_DIR"
echo "state_root=$STATE_ROOT"

if [[ ! -s "$CONFIG_DIR/restic-password" ]]; then
    command -v openssl >/dev/null || die "openssl is required to create the Restic password."
    temporary_password="$(mktemp "$CONFIG_DIR/.restic-password.XXXXXX")"
    openssl rand -base64 48 >"$temporary_password"
    chmod 600 "$temporary_password"
    mv "$temporary_password" "$CONFIG_DIR/restic-password"
    echo "Generated the encrypted repository password at $CONFIG_DIR/restic-password."
fi
chmod 600 "$CONFIG_DIR/restic-password"

temporary_config="$(mktemp "$CONFIG_DIR/.backup.env.XXXXXX")"
trap '[[ -z "${temporary_config:-}" ]] || rm -f -- "$temporary_config"' EXIT
(
    cd "$PROJECT_DIR"
    docker compose --profile backup config --format json
) | python3 "$OPS_DIR/render_backup_config.py" "$temporary_config"
chmod 600 "$temporary_config"

if [[ -s "$CONFIG_DIR/backup.env" ]]; then
    existing_repository="$(sed -n 's/^RESTIC_REPOSITORY=//p' "$CONFIG_DIR/backup.env")"
    new_repository="$(sed -n 's/^RESTIC_REPOSITORY=//p' "$temporary_config")"
    [[ "$existing_repository" == "$new_repository" ]] \
        || die "Resolved S3 repository changed; refusing to redirect existing backups automatically."
fi
mv "$temporary_config" "$CONFIG_DIR/backup.env"
temporary_config=""

cd "$PROJECT_DIR"
docker compose --profile backup config -q
docker compose --profile backup build sqlite-backup
docker compose --profile backup pull backup-restic
docker compose --profile backup run --rm --no-deps backup-restic version
docker compose --profile backup run --rm --no-deps sqlite-backup aipa-api verify-backup --help \
    >/dev/null

server_timezone="$(timedatectl show -p Timezone --value 2>/dev/null || date +%Z)"
temporary_crontab="$(mktemp)"
preserved_crontab="$(mktemp)"
trap 'rm -f -- "$temporary_crontab" "$preserved_crontab" "${temporary_config:-}"' EXIT
crontab -l >"$temporary_crontab" 2>/dev/null || true
awk -v start="$MARKER_BEGIN" -v stop="$MARKER_END" '
    $0 == start { if (inside || found) bad=1; inside=1; found=1; next }
    $0 == stop { if (!inside) bad=1; inside=0; next }
    !inside { print }
    END { if (inside || bad) exit 2 }
' "$temporary_crontab" >"$preserved_crontab" \
    || die "Existing managed crontab block is malformed; left crontab unchanged."

cat >>"$preserved_crontab" <<EOF
$MARKER_BEGIN
# Runs in the server's $server_timezone timezone. Backup daily; drill monthly on day 2.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
HOME=$HOME
AIPA_BACKUP_HOME=$STATE_ROOT
35 3 * * * $PROJECT_DIR/ops/backup/backup.sh
0 4 2 * * $PROJECT_DIR/ops/backup/drill.sh
$MARKER_END
EOF

crontab "$preserved_crontab"
echo "Installed cron jobs; unrelated crontab lines were preserved."
echo "Daily backup: 03:35 $server_timezone"
echo "Monthly full restore drill: 04:00 on day 2 ($server_timezone)"
echo "Sundays prune snapshots after that day's backup succeeds."
echo "Run history: $STATE_ROOT/runs/"
echo "Restic password: $CONFIG_DIR/restic-password (store an off-host copy securely)."
echo "install_finished_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
crontab -l
