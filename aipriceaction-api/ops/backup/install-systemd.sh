#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this installer as root, for example: sudo -n $0" >&2
    exit 1
fi

OPS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$OPS_DIR/../.." && pwd)"
BACKUP_USER="${AIPA_BACKUP_USER:-${SUDO_USER:-admin}}"
BACKUP_GROUP="$(id -gn "$BACKUP_USER")"
DOCKER_GROUP="$(getent group docker | cut -d: -f1)"
BACKUP_UID="$(id -u "$BACKUP_USER")"
BACKUP_GID="$(id -g "$BACKUP_USER")"
USER_HOME="$(getent passwd "$BACKUP_USER" | cut -d: -f6)"
STATE_ROOT="$USER_HOME/.aipriceaction/sqlite-backup"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

[[ -n "$DOCKER_GROUP" ]] || die "The docker group does not exist."
[[ -n "$USER_HOME" && -d "$USER_HOME" ]] || die "Could not resolve home for $BACKUP_USER."
[[ -f "$PROJECT_DIR/docker-compose.yml" && -f "$PROJECT_DIR/.env" ]] || die "Compose project or production .env is missing."
[[ "$PROJECT_DIR$USER_HOME$STATE_ROOT" != *'|'* && "$PROJECT_DIR$USER_HOME$STATE_ROOT" != *'&'* ]] \
    || die "Installer paths contain a character not supported by the unit renderer."

install -d -o "$BACKUP_USER" -g "$BACKUP_GROUP" -m 700 \
    "$USER_HOME/.aipriceaction" "$STATE_ROOT" "$STATE_ROOT/config" \
    "$STATE_ROOT/runs" "$STATE_ROOT/work" "$STATE_ROOT/locks"
chown "$BACKUP_USER:$BACKUP_GROUP" "$USER_HOME/.aipriceaction"
chmod 700 "$USER_HOME/.aipriceaction" "$STATE_ROOT" "$STATE_ROOT/config" \
    "$STATE_ROOT/runs" "$STATE_ROOT/work" "$STATE_ROOT/locks"

if [[ ! -s "$STATE_ROOT/config/restic-password" ]]; then
    command -v openssl >/dev/null || die "openssl is required to create the Restic password."
    temporary_password="$(mktemp "$STATE_ROOT/config/.restic-password.XXXXXX")"
    umask 077
    openssl rand -base64 48 >"$temporary_password"
    chown "$BACKUP_USER:$BACKUP_GROUP" "$temporary_password"
    chmod 600 "$temporary_password"
    mv "$temporary_password" "$STATE_ROOT/config/restic-password"
    echo "Generated a new encrypted-repository password at $STATE_ROOT/config/restic-password."
fi
chown "$BACKUP_USER:$BACKUP_GROUP" "$STATE_ROOT/config/restic-password"
chmod 600 "$STATE_ROOT/config/restic-password"

temporary_config="$(mktemp "$STATE_ROOT/config/.backup.env.XXXXXX")"
trap '[[ -z "${temporary_config:-}" ]] || rm -f -- "$temporary_config"' EXIT
(
    cd "$PROJECT_DIR"
    docker compose --profile backup config --format json
) | python3 "$OPS_DIR/render_backup_config.py" "$temporary_config"
chown "$BACKUP_USER:$BACKUP_GROUP" "$temporary_config"
chmod 600 "$temporary_config"

if [[ -s "$STATE_ROOT/config/backup.env" ]]; then
    existing_repository="$(sed -n 's/^RESTIC_REPOSITORY=//p' "$STATE_ROOT/config/backup.env")"
    new_repository="$(sed -n 's/^RESTIC_REPOSITORY=//p' "$temporary_config")"
    [[ "$existing_repository" == "$new_repository" ]] \
        || die "Resolved S3 repository changed; refusing to redirect existing backups automatically."
fi
mv "$temporary_config" "$STATE_ROOT/config/backup.env"
temporary_config=""

cd "$PROJECT_DIR"
docker compose --profile backup config -q
docker compose --profile backup build sqlite-backup
docker compose --profile backup pull backup-restic
docker compose --profile backup run --rm --no-deps backup-restic version
docker compose --profile backup run --rm --no-deps sqlite-backup aipa-api verify-backup --help \
    >/dev/null

render_unit() {
    local input="$1" output="$2" temporary
    temporary="$(mktemp)"
    sed \
        -e "s|@BACKUP_USER@|$BACKUP_USER|g" \
        -e "s|@BACKUP_GROUP@|$BACKUP_GROUP|g" \
        -e "s|@BACKUP_UID@|$BACKUP_UID|g" \
        -e "s|@BACKUP_GID@|$BACKUP_GID|g" \
        -e "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
        -e "s|@USER_HOME@|$USER_HOME|g" \
        -e "s|@STATE_ROOT@|$STATE_ROOT|g" \
        "$input" >"$temporary"
    install -o root -g root -m 644 "$temporary" "$output"
    rm -f -- "$temporary"
}

render_unit "$OPS_DIR/aipriceaction-sqlite-backup.service.in" \
    /etc/systemd/system/aipriceaction-sqlite-backup.service
render_unit "$OPS_DIR/aipriceaction-sqlite-backup.timer.in" \
    /etc/systemd/system/aipriceaction-sqlite-backup.timer
render_unit "$OPS_DIR/aipriceaction-sqlite-drill.service.in" \
    /etc/systemd/system/aipriceaction-sqlite-drill.service
render_unit "$OPS_DIR/aipriceaction-sqlite-drill.timer.in" \
    /etc/systemd/system/aipriceaction-sqlite-drill.timer

systemd-analyze verify \
    /etc/systemd/system/aipriceaction-sqlite-backup.service \
    /etc/systemd/system/aipriceaction-sqlite-backup.timer \
    /etc/systemd/system/aipriceaction-sqlite-drill.service \
    /etc/systemd/system/aipriceaction-sqlite-drill.timer
systemctl daemon-reload
systemctl enable --now aipriceaction-sqlite-backup.timer aipriceaction-sqlite-drill.timer

echo "Installed daily backup and monthly restore-drill timers."
echo "Run history: $STATE_ROOT/runs/"
echo "Restic password: $STATE_ROOT/config/restic-password (store an off-host copy securely)."
systemctl list-timers --all 'aipriceaction-sqlite-*'
