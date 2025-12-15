#!/bin/bash

# Debug script for testing weekly (1W) aggregation performance
# Tests cache behavior, CSV reads, and aggregation timing

set -e

API_URL="${API_URL:-http://localhost:3000}"
LOG_FILE="/tmp/weekly_debug_$(date +%Y%m%d_%H%M%S).log"

echo "=== Weekly Aggregation Debug Script ==="
echo "API URL: $API_URL"
echo "Log file: $LOG_FILE"
echo ""

# Function to make request and log timing
test_week_request() {
    local symbol=$1
    local limit=$2
    local label=$3

    echo "Testing: $label"
    echo "  Symbol: $symbol"
    echo "  Limit: $limit weeks"
    echo "  Expected daily records needed: $((limit * 5 + 200))" # ~5 trading days/week + 200 MA buffer

    # Start server in background if not running
    if ! curl -s "$API_URL/health" > /dev/null 2>&1; then
        echo "Starting server..."
        RUST_LOG=info ./target/release/aipriceaction serve --port 3000 > "$LOG_FILE" 2>&1 &
        SERVER_PID=$!
        echo "Server PID: $SERVER_PID"
        sleep 5

        # Wait for server to be ready
        for i in {1..30}; do
            if curl -s "$API_URL/health" > /dev/null; then
                echo "Server ready!"
                break
            fi
            sleep 1
        done
    fi

    # Make request with timing
    echo -n "  Request time: "
    time curl -s "$API_URL/tickers?symbol=$symbol&interval=1W&limit=$limit" > /dev/null

    # Check logs for cache/CSV behavior
    echo ""
    echo "  Cache/CSV behavior:"
    if [ -f "$LOG_FILE" ]; then
        # Look for PROFILE logs for this symbol
        grep -E "\[PROFILE\].*$symbol" "$LOG_FILE" | tail -10 | sed 's/^/    /'

        # Look for aggregation logs
        grep -E "aggregation.*Week|Starting.*1W" "$LOG_FILE" | tail -5 | sed 's/^/    /'

        # Look for CSV read strategy
        grep -E "strategy.*from_end|Reading CSV from end" "$LOG_FILE" | tail -5 | sed 's/^/    /'
    fi

    echo ""
    echo "----------------------------------------"
}

# Build the project first
echo "Building project..."
cargo build --release > /dev/null 2>&1

# Test various scenarios
echo "Running weekly aggregation tests..."
echo ""

# Test 1: Small request (should use cache)
test_week_request "VNINDEX" 26 "Small - 26 weeks (~6 months)"

# Test 2: Medium request (might need CSV)
test_week_request "VNINDEX" 52 "Medium - 52 weeks (1 year)"

# Test 3: Large request (definitely needs CSV)
test_week_request "VNINDEX" 128 "Large - 128 weeks (~2.5 years)"

# Test 4: Very large request
test_week_request "VCB" 156 "Very Large - 156 weeks (3 years)"

# Test 5: Test with different ticker
test_week_request "FPT" 100 "FPT - 100 weeks"

# Summary
echo ""
echo "=== Test Summary ==="
echo "Check the logs for:"
echo "  - CACHE HIT vs CACHE MISS"
echo "  - READING FROM DISK/CSV messages"
echo "  - Aggregation timing (should be < 5ms)"
echo "  - FromEnd strategy usage (faster reads)"
echo ""
echo "Log file: $LOG_FILE"

# Stop server if we started it
if [ -n "$SERVER_PID" ]; then
    echo "Stopping server (PID: $SERVER_PID)..."
    kill $SERVER_PID 2>/dev/null || true
fi