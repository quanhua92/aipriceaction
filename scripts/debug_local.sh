#!/bin/bash

# debug_local.sh - Test local API endpoints for performance analysis
# Usage: ./scripts/debug_local.sh

set -e

BASE_URL="http://localhost:3000"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_header() {
    echo -e "${BLUE}=== $1 ===${NC}"
}

print_test() {
    echo -e "${YELLOW}Testing: $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# Function to test API endpoint with timing
test_endpoint() {
    local url="$1"
    local description="$2"
    local timeout="${3:-10}"

    print_test "$description"
    echo "URL: $url"

    # Test with curl and capture timing
    local start_time=$(date +%s.%N)
    local http_code=$(curl -s -w "%{http_code}" \
        -o /tmp/debug_response.json \
        --max-time "$timeout" \
        --connect-timeout 5 \
        -H "Accept: application/json" \
        "$url" 2>&1 || echo "000")
    local end_time=$(date +%s.%N)
    local duration=$(echo "$end_time - $start_time" | bc -l 2>/dev/null || echo "N/A")

    echo "HTTP Status: $http_code, Duration: ${duration}s"

    if [ "$http_code" = "200" ]; then
        print_success "SUCCESS"
        if [ -s /tmp/debug_response.json ]; then
            # Show response size and structure
            local response_size=$(wc -c < /tmp/debug_response.json)
            echo "Response size: ${response_size} bytes"

            # Count tickers in response
            local ticker_count=$(jq -r 'keys | length' /tmp/debug_response.json 2>/dev/null || echo "0")
            echo "Tickers returned: $ticker_count"

            # Show first few ticker keys
            if [ "$ticker_count" -gt 0 ]; then
                echo "Sample tickers:"
                jq -r 'keys[0:4] | .[]' /tmp/debug_response.json 2>/dev/null | sed 's/^/  - /' || echo "  (Unable to parse ticker list)"
            fi
        fi

        # Performance evaluation
        if (( $(echo "$duration > 10.0" | bc -l 2>/dev/null) )); then
            print_warning "SLOW: More than 10 seconds"
        elif (( $(echo "$duration > 5.0" | bc -l 2>/dev/null) )); then
            print_warning "MODERATE: More than 5 seconds"
        else
            print_success "FAST: Less than 5 seconds"
        fi
    else
        print_error "FAILED (HTTP $http_code)"
        if [ -f /tmp/debug_response.json ] && [ -s /tmp/debug_response.json ]; then
            echo "Error response:"
            head -5 /tmp/debug_response.json 2>/dev/null | sed 's/^/  /' || echo "  (Unable to read error response)"
        fi
    fi
    echo ""
}

# Function to run multiple iterations and get average
test_performance() {
    local url="$1"
    local description="$2"
    local iterations="${3:-3}"

    print_header "Performance Test: $description ($iterations iterations)"

    local total_time=0
    local success_count=0

    for i in $(seq 1 $iterations); do
        echo -n "Run $i/$iterations: "
        local start_time=$(date +%s.%N)
        local http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 "$url")
        local end_time=$(date +%s.%N)
        local duration=$(echo "$end_time - $start_time" | bc -l)

        if [ "$http_code" = "200" ]; then
            echo "${duration}s ✅"
            total_time=$(echo "$total_time + $duration" | bc -l)
            ((success_count++))
        else
            echo "FAILED (HTTP $http_code) ❌"
        fi
    done

    if [ $success_count -gt 0 ]; then
        local avg_time=$(echo "scale=3; $total_time / $success_count" | bc -l)
        echo "Average time: ${avg_time}s (based on $success_count successful runs)"

        if (( $(echo "$avg_time > 10.0" | bc -l 2>/dev/null) )); then
            print_error "AVERAGE IS TOO SLOW: More than 10 seconds"
        elif (( $(echo "$avg_time > 5.0" | bc -l 2>/dev/null) )); then
            print_warning "AVERAGE IS SLOW: More than 5 seconds"
        else
            print_success "AVERAGE IS GOOD: Less than 5 seconds"
        fi
    else
        print_error "ALL TESTS FAILED"
    fi
    echo ""
}

echo -e "${BLUE}🔍 Debugging Local API Performance: $BASE_URL${NC}"
echo "This script will test various endpoints to identify performance bottlenecks."
echo ""

# Check if server is running
print_header "Server Health Check"
if ! curl -s --max-time 5 "$BASE_URL/health" >/dev/null; then
    print_error "Server is not running at $BASE_URL"
    echo "Please start the server first: ./target/release/aipriceaction serve --port 3000"
    exit 1
fi
print_success "Server is responding"
echo ""

# Test 1: Single ticker requests (should be FAST)
print_header "1. Single Ticker Requests (Target: <10ms)"

test_endpoint "$BASE_URL/tickers?symbol=VCB&interval=1D&limit=1" "VN Stock - Single Daily Record" 10
test_endpoint "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1m&limit=10" "Crypto - Single Minute Ticker" 10
test_endpoint "$BASE_URL/tickers?symbol=ETH&mode=crypto&interval=1H&limit=5" "Crypto - Single Hourly Ticker" 10

# Performance test for single ticker
test_performance "$BASE_URL/tickers?symbol=VCB&interval=1D&limit=1" "Single VCN Ticker" 5
test_performance "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1m&limit=10" "Single BTC Minute" 5

# Test 2: Small group requests (should be FAST)
print_header "2. Small Group Requests (Target: <1s)"

test_endpoint "$BASE_URL/tickers?symbol=VCB&symbol=FPT&symbol=VNM&interval=1D&limit=5" "VN - 3 Stocks Daily" 15
test_endpoint "$BASE_URL/tickers?symbol=BTC&symbol=ETH&symbol=XRP&mode=crypto&interval=1m&limit=10" "Crypto - 3 Tickers Minute" 15

# Performance test for small groups
test_performance "$BASE_URL/tickers?symbol=BTC&symbol=ETH&mode=crypto&interval=1m&limit=10" "2 Crypto Tickers" 3

# Test 3: Medium group requests (potential bottlenecks)
print_header "3. Medium Group Requests (Watch for slowness)"

test_endpoint "$BASE_URL/tickers?mode=vn&interval=1D&limit=10" "VN - All Tickers Daily (limit=10)" 30
test_endpoint "$BASE_URL/tickers?mode=crypto&interval=1D&limit=10" "Crypto - All Tickers Daily (limit=10)" 30

# Test 4: Large limit requests (STRESS TEST)
print_header "4. Large Limit Requests (STRESS TEST - might be slow)"

test_endpoint "$BASE_URL/tickers?mode=vn&interval=1D&limit=100" "VN - All Tickers Daily (limit=100)" 60
test_endpoint "$BASE_URL/tickers?mode=crypto&interval=1D&limit=100" "Crypto - All Tickers Daily (limit=100)" 60

# Performance test for medium load
test_performance "$BASE_URL/tickers?mode=vn&interval=1D&limit=10" "VN All Stocks (limit=10)" 3

# Test 5: Historical data requests (POTENTIAL BOTTLENECK)
print_header "5. Historical Data Requests (POTENTIAL BOTTLENECK)"

test_endpoint "$BASE_URL/tickers?symbol=VCB&interval=1D&end_date=2024-12-31&limit=10" "VN - Historical Daily 2024" 30
test_endpoint "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1D&end_date=2024-12-31&limit=10" "Crypto - Historical Daily 2024" 30
test_endpoint "$BASE_URL/tickers?mode=vn&interval=1D&end_date=2024-01-01&limit=50" "VN - Deep Historical (2024 start)" 45

# Test 6: Minute data requests (MOST LIKELY TO BE SLOW)
print_header "6. Minute Data Requests (CRITICAL PERFORMANCE TEST)"

test_endpoint "$BASE_URL/tickers?symbol=VCB&interval=1m&limit=100" "VN - Single Ticker Minute Data" 45
test_endpoint "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1m&limit=100" "Crypto - Single Ticker Minute Data" 45

# Stress test: All tickers minute data (MIGHT BE VERY SLOW)
print_header "7. STRESS TEST - All Tickers Minute Data"

test_endpoint "$BASE_URL/tickers?mode=vn&interval=1m&limit=10" "VN - All Tickers Minute (limit=10)" 90
test_endpoint "$BASE_URL/tickers?mode=crypto&interval=1m&limit=10" "Crypto - All Tickers Minute (limit=10)" 90

# Test 8: Date-filtered minute data (the original problem)
print_header "8. Date-Filtered Minute Data (ORIGINAL PROBLEM)"

test_endpoint "$BASE_URL/tickers?mode=crypto&interval=1m&start_date=2025-12-02&limit=1" "Crypto - Minute with Date Filter" 60
test_endpoint "$BASE_URL/tickers?mode=vn&interval=1m&start_date=2025-12-02&limit=1" "VN - Minute with Date Filter" 60

# Performance test for the problem case
test_performance "$BASE_URL/tickers?mode=crypto&interval=1m&start_date=2025-12-02&limit=1" "Crypto Minute Date Filter" 3

# Test 9: CSV export (different performance profile)
print_header "9. CSV Export Performance"

test_endpoint "$BASE_URL/tickers?symbol=VCB&interval=1D&limit=100&format=csv" "VN - CSV Export Daily" 30
test_endpoint "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1D&limit=100&format=csv" "Crypto - CSV Export Daily" 30

echo ""
print_header "Summary and Recommendations"
echo "Performance Analysis Results:"
echo ""
echo "✅ FAST (<1s): Single tickers, small groups, daily data"
echo "⚠️  MODERATE (1-5s): Medium groups, some historical queries"
echo "❌ SLOW (>5s): Large datasets, minute data, extensive historical queries"
echo ""
echo "Key Performance Indicators to Watch:"
echo "- Single ticker: Should be <10ms"
echo "- Small group (2-5 tickers): Should be <100ms"
echo "- Medium group (limit=10): Should be <1s"
echo "- Large queries (limit=100+): May take 2-10s"
echo "- Minute data: Most likely to be slow due to file sizes"
echo "- Historical queries: Performance depends on date range"
echo ""
echo "If any tests show >10s for simple queries, there's a performance issue!"
echo ""
echo "Debugging tips:"
echo "1. Check server logs: docker logs aipriceaction --tail 100"
echo "2. Monitor CSV reading strategy in logs (should show 'smart CSV read')"
echo "3. Look for file sizes and reading strategies in the logs"
echo "4. Check if minute data is reading from end vs complete file"
echo ""
echo "Files created during test:"
echo "- /tmp/debug_response.json (last API response)"

# Cleanup
rm -f /tmp/debug_response.json