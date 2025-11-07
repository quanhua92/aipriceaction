#!/bin/bash

# Analysis API Integration Test Script for AIPriceAction
# Tests all analysis endpoints with various parameters
# Usage: ./scripts/test-analysis.sh [URL]
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
            else
                print_error "$test_name: ✗ (empty response)"
            fi
            ;;
        "contains")
            if echo "$actual" | grep -q "$3"; then
                print_success "$test_name: ✓"
            else
                print_error "$test_name: ✗ (missing '$3')"
            fi
            ;;
        "equals")
            if [[ "$actual" == "$3" ]]; then
                print_success "$test_name: ✓"
            else
                print_error "$test_name: ✗ (expected '$3', got '$actual')"
            fi
            ;;
        "status_200")
            if [[ "$actual" == "200" ]]; then
                print_success "$test_name: ✓"
            else
                print_error "$test_name: ✗ (HTTP $actual)"
            fi
            ;;
    esac
}

# HTTP request function
make_request() {
    local url="$1"
    local description="$2"

    print_test "Request: $description"
    echo -e "${BLUE}🌐 GET: $url${NC}"

    local response
    local http_code

    response=$(curl -s -w "HTTPSTATUS:%{http_code}" "$url")
    http_code=$(echo "$response" | grep -o "HTTPSTATUS:[0-9]*" | cut -d: -f2)
    local body=$(echo "$response" | sed -E 's/HTTPSTATUS:[0-9]*$//')

    echo -e "${BLUE}📊 Status: $http_code${NC}"

    if [[ "$http_code" == "200" ]]; then
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
    else
        echo "$body"
    fi

    echo ""
    echo "$body|$http_code"
}

# Test Top Performers Endpoint
test_top_performers() {
    print_header "Testing Top Performers Endpoint (/analysis/top-performers)"

    # Test 1: Basic top performers
    echo "Basic top performers test"
    local response=$(make_request "$BASE_URL/analysis/top-performers" "Get top performers (default)")
    local body=$(echo "$response" | cut -d'|' -f1)
    local http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "HTTP Status" "status_200" "$http_code"
    print_result "Response not empty" "not_empty" "$body"

    # Test 2: Sort by close change
    response=$(make_request "$BASE_URL/analysis/top-performers?sort_by=close_change&limit=5" "Sort by close change")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Sort by close change" "status_200" "$http_code"
    print_result "Close change data exists" "contains" "$body" "close_change"

    # Test 3: Sort by volume
    response=$(make_request "$BASE_URL/analysis/top-performers?sort_by=volume&limit=5&direction=desc" "Sort by volume")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Sort by volume" "status_200" "$http_code"
    print_result "Volume data exists" "contains" "$body" "volume"

    # Test 4: Sort by MA score
    response=$(make_request "$BASE_URL/analysis/top-performers?sort_by=ma20_score&limit=5" "Sort by MA20 score")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Sort by MA20 score" "status_200" "$http_code"
    print_result "MA score data exists" "contains" "$body" "ma20_score"

    # Test 5: Ascending order
    response=$(make_request "$BASE_URL/analysis/top-performers?sort_by=close_change_percent&direction=asc&limit=5" "Ascending order")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Ascending order" "status_200" "$http_code"

    # Test 6: Minimum volume filter
    response=$(make_request "$BASE_URL/analysis/top-performers?min_volume=1000000&limit=5" "Minimum volume filter")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Volume filter" "status_200" "$http_code"

    # Test 7: Specific date (if available)
    response=$(make_request "$BASE_URL/analysis/top-performers?date=2024-01-02&limit=5" "Specific date")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Specific date" "status_200" "$http_code"

    # Test 8: Sector filter (if ticker groups are available)
    response=$(make_request "$BASE_URL/analysis/top-performers?sector=VN30&limit=5" "Sector filter")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Sector filter" "status_200" "$http_code"

    # Test 9: Invalid sort metric (should use default)
    response=$(make_request "$BASE_URL/analysis/top-performers?sort_by=invalid_metric&limit=5" "Invalid sort metric")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Invalid sort metric handled" "status_200" "$http_code"

    echo ""
}

# Test MA Scores by Sector Endpoint
test_ma_scores_by_sector() {
    print_header "Testing MA Scores by Sector Endpoint (/analysis/ma-scores-by-sector)"

    # Test 1: Basic MA20 scores by sector
    echo "Basic MA20 scores by sector test"
    local response=$(make_request "$BASE_URL/analysis/ma-scores-by-sector?ma_period=20" "MA20 scores by sector")
    local body=$(echo "$response" | cut -d'|' -f1)
    local http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "HTTP Status" "status_200" "$http_code"
    print_result "Response not empty" "not_empty" "$body"
    print_result "Sector data exists" "contains" "$body" "sectors"

    # Test 2: MA10 scores
    response=$(make_request "$BASE_URL/analysis/ma-scores-by-sector?ma_period=10" "MA10 scores by sector")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "MA10 scores" "status_200" "$http_code"
    print_result "MA period 10 specified" "contains" "$body" '"ma_period":10'

    # Test 3: MA50 scores
    response=$(make_request "$BASE_URL/analysis/ma-scores-by-sector?ma_period=50" "MA50 scores by sector")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "MA50 scores" "status_200" "$http_code"
    print_result "MA period 50 specified" "contains" "$body" '"ma_period":50'

    # Test 4: With threshold
    response=$(make_request "$BASE_URL/analysis/ma-scores-by-sector?ma_period=20&min_score=1.0" "With threshold")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "With threshold" "status_200" "$http_code"
    print_result "Threshold specified" "contains" "$body" '"threshold":1.0'

    # Test 5: Above threshold only
    response=$(make_request "$BASE_URL/analysis/ma-scores-by-sector?ma_period=20&min_score=0.5&above_threshold_only=true" "Above threshold only")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Above threshold only" "status_200" "$http_code"

    # Test 6: Top per sector limit
    response=$(make_request "$BASE_URL/analysis/ma-scores-by-sector?ma_period=20&top_per_sector=5" "Top per sector limit")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Top per sector limit" "status_200" "$http_code"

    # Test 7: Specific date
    response=$(make_request "$BASE_URL/analysis/ma-scores-by-sector?ma_period=20&date=2024-01-02" "Specific date")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Specific date" "status_200" "$http_code"

    # Test 8: Invalid MA period
    response=$(make_request "$BASE_URL/analysis/ma-scores-by-sector?ma_period=999" "Invalid MA period")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "Invalid MA period (400 expected)" "status_200" "$http_code"

    # Test 9: MA100 scores
    response=$(make_request "$BASE_URL/analysis/ma-scores-by-sector?ma_period=100" "MA100 scores by sector")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "MA100 scores" "status_200" "$http_code"

    # Test 10: MA200 scores
    response=$(make_request "$BASE_URL/analysis/ma-scores-by-sector?ma_period=200" "MA200 scores by sector")
    body=$(echo "$response" | cut -d'|' -f1)
    http_code=$(echo "$response" | cut -d'|' -f2)

    print_result "MA200 scores" "status_200" "$http_code"

    echo ""
}

# Check if server is running
check_server() {
    print_header "Checking Server Availability"

    local response=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health" 2>/dev/null || echo "000")

    if [[ "$response" == "200" ]]; then
        print_success "Server is running at $BASE_URL"
        echo ""
        return 0
    else
        print_error "Server is not accessible at $BASE_URL (HTTP $response)"
        echo -e "${YELLOW}💡 Make sure the server is running with: cargo run -- serve --port 3000${NC}"
        echo ""
        return 1
    fi
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