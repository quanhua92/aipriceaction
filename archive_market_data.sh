#!/bin/bash

# Archive market_data folder
# Creates market_data.tar.gz in project root

set -e

ARCHIVE_NAME="market_data.tar.gz"
MARKET_DATA_DIR="market_data"

echo "📦 Creating archive of ${MARKET_DATA_DIR}..."

# Remove existing archive if it exists
if [ -f "$ARCHIVE_NAME" ]; then
    echo "🗑️  Removing existing ${ARCHIVE_NAME}..."
    rm -f "$ARCHIVE_NAME"
fi

# Create tar.gz archive
tar -czf "$ARCHIVE_NAME" "$MARKET_DATA_DIR"

# Get archive size
SIZE=$(du -h "$ARCHIVE_NAME" | cut -f1)

echo "✅ Archive created: ${ARCHIVE_NAME} (${SIZE})"
echo "🎉 Done!"
