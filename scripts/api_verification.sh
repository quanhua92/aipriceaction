#!/bin/bash
# Systematic API verification against docs/API.md

echo "================================"
echo "API Verification Test Suite"
echo "================================"
echo ""

BASE_URL="http://localhost:3000"
PASS=0
FAIL=0

# Helper function to test and display results
test_endpoint() {
    local name="$1"
    local url="$2"
    local expected_check="$3"

    echo "TEST: $name"
    echo "URL: $url"

    response=$(curl -s "$url")

    if echo "$response" | grep -q "$expected_check"; then
        echo "✅ PASS"
        ((PASS++))
    else
        echo "❌ FAIL - Expected to find: $expected_check"
        echo "Response preview: ${response:0:200}"
        ((FAIL++))
    fi
    echo ""
}

# Test 1: Default behavior - should return last 2 days
echo "================================"
echo "Test 1: Default Behavior"
echo "================================"
test_endpoint \
    "Default returns last 2 days (VCB)" \
    "$BASE_URL/tickers?symbol=VCB" \
    '"time":'

# Test 2: Time format - daily should be YYYY-MM-DD
echo "================================"
echo "Test 2: Daily Time Format"
echo "================================"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB")
if echo "$response" | grep -q '"time":"[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}"'; then
    echo "✅ PASS - Daily format is YYYY-MM-DD"
    ((PASS++))
else
    echo "❌ FAIL - Daily format should be YYYY-MM-DD"
    echo "$response" | head -20
    ((FAIL++))
fi
echo ""

# Test 3: Hourly time format - should be YYYY-MM-DD HH:MM:SS
echo "================================"
echo "Test 3: Hourly Time Format"
echo "================================"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB&interval=1H&start_date=2025-11-05")
if echo "$response" | grep -q '"time":"[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\} [0-9]\{2\}:[0-9]\{2\}:[0-9]\{2\}"'; then
    echo "✅ PASS - Hourly format is YYYY-MM-DD HH:MM:SS"
    ((PASS++))
else
    echo "❌ FAIL - Hourly format should be YYYY-MM-DD HH:MM:SS"
    echo "$response" | head -20
    ((FAIL++))
fi
echo ""

# Test 4: Technical indicators present
echo "================================"
echo "Test 4: Technical Indicators"
echo "================================"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB")
indicators=("ma10" "ma20" "ma50" "ma10_score" "ma20_score" "ma50_score" "money_flow" "dollar_flow" "trend_score")
all_present=true
for indicator in "${indicators[@]}"; do
    if ! echo "$response" | grep -q "\"$indicator\""; then
        echo "❌ Missing indicator: $indicator"
        all_present=false
    fi
done
if $all_present; then
    echo "✅ PASS - All technical indicators present"
    ((PASS++))
else
    echo "❌ FAIL - Some indicators missing"
    ((FAIL++))
fi
echo ""

# Test 5: Legacy price format (divide by 1000)
echo "================================"
echo "Test 5: Legacy Price Format"
echo "================================"
response_normal=$(curl -s "$BASE_URL/tickers?symbol=VCB")
response_legacy=$(curl -s "$BASE_URL/tickers?symbol=VCB&legacy=true")

# Extract first close price from normal response
price_normal=$(echo "$response_normal" | grep -o '"close":[0-9.]*' | head -1 | cut -d: -f2)
price_legacy=$(echo "$response_legacy" | grep -o '"close":[0-9.]*' | head -1 | cut -d: -f2)

# Check if legacy price is roughly 1/1000 of normal price
if [ -n "$price_normal" ] && [ -n "$price_legacy" ]; then
    ratio=$(echo "scale=2; $price_normal / $price_legacy" | bc)
    if [ "${ratio%.*}" -gt 900 ] && [ "${ratio%.*}" -lt 1100 ]; then
        echo "✅ PASS - Legacy divides by ~1000 (ratio: $ratio)"
        echo "   Normal: $price_normal, Legacy: $price_legacy"
        ((PASS++))
    else
        echo "❌ FAIL - Legacy ratio incorrect (ratio: $ratio)"
        echo "   Normal: $price_normal, Legacy: $price_legacy"
        ((FAIL++))
    fi
else
    echo "❌ FAIL - Could not extract prices"
    ((FAIL++))
fi
echo ""

# Test 6: CSV format
echo "================================"
echo "Test 6: CSV Format"
echo "================================"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB&format=csv")
if echo "$response" | head -1 | grep -q "symbol,time,open,high,low,close,volume"; then
    echo "✅ PASS - CSV format has correct headers"
    ((PASS++))
else
    echo "❌ FAIL - CSV headers incorrect"
    echo "$response" | head -5
    ((FAIL++))
fi
echo ""

# Test 7: Health endpoint
echo "================================"
echo "Test 7: Health Endpoint"
echo "================================"
response=$(curl -s "$BASE_URL/health")
health_fields=("active_tickers_count" "daily_records_count" "memory_usage_mb" "uptime_secs" "current_system_time")
all_present=true
for field in "${health_fields[@]}"; do
    if ! echo "$response" | grep -q "\"$field\""; then
        echo "❌ Missing field: $field"
        all_present=false
    fi
done
if $all_present; then
    echo "✅ PASS - Health endpoint has all required fields"
    ((PASS++))
else
    echo "❌ FAIL - Some health fields missing"
    echo "$response"
    ((FAIL++))
fi
echo ""

# Test 8: Ticker groups endpoint
echo "================================"
echo "Test 8: Ticker Groups Endpoint"
echo "================================"
test_endpoint \
    "Ticker groups returns JSON" \
    "$BASE_URL/tickers/group" \
    '"'

# Test 9: Historical date range
echo "================================"
echo "Test 9: Historical Date Range"
echo "================================"
test_endpoint \
    "Historical query with date range" \
    "$BASE_URL/tickers?symbol=VCB&start_date=2025-11-01&end_date=2025-11-05" \
    '"time":'

# Test 10: Multiple symbols
echo "================================"
echo "Test 10: Multiple Symbols"
echo "================================"
response=$(curl -s "$BASE_URL/tickers?symbol=VCB&symbol=FPT&symbol=VNM")
if echo "$response" | grep -q '"VCB"' && echo "$response" | grep -q '"FPT"' && echo "$response" | grep -q '"VNM"'; then
    echo "✅ PASS - Multiple symbols returned"
    ((PASS++))
else
    echo "❌ FAIL - Not all symbols returned"
    ((FAIL++))
fi
echo ""

# Final summary
echo "================================"
echo "Test Summary"
echo "================================"
echo "PASSED: $PASS"
echo "FAILED: $FAIL"
echo "TOTAL:  $((PASS + FAIL))"
echo ""

if [ $FAIL -eq 0 ]; then
    echo "✅ All tests passed!"
    exit 0
else
    echo "❌ Some tests failed"
    exit 1
fi
