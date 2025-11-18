#!/bin/bash

# Debug performance for CSV endpoint
# Usage: ./scripts/debug_perf_csv.sh

set -e

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== CSV Performance Debug Test ==="
echo "Working directory: $(pwd)"
echo ""

# Stop any running server
echo "Stopping existing server..."
pkill -f "aipriceaction serve" 2>/dev/null || true
sleep 2

# Start server with debug logging
echo "Starting server with RUST_LOG=debug..."
RUST_LOG=debug "$PROJECT_ROOT/target/release/aipriceaction" serve --port 3000 > /tmp/perf_debug.log 2>&1 &
SERVER_PID=$!
echo "Server PID: $SERVER_PID"

# Wait for background prefetch
echo "Waiting 15 seconds for background prefetch..."
sleep 15

# Make ONE HTTP request
echo "Making HTTP request: /tickers?interval=1D&format=csv&limit=100"
echo ""
time curl -s "http://localhost:3000/tickers?interval=1D&format=csv&limit=100" -o /tmp/test_output.csv
echo ""

# Wait for logs to flush
sleep 1

# Extract performance logs
echo "=== Performance Logs ==="
echo ""
echo "Background prefetch:"
grep -a "\[DEBUG:PERF:BG_TASK\]" /tmp/perf_debug.log | tail -10
echo ""
echo "Request breakdown:"
grep -a "\[DEBUG:PERF\]" /tmp/perf_debug.log | grep -v "BG_TASK" | tail -15

# Show summary
echo ""
echo "=== Output Summary ==="
wc -l /tmp/test_output.csv
ls -lh /tmp/test_output.csv

# Cleanup
echo ""
echo "Stopping server (PID: $SERVER_PID)..."
kill $SERVER_PID 2>/dev/null || true
