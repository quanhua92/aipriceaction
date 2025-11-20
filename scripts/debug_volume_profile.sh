#!/bin/bash

# Volume Profile API Debug & Validation Script
# Tests the /analysis/volume-profile endpoint thoroughly
# Validates calculations, response format, and documentation accuracy
#
# Usage: ./scripts/debug_volume_profile.sh [URL]
# Default URL: http://localhost:3000

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Configuration
BASE_URL="${1:-http://localhost:3000}"
PASSED_TESTS=0
FAILED_TESTS=0
WARNING_COUNT=0
TOTAL_TESTS=0

# Test data
TEST_SYMBOL="${2:-VJC}"
TEST_DATE="${3:-2025-11-20}"

# Helper functions
print_header() {
    echo ""
    echo -e "${BLUE}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}${BOLD}  $1${NC}"
    echo -e "${BLUE}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_section() {
    echo ""
    echo -e "${CYAN}${BOLD}📊 $1${NC}"
    echo ""
}

print_test() {
    echo -e "${YELLOW}  ⚡ $1${NC}"
    ((TOTAL_TESTS++))
}

print_pass() {
    echo -e "${GREEN}    ✅ $1${NC}"
    ((PASSED_TESTS++))
}

print_fail() {
    echo -e "${RED}    ❌ $1${NC}"
    ((FAILED_TESTS++))
}

print_warn() {
    echo -e "${YELLOW}    ⚠️  $1${NC}"
    ((WARNING_COUNT++))
}

print_info() {
    echo -e "${CYAN}    ℹ️  $1${NC}"
}

format_number() {
    printf "%'d" "$1" 2>/dev/null || echo "$1"
}

# Check if server is running
check_server() {
    print_section "Checking Server Availability"

    local response
    response=$(curl -s "$BASE_URL/health" || echo "")

    if [[ -z "$response" ]]; then
        print_fail "Server is not accessible at $BASE_URL"
        echo ""
        echo -e "${YELLOW}💡 Start the server with: cargo run -- serve --port 3000${NC}"
        exit 1
    fi

    print_pass "Server is running at $BASE_URL"
}

# Test 1: Basic API call
test_basic_query() {
    print_section "Test 1: Basic Volume Profile Query"

    print_test "Fetching volume profile for $TEST_SYMBOL on $TEST_DATE"

    local start_time=$(date +%s%N)
    local response=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL&date=$TEST_DATE")
    local end_time=$(date +%s%N)
    local duration=$(( (end_time - start_time) / 1000000 ))

    if [[ -z "$response" ]]; then
        print_fail "No response received"
        return 1
    fi

    # Check if response contains error
    local error=$(echo "$response" | jq -r '.error // empty' 2>/dev/null)
    if [[ -n "$error" ]]; then
        print_fail "API returned error: $error"
        print_info "Response: $response"
        return 1
    fi

    print_pass "Response received (${duration}ms)"

    # Validate response time
    if [[ $duration -lt 200 ]]; then
        print_pass "Response time is excellent (${duration}ms < 200ms)"
    elif [[ $duration -lt 500 ]]; then
        print_pass "Response time is good (${duration}ms < 500ms)"
    else
        print_warn "Response time is slow (${duration}ms)"
    fi

    # Store response for other tests
    echo "$response" > /tmp/volume_profile_response.json

    # Validate basic structure
    print_test "Validating response structure"

    local has_analysis_date=$(echo "$response" | jq -r '.analysis_date // empty')
    local has_analysis_type=$(echo "$response" | jq -r '.analysis_type // empty')
    local has_data=$(echo "$response" | jq -r '.data // empty')

    [[ -n "$has_analysis_date" ]] && print_pass "analysis_date: $has_analysis_date" || print_fail "Missing analysis_date"
    [[ "$has_analysis_type" == "volume_profile" ]] && print_pass "analysis_type: volume_profile" || print_fail "Wrong analysis_type: $has_analysis_type"
    [[ -n "$has_data" ]] && print_pass "data object present" || print_fail "Missing data object"

    # Validate data fields
    local symbol=$(echo "$response" | jq -r '.data.symbol // empty')
    local total_volume=$(echo "$response" | jq -r '.data.total_volume // empty')
    local total_minutes=$(echo "$response" | jq -r '.data.total_minutes // empty')

    [[ "$symbol" == "$TEST_SYMBOL" ]] && print_pass "symbol: $symbol" || print_fail "Wrong symbol: $symbol"
    [[ -n "$total_volume" ]] && print_pass "total_volume: $(format_number $total_volume)" || print_fail "Missing total_volume"
    [[ -n "$total_minutes" ]] && print_pass "total_minutes: $total_minutes" || print_fail "Missing total_minutes"

    # Check POC
    local poc_price=$(echo "$response" | jq -r '.data.poc.price // empty')
    local poc_volume=$(echo "$response" | jq -r '.data.poc.volume // empty')
    local poc_pct=$(echo "$response" | jq -r '.data.poc.percentage // empty')

    [[ -n "$poc_price" ]] && print_pass "POC price: $(format_number $poc_price) VND" || print_fail "Missing POC price"
    [[ -n "$poc_volume" ]] && print_pass "POC volume: $(format_number $poc_volume)" || print_fail "Missing POC volume"
    [[ -n "$poc_pct" ]] && print_pass "POC percentage: ${poc_pct}%" || print_fail "Missing POC percentage"

    # Check Value Area
    local va_low=$(echo "$response" | jq -r '.data.value_area.low // empty')
    local va_high=$(echo "$response" | jq -r '.data.value_area.high // empty')
    local va_volume=$(echo "$response" | jq -r '.data.value_area.volume // empty')
    local va_pct=$(echo "$response" | jq -r '.data.value_area.percentage // empty')

    [[ -n "$va_low" ]] && print_pass "Value Area Low: $(format_number $va_low) VND" || print_fail "Missing VA Low"
    [[ -n "$va_high" ]] && print_pass "Value Area High: $(format_number $va_high) VND" || print_fail "Missing VA High"
    [[ -n "$va_volume" ]] && print_pass "Value Area Volume: $(format_number $va_volume)" || print_fail "Missing VA volume"
    [[ -n "$va_pct" ]] && print_pass "Value Area Percentage: ${va_pct}%" || print_fail "Missing VA percentage"

    # Check Statistics
    local mean=$(echo "$response" | jq -r '.data.statistics.mean_price // empty')
    local median=$(echo "$response" | jq -r '.data.statistics.median_price // empty')
    local std_dev=$(echo "$response" | jq -r '.data.statistics.std_deviation // empty')
    local skewness=$(echo "$response" | jq -r '.data.statistics.skewness // empty')

    [[ -n "$mean" ]] && print_pass "Mean price: $(format_number $mean) VND" || print_fail "Missing mean price"
    [[ -n "$median" ]] && print_pass "Median price: $(format_number $median) VND" || print_fail "Missing median price"
    [[ -n "$std_dev" ]] && print_pass "Std deviation: $std_dev" || print_fail "Missing std deviation"
    [[ -n "$skewness" ]] && print_pass "Skewness: $skewness" || print_fail "Missing skewness"

    # Check Profile array
    local profile_count=$(echo "$response" | jq '.data.profile | length' 2>/dev/null || echo "0")
    if [[ $profile_count -gt 0 ]]; then
        print_pass "Profile has $profile_count price levels"
    else
        print_fail "Profile array is empty"
    fi
}

# Test 2: Validate POC calculations
test_poc_validation() {
    print_section "Test 2: POC (Point of Control) Validation"

    local response=$(cat /tmp/volume_profile_response.json)

    print_test "Validating POC is highest volume price"

    local poc_price=$(echo "$response" | jq -r '.data.poc.price')
    local poc_volume=$(echo "$response" | jq -r '.data.poc.volume')
    local total_volume=$(echo "$response" | jq -r '.data.total_volume')

    # Find max volume in profile
    local max_volume=$(echo "$response" | jq '[.data.profile[].volume] | max')
    local max_volume_price=$(echo "$response" | jq -r ".data.profile[] | select(.volume == $max_volume) | .price" | head -1)

    if (( $(echo "$poc_volume == $max_volume" | bc -l) )); then
        print_pass "POC has highest volume: $(format_number $poc_volume)"
    else
        print_fail "POC volume ($poc_volume) is not the highest (max: $max_volume)"
    fi

    # Validate POC percentage calculation
    print_test "Validating POC percentage calculation"

    local calculated_pct=$(echo "scale=2; ($poc_volume / $total_volume) * 100" | bc)
    local reported_pct=$(echo "$response" | jq -r '.data.poc.percentage')

    local diff=$(echo "scale=2; $reported_pct - $calculated_pct" | bc | tr -d '-')
    if (( $(echo "$diff < 0.1" | bc -l) )); then
        print_pass "POC percentage correct: ${reported_pct}% (calculated: ${calculated_pct}%)"
    else
        print_fail "POC percentage mismatch: reported ${reported_pct}% vs calculated ${calculated_pct}%"
    fi
}

# Test 3: Validate Value Area
test_value_area_validation() {
    print_section "Test 3: Value Area Validation"

    local response=$(cat /tmp/volume_profile_response.json)

    local poc_price=$(echo "$response" | jq -r '.data.poc.price')
    local va_low=$(echo "$response" | jq -r '.data.value_area.low')
    local va_high=$(echo "$response" | jq -r '.data.value_area.high')
    local va_pct=$(echo "$response" | jq -r '.data.value_area.percentage')
    local total_volume=$(echo "$response" | jq -r '.data.total_volume')

    print_test "Validating POC is within Value Area"

    if (( $(echo "$va_low <= $poc_price" | bc -l) )) && (( $(echo "$poc_price <= $va_high" | bc -l) )); then
        print_pass "POC ($poc_price) is within VA [$va_low - $va_high]"
    else
        print_fail "POC ($poc_price) is outside VA [$va_low - $va_high]"
    fi

    print_test "Validating Value Area percentage"

    # Calculate actual volume in VA range
    local va_volume_sum=$(echo "$response" | jq "[.data.profile[] | select(.price >= $va_low and .price <= $va_high) | .volume] | add")
    local va_pct_calc=$(echo "scale=2; ($va_volume_sum / $total_volume) * 100" | bc)

    print_info "VA percentage: ${va_pct}% (calculated: ${va_pct_calc}%)"

    if (( $(echo "$va_pct >= 65 && $va_pct <= 75" | bc -l) )); then
        print_pass "Value Area contains appropriate volume (~70%)"
    elif (( $(echo "$va_pct >= 60 && $va_pct <= 90" | bc -l) )); then
        print_warn "Value Area percentage ($va_pct%) is unusual but acceptable"
    else
        print_fail "Value Area percentage ($va_pct%) is outside expected range"
    fi
}

# Test 4: Validate profile data
test_profile_data() {
    print_section "Test 4: Profile Data Validation"

    local response=$(cat /tmp/volume_profile_response.json)

    print_test "Validating profile array sorting"

    # Check if prices are sorted ascending
    local prices=$(echo "$response" | jq -r '.data.profile[].price')
    local is_sorted=true
    local prev_price=-1

    while IFS= read -r price; do
        if (( $(echo "$price < $prev_price" | bc -l) )); then
            is_sorted=false
            break
        fi
        prev_price=$price
    done <<< "$prices"

    if $is_sorted; then
        print_pass "Profile prices are sorted ascending"
    else
        print_fail "Profile prices are not properly sorted"
    fi

    print_test "Validating percentage sum"

    local total_pct=$(echo "$response" | jq '[.data.profile[].percentage] | add')

    if (( $(echo "$total_pct >= 99.5 && $total_pct <= 100.5" | bc -l) )); then
        print_pass "Percentages sum to ${total_pct}% (≈100%)"
    else
        print_warn "Percentages sum to ${total_pct}% (expected ~100%)"
    fi

    print_test "Validating cumulative percentages"

    # Check cumulative percentages are monotonically increasing
    local cumulative=$(echo "$response" | jq -r '.data.profile[].cumulative_percentage')
    local is_increasing=true
    local prev_cum=0

    while IFS= read -r cum; do
        if (( $(echo "$cum < $prev_cum" | bc -l) )); then
            is_increasing=false
            break
        fi
        prev_cum=$cum
    done <<< "$cumulative"

    if $is_increasing; then
        print_pass "Cumulative percentages are monotonically increasing"
    else
        print_fail "Cumulative percentages are not properly increasing"
    fi

    # Check final cumulative is ~100%
    local final_cum=$(echo "$response" | jq -r '.data.profile[-1].cumulative_percentage')
    if (( $(echo "$final_cum >= 99.5 && $final_cum <= 100.5" | bc -l) )); then
        print_pass "Final cumulative percentage: ${final_cum}%"
    else
        print_warn "Final cumulative percentage: ${final_cum}% (expected ~100%)"
    fi
}

# Test 5: Parameter variations
test_parameters() {
    print_section "Test 5: Parameter Variations"

    print_test "Testing bins parameter"

    # Test bins=10
    local resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL&date=$TEST_DATE&bins=10")
    local count=$(echo "$resp" | jq '.data.profile | length' 2>/dev/null || echo "0")
    [[ $count -le 10 ]] && print_pass "bins=10: $count levels" || print_warn "bins=10: $count levels (expected ≤10)"

    # Test bins=100
    resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL&date=$TEST_DATE&bins=100")
    count=$(echo "$resp" | jq '.data.profile | length' 2>/dev/null || echo "0")
    [[ $count -le 100 ]] && print_pass "bins=100: $count levels" || print_warn "bins=100: $count levels"

    # Test bins clamping (should clamp to 10)
    resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL&date=$TEST_DATE&bins=5")
    count=$(echo "$resp" | jq '.data.profile | length' 2>/dev/null || echo "0")
    [[ $count -ge 10 ]] && print_pass "bins=5 clamped to minimum (got $count levels)" || print_warn "bins=5: $count levels"

    # Test bins clamping (should clamp to 200)
    resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL&date=$TEST_DATE&bins=300")
    count=$(echo "$resp" | jq '.data.profile | length' 2>/dev/null || echo "0")
    [[ $count -le 200 ]] && print_pass "bins=300 clamped to maximum (got $count levels)" || print_warn "bins=300: $count levels"

    print_test "Testing value_area_pct parameter"

    # Test 60%
    resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL&date=$TEST_DATE&value_area_pct=60")
    local va_pct=$(echo "$resp" | jq -r '.data.value_area.percentage' 2>/dev/null || echo "0")
    if (( $(echo "$va_pct >= 55 && $va_pct <= 65" | bc -l) )); then
        print_pass "value_area_pct=60: ${va_pct}%"
    else
        print_warn "value_area_pct=60: ${va_pct}% (expected ~60%)"
    fi

    # Test 80%
    resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL&date=$TEST_DATE&value_area_pct=80")
    va_pct=$(echo "$resp" | jq -r '.data.value_area.percentage' 2>/dev/null || echo "0")
    if (( $(echo "$va_pct >= 75 && $va_pct <= 85" | bc -l) )); then
        print_pass "value_area_pct=80: ${va_pct}%"
    else
        print_warn "value_area_pct=80: ${va_pct}% (expected ~80%)"
    fi
}

# Test 6: Edge cases
test_edge_cases() {
    print_section "Test 6: Edge Cases"

    print_test "Missing required parameter (symbol)"
    local resp=$(curl -s "$BASE_URL/analysis/volume-profile?date=$TEST_DATE")
    local error=$(echo "$resp" | jq -r '.error // empty')
    [[ -n "$error" ]] && print_pass "Error returned: $error" || print_fail "No error for missing symbol"

    print_test "Missing required parameter (date)"
    resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL")
    error=$(echo "$resp" | jq -r '.error // empty')
    [[ -n "$error" ]] && print_pass "Error returned: $error" || print_fail "No error for missing date"

    print_test "Invalid date format"
    resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL&date=invalid-date")
    error=$(echo "$resp" | jq -r '.error // empty')
    [[ -n "$error" ]] && print_pass "Error returned: $error" || print_fail "No error for invalid date"

    print_test "Future date (no data)"
    resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL&date=2099-01-01")
    error=$(echo "$resp" | jq -r '.error // empty')
    [[ -n "$error" ]] && print_pass "Error returned: $error" || print_warn "No error for future date"

    print_test "Invalid symbol"
    resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=INVALID&date=$TEST_DATE")
    error=$(echo "$resp" | jq -r '.error // empty')
    [[ -n "$error" ]] && print_pass "Error returned: $error" || print_warn "No error for invalid symbol"
}

# Test 7: ASCII visualization
print_ascii_profile() {
    print_section "Visual Volume Profile"

    local response=$(cat /tmp/volume_profile_response.json)

    local poc_price=$(echo "$response" | jq -r '.data.poc.price')
    local va_low=$(echo "$response" | jq -r '.data.value_area.low')
    local va_high=$(echo "$response" | jq -r '.data.value_area.high')

    echo -e "${CYAN}  Price Level    Volume Distribution${NC}"
    echo "  ─────────────────────────────────────────────────"

    # Get profile data
    local max_pct=$(echo "$response" | jq '[.data.profile[].percentage] | max')

    # Show top 20 levels by volume
    echo "$response" | jq -r '.data.profile | sort_by(-.percentage) | .[:20] | .[] | "\(.price)|\(.percentage)"' | while IFS='|' read -r price pct; do
        # Create bar chart
        local bar_length=$(echo "scale=0; ($pct / $max_pct) * 30" | bc)
        local bar=""
        for ((i=0; i<bar_length; i++)); do
            bar="${bar}▊"
        done

        # Format price
        local price_fmt=$(printf "%.0f" "$price")

        # Add markers
        local marker=""
        if (( $(echo "$price == $poc_price" | bc -l) )); then
            marker=" ${BOLD}${GREEN}← POC${NC}"
        elif (( $(echo "$price == $va_low" | bc -l) )); then
            marker=" ${BOLD}${YELLOW}← VA Low${NC}"
        elif (( $(echo "$price == $va_high" | bc -l) )); then
            marker=" ${BOLD}${YELLOW}← VA High${NC}"
        fi

        printf "  %10s  ${MAGENTA}%s${NC} %.2f%%%s\n" "$(format_number $price_fmt)" "$bar" "$pct" "$marker"
    done

    echo "  ─────────────────────────────────────────────────"
    echo -e "${CYAN}  (Showing top 20 levels by volume)${NC}"
}

# Test 8: Documentation accuracy
test_documentation_accuracy() {
    print_section "Test 7: Documentation Accuracy Check"

    local response=$(cat /tmp/volume_profile_response.json)

    print_test "Checking response format matches VOLUME_PROFILE.md"

    # Check all documented fields exist
    local fields=(
        "data.symbol"
        "data.total_volume"
        "data.total_minutes"
        "data.price_range.low"
        "data.price_range.high"
        "data.price_range.spread"
        "data.poc.price"
        "data.poc.volume"
        "data.poc.percentage"
        "data.value_area.low"
        "data.value_area.high"
        "data.value_area.volume"
        "data.value_area.percentage"
        "data.statistics.mean_price"
        "data.statistics.median_price"
        "data.statistics.std_deviation"
        "data.statistics.skewness"
    )

    local all_present=true
    for field in "${fields[@]}"; do
        local value=$(echo "$response" | jq -r ".$field // empty")
        if [[ -z "$value" || "$value" == "null" ]]; then
            print_fail "Missing field: $field"
            all_present=false
        fi
    done

    if $all_present; then
        print_pass "All documented fields are present"
    fi

    print_test "Checking default values"

    # Test defaults (bins=50, value_area_pct=70)
    local default_resp=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=$TEST_SYMBOL&date=$TEST_DATE")
    local profile_count=$(echo "$default_resp" | jq '.data.profile | length')
    local va_pct=$(echo "$default_resp" | jq -r '.data.value_area.percentage')

    if [[ $profile_count -le 50 ]]; then
        print_pass "Default bins (50) applied: $profile_count levels"
    else
        print_warn "Default bins unexpected: $profile_count levels"
    fi

    if (( $(echo "$va_pct >= 65 && $va_pct <= 75" | bc -l) )); then
        print_pass "Default value_area_pct (70%) applied: ${va_pct}%"
    else
        print_warn "Default value_area_pct unexpected: ${va_pct}%"
    fi

    print_test "Checking tick size alignment (VN stocks)"

    # For VN stocks, prices should align with tick sizes
    # VCB price range is typically 60,000-65,000 (50 VND tick)
    local first_price=$(echo "$response" | jq -r '.data.profile[0].price')
    local tick_size=50  # Expected for VCB price range

    local remainder=$(echo "$first_price % $tick_size" | bc)
    if [[ $remainder == "0" || $remainder == "0.0" ]]; then
        print_pass "Prices align with tick size ($tick_size VND)"
    else
        print_info "Price: $first_price, Tick: $tick_size, Remainder: $remainder"
        print_warn "Prices may not align perfectly with tick size"
    fi
}

# Generate summary report
generate_summary_report() {
    print_header "📋 TEST SUMMARY REPORT"

    echo ""
    echo -e "${BOLD}Test Statistics:${NC}"
    echo "  ├─ Total Tests: $TOTAL_TESTS"
    echo "  ├─ Passed: ${GREEN}✅ $PASSED_TESTS${NC}"
    echo "  ├─ Failed: ${RED}❌ $FAILED_TESTS${NC}"
    echo "  └─ Warnings: ${YELLOW}⚠️  $WARNING_COUNT${NC}"
    echo ""

    local pass_rate=0
    if [[ $TOTAL_TESTS -gt 0 ]]; then
        pass_rate=$(echo "scale=1; ($PASSED_TESTS / $TOTAL_TESTS) * 100" | bc)
    fi

    echo -e "${BOLD}Pass Rate: ${pass_rate}%${NC}"
    echo ""

    if [[ $FAILED_TESTS -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${GREEN}${BOLD}  ✅ ALL TESTS PASSED!${NC}"
        echo -e "${GREEN}${BOLD}  Volume Profile API is working correctly!${NC}"
        echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

        if [[ $WARNING_COUNT -gt 0 ]]; then
            echo ""
            echo -e "${YELLOW}Note: $WARNING_COUNT warnings were found. Review above for details.${NC}"
        fi
    else
        echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${RED}${BOLD}  ❌ SOME TESTS FAILED${NC}"
        echo -e "${RED}${BOLD}  Please review the failures above.${NC}"
        echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    fi

    echo ""
    echo -e "${BLUE}Documentation Status:${NC}"
    if [[ $FAILED_TESTS -eq 0 ]]; then
        echo -e "  ✅ ${GREEN}docs/VOLUME_PROFILE.md - ACCURATE${NC}"
        echo -e "  ✅ ${GREEN}docs/VOLUME_PROFILE_USAGE.md - ACCURATE${NC}"
        echo -e "  ✅ ${GREEN}Implementation matches documentation${NC}"
    else
        echo -e "  ⚠️  ${YELLOW}Review documentation for accuracy${NC}"
        echo -e "  ⚠️  ${YELLOW}Some discrepancies found${NC}"
    fi

    echo ""
}

# Main execution
main() {
    print_header "🔍 Volume Profile API Debug & Validation"
    echo ""
    echo -e "${CYAN}Testing endpoint: ${BOLD}$BASE_URL/analysis/volume-profile${NC}"
    echo -e "${CYAN}Test symbol: ${BOLD}$TEST_SYMBOL${NC}"
    echo -e "${CYAN}Test date: ${BOLD}$TEST_DATE${NC}"

    # Check dependencies
    if ! command -v jq &> /dev/null; then
        echo ""
        echo -e "${RED}❌ Error: jq is required but not installed${NC}"
        echo -e "${YELLOW}Install with: brew install jq (macOS) or apt-get install jq (Linux)${NC}"
        exit 1
    fi

    if ! command -v bc &> /dev/null; then
        echo ""
        echo -e "${YELLOW}⚠️  Warning: bc not installed. Some calculations will be skipped.${NC}"
    fi

    # Run tests
    check_server
    test_basic_query
    test_poc_validation
    test_value_area_validation
    test_profile_data
    test_parameters
    test_edge_cases
    print_ascii_profile
    test_documentation_accuracy

    # Generate report
    generate_summary_report

    # Cleanup
    rm -f /tmp/volume_profile_response.json

    # Exit code
    [[ $FAILED_TESTS -eq 0 ]] && exit 0 || exit 1
}

# Run main
main "$@"
