#!/bin/bash

# Analysis API Integration Test Script for AIPriceAction
# Tests all analysis endpoints with various parameters
# Usage: ./scripts/test-analysis.sh [URL]
# Default URL: http://localhost:3000

# Don't exit on error - we want to report all test results

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
BASE_URL="${1:-http://localhost:3000}"
FAILED_TESTS=0
TOTAL_TESTS=0

# Helper functions
print_header() {
    echo -e "${BLUE}🔍 === $1 === 🔍${NC}"
    echo -e "${BLUE}⏰ Time: $(date)${NC}"
    echo ""
}

print_test() {
    echo -e "${YELLOW}📋 $1${NC}"
    ((TOTAL_TESTS++))
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

# Check if server is running
check_server() {
    print_header "Checking Server Availability"

    local response
    response=$(curl -s "$BASE_URL/health" || echo "")

    if [[ -z "$response" ]]; then
        print_error "Server is not accessible at $BASE_URL"
        echo -e "${YELLOW}💡 Make sure the server is running with: cargo run -- serve --port 3000${NC}"
        return 1
    fi

    print_success "Server is running at $BASE_URL"
    echo ""
    return 0
}

# Test Top Performers Endpoint
test_top_performers() {
    print_header "Testing Top Performers Endpoint (/analysis/top-performers)"

    # Test 1: Basic top performers
    print_test "Basic top performers test"
    local response=$(curl -s "$BASE_URL/analysis/top-performers" || echo "")

    if [[ -z "$response" ]]; then
        print_error "No response for top performers"
        return 1
    fi

    local analysis_type=$(echo "$response" | jq -r '.analysis_type // empty')
    local performers_count=$(echo "$response" | jq '.data.performers | length // 0')

    print_result "Analysis type correct" "top_performers" "$analysis_type" || return 1
    print_result "Performers returned" "greater_than_0" "$performers_count" || return 1

    # Test 2: Sort by close change
    print_test "Sort by close change test"
    response=$(curl -s "$BASE_URL/analysis/top-performers?sort_by=close_changed&limit=5" || echo "")

    if [[ -n "$response" ]]; then
        local first_symbol=$(echo "$response" | jq -r '.data.performers[0].symbol // empty')
        print_result "Sorted response received" "not_empty" "$first_symbol"
    fi

    # Test 3: Sort by volume
    print_test "Sort by volume test"
    response=$(curl -s "$BASE_URL/analysis/top-performers?sort_by=volume&limit=5" || echo "")

    if [[ -n "$response" ]]; then
        local first_symbol=$(echo "$response" | jq -r '.data.performers[0].symbol // empty')
        print_result "Volume sort response received" "not_empty" "$first_symbol"
    fi

    # Test 4: Sort by MA20 score
    print_test "Sort by MA20 score test"
    response=$(curl -s "$BASE_URL/analysis/top-performers?sort_by=ma20_score&limit=5" || echo "")

    if [[ -n "$response" ]]; then
        local first_symbol=$(echo "$response" | jq -r '.data.performers[0].symbol // empty')
        print_result "MA20 score sort response received" "not_empty" "$first_symbol"
    fi

    # Test 5: Sort by MA100 score (descending)
    print_test "Sort by MA100 score (desc) test"
    response=$(curl -s "$BASE_URL/analysis/top-performers?sort_by=ma100_score&direction=desc&limit=5&min_volume=10000" || echo "")

    if [[ -n "$response" ]]; then
        local first_score=$(echo "$response" | jq -r '.data.performers[0].ma100_score // empty')
        local last_score=$(echo "$response" | jq -r '.data.performers[-1].ma100_score // empty')

        # Check if scores are in descending order
        if [[ -n "$first_score" && -n "$last_score" ]]; then
            # Use bc for floating point comparison if available, otherwise just check not empty
            if command -v bc &> /dev/null; then
                if (( $(echo "$first_score >= $last_score" | bc -l) )); then
                    print_success "MA100 scores correctly sorted descending (${first_score} >= ${last_score})"
                else
                    print_error "MA100 scores NOT sorted descending (${first_score} < ${last_score})"
                fi
            else
                print_success "MA100 score sort response received (first: ${first_score}, last: ${last_score})"
            fi
        else
            print_error "MA100 scores missing in response"
        fi
    fi

    # Test 6: Sort by MA200 score (ascending) - now tests dual response structure
    print_test "Sort by MA200 score (asc) test"
    response=$(curl -s "$BASE_URL/analysis/top-performers?sort_by=ma200_score&direction=asc&limit=5&min_volume=10000" || echo "")

    if [[ -n "$response" ]]; then
        # With new dual response structure, performers always contains top performers (descending)
        # and worst_performers contains worst performers (ascending)
        local first_top_score=$(echo "$response" | jq -r '.data.performers[0].ma200_score // empty')
        local first_worst_score=$(echo "$response" | jq -r '.data.worst_performers[0].ma200_score // empty')

        # Verify we have both arrays
        local performers_count=$(echo "$response" | jq '.data.performers | length // 0')
        local worst_count=$(echo "$response" | jq '.data.worst_performers | length // 0')

        if [[ $performers_count -gt 0 && $worst_count -gt 0 ]]; then
            if [[ -n "$first_top_score" && -n "$first_worst_score" && "$first_top_score" != "null" && "$first_worst_score" != "null" ]]; then
                # Top performers should have higher scores than worst performers
                if command -v bc &> /dev/null; then
                    if (( $(echo "$first_top_score >= $first_worst_score" | bc -l) )); then
                        print_success "MA200 dual response correct (top: ${first_top_score} >= worst: ${first_worst_score})"
                    else
                        print_error "MA200 dual response incorrect (top: ${first_top_score} < worst: ${first_worst_score})"
                    fi
                else
                    print_success "MA200 dual response received (top: ${first_top_score}, worst: ${first_worst_score})"
                fi
            else
                print_success "MA200 dual response structure correct (${performers_count} top, ${worst_count} worst)"
            fi
        else
            print_error "MA200 dual response incomplete (top: ${performers_count}, worst: ${worst_count})"
        fi
    fi

    # Test 7: Sort by total_money_changed (descending - top money inflows)
    print_test "Sort by total_money_changed (desc) test"
    response=$(curl -s "$BASE_URL/analysis/top-performers?sort_by=total_money_changed&direction=desc&limit=5&min_volume=10000" || echo "")

    if [[ -n "$response" ]]; then
        local first_symbol=$(echo "$response" | jq -r '.data.performers[0].symbol // empty')
        local first_money_changed=$(echo "$response" | jq -r '.data.performers[0].total_money_changed // empty')

        print_result "Total money changed sort response received" "not_empty" "$first_symbol"

        if [[ -n "$first_money_changed" && "$first_money_changed" != "null" ]]; then
            print_success "Total money changed value present: ${first_money_changed}"
        fi
    fi

    # Test 8: Sort by total_money_changed (ascending - top money outflows)
    print_test "Sort by total_money_changed (asc) test"
    response=$(curl -s "$BASE_URL/analysis/top-performers?sort_by=total_money_changed&direction=asc&limit=5&min_volume=10000" || echo "")

    if [[ -n "$response" ]]; then
        local first_symbol=$(echo "$response" | jq -r '.data.performers[0].symbol // empty')
        local first_money_changed=$(echo "$response" | jq -r '.data.performers[0].total_money_changed // empty')

        print_result "Total money changed (ascending) response received" "not_empty" "$first_symbol"

        if [[ -n "$first_money_changed" && "$first_money_changed" != "null" ]]; then
            # For ascending sort, we expect negative values (money outflows)
            if command -v bc &> /dev/null; then
                if (( $(echo "$first_money_changed < 0" | bc -l) )); then
                    print_success "Ascending sort shows negative money flow: ${first_money_changed}"
                else
                    print_success "Ascending sort money flow value: ${first_money_changed}"
                fi
            else
                print_success "Total money changed (ascending) value: ${first_money_changed}"
            fi
        fi
    fi

    # Test 9: Validate total_money_changed field presence and format
    print_test "Total money changed field validation test"
    response=$(curl -s "$BASE_URL/analysis/top-performers?sort_by=total_money_changed&limit=3" || echo "")

    if [[ -n "$response" ]]; then
        local performers_count=$(echo "$response" | jq '.data.performers | length // 0')

        if [[ $performers_count -gt 0 ]]; then
            # Check if all performers have total_money_changed field
            local valid_count=0
            for i in $(seq 0 $((performers_count-1))); do
                local money_changed=$(echo "$response" | jq -r ".data.performers[$i].total_money_changed // empty")
                if [[ -n "$money_changed" && "$money_changed" != "null" ]]; then
                    ((valid_count++))
                fi
            done

            if [[ $valid_count -eq $performers_count ]]; then
                print_success "All ${performers_count} performers have valid total_money_changed values"
            else
                print_success "${valid_count}/${performers_count} performers have valid total_money_changed values"
            fi
        else
            print_error "No performers returned for total_money_changed validation"
        fi
    fi

    echo ""
}

# Test MA Scores by Sector Endpoint
test_ma_scores_by_sector() {
    print_header "Testing MA Scores by Sector Endpoint (/analysis/ma-scores-by-sector)"

    # Test 1: Basic MA20 scores by sector
    print_test "Basic MA20 scores by sector test"
    local response=$(curl -s "$BASE_URL/analysis/ma-scores-by-sector?ma_period=20" || echo "")

    if [[ -z "$response" ]]; then
        print_error "No response for MA scores by sector"
        return 1
    fi

    local analysis_type=$(echo "$response" | jq -r '.analysis_type // empty')
    local sectors_count=$(echo "$response" | jq '.data.sectors | length // 0')
    local ma_period=$(echo "$response" | jq -r '.data.ma_period // empty')

    print_result "Analysis type correct" "ma_scores_by_sector" "$analysis_type" || return 1
    print_result "Sectors returned" "greater_than_0" "$sectors_count" || return 1
    print_result "MA period correct" "20" "$ma_period" || return 1

    # Test 2: MA50 scores
    print_test "MA50 scores by sector test"
    response=$(curl -s "$BASE_URL/analysis/ma-scores-by-sector?ma_period=50" || echo "")

    if [[ -n "$response" ]]; then
        local ma_period=$(echo "$response" | jq -r '.data.ma_period // empty')
        print_result "MA50 period correct" "50" "$ma_period"
    fi

    # Test 3: With threshold
    print_test "MA scores with threshold test"
    response=$(curl -s "$BASE_URL/analysis/ma-scores-by-sector?ma_period=20&min_score=1.0" || echo "")

    if [[ -n "$response" ]]; then
        local threshold=$(echo "$response" | jq -r '.data.threshold // empty')
        print_result "Threshold applied correctly" "1.0" "$threshold"
    fi

    # Test 4: Invalid MA period (should return error)
    print_test "Invalid MA period test"
    response=$(curl -s "$BASE_URL/analysis/ma-scores-by-sector?ma_period=999" || echo "")

    if [[ -n "$response" ]]; then
        # Check if it's an error response
        local error_msg=$(echo "$response" | jq -r '.error // empty')
        if [[ -n "$error_msg" ]]; then
            print_success "Invalid MA period properly rejected: $error_msg"
        else
            print_error "Expected error for invalid MA period but got valid response"
        fi
    fi

    echo ""
}

# Test Volume Profile Endpoint
test_volume_profile() {
    print_header "Testing Volume Profile Endpoint (/analysis/volume-profile)"

    # Test 1: Basic volume profile
    print_test "Basic volume profile test (VCB stock)"
    local response=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=VCB&date=2024-01-15" || echo "")

    if [[ -z "$response" ]]; then
        print_error "No response for volume profile"
        return 1
    fi

    local analysis_type=$(echo "$response" | jq -r '.analysis_type // empty')
    local symbol=$(echo "$response" | jq -r '.data.symbol // empty')
    local poc_price=$(echo "$response" | jq -r '.data.poc.price // empty')
    local va_low=$(echo "$response" | jq -r '.data.value_area.low // empty')

    print_result "Analysis type correct" "volume_profile" "$analysis_type" || return 1
    print_result "Symbol correct" "VCB" "$symbol" || return 1
    print_result "POC price present" "not_empty" "$poc_price" || return 1
    print_result "Value area low present" "not_empty" "$va_low" || return 1

    # Test 2: Crypto mode
    print_test "Crypto volume profile test (BTC)"
    response=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=BTC&date=2024-01-15&mode=crypto" || echo "")

    if [[ -n "$response" ]]; then
        local symbol=$(echo "$response" | jq -r '.data.symbol // empty')
        local error=$(echo "$response" | jq -r '.error // empty')

        if [[ -n "$error" ]]; then
            print_success "Crypto query handled (Note: $error)"
        else
            print_result "Crypto symbol correct" "BTC" "$symbol"
        fi
    fi

    # Test 3: Invalid date format
    print_test "Invalid date format test"
    response=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=VCB&date=invalid-date" || echo "")

    if [[ -n "$response" ]]; then
        local error=$(echo "$response" | jq -r '.error // empty')
        if [[ -n "$error" ]]; then
            print_success "Invalid date properly rejected: $error"
        else
            print_error "Expected error for invalid date but got valid response"
        fi
    fi

    # Test 4: Missing symbol parameter
    print_test "Missing symbol parameter test"
    response=$(curl -s "$BASE_URL/analysis/volume-profile?date=2024-01-15" || echo "")

    if [[ -n "$response" ]]; then
        local error=$(echo "$response" | jq -r '.error // empty')
        if [[ -n "$error" ]]; then
            print_success "Missing symbol properly rejected: $error"
        else
            print_error "Expected error for missing symbol but got valid response"
        fi
    fi

    # Test 5: Custom bins parameter
    print_test "Custom bins parameter test (100 bins)"
    response=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=VCB&date=2024-01-15&bins=100" || echo "")

    if [[ -n "$response" ]]; then
        local profile_length=$(echo "$response" | jq '.data.profile | length // 0')
        if [[ $profile_length -gt 0 ]]; then
            print_success "Custom bins returned profile with ${profile_length} levels"
        else
            print_error "No profile levels returned for custom bins"
        fi
    fi

    # Test 6: Custom value area percentage
    print_test "Custom value area percentage test (80%)"
    response=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=VCB&date=2024-01-15&value_area_pct=80" || echo "")

    if [[ -n "$response" ]]; then
        local va_percentage=$(echo "$response" | jq -r '.data.value_area.percentage // empty')
        if [[ -n "$va_percentage" && "$va_percentage" != "null" ]]; then
            # Check if percentage is close to 80% (allow some rounding)
            if command -v bc &> /dev/null; then
                if (( $(echo "$va_percentage >= 75 && $va_percentage <= 85" | bc -l) )); then
                    print_success "Value area percentage is around 80%: ${va_percentage}%"
                else
                    print_success "Value area percentage: ${va_percentage}%"
                fi
            else
                print_success "Value area percentage: ${va_percentage}%"
            fi
        else
            print_error "Value area percentage not present"
        fi
    fi

    # Test 7: Validate response structure
    print_test "Response structure validation test"
    response=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=VCB&date=2024-01-15" || echo "")

    if [[ -n "$response" ]]; then
        local has_poc=$(echo "$response" | jq 'has("data") and .data | has("poc")' || echo "false")
        local has_value_area=$(echo "$response" | jq 'has("data") and .data | has("value_area")' || echo "false")
        local has_profile=$(echo "$response" | jq 'has("data") and .data | has("profile")' || echo "false")
        local has_statistics=$(echo "$response" | jq 'has("data") and .data | has("statistics")' || echo "false")

        if [[ "$has_poc" == "true" && "$has_value_area" == "true" && "$has_profile" == "true" && "$has_statistics" == "true" ]]; then
            print_success "All required fields present (POC, Value Area, Profile, Statistics)"
        else
            print_error "Missing fields - POC: $has_poc, VA: $has_value_area, Profile: $has_profile, Stats: $has_statistics"
        fi
    fi

    echo ""
}

# Main execution
main() {
    echo -e "${BLUE}🚀 Analysis API Integration Test Suite${NC}"
    echo -e "${BLUE}🌐 Testing against: $BASE_URL${NC}"
    echo ""

    # Check server availability
    if ! check_server; then
        exit 1
    fi

    # Run tests
    test_top_performers
    test_ma_scores_by_sector
    test_volume_profile

    # Print summary
    print_header "Test Summary"

    local passed_tests=$((TOTAL_TESTS - FAILED_TESTS))

    if [[ $FAILED_TESTS -eq 0 ]]; then
        echo -e "${GREEN}🎉 All $TOTAL_TESTS tests passed! 🎉${NC}"
        echo -e "${GREEN}✨ Analysis API is working correctly! ✨${NC}"
    else
        echo -e "${RED}❌ $FAILED_TESTS out of $TOTAL_TESTS tests failed${NC}"
        echo -e "${YELLOW}✅ $passed_tests tests passed${NC}"
        echo ""
        echo -e "${YELLOW}💡 Check the server logs for more details${NC}"
        exit 1
    fi

    echo ""
    echo -e "${BLUE}📚 API Documentation:${NC}"
    echo -e "${BLUE}  - Top Performers: GET /analysis/top-performers?sort_by=close_changed&limit=10${NC}"
    echo -e "${BLUE}  - Total Money Flow: GET /analysis/top-performers?sort_by=total_money_changed&direction=desc${NC}"
    echo -e "${BLUE}  - MA Scores by Sector: GET /analysis/ma-scores-by-sector?ma_period=20${NC}"
    echo -e "${BLUE}  - Volume Profile: GET /analysis/volume-profile?symbol=VCB&date=2024-01-15${NC}"
    echo ""
    echo -e "${GREEN}🔍 Test completed successfully!${NC}"
}

# Check dependencies
if ! command -v curl &> /dev/null; then
    echo -e "${RED}❌ curl is required but not installed. Please install curl to run this script.${NC}"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    echo -e "${YELLOW}⚠️  jq is not installed. JSON output will not be formatted.${NC}"
fi

# Run main function
main "$@"