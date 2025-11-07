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
    response=$(curl -s "$BASE_URL/analysis/top-performers?sort_by=close_change&limit=5" || echo "")

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

    # Test 4: Sort by MA score
    print_test "Sort by MA20 score test"
    response=$(curl -s "$BASE_URL/analysis/top-performers?sort_by=ma20_score&limit=5" || echo "")

    if [[ -n "$response" ]]; then
        local first_symbol=$(echo "$response" | jq -r '.data.performers[0].symbol // empty')
        print_result "MA score sort response received" "not_empty" "$first_symbol"
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
    echo -e "${BLUE}  - Top Performers: GET /analysis/top-performers?sort_by=close_change_percent&limit=10${NC}"
    echo -e "${BLUE}  - MA Scores by Sector: GET /analysis/ma-scores-by-sector?ma_period=20${NC}"
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