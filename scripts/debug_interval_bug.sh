#!/bin/bash

# Debug script to confirm interval deduplication bug
# Tests local server at port 3000

set -e

API_URL="http://localhost:3000"
echo "=== Interval Deduplication Bug Verification ==="
echo "Testing against: $API_URL"
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

test_interval() {
    local ticker="$1"
    local interval="$2"
    local expected_min="$3"
    local description="$4"

    echo -e "${BLUE}Testing $ticker $interval - $description${NC}"

    local response=$(curl -s "$API_URL/tickers?symbol=$ticker&interval=$interval&mode=vn&format=csv" 2>/dev/null || echo "")
    if [[ -z "$response" ]]; then
        echo -e "${RED}❌ FAILED: No response from server${NC}"
        return 1
    fi

    local line_count=$(echo "$response" | wc -l | xargs)
    local record_count=$((line_count - 1)) # Subtract header

    echo "  CSV lines: $line_count (header + $record_count records)"

    if [[ $record_count -lt $expected_min ]]; then
        echo -e "${RED}❌ BUG CONFIRMED: Only $record_count records (expected at least $expected_min)${NC}"
        echo -e "${YELLOW}  Showing last 5 lines:${NC}"
        echo "$response" | tail -5
        echo ""
        return 1
    else
        echo -e "${GREEN}✅ GOOD: $record_count records (>= $expected_min expected)${NC}"
        echo ""
        return 0
    fi
}

echo "Testing VCB (Stocks):"
echo ""

# Test 1D - should work correctly (daily)
test_interval "VCB" "1D" "200" "Daily (should work - 252 trading days)"

# Test 1H - should have ~175 records (1 per hour)
test_interval "VCB" "1H" "150" "Hourly (currently broken - returns daily)"

# Test 1m - should have ~250+ records (all intraday minutes)
test_interval "VCB" "1m" "200" "Minute (currently broken - only 7 records)"

# Test 15m - should have ~100 records (proper aggregation)
test_interval "VCB" "15m" "80" "15-minute (currently broken - returns daily)"

echo ""
echo "Testing BTC (Crypto):"
echo ""

# Test crypto 1D - should work correctly
test_interval "BTC" "1D" "200" "Crypto Daily (should work)"

# Test crypto 1m - should have 250+ records
test_interval "BTC" "1m" "200" "Crypto Minute (currently broken - only 4 records)"

echo "=== Bug Analysis ==="
echo ""
echo "Expected results after fix:"
echo "  VCB 1m: 8 records → 250+ records"
echo "  VCB 1H: 253 records → ~175 records"
echo "  VCB 15m: 253 records → ~100 records"
echo "  BTC 1m: 5 records → 250+ records"
echo ""
echo "Root cause: date_naive() used for ALL intervals"
echo "  - 1m: All intraday records on same day treated as duplicates"
echo "  - 1H: All hourly records on same day treated as duplicates"
echo "  - Fix: Use interval-aware deduplication keys"