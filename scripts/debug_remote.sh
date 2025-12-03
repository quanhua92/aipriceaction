#!/bin/bash

# debug_remote.sh - Test remote API endpoints for aipriceaction
# Usage: ./scripts/debug_remote.sh [IP_ADDRESS] [HOST_HEADER]
# Examples:
#   ./scripts/debug_remote.sh 127.0.0.1                           # Basic IP test
#   ./scripts/debug_remote.sh 127.0.0.1 api.aipriceaction.com     # IP with host header
#   ./scripts/debug_remote.sh api.aipriceaction.com                 # Domain test

set -e

# IP_ADDRESS is required parameter
if [ -z "$1" ]; then
    echo "Error: IP_ADDRESS is required"
    echo "Usage: $0 <IP_ADDRESS> [HOST_HEADER]"
    echo "Examples:"
    echo "  $0 127.0.0.1"
    echo "  $0 127.0.0.1 api.aipriceaction.com"
    exit 1
fi

IP="$1"
HOST_HEADER=${2:-""}
BASE_URL="http://${IP}"

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

# Function to test API endpoint with timeout
test_endpoint() {
    local url="$1"
    local description="$2"
    local timeout="${3:-10}"
    local host_header="${4:-}"

    print_test "$description"
    echo "URL: $url"
    [ -n "$host_header" ] && echo "Host Header: $host_header"

    # Build curl command
    local curl_cmd="curl -s -w \"%{http_code}\" \
        -o /tmp/debug_response.json \
        --max-time \"$timeout\" \
        --connect-timeout 5 \
        -H \"Accept: application/json\""

    # Add host header if provided
    if [ -n "$host_header" ]; then
        curl_cmd="$curl_cmd -H \"Host: $host_header\""
    fi

    curl_cmd="$curl_cmd \"$url\""

    # Test with curl and capture timing
    local start_time=$(date +%s.%N)
    local http_code=$(eval "$curl_cmd" 2>&1 || echo "000")
    local end_time=$(date +%s.%N)
    local duration=$(echo "$end_time - $start_time" | bc -l 2>/dev/null || echo "N/A")

    echo "HTTP Status: $http_code, Duration: ${duration}s"

    if [ "$http_code" = "200" ]; then
        print_success "SUCCESS"
        if [ -s /tmp/debug_response.json ]; then
            # Show only the keys/structure, not full data
            echo "Response structure:"
            jq -r 'keys[]' /tmp/debug_response.json 2>/dev/null | head -5 | sed 's/^/  - /' || echo "  (JSON parse error)"
        fi
    else
        print_error "FAILED (HTTP $http_code)"
        if [ -f /tmp/debug_response.json ] && [ -s /tmp/debug_response.json ]; then
            # Show what ticker is actually being returned
            local returned_ticker=$(jq -r 'keys[0]' /tmp/debug_response.json 2>/dev/null)
            echo "Expected: crypto, Returned ticker: ${returned_ticker:-"unknown"}"
        fi
    fi
    echo ""
}

# Function to test with different timeout values
test_with_timeouts() {
    local url="$1"
    local description="$2"
    local host_header="${3:-}"

    print_header "Testing $description with different timeouts"

    # Test with 10s timeout
    test_endpoint "$url" "10s timeout" 10 "$host_header"

    # Test with 30s timeout
    test_endpoint "$url" "30s timeout" 30 "$host_header"

    # Test with 60s timeout
    test_endpoint "$url" "60s timeout" 60 "$host_header"
}

# Variables already parsed above

echo -e "${BLUE}🔍 Debugging Remote API: $BASE_URL${NC}"
if [ -n "$HOST_HEADER" ]; then
    echo -e "${BLUE}Host Header: $HOST_HEADER${NC}"
fi
echo "This script will test various endpoints to diagnose performance issues."
echo ""

# Test 1: Basic Health Check
print_header "1. Basic Health Check"
test_endpoint "$BASE_URL/health" "Health Check" 10 "$HOST_HEADER"

# Test 2: VN Mode - Single Ticker
print_header "2. VN Mode - Single Ticker (FPT)"
test_endpoint "$BASE_URL/tickers?symbol=FPT&mode=vn&interval=1D&limit=1" "VN Stock - Daily" 10 "$HOST_HEADER"

# Test 3: VN Mode - Different Intervals
print_header "3. VN Mode - Different Intervals"
test_endpoint "$BASE_URL/tickers?symbol=VCB&mode=vn&interval=1H&limit=5" "VN Stock - Hourly" 10 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=BID&mode=vn&interval=1m&limit=10" "VN Stock - Minute" 10 "$HOST_HEADER"

# Test 4: Crypto Mode - Basic
print_header "4. Crypto Mode - Basic Tests"
test_endpoint "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1D&limit=1" "Crypto - Daily" 10 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1H&limit=1" "Crypto - Hourly" 10 "$HOST_HEADER"

# Test 5: Crypto Mode - Problematic Endpoint (Extended Timeouts)
print_header "5. Crypto Mode - Problematic Endpoint"
print_test "This is the endpoint that was timing out..."
echo "Original URL: $BASE_URL/tickers?mode=crypto&interval=1m&start_date=2025-12-02&limit=1"

# Clean up the original URL (remove extra spaces)
CLEAN_URL="$BASE_URL/tickers?mode=crypto&interval=1m&start_date=2025-12-02&limit=1"
test_with_timeouts "$CLEAN_URL" "Crypto - Minute with Date" "$HOST_HEADER"

# Test 6: Crypto Mode - Alternative Minute Query
print_header "6. Crypto Mode - Alternative Minute Queries"
test_endpoint "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1m&limit=1" "Crypto - Minute (Single Ticker)" 30 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=ETH&mode=crypto&interval=1m&limit=5" "Crypto - Minute (5 records)" 30 "$HOST_HEADER"

# Test 7: VN Mode - Historical Data Tests (Forces Disk Reading)
print_header "7. VN Mode - Historical Data Tests (CSV Reading)"
test_endpoint "$BASE_URL/tickers?symbol=VCB&mode=vn&interval=1D&end_date=2020-12-31&limit=1" "VN - Historical Daily (2020)" 15 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=FPT&mode=vn&interval=1D&end_date=2019-06-30&limit=5" "VN - Historical Daily (2019)" 15 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=VCB&mode=vn&interval=1H&end_date=2020-12-31&limit=10" "VN - Historical Hourly (2020)" 15 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=BID&mode=vn&interval=1D&end_date=2018-12-31&limit=1" "VN - Deep Historical (2018)" 20 "$HOST_HEADER"

# Test 8: VN Mode - Large Limit Tests
print_header "8. VN Mode - Large Limit Tests"
test_endpoint "$BASE_URL/tickers?symbol=VCB&mode=vn&interval=1D&limit=100" "VN - Large Limit Daily" 15 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=FPT&mode=vn&interval=1D&limit=500" "VN - Very Large Limit Daily" 30 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=VCB&mode=vn&interval=1H&limit=200" "VN - Large Limit Hourly" 20 "$HOST_HEADER"

# Test 9: Crypto Mode - Historical Data Tests
print_header "9. Crypto Mode - Historical Data Tests"
test_endpoint "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1D&end_date=2020-12-31&limit=1" "Crypto - Historical Daily (2020)" 15 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=ETH&mode=crypto&interval=1D&end_date=2019-06-30&limit=5" "Crypto - Historical Daily (2019)" 15 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1H&end_date=2020-12-31&limit=10" "Crypto - Historical Hourly (2020)" 20 "$HOST_HEADER"

# Test 10: Mixed Mode - Multiple Symbols
print_header "10. Mixed Mode - Multiple Symbols"
test_endpoint "$BASE_URL/tickers?symbol=VCB&symbol=FPT&mode=vn&interval=1D&limit=5" "VN - Multiple Symbols Daily" 15 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=BTC&symbol=ETH&mode=crypto&interval=1D&limit=5" "Crypto - Multiple Symbols Daily" 20 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=VCB&symbol=FPT&mode=vn&interval=1D&end_date=2020-12-31&limit=3" "VN - Multiple Symbols Historical" 20 "$HOST_HEADER"

# Test 11: Date Range Tests
print_header "11. Date Range Tests"
test_endpoint "$BASE_URL/tickers?symbol=VCB&mode=vn&interval=1D&start_date=2020-01-01&end_date=2020-01-31&limit=10" "VN - Date Range Daily (2020 Jan)" 20 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1D&start_date=2020-01-01&end_date=2020-01-31&limit=10" "Crypto - Date Range Daily (2020 Jan)" 20 "$HOST_HEADER"

# Test 12: Server Information
print_header "12. Server Information"
test_endpoint "$BASE_URL/health?verbose=true" "Detailed Health Info" 10 "$HOST_HEADER"

# Test 13: Ticker Groups
print_header "13. Ticker Groups"
test_endpoint "$BASE_URL/tickers/group?mode=vn" "VN Ticker Groups" 10 "$HOST_HEADER"
test_endpoint "$BASE_URL/tickers/group?mode=crypto" "Crypto Ticker Groups" 10 "$HOST_HEADER"

# Test 14: Connection Diagnostics
print_header "14. Connection Diagnostics"
print_test "Basic connectivity test"
if ping -c 1 -W 5 "$IP" >/dev/null 2>&1; then
    print_success "Ping successful"
else
    print_error "Ping failed"
fi

print_test "Port connectivity test"
if nc -z -w5 "$IP" 80 2>/dev/null; then
    print_success "Port 80 accessible"
else
    print_error "Port 80 not accessible"
fi

echo ""
print_header "Summary"
echo "All tests completed. Review the results above:"
echo "- ✅ If health and basic VN endpoints work but historical data fails: CSV reading issue"
echo "- ❌ If all endpoints fail: network/connectivity problem"
echo "- ⏱️ If some endpoints timeout: performance optimization needed"
echo "- 📊 If historical VN/Crypto data with end_date works: CSV streaming optimization successful"
echo "- 🗃️ If large limit tests work: memory management and early termination working"
echo "- 🔄 If multiple symbols work: batch processing functioning correctly"
echo ""
echo "Debugging tips:"
echo "1. Check server logs: docker logs aipriceaction --tail 100"
echo "2. Monitor resource usage: docker stats aipriceaction"
echo "3. Test locally: curl http://localhost:3000/health"
echo ""
echo "Files created during test:"
echo "- /tmp/debug_response.json (last API response)"

# Cleanup
rm -f /tmp/debug_response.json