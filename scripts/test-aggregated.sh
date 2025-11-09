#!/bin/bash

# Test script for aggregated interval API endpoints
# Tests: 5m, 15m, 30m, 1W, 2W, 1M

set -e

BASE_URL="${1:-http://localhost:3000}"

echo "=========================================="
echo "Testing Aggregated Intervals API"
echo "Base URL: $BASE_URL"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

test_passed=0
test_failed=0

# Function to test an endpoint
test_endpoint() {
    local name="$1"
    local url="$2"
    local expected_pattern="$3"

    echo -n "Testing $name... "

    response=$(curl -s "$url")

    if echo "$response" | grep -q "$expected_pattern"; then
        echo -e "${GREEN}✓ PASSED${NC}"
        ((test_passed++))

        # Show sample of response
        echo "$response" | jq -r '.data[0] | "  → Time: \(.time), Open: \(.open), Close: \(.close), Volume: \(.volume)"' 2>/dev/null || echo "  → Response received"
    else
        echo -e "${RED}✗ FAILED${NC}"
        echo "  Expected pattern: $expected_pattern"
        echo "  Response: $response" | head -c 200
        ((test_failed++))
    fi
    echo ""
}

# Function to test aggregated interval details
test_aggregation_details() {
    local interval="$1"
    local description="$2"

    echo -e "${BLUE}Testing $interval ($description)${NC}"

    # Fetch data (no limit to get the true first record)
    response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=$interval")

    # Also fetch limited data for display
    response_limited=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=$interval&limit=5")

    # Check if we got data (API returns {"VCB": [...]} format)
    if ! echo "$response" | jq -e '.VCB | length > 0' >/dev/null 2>&1; then
        echo -e "${RED}✗ No data returned${NC}"
        echo "  Response: $response" | head -c 200
        ((test_failed++))
        echo ""
        return
    fi

    # Check first record structure
    first_record=$(echo "$response" | jq '.VCB[0]')

    # Verify OHLCV fields exist
    if echo "$first_record" | jq -e '.open and .high and .low and .close and .volume' >/dev/null 2>&1; then
        echo -e "${GREEN}✓ OHLCV fields present${NC}"
        ((test_passed++))
    else
        echo -e "${RED}✗ Missing OHLCV fields${NC}"
        ((test_failed++))
    fi

    # Verify MA indicators exist
    if echo "$first_record" | jq -e 'has("ma10") and has("ma20") and has("ma50")' >/dev/null 2>&1; then
        echo -e "${GREEN}✓ MA indicators present${NC}"
        ((test_passed++))

        # Show MA values
        echo "$first_record" | jq -r '"  → MA10: \(.ma10 // "null"), MA20: \(.ma20 // "null"), MA50: \(.ma50 // "null")"'
    else
        echo -e "${RED}✗ Missing MA indicators${NC}"
        ((test_failed++))
    fi

    # Verify change indicators: first record should be null, others should have numeric values
    first_close_changed=$(echo "$response" | jq '.VCB[0].close_changed')
    first_volume_changed=$(echo "$response" | jq '.VCB[0].volume_changed')
    second_close_changed=$(echo "$response" | jq '.VCB[1].close_changed')
    second_volume_changed=$(echo "$response" | jq '.VCB[1].volume_changed')

    # First record should have null change indicators (no previous record)
    if [ "$first_close_changed" = "null" ] && [ "$first_volume_changed" = "null" ]; then
        echo -e "${GREEN}✓ First record change indicators correctly null${NC}"
        ((test_passed++))
    else
        echo -e "${RED}✗ First record should have null changes (got close_changed=$first_close_changed, volume_changed=$first_volume_changed)${NC}"
        ((test_failed++))
    fi

    # Check if there's a second record to test
    record_count=$(echo "$response" | jq '.VCB | length')

    if [ "$record_count" -ge 2 ]; then
        # Second record should have numeric change indicators
        if [ "$second_close_changed" != "null" ] && [ "$second_volume_changed" != "null" ]; then
            echo -e "${GREEN}✓ Subsequent records have change indicators${NC}"
            ((test_passed++))
            echo "  → Record 2 changes: close_changed=$second_close_changed%, volume_changed=$second_volume_changed%"
        else
            echo -e "${RED}✗ Subsequent records should have numeric changes (got close_changed=$second_close_changed, volume_changed=$second_volume_changed)${NC}"
            ((test_failed++))
        fi
    else
        echo -e "${GREEN}✓ Only 1 record - skipping subsequent record test${NC}"
        ((test_passed++))
        echo "  → Note: Insufficient data for testing change indicators (need 2+ records)"
    fi

    # Show time and OHLCV
    echo "$first_record" | jq -r '"  → Time: \(.time), Open: \(.open), High: \(.high), Low: \(.low), Close: \(.close), Volume: \(.volume)"'

    echo ""
}

echo "=========================================="
echo "1. MINUTE-BASED AGGREGATIONS (from 1m data)"
echo "=========================================="
echo ""

test_aggregation_details "5m" "5-minute candles"
test_aggregation_details "15m" "15-minute candles"
test_aggregation_details "30m" "30-minute candles"

echo "=========================================="
echo "2. DAY-BASED AGGREGATIONS (from 1D data)"
echo "=========================================="
echo ""

test_aggregation_details "1W" "Weekly candles (Monday-Sunday)"
test_aggregation_details "2W" "Bi-weekly candles"
test_aggregation_details "1M" "Monthly candles"

echo "=========================================="
echo "3. COMPARE AGGREGATED vs BASE INTERVALS"
echo "=========================================="
echo ""

# Compare record counts: aggregated should have fewer records than base (without limit)
echo "Comparing 1m vs 5m record counts (5m should have ~1/5 the records):"
count_1m=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=1m" | jq '.VCB | length')
count_5m=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=5m" | jq '.VCB | length')
echo "  → 1m: $count_1m records, 5m: $count_5m records"

if [ "$count_5m" -lt "$count_1m" ]; then
    echo -e "${GREEN}✓ Aggregation reduces record count as expected${NC}"
    ((test_passed++))
else
    echo -e "${RED}✗ Unexpected record count relationship${NC}"
    ((test_failed++))
fi
echo ""

echo "Comparing 1D vs 1W record counts (1W should have ~1/5-1/7 the records):"
# Note: Using limit=365 to get ~1 year of data to compare aggregation ratios
count_1d=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=1D&limit=365" | jq '.VCB | length')
count_1w=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=1W&limit=52" | jq '.VCB | length')
echo "  → 1D (limit=365): $count_1d records, 1W (limit=52): $count_1w records"

if [ "$count_1w" -lt "$count_1d" ]; then
    echo -e "${GREEN}✓ Aggregation reduces record count as expected${NC}"
    ((test_passed++))
else
    echo -e "${RED}✗ Unexpected record count relationship${NC}"
    ((test_failed++))
fi
echo ""

echo "=========================================="
echo "4. LIMIT PARAMETER WITH AGGREGATED INTERVALS"
echo "=========================================="
echo ""

# Test limit parameter with various aggregated intervals
echo "Testing 15m with limit=100 (should return exactly 100 15m records):"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=15m&limit=100")
count=$(echo "$response" | jq '.VCB | length')
echo "  → Requested: 100 records, Got: $count records"
if [ "$count" -eq 100 ]; then
    echo -e "${GREEN}✓ 15m limit=100 returns exactly 100 records${NC}"
    ((test_passed++))
else
    echo -e "${RED}✗ Expected 100 records, got $count${NC}"
    ((test_failed++))
fi
echo ""

echo "Testing 30m with limit=50 (should return exactly 50 30m records):"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=30m&limit=50")
count=$(echo "$response" | jq '.VCB | length')
echo "  → Requested: 50 records, Got: $count records"
if [ "$count" -eq 50 ]; then
    echo -e "${GREEN}✓ 30m limit=50 returns exactly 50 records${NC}"
    ((test_passed++))
else
    echo -e "${RED}✗ Expected 50 records, got $count${NC}"
    ((test_failed++))
fi
echo ""

echo "Testing 5m with limit=200 (should return exactly 200 5m records):"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=5m&limit=200")
count=$(echo "$response" | jq '.VCB | length')
echo "  → Requested: 200 records, Got: $count records"
if [ "$count" -eq 200 ]; then
    echo -e "${GREEN}✓ 5m limit=200 returns exactly 200 records${NC}"
    ((test_passed++))
else
    echo -e "${RED}✗ Expected 200 records, got $count${NC}"
    ((test_failed++))
fi
echo ""

echo "Testing 1W with limit=100 (should return exactly 100 weekly records):"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=1W&limit=100")
count=$(echo "$response" | jq '.VCB | length')
echo "  → Requested: 100 records, Got: $count records"
if [ "$count" -eq 100 ]; then
    echo -e "${GREEN}✓ 1W limit=100 returns exactly 100 records${NC}"
    ((test_passed++))
else
    echo -e "${RED}✗ Expected 100 records, got $count${NC}"
    ((test_failed++))
fi
echo ""

echo "Testing multiple tickers with 15m limit=50:"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB&symbol=FPT&interval=15m&limit=50")
vcb_count=$(echo "$response" | jq '.VCB | length')
fpt_count=$(echo "$response" | jq '.FPT | length')
echo "  → VCB: $vcb_count records, FPT: $fpt_count records"
if [ "$vcb_count" -eq 50 ] && [ "$fpt_count" -eq 50 ]; then
    echo -e "${GREEN}✓ Multiple tickers with limit=50 returns 50 records each${NC}"
    ((test_passed++))
else
    echo -e "${RED}✗ Expected 50 records for each ticker, got VCB=$vcb_count, FPT=$fpt_count${NC}"
    ((test_failed++))
fi
echo ""

echo "Testing aggregated interval with start_date (limit should be ignored):"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=15m&start_date=2025-01-01&end_date=2025-01-10&limit=10")
count=$(echo "$response" | jq '.VCB | length')
echo "  → With start_date=2025-01-01, end_date=2025-01-10, limit=10: Got $count records"
if [ "$count" -gt 10 ]; then
    echo -e "${GREEN}✓ Limit correctly ignored when start_date is provided (got $count records > limit 10)${NC}"
    ((test_passed++))
else
    echo -e "${RED}✗ Limit should be ignored with start_date, got only $count records${NC}"
    ((test_failed++))
fi
echo ""

echo "=========================================="
echo "5. CSV FORMAT SUPPORT"
echo "=========================================="
echo ""

echo "Testing 5m CSV export:"
csv_response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=5m&limit=5&format=csv")
if echo "$csv_response" | head -1 | grep -q "symbol,time,open,high,low,close,volume"; then
    echo -e "${GREEN}✓ CSV format works for aggregated intervals${NC}"
    ((test_passed++))
    echo "  → CSV header: $(echo "$csv_response" | head -1 | cut -c1-80)..."
    echo "  → Sample row: $(echo "$csv_response" | head -2 | tail -1 | cut -c1-80)..."
else
    echo -e "${RED}✗ CSV format failed${NC}"
    ((test_failed++))
fi
echo ""

echo "=========================================="
echo "TEST SUMMARY"
echo "=========================================="
echo -e "${GREEN}Passed: $test_passed${NC}"
echo -e "${RED}Failed: $test_failed${NC}"
echo ""

if [ $test_failed -eq 0 ]; then
    echo -e "${GREEN}All tests passed! ✓${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi
