#!/bin/bash

# Archive spot folder
# Creates spot.tar.gz in project root

set -e

ARCHIVE_NAME="spot.tar.gz"
SPOT_DATA_DIR="spot"

echo "📦 Creating archive of ${SPOT_DATA_DIR}..."

# Check if spot directory exists
if [ ! -d "$SPOT_DATA_DIR" ]; then
    echo "❌ Error: ${SPOT_DATA_DIR} directory not found!"
    exit 1
fi

# Count spot pairs and files
SPOT_COUNT=$(find "$SPOT_DATA_DIR/daily/klines" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
FILE_COUNT=$(find "$SPOT_DATA_DIR" -type f -name "*.zip" | wc -l | tr -d ' ')

echo "📊 Found ${SPOT_COUNT} spot trading pairs with ${FILE_COUNT} ZIP files"

# Remove existing archive if it exists
if [ -f "$ARCHIVE_NAME" ]; then
    echo "🗑️  Removing existing ${ARCHIVE_NAME}..."
    rm -f "$ARCHIVE_NAME"
fi

# Create tar.gz archive with verbose output
echo "🗜️  Compressing..."
tar -czf "$ARCHIVE_NAME" "$SPOT_DATA_DIR"

# Verify archive was created and get stats
if [ ! -f "$ARCHIVE_NAME" ]; then
    echo "❌ Error: Archive creation failed!"
    exit 1
fi

SIZE=$(du -h "$ARCHIVE_NAME" | cut -f1)
ARCHIVE_FILE_COUNT=$(tar -tzf "$ARCHIVE_NAME" | grep -c "\\.zip$" || true)
ARCHIVE_1M_COUNT=$(tar -tzf "$ARCHIVE_NAME" | grep -c "/1m/" || true)
ARCHIVE_1H_COUNT=$(tar -tzf "$ARCHIVE_NAME" | grep -c "/1h/" || true)
ARCHIVE_1D_COUNT=$(tar -tzf "$ARCHIVE_NAME" | grep -c "/1d/" || true)

echo "✅ Archive created: ${ARCHIVE_NAME} (${SIZE})"
echo "📁 Archived ${ARCHIVE_FILE_COUNT} ZIP files from ${SPOT_COUNT} spot trading pairs"
echo "   📊 Breakdown: ${ARCHIVE_1M_COUNT} 1m intervals, ${ARCHIVE_1H_COUNT} 1h intervals, ${ARCHIVE_1D_COUNT} 1d intervals"
echo "🎉 Done!"
