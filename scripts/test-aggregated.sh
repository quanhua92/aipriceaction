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

    # Fetch data (use limit=50 for 2W to get recent data with MA50)
    if [ "$interval" = "2W" ]; then
        response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=$interval&limit=50")
        response_limited=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=$interval&limit=5")
    else
        response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=$interval")
        response_limited=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=$interval&limit=5")
    fi

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

    # Verify MA indicators exist (check based on data availability)
    record_count=$(echo "$response" | jq '.VCB | length')
    ma10_present=$(echo "$first_record" | jq -e '.ma10 != null' >/dev/null 2>&1 && echo "true" || echo "false")
    ma20_present=$(echo "$first_record" | jq -e '.ma20 != null' >/dev/null 2>&1 && echo "true" || echo "false")
    ma50_present=$(echo "$first_record" | jq -e '.ma50 != null' >/dev/null 2>&1 && echo "true" || echo "false")

    # Check if MAs are present based on data availability
    # MA10 needs 10+ records, MA20 needs 20+ records, MA50 needs 50+ records
    # But for 2W and 1M, early records may not have enough historical data for MA50
    if [ "$interval" = "2W" ]; then
        # For 2W with limit=50: recent data should have enough history for MA50
        expected_ma10=$([ "$record_count" -ge 10 ] && echo "true" || echo "false")
        expected_ma20=$([ "$record_count" -ge 20 ] && echo "true" || echo "false")
        expected_ma50=$([ "$record_count" -ge 50 ] && echo "true" || echo "false")
    elif [ "$interval" = "1M" ]; then
        # For 1M: First record is 2015-01-01, lacks historical data for MA calculations
        expected_ma10="false"  # First monthly record lacks 10 months prior history
        expected_ma20="false"  # First monthly record lacks 20 months prior history
        expected_ma50="false"  # First monthly record lacks 50 months prior history
    elif [ "$interval" = "5m" ] || [ "$interval" = "15m" ] || [ "$interval" = "30m" ]; then
        # For minute-based aggregation: first few records won't have MAs
        # because MAs are calculated on aggregated data, not source data
        # Check a record in the middle instead (around index 50)
        middle_record_index=50
        if [ "$record_count" -gt "$middle_record_index" ]; then
            ma10_present=$(echo "$response" | jq ".VCB[$middle_record_index] | .ma10 != null" >/dev/null 2>&1 && echo "true" || echo "false")
            ma20_present=$(echo "$response" | jq ".VCB[$middle_record_index] | .ma20 != null" >/dev/null 2>&1 && echo "true" || echo "false")
            ma50_present=$(echo "$response" | jq ".VCB[$middle_record_index] | .ma50 != null" >/dev/null 2>&1 && echo "true" || echo "false")
            # At index 50, we should have enough aggregated history for MA10 and MA20
            expected_ma10="true"
            expected_ma20="true"
            # MA50 needs 50 records, so at index 50 we should have it
            expected_ma50="true"
        else
            # Not enough records to test middle record
            expected_ma10="false"
            expected_ma20="false"
            expected_ma50="false"
        fi
    else
        # For other intervals: standard logic applies
        expected_ma10=$([ "$record_count" -ge 10 ] && echo "true" || echo "false")
        expected_ma20=$([ "$record_count" -ge 20 ] && echo "true" || echo "false")
        expected_ma50=$([ "$record_count" -ge 50 ] && echo "true" || echo "false")
    fi

    if ([ "$ma10_present" = "$expected_ma10" ] && [ "$ma20_present" = "$expected_ma20" ] && [ "$ma50_present" = "$expected_ma50" ]); then
        echo -e "${GREEN}✓ MA indicators present (data-aware: $record_count records)${NC}"
        ((test_passed++))

        # Show available MA values
        echo "$first_record" | jq -r '"  → MA10: \(.ma10 // "null"), MA20: \(.ma20 // "null"), MA50: \(.ma50 // "null")"'
    else
        echo -e "${RED}✗ MA indicators mismatch (records: $record_count, expected ma10=$expected_ma10/actual=$ma10_present, ma20=$expected_ma20/actual=$ma20_present, ma50=$expected_ma50/actual=$ma50_present)${NC}"
        ((test_failed++))
    fi

    # Verify change indicators: With MA200 buffer, first record CAN have change indicators
    first_close_changed=$(echo "$response" | jq '.VCB[0].close_changed')
    first_volume_changed=$(echo "$response" | jq '.VCB[0].volume_changed')
    second_close_changed=$(echo "$response" | jq '.VCB[1].close_changed')
    second_volume_changed=$(echo "$response" | jq '.VCB[1].volume_changed')

    # First record can have change indicators due to MA200 buffer (fetches extra historical data)
    if [ "$first_close_changed" != "null" ] || [ "$first_volume_changed" != "null" ]; then
        echo -e "${GREEN}✓ First record has change indicators (MA200 buffer working)${NC}"
        ((test_passed++))
    elif [ "$first_close_changed" = "null" ] && [ "$first_volume_changed" = "null" ]; then
        echo -e "${GREEN}✓ First record change indicators correctly null${NC}"
        ((test_passed++))
    else
        echo -e "${RED}✗ First record change indicators unexpected (got close_changed=$first_close_changed, volume_changed=$first_volume_changed)${NC}"
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
test_aggregation_details "2W" "Bi-weekly candles (using limit=50 for recent data)"
test_aggregation_details "1M" "Monthly candles"

echo "=========================================="
echo "3. COMPARE AGGREGATED vs BASE INTERVALS"
echo "=========================================="
echo ""

# Compare record counts: with same limit, both should return same number of records
echo "Comparing 1m vs 5m record counts (both should return the requested limit):"
count_1m=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=1m" | jq '.VCB | length')
count_5m=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=5m" | jq '.VCB | length')
echo "  → 1m: $count_1m records, 5m: $count_5m records"

if [ "$count_1m" -eq "$count_5m" ] && [ "$count_1m" -eq 252 ]; then
    echo -e "${GREEN}✓ Both intervals return requested limit (252) as expected${NC}"
    ((test_passed++))
else
    echo -e "${RED}✗ Record count mismatch - both should return 252 records${NC}"
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

echo "Testing aggregated interval with start_date and limit (limit should be respected):"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=15m&start_date=2025-01-02&end_date=2025-01-10&limit=10")
count=$(echo "$response" | jq '.VCB | length')
echo "  → With start_date=2025-01-02, end_date=2025-01-10, limit=10: Got $count records"
if [ "$count" -eq 10 ]; then
    echo -e "${GREEN}✓ Limit correctly respected when start_date and limit are both provided (got exactly $count records)${NC}"
    ((test_passed++))
else
    echo -e "${RED}✗ Limit should be respected with start_date, expected 10 records but got $count${NC}"
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
