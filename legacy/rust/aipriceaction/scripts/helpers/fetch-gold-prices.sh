#!/usr/bin/env bash
# Fetch all gold price data and save as CSV in helpers folder.
# Usage: bash scripts/helpers/fetch-gold-prices.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FETCHER="$SCRIPT_DIR/gold-price.py"

echo "=== Fetching gold prices ==="

echo "--- CafeF SJC history ---"
python3 "$FETCHER" cafef-sjc > "$SCRIPT_DIR/cafef-sjc.csv"

echo "--- CafeF SJC OHLCV ---"
python3 "$FETCHER" cafef-sjc --format ohlcv > "$SCRIPT_DIR/cafef-sjc-ohlcv.csv"

echo "--- CafeF Gold Ring history ---"
python3 "$FETCHER" cafef-ring > "$SCRIPT_DIR/cafef-ring.csv"

echo "--- CafeF Gold Ring OHLCV ---"
python3 "$FETCHER" cafef-ring --format ohlcv > "$SCRIPT_DIR/cafef-ring-ohlcv.csv"

echo "--- SJC today (all branches) ---"
python3 "$FETCHER" sjc > "$SCRIPT_DIR/sjc-today.csv"

echo "--- SJC batch (2016-now, resumable) ---"
python3 "$FETCHER" sjc-batch --output "$SCRIPT_DIR/sjc-batch.csv"

echo ""
echo "=== Done. Files in $SCRIPT_DIR/ ==="
ls -lh "$SCRIPT_DIR"/*.csv
