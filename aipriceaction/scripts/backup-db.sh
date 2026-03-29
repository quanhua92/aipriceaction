#!/usr/bin/env bash
# Backup PostgreSQL database to a timestamped dump file.
#
# Usage:
#   docker exec aipriceaction-postgres /app/scripts/backup-db.sh       # in postgres container
#   PGHOST=localhost ./scripts/backup-db.sh                            # local psql
#   docker exec aipriceaction-postgres /app/scripts/backup-db.sh /path  # custom dir

set -euo pipefail

BACKUP_DIR="${1:-/app/backups}"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="aipriceaction_${TIMESTAMP}.sql.gz"
DEST="$BACKUP_DIR/$FILENAME"

echo "Backing up aipriceaction database..."

pg_dump -U aipriceaction -d aipriceaction --format=plain | gzip > "$DEST"

SIZE=$(du -h "$DEST" | cut -f1)
echo "Saved: $DEST ($SIZE)"
