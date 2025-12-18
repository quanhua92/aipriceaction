#!/bin/bash

# Proper Data Correctness Test with Ground Truth Validation
# Tests VNINDEX (stocks) and BTC (crypto) with realistic expectations

set -e

API_URL="${1:-http://localhost:3000}"
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Proper Data Correctness Test with Ground Truth ==="
echo "API URL: $API_URL"
echo ""

# Function to test interval with ground truth validation
test_interval_with_ground_truth() {
    local symbol="$1"
    local mode="$2"
    local interval="$3"
    local limit="$4"
    local expected_end_date="$5"
    local expected_min_date="$6"  # Optional - for validating start date
    local description="$7"

    TOTAL_TESTS=$((TOTAL_TESTS + 1))

    echo -e "${BLUE}Testing $description${NC}"
    echo "  Symbol: $symbol | Mode: $mode | Interval: $interval | Limit: $limit"
    echo "  Expected End Date: $expected_end_date${EXPECTED_START:+ | Expected Min Date: $expected_min_date}"

    # Build URL
    local url="$API_URL/tickers?symbol=$symbol&mode=$mode&interval=$interval&limit=$limit&end_date=$expected_end_date&format=csv"

    # Make API call
    local response=$(curl -s "$url" 2>/dev/null || echo "")
    if [[ -z "$response" ]]; then
        echo -e "  ${RED}❌ FAILED: No response from server${NC}"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        echo ""
        return 1
    fi

    # Count lines
    local line_count=$(echo "$response" | wc -l | xargs)
    local record_count=$((line_count - 1)) # Subtract header

    if [[ $record_count -eq 0 ]]; then
        echo -e "  ${RED}❌ FAILED: No data returned (only header)${NC}"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        echo ""
        return 1
    fi

    # Get first and last data lines
    local first_data=$(echo "$response" | tail -n +2 | head -1)
    local last_data=$(echo "$response" | tail -1)

    # Extract dates from first and last records
    local first_date=$(echo "$first_data" | cut -d',' -f2 | cut -d'T' -f1)
    local last_date=$(echo "$last_data" | cut -d',' -f2 | cut -d'T' -f1)

    echo "  Records: $record_count"
    echo "  First Date: $first_date"
    echo "  Last Date: $last_date"

    # Validate end date is correct
    if [[ "$last_date" != "$expected_end_date" ]]; then
        echo -e "  ${RED}❌ FAILED: Last date is '$last_date', expected '$expected_end_date'${NC}"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        echo ""
        return 1
    fi

    # Validate start date if provided (account for trading days/weeks)
    if [[ -n "$expected_min_date" ]]; then
        # For daily data, check if first date is >= expected (trading days may skip)
        # For intraday data, check if first date is <= expected (going backwards in time)
        case "$interval" in
            "1D")
                # Daily trading days can be skipped (weekends, holidays)
                # Use a more flexible check for daily data
                local date_diff_days=$(python3 -c "
from datetime import datetime
d1 = datetime.strptime('$first_date', '%Y-%m-%d')
d2 = datetime.strptime('$expected_min_date', '%Y-%m-%d')
diff = (d2 - d1).days
if diff >= 0:
    print('PASS')
else:
    print('FAIL')
" 2>/dev/null || echo "FAIL")
                ;;
            *)
                # For intraday data, first date should be close to expected (allowing some slack)
                local date_diff_days=$(python3 -c "
from datetime import datetime
d1 = datetime.strptime('$first_date', '%Y-%m-%d')
d2 = datetime.strptime('$expected_min_date', '%Y-%m-%d')
diff = abs((d1 - d2).days)
if diff <= 7:  # Allow up to 7 days difference
    print('PASS')
else:
    print('FAIL')
" 2>/dev/null || echo "FAIL")
                ;;
        esac

        if [[ "$date_diff_days" != "PASS" ]]; then
            echo -e "  ${RED}❌ FAILED: First date '$first_date' is not within reasonable range of expected '$expected_min_date'${NC}"
            FAILED_TESTS=$((FAILED_TESTS + 1))
            echo ""
            return 1
        fi
    fi

    # Additional validation based on interval and symbol
    local validation_passed=true

    case "$interval" in
        "1D")
            # Daily should have OHLCV values and different dates
            local unique_dates=$(echo "$response" | tail -n +2 | cut -d',' -f2 | cut -d'T' -f1 | sort -u | wc -l)
            if [[ $unique_dates -lt 2 ]]; then
                echo -e "  ${RED}❌ FAILED: Daily data should have different dates (only $unique_dates unique for $record_count records)${NC}"
                validation_passed=false
            fi
            # Check OHLCV structure (columns 3-7 are OHLCV in enhanced format)
            local ohlcv_open=$(echo "$first_data" | cut -d',' -f3)
            local ohlcv_close=$(echo "$first_data" | cut -d',' -f6)
            local ohlcv_volume=$(echo "$first_data" | cut -d',' -f7)

            # Validate OHLCV values are reasonable (not zero for stocks/indices)
            # Note: Volume can be 0 for indices, but open/close should not be 0
            if [[ "$symbol" == "VNINDEX" ]]; then
                # For VNINDEX, check that close is reasonable (not zero)
                if [[ "$(echo "$ohlcv_close > 1000" | bc -l 2>/dev/null || echo "0")" == "0" ]]; then
                    echo -e "  ${RED}❌ FAILED: VNINDEX should have reasonable close value (got: $ohlcv_close)${NC}"
                    validation_passed=false
                fi
            else
                # For stocks like BTC, check both open and close are reasonable
                if [[ "$(echo "$ohlcv_open > 0" | bc -l 2>/dev/null || echo "0")" == "0" || "$(echo "$ohlcv_close > 0" | bc -l 2>/dev/null || echo "0")" == "0" ]]; then
                    echo -e "  ${RED}❌ FAILED: Stock should have proper OHLCV values (Open: $ohlcv_open, Close: $ohlcv_close)${NC}"
                    validation_passed=false
                fi
            fi
            ;;
        "1H")
            # Hourly should have HH:MM:SS times
            local has_hourly_time=$(echo "$first_data" | cut -d',' -f2 | grep -E "T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
            if [[ -z "$has_hourly_time" ]]; then
                echo -e "  ${RED}❌ FAILED: Hourly data should include full HH:MM:SS timestamp${NC}"
                validation_passed=false
            fi
            ;;
        "1m")
            # Minute should have HH:MM:SS times and unique timestamps (CRITICAL TEST)
            local has_minute_time=$(echo "$first_data" | cut -d',' -f2 | grep -E "T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
            if [[ -z "$has_minute_time" ]]; then
                echo -e "  ${RED}❌ FAILED: Minute data should include full HH:MM:SS timestamp${NC}"
                validation_passed=false
            fi

            # CRITICAL: Check for deduplication by verifying different timestamps
            if [[ "$symbol" == "VNINDEX" && "$limit" -gt 10 ]]; then
                # For VNINDEX with enough records, check for different timestamps
                local sample_timestamps=$(echo "$response" | tail -n +2 | head -10 | cut -d',' -f2)
                local unique_timestamps=$(echo "$sample_timestamps" | sort -u | wc -l)
                if [[ $unique_timestamps -lt 8 ]]; then
                    echo -e "  ${RED}❌ CRITICAL FAILED: VNINDEX 1m should have unique timestamps (deduplication bug!) Only $unique_timestamps unique for 10 records${NC}"
                    validation_passed=false
                else
                    echo -e "  ${GREEN}✅ PASS: VNINDEX 1m deduplication working correctly ($unique_timestamps unique timestamps for 10 records)${NC}"
                fi
            elif [[ "$symbol" == "BTC" && "$limit" -gt 5 ]]; then
                # For BTC with enough records, check for different timestamps
                local sample_timestamps=$(echo "$response" | tail -n +2 | head -8 | cut -d',' -f2)
                local unique_timestamps=$(echo "$sample_timestamps" | sort -u | wc -l)
                if [[ $unique_timestamps -lt 6 ]]; then
                    echo -e "  ${RED}❌ CRITICAL FAILED: BTC 1m should have unique timestamps (deduplication bug!) Only $unique_timestamps unique for 8 records${NC}"
                    validation_passed=false
                else
                    echo -e "  ${GREEN}✅ PASS: BTC 1m deduplication working correctly ($unique_timestamps unique timestamps for 8 records)${NC}"
                fi
            fi
            ;;
        "15m")
            # 15-minute should have HH:MM:SS times with 15-minute intervals
            local has_minute_time=$(echo "$first_data" | cut -d',' -f2 | grep -E "T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
            if [[ -z "$has_minute_time" ]]; then
                echo -e "  ${RED}❌ FAILED: 15-minute data should include full HH:MM:SS timestamp${NC}"
                validation_passed=false
            fi

            # Check for proper 15-minute intervals (14:45, 15:00, 15:15, etc.)
            local minute_part=$(echo "$first_data" | cut -d',' -f2 | cut -d':' -f2)
            if [[ $((minute_part % 15)) -ne 0 ]]; then
                echo -e "  ${RED}❌ FAILED: 15-minute data should be at multiples of 15 minutes (found ${minute_part})${NC}"
                validation_passed=false
            fi
            ;;
        "1W")
            # Weekly should be on Monday or have weekly granularity
            local unique_dates=$(echo "$response" | tail -n +2 | cut -d',' -f2 | cut -d'T' -f1 | sort -u | wc -l)
            if [[ $unique_dates -lt 2 ]]; then
                echo -e "  ${RED}❌ FAILED: Weekly data should have different dates (only $unique_dates unique for $record_count records)${NC}"
                validation_passed=false
            fi
            ;;
    esac

    if [[ "$validation_passed" == "true" ]]; then
        echo -e "  ${GREEN}✅ PASSED: Data structure and values correct${NC}"
        PASSED_TESTS=$((PASSED_TESTS + 1))
    else
        FAILED_TESTS=$((FAILED_TESTS + 1))
    fi

    echo ""
    return 0
}

echo "=== STOCK DATA TESTS (VNINDEX) ==="
echo ""

# Test VNINDEX Stock Data with ground truth
test_interval_with_ground_truth "VNINDEX" "vn" "1D" "30" "2025-10-20" "2025-09-09" "VNINDEX Daily (1D) - 30 trading days ending 2025-10-20"
test_interval_with_ground_truth "VNINDEX" "vn" "1H" "24" "2025-10-20" "2025-10-13" "VNINDEX Hourly (1H) - 24 hours ending 2025-10-20"
test_interval_with_ground_truth "VNINDEX" "vn" "1m" "60" "2025-10-20" "2025-10-20" "VNINDEX Minute (1m) - 60 minutes on 2025-10-20 (CRITICAL DEDUPLICATION TEST)"
test_interval_with_ground_truth "VNINDEX" "vn" "15m" "48" "2025-10-20" "2025-10-16" "VNINDEX 15-minute (15m) - 48 periods ending 2025-10-20"
test_interval_with_ground_truth "VNINDEX" "vn" "1W" "12" "2025-10-20" "2025-07-28" "VNINDEX Weekly (1W) - 12 weeks ending 2025-10-20"

echo "=== CRYPTO DATA TESTS (BTC) ==="
echo ""

# Test BTC Crypto Data with ground truth
test_interval_with_ground_truth "BTC" "crypto" "1D" "30" "2025-10-20" "2025-09-21" "BTC Crypto Daily (1D) - 30 days ending 2025-10-20"
test_interval_with_ground_truth "BTC" "crypto" "1H" "24" "2025-10-20" "2025-10-20" "BTC Crypto Hourly (1H) - 24 hours on 2025-10-20"
test_interval_with_ground_truth "BTC" "crypto" "1m" "60" "2025-11-11" "2025-11-11" "BTC Crypto Minute (1m) - 60 minutes on 2025-11-11 (CRITICAL DEDUPLICATION TEST)"
test_interval_with_ground_truth "BTC" "crypto" "15m" "48" "2025-12-09" "2025-12-09" "BTC Crypto 15-minute (15m) - 48 periods on 2025-12-09"
test_interval_with_ground_truth "BTC" "crypto" "1W" "12" "2025-10-20" "2025-07-28" "BTC Crypto Weekly (1W) - 12 weeks ending 2025-10-20"

echo "=== SUMMARY ==="
echo -e "Total Tests: $TOTAL_TESTS"
echo -e "${GREEN}Passed: $PASSED_TESTS${NC}"
echo -e "${RED}Failed: $FAILED_TESTS${NC}"

if [[ $FAILED_TESTS -eq 0 ]]; then
    echo -e "${GREEN}🎉 ALL TESTS PASSED! Data correctness and deduplication verified.${NC}"
    echo -e "${GREEN}✅ Interval-aware deduplication system working perfectly!${NC}"
    exit 0
else
    echo -e "${RED}❌ $FAILED_TESTS test(s) failed. Data correctness issues detected.${NC}"
    exit 1
fi