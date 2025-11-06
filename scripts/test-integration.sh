#!/bin/bash

# Comprehensive Integration Test Script for AIPriceAction API
# Usage: ./scripts/test-integration.sh [URL]
# Default URL: http://localhost:3000

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
BASE_URL="${1:-http://localhost:3000}"
TEST_TICKERS=("VCB" "FPT" "VIC" "AGG" "VNINDEX")
FAILED_TESTS=0
TOTAL_TESTS=0

# Helper functions
print_header() {
    echo -e "${BLUE}🧪 === $1 === 🧪${NC}"
    echo -e "${BLUE}⏰ Time: $(date)${NC}"
    echo ""
}

print_test() {
    echo -e "${YELLOW}📋 $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
    ((FAILED_TESTS++))
}

print_result() {
    local test_name="$1"
    local expected="$2"
    local actual="$3"

    case "$expected" in
        "not_empty")
            if [[ -n "$actual" && "$actual" != "null" && "$actual" != "empty" ]]; then
                print_success "$test_name: ✓"
                return 0
            else
                print_error "$test_name: ✗ (Expected: not empty, Got: '$actual')"
                return 1
            fi
            ;;
        "greater_than_0")
            if [[ "$actual" -gt 0 ]]; then
                print_success "$test_name: ✓"
                return 0
            else
                print_error "$test_name: ✗ (Expected: > 0, Got: $actual)"
                return 1
            fi
            ;;
        "present")
            if [[ "$actual" == "present" ]]; then
                print_success "$test_name: ✓"
                return 0
            else
                print_error "$test_name: ✗ (Expected: present, Got: $actual)"
                return 1
            fi
            ;;
        *)
            if [[ "$actual" == "$expected" ]]; then
                print_success "$test_name: ✓"
                return 0
            else
                print_error "$test_name: ✗ (Expected: $expected, Got: $actual)"
                return 1
            fi
            ;;
    esac
}

# Test functions
test_server_health() {
    print_test "Server Health Check"

    local response=$(curl -s "$BASE_URL/health" || echo "")
    if [[ -z "$response" ]]; then
        print_error "Server not responding at $BASE_URL"
        return 1
    fi

    local memory_usage=$(echo "$response" | jq -r '.memory_usage_mb // "null"')
    local active_tickers=$(echo "$response" | jq -r '.active_tickers_count // "null"')

    if [[ "$memory_usage" == "null" || "$active_tickers" == "null" ]]; then
        print_error "Invalid health response format"
        echo "$response"
        return 1
    fi

    print_success "Health check passed - Memory: ${memory_usage}MB, Tickers: ${active_tickers}"
    return 0
}

test_basic_tickers_api() {
    local ticker="$1"
    local cache_param="$2"

    print_test "Basic tickers API - $ticker (cache=$cache_param)"

    local response=$(curl -s "$BASE_URL/tickers?symbol=$ticker&start_date=2025-11-05&cache=$cache_param" || echo "")
    if [[ -z "$response" ]]; then
        print_error "No response for $ticker"
        return 1
    fi

    local time=$(echo "$response" | jq -r ".$ticker[0].time // empty")
    local close=$(echo "$response" | jq -r ".$ticker[0].close // empty")
    local ma10=$(echo "$response" | jq -r ".$ticker[0].ma10 // empty")
    local ma20_score=$(echo "$response" | jq -r ".$ticker[0].ma20_score // empty")

    # Validate required fields
    print_result "Time field present" "2025-11-05" "$time" || return 1
    local close_status="$(if [[ -n "$close" && "$close" != "null" ]]; then echo "not_empty"; else echo "empty"; fi)"
    print_result "Close price present" "not_empty" "$close_status" || return 1

    # Check indicators (may be null for some tickers)
    if [[ "$ma10" != "null" && -n "$ma10" ]]; then
        print_success "MA10 indicator present: $ma10"
    else
        print_success "MA10 indicator not available (acceptable)"
    fi

    if [[ "$ma20_score" != "null" && -n "$ma20_score" ]]; then
        print_success "MA20 score indicator present: $ma20_score"
    else
        print_success "MA20 score indicator not available (acceptable)"
    fi

    return 0
}

test_multiple_tickers() {
    print_test "Multiple tickers API"

    local response=$(curl -s "$BASE_URL/tickers?symbol=VCB&symbol=FPT&cache=true" || echo "")
    if [[ -z "$response" ]]; then
        print_error "No response for multiple tickers"
        return 1
    fi

    local vcb_count=$(echo "$response" | jq '.VCB | length // 0')
    local fpt_count=$(echo "$response" | jq '.FPT | length // 0')

    print_result "VCB records returned" "greater_than_0" "$vcb_count" || return 1
    print_result "FPT records returned" "greater_than_0" "$fpt_count" || return 1

    print_success "Multiple tickers test passed"
    return 0
}

test_tickers_group_api() {
    print_test "Tickers Group API"

    local response=$(curl -s "$BASE_URL/tickers/group" || echo "")
    if [[ -z "$response" ]]; then
        print_error "No response for tickers group"
        return 1
    fi

    local group_count=$(echo "$response" | jq 'keys | length // 0')

    print_result "Groups count" "greater_than_0" "$group_count" || return 1

    # Check if common groups exist
    local has_banking=$(echo "$response" | jq 'has("banking")')
    local has_construction=$(echo "$response" | jq 'has("construction")')

    print_success "Groups API test passed - Found $group_count groups"
    return 0
}

test_cache_behavior() {
    print_test "Cache Behavior Test"

    # Test cache=true
    local start_time=$(date +%s.%N)
    local response1=$(curl -s "$BASE_URL/tickers?symbol=VCB&cache=true" >/dev/null)
    local cache_time=$(echo "$(date +%s.%N) - $start_time" | bc)

    # Test cache=false
    start_time=$(date +%s.%N)
    local response2=$(curl -s "$BASE_URL/tickers?symbol=VCB&cache=false" >/dev/null)
    local no_cache_time=$(echo "$(date +%s.%N) - $start_time" | bc)

    # Cache should generally be faster (allowing some variance)
    local cache_ms=$(echo "$cache_time * 1000" | bc)
    local no_cache_ms=$(echo "$no_cache_time * 1000" | bc)

    print_success "Cache=true response time: ${cache_ms}ms"
    print_success "Cache=false response time: ${no_cache_ms}ms"

    return 0
}

test_error_handling() {
    print_test "Error Handling Tests"

    # Test invalid ticker
    local response=$(curl -s "$BASE_URL/tickers?symbol=INVALIDTICKER123" || echo "")
    local count=$(echo "$response" | jq 'keys | length // 0')

    print_result "Invalid ticker returns empty" "0" "$count" || return 1

    # Test invalid date format
    local response=$(curl -s "$BASE_URL/tickers?symbol=VCB&start_date=invalid-date" || echo "")
    if [[ -n "$response" && "$response" != *"error"* && "$response" != *"Error"* ]]; then
        # Check if it's a valid JSON response (API might handle this gracefully)
        local error_check=$(echo "$response" | jq -r '.error // "no_error"' 2>/dev/null || echo "parse_error")
        if [[ "$error_check" == "no_error" ]]; then
            print_error "Invalid date should return error"
            return 1
        else
            print_success "Invalid date properly handled with error response"
        fi
    else
        print_success "Invalid date properly handled"
    fi

    print_success "Error handling tests passed"
    return 0
}

test_raw_proxy() {
    print_test "Raw GitHub Proxy (Legacy Endpoint)"

    # Test basic proxy functionality
    local response=$(curl -s "$BASE_URL/raw/ticker_group.json" || echo "")
    if [[ -z "$response" ]]; then
        print_error "Raw proxy not responding"
        return 1
    fi

    # Check if it's valid JSON
    local is_valid=$(echo "$response" | jq empty 2>/dev/null && echo "true" || echo "false")
    print_result "Proxy returns valid JSON" "true" "$is_valid" || return 1

    # Check if it has expected structure (groups)
    local group_count=$(echo "$response" | jq 'keys | length // 0' 2>/dev/null || echo "0")
    print_result "Has ticker groups" "greater_than_0" "$group_count" || return 1

    # Check for common groups
    local has_banking=$(echo "$response" | jq 'has("BANKING")' 2>/dev/null || echo "false")
    local has_tech=$(echo "$response" | jq 'has("TECH")' 2>/dev/null || echo "false")

    print_success "Raw proxy working - Found $group_count groups"
    if [[ "$has_banking" == "true" ]]; then
        print_success "Banking group found"
    fi
    if [[ "$has_tech" == "true" ]]; then
        print_success "Tech group found"
    fi

    return 0
}

test_indicators_completeness() {
    local ticker="$1"

    print_test "Indicators Completeness - $ticker"

    local response=$(curl -s "$BASE_URL/tickers?symbol=$ticker&start_date=2025-11-05&cache=false" || echo "")
    if [[ -z "$response" ]]; then
        print_error "No response for indicators test"
        return 1
    fi

    local data=$(echo "$response" | jq ".$ticker[0] // {}")
    local has_all_indicators=true

    # Check for key indicators
    local indicators=("ma10" "ma20" "ma50" "ma10_score" "ma20_score" "ma50_score" "money_flow" "dollar_flow" "trend_score")
    local present_count=0

    for indicator in "${indicators[@]}"; do
        local value=$(echo "$data" | jq -r ".$indicator // null")
        if [[ "$value" != "null" && -n "$value" ]]; then
            ((present_count++))
        fi
    done

    print_success "Indicators present: $present_count/9"

    # At least some indicators should be present (allowing for edge cases)
    if [[ $present_count -ge 3 ]]; then
        print_success "Sufficient indicators available"
        return 0
    else
        print_error "Insufficient indicators ($present_count/9)"
        return 1
    fi
}

test_csv_export_performance() {
    print_test "CSV Export Performance Tests"

    # Test 1D CSV (all tickers)
    local response=$(curl -s -o /dev/null -w "%{time_total},%{size_download}" "$BASE_URL/tickers?interval=1D&format=csv" || echo "")
    if [[ -z "$response" ]]; then
        print_error "Failed to get 1D CSV response"
        return 1
    fi

    local daily_time=$(echo "$response" | cut -d',' -f1)
    local daily_size=$(echo "$response" | cut -d',' -f2)
    print_success "1D CSV: ${daily_size} bytes in ${daily_time}s"

    # Test 1m CSV (single ticker)
    response=$(curl -s -o /dev/null -w "%{time_total},%{size_download}" "$BASE_URL/tickers?symbol=VCB&interval=1m&format=csv" || echo "")
    if [[ -z "$response" ]]; then
        print_error "Failed to get 1m CSV response"
        return 1
    fi

    local minute_time=$(echo "$response" | cut -d',' -f1)
    local minute_size=$(echo "$response" | cut -d',' -f2)
    print_success "1m CSV (VCB): ${minute_size} bytes in ${minute_time}s"

    # Test 1m CSV (10 tickers)
    response=$(curl -s -o /dev/null -w "%{time_total},%{size_download}" "$BASE_URL/tickers?symbol=VCB&symbol=FPT&symbol=VIC&symbol=VNM&symbol=HPG&symbol=MWG&symbol=ACB&symbol=BID&symbol=CTG&symbol=TCB&interval=1m&format=csv" || echo "")
    if [[ -z "$response" ]]; then
        print_error "Failed to get 10-ticker 1m CSV response"
        return 1
    fi

    local ten_tickers_time=$(echo "$response" | cut -d',' -f1)
    local ten_tickers_size=$(echo "$response" | cut -d',' -f2)
    print_success "1m CSV (10 tickers): ${ten_tickers_size} bytes in ${ten_tickers_time}s"

    # Performance validation
    local daily_time_ms=$(echo "$daily_time * 1000" | bc)
    local minute_time_ms=$(echo "$minute_time * 1000" | bc)
    local ten_tickers_time_ms=$(echo "$ten_tickers_time * 1000" | bc)

    # Check that daily is fast (< 50ms)
    if (( $(echo "$daily_time_ms < 50" | bc -l) )); then
        print_success "Daily CSV performance: ${daily_time_ms}ms ✓"
    else
        print_error "Daily CSV too slow: ${daily_time_ms}ms"
        return 1
    fi

    # Check that single ticker minute is reasonable (< 500ms)
    if (( $(echo "$minute_time_ms < 500" | bc -l) )); then
        print_success "Single ticker minute CSV: ${minute_time_ms}ms ✓"
    else
        print_error "Single ticker minute CSV too slow: ${minute_time_ms}ms"
        return 1
    fi

    # Check that 10 tickers minute is reasonable (< 1000ms)
    if (( $(echo "$ten_tickers_time_ms < 1000" | bc -l) )); then
        print_success "10 tickers minute CSV: ${ten_tickers_time_ms}ms ✓"
    else
        print_error "10 tickers minute CSV too slow: ${ten_tickers_time_ms}ms"
        return 1
    fi

    print_success "CSV performance tests passed"
    return 0
}

test_limit_parameter() {
    print_test "Limit Parameter Test"

    # Test 1: Get last 5 records with end_date
    local response=$(curl -s "$BASE_URL/tickers?symbol=VCB&end_date=2024-06-15&limit=5" || echo "")
    if [[ -z "$response" ]]; then
        print_error "No response for limit parameter test"
        return 1
    fi

    local record_count=$(echo "$response" | jq '.VCB | length // 0')
    print_result "Limit=5 returns 5 records" "5" "$record_count" || return 1

    # Verify records are before end_date
    local last_date=$(echo "$response" | jq -r '.VCB[-1].time // empty')
    if [[ "$last_date" > "2024-06-15" ]]; then
        print_error "Last date ($last_date) exceeds end_date (2024-06-15)"
        return 1
    fi
    print_success "Records respect end_date: last record is $last_date"

    # Test 2: Get last 10 records without date (should get last 10 trading days)
    response=$(curl -s "$BASE_URL/tickers?symbol=FPT&limit=10" || echo "")
    if [[ -z "$response" ]]; then
        print_error "No response for limit without date"
        return 1
    fi

    record_count=$(echo "$response" | jq '.FPT | length // 0')

    # Should get at most 10 records (may be less if fewer trading days available)
    if (( record_count > 10 )); then
        print_error "Limit=10 returned $record_count records (expected max 10)"
        return 1
    fi
    print_success "Limit=10 returns $record_count records (max 10)"

    # Test 3: Verify limit is ignored when start_date is provided
    response=$(curl -s "$BASE_URL/tickers?symbol=VCB&start_date=2024-06-01&end_date=2024-06-15&limit=5" || echo "")
    if [[ -z "$response" ]]; then
        print_error "No response for limit with start_date"
        return 1
    fi

    record_count=$(echo "$response" | jq '.VCB | length // 0')

    # Should get all records in range, not limited to 5
    if (( record_count < 8 )); then
        print_error "Limit incorrectly applied with start_date: got $record_count records (expected ~10)"
        return 1
    fi
    print_success "Limit ignored when start_date provided: got $record_count records"

    print_success "Limit parameter test passed"
    return 0
}

test_historical_data_range() {
    print_test "Historical Data Range (2023-2024)"

    # Test 1: cache=true (should auto-read from disk if cache insufficient)
    local start_time=$(date +%s.%N)
    local response=$(curl -s "$BASE_URL/tickers?symbol=VCB&start_date=2023-01-01&end_date=2024-12-31&interval=1D&cache=true" || echo "")
    local cache_true_time=$(echo "$(date +%s.%N) - $start_time" | bc)

    if [[ -z "$response" ]]; then
        print_error "No response for historical data range (cache=true)"
        return 1
    fi

    local record_count_cache_true=$(echo "$response" | jq '.VCB | length // 0')
    local cache_true_ms=$(echo "$cache_true_time * 1000" | bc)

    # Should have data for 2023-2024 (approximately 500+ trading days)
    if (( record_count_cache_true < 400 )); then
        print_error "Insufficient historical data (cache=true): ${record_count_cache_true} records (expected 400+)"
        return 1
    fi

    print_success "cache=true: ${record_count_cache_true} records in ${cache_true_ms}ms"

    # Verify date range for cache=true
    local first_date=$(echo "$response" | jq -r '.VCB[0].time // empty')
    local last_date=$(echo "$response" | jq -r '.VCB[-1].time // empty')

    if [[ -z "$first_date" || -z "$last_date" ]]; then
        print_error "Missing date fields in response (cache=true)"
        return 1
    fi

    # Check that first date is in 2023 range
    if [[ "$first_date" < "2023-01-01" || "$first_date" > "2023-12-31" ]]; then
        print_error "First date out of range (cache=true): $first_date (expected 2023)"
        return 1
    fi

    # Check that last date is in 2024 range
    if [[ "$last_date" < "2024-01-01" || "$last_date" > "2024-12-31" ]]; then
        print_error "Last date out of range (cache=true): $last_date (expected 2024)"
        return 1
    fi

    print_success "cache=true date range: $first_date to $last_date ✓"

    # Test 2: cache=false (force disk read)
    start_time=$(date +%s.%N)
    response=$(curl -s "$BASE_URL/tickers?symbol=VCB&start_date=2023-01-01&end_date=2024-12-31&interval=1D&cache=false" || echo "")
    local cache_false_time=$(echo "$(date +%s.%N) - $start_time" | bc)

    if [[ -z "$response" ]]; then
        print_error "No response for historical data range (cache=false)"
        return 1
    fi

    local record_count_cache_false=$(echo "$response" | jq '.VCB | length // 0')
    local cache_false_ms=$(echo "$cache_false_time * 1000" | bc)

    if (( record_count_cache_false < 400 )); then
        print_error "Insufficient historical data (cache=false): ${record_count_cache_false} records (expected 400+)"
        return 1
    fi

    print_success "cache=false: ${record_count_cache_false} records in ${cache_false_ms}ms"

    # Verify both return same data
    if [[ "$record_count_cache_true" != "$record_count_cache_false" ]]; then
        print_error "Record count mismatch: cache=true (${record_count_cache_true}) vs cache=false (${record_count_cache_false})"
        return 1
    fi

    print_success "Both cache modes return same data ✓"

    print_success "Historical data range test passed"
    return 0
}


# Main test execution
main() {
    echo -e "${BLUE}🚀 Starting AIPriceAction Integration Tests${NC}"
    echo -e "${BLUE}🌐 Testing against: $BASE_URL${NC}"
    echo ""

    # Test server is up
    if ! curl -s "$BASE_URL/health" >/dev/null 2>&1; then
        print_error "Server not available at $BASE_URL"
        exit 1
    fi

    print_success "Server is reachable"
    echo ""

    # Run all tests
    ((TOTAL_TESTS++))
    test_server_health || true

    ((TOTAL_TESTS++))
    test_basic_tickers_api "VCB" "true" || true

    ((TOTAL_TESTS++))
    test_basic_tickers_api "FPT" "false" || true

    ((TOTAL_TESTS++))
    test_multiple_tickers || true

    ((TOTAL_TESTS++))
    test_tickers_group_api || true

    ((TOTAL_TESTS++))
    test_cache_behavior || true

    ((TOTAL_TESTS++))
    test_error_handling || true

    ((TOTAL_TESTS++))
    test_raw_proxy || true

    ((TOTAL_TESTS++))
    test_indicators_completeness "VCB" || true

    ((TOTAL_TESTS++))
    test_indicators_completeness "VIC" || true

    ((TOTAL_TESTS++))
    test_csv_export_performance || true

    ((TOTAL_TESTS++))
    test_limit_parameter || true

    ((TOTAL_TESTS++))
    test_historical_data_range || true

    # Final results
    echo ""
    print_header "TEST RESULTS"

    local success_count=$((TOTAL_TESTS - FAILED_TESTS))

    if [[ $FAILED_TESTS -eq 0 ]]; then
        print_success "🎉 ALL TESTS PASSED! ($success_count/$TOTAL_TESTS)"
        exit 0
    else
        print_error "❌ TESTS FAILED: $FAILED_TESTS/$TOTAL_TESTS failed"
        exit 1
    fi
}

# Run main function
main "$@"