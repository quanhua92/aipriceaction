#!/usr/bin/env bash
# Restore PostgreSQL database from a gzipped dump file.
#
# Usage:
#   docker exec -it aipriceaction-postgres /app/scripts/restore-db.sh <file>
#
# WARNING: Drops and recreates the database before restoring.

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <backup-file.sql.gz>"
  echo ""
  echo "Available backups:"
  ls -lht /app/backups/*.sql.gz 2>/dev/null || echo "  (none in /app/backups/)"
  exit 1
fi

BACKUP="$1"

if [ ! -f "$BACKUP" ]; then
  echo "Error: file not found: $BACKUP"
  exit 1
fi

echo "WARNING: This will drop and recreate the aipriceaction database!"
echo "Press Ctrl+C to abort, or Enter to continue..."
read -r

echo "Restoring from $BACKUP ..."

psql -U aipriceaction -d postgres -c "DROP DATABASE IF EXISTS aipriceaction;"
psql -U aipriceaction -d postgres -c "CREATE DATABASE aipriceaction OWNER aipriceaction;"
gunzip -c "$BACKUP" | psql -U aipriceaction -d aipriceaction > /dev/null

echo "Restore complete."
