#!/bin/bash

# Archive market_data folder
# Creates market_data.tar.gz in project root

set -e

ARCHIVE_NAME="market_data.tar.gz"
MARKET_DATA_DIR="market_data"

echo "📦 Creating archive of ${MARKET_DATA_DIR}..."

# Check if market_data directory exists
if [ ! -d "$MARKET_DATA_DIR" ]; then
    echo "❌ Error: ${MARKET_DATA_DIR} directory not found!"
    exit 1
fi

# Count tickers and files
TICKER_COUNT=$(find "$MARKET_DATA_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
FILE_COUNT=$(find "$MARKET_DATA_DIR" -type f -name "*.csv" | wc -l | tr -d ' ')

echo "📊 Found ${TICKER_COUNT} tickers with ${FILE_COUNT} CSV files"

# Remove existing archive if it exists
if [ -f "$ARCHIVE_NAME" ]; then
    echo "🗑️  Removing existing ${ARCHIVE_NAME}..."
    rm -f "$ARCHIVE_NAME"
fi

# Create tar.gz archive with verbose output
echo "🗜️  Compressing..."
tar -czf "$ARCHIVE_NAME" "$MARKET_DATA_DIR"

# Verify archive was created and get stats
if [ ! -f "$ARCHIVE_NAME" ]; then
    echo "❌ Error: Archive creation failed!"
    exit 1
fi

SIZE=$(du -h "$ARCHIVE_NAME" | cut -f1)
ARCHIVE_FILE_COUNT=$(tar -tzf "$ARCHIVE_NAME" | grep -c "\.csv$" || true)
ARCHIVE_1D_COUNT=$(tar -tzf "$ARCHIVE_NAME" | grep -c "1D\.csv$" || true)
ARCHIVE_1H_COUNT=$(tar -tzf "$ARCHIVE_NAME" | grep -c "1H\.csv$" || true)
ARCHIVE_1M_COUNT=$(tar -tzf "$ARCHIVE_NAME" | grep -c "1m\.csv$" || true)

echo "✅ Archive created: ${ARCHIVE_NAME} (${SIZE})"
echo "📁 Archived ${ARCHIVE_FILE_COUNT} CSV files from ${TICKER_COUNT} tickers"
echo "   📊 Breakdown: ${ARCHIVE_1D_COUNT} daily, ${ARCHIVE_1H_COUNT} hourly, ${ARCHIVE_1M_COUNT} minute"
echo "🎉 Done!"
