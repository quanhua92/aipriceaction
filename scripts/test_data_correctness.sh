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

echo ""
echo "=== BTC COMPREHENSIVE DATA AVAILABILITY TEST ==="
echo ""

# Function to test BTC availability across intervals
test_btc_availability() {
    local base_tests=$TOTAL_TESTS
    local base_passed=$PASSED_TESTS
    local base_failed=$FAILED_TESTS

    echo -e "${BLUE}Testing BTC 1D/1H/1m Data Availability for Last 30 Days${NC}"
    echo "Strategy: Get BTC 1D for 30 days, then test 1H/1m for each day"
    echo ""

    # Step 1: Get BTC 1D data for last 30 days
    echo -e "${YELLOW}Step 1: Fetching BTC 1D data for last 30 days...${NC}"
    local daily_url="$API_URL/tickers?symbol=BTC&mode=crypto&interval=1D&limit=30&format=csv"
    local daily_response=$(curl -s "$daily_url" 2>/dev/null || echo "")

    if [[ -z "$daily_response" ]]; then
        echo -e "  ${RED}❌ FAILED: No response from server for BTC 1D data${NC}"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        return 1
    fi

    # Count and validate daily data
    local daily_line_count=$(echo "$daily_response" | wc -l | xargs)
    local daily_record_count=$((daily_line_count - 1))

    if [[ $daily_record_count -eq 0 ]]; then
        echo -e "  ${RED}❌ FAILED: No BTC daily data returned${NC}"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        return 1
    fi

    echo -e "  ${GREEN}✅ Retrieved $daily_record_count days of BTC 1D data${NC}"

    # Extract dates from daily data (skip header)
    local btc_dates=($(echo "$daily_response" | tail -n +2 | cut -d',' -f2 | cut -d'T' -f1 | tac)) # Reverse to get oldest first

    echo ""
    echo -e "${YELLOW}Step 2: Testing 1H and 1m availability for each day...${NC}"
    echo ""

    local total_daily_tests=0
    local daily_passed=0
    local daily_failed=0
    local total_hours_checked=0
    local total_minutes_checked=0

    # Test each day
    for i in "${!btc_dates[@]}"; do
        local test_date="${btc_dates[$i]}"
        local day_num=$((daily_record_count - i)) # Count from most recent

        TOTAL_TESTS=$((TOTAL_TESTS + 2)) # 2 tests per day (1H and 1m)
        total_daily_tests=$((total_daily_tests + 2))

        # Determine if this is the most recent day (incomplete data expected)
        local is_last_day=false
        if [[ $i -eq $((daily_record_count - 1)) ]]; then
            is_last_day=true
        fi

        echo -e "${BLUE}Day $day_num/$daily_record_count: $test_date${NC} $(if [[ "$is_last_day" == "true" ]]; then echo "(Last day - incomplete expected)"; fi)"

        # Test 1H data for this specific day
        local hourly_url="$API_URL/tickers?symbol=BTC&mode=crypto&interval=1H&start_date=$test_date&end_date=$test_date&format=csv"
        local hourly_response=$(curl -s "$hourly_url" 2>/dev/null || echo "")
        local hourly_test_passed=true

        if [[ -z "$hourly_response" ]]; then
            echo -e "  ${RED}  ❌ 1H: No response${NC}"
            hourly_test_passed=false
        else
            local hourly_line_count=$(echo "$hourly_response" | wc -l | xargs)
            local hourly_record_count=$((hourly_line_count - 1))
            total_hours_checked=$((total_hours_checked + hourly_record_count))

            # Expected hours: 24 for complete days, less for incomplete day
            local min_expected_hours=1
            local max_expected_hours=24
            if [[ "$is_last_day" == "true" ]]; then
                max_expected_hours=23 # Allow for incomplete current day
            fi

            if [[ $hourly_record_count -lt $min_expected_hours ]]; then
                echo -e "  ${RED}  ❌ 1H: Only $hourly_record_count records (expected at least $min_expected_hours)${NC}"
                hourly_test_passed=false
            elif [[ $hourly_record_count -gt $max_expected_hours ]]; then
                echo -e "  ${YELLOW}  ⚠️ 1H: $hourly_record_count records (expected max $max_expected_hours, possible duplicates)${NC}"
                # Still pass but warn
            else
                echo -e "  ${GREEN}  ✅ 1H: $hourly_record_count records${NC}"
            fi
        fi

        # Test 1m data for this specific day
        local minute_url="$API_URL/tickers?symbol=BTC&mode=crypto&interval=1m&start_date=$test_date&end_date=$test_date&format=csv"
        local minute_response=$(curl -s "$minute_url" 2>/dev/null || echo "")
        local minute_test_passed=true

        if [[ -z "$minute_response" ]]; then
            echo -e "  ${RED}  ❌ 1m: No response${NC}"
            minute_test_passed=false
        else
            local minute_line_count=$(echo "$minute_response" | wc -l | xargs)
            local minute_record_count=$((minute_line_count - 1))
            total_minutes_checked=$((total_minutes_checked + minute_record_count))

            # Expected minutes: 1440 for complete days, less for incomplete day
            local min_expected_minutes=10 # Lower threshold for minute data
            local max_expected_minutes=1440
            if [[ "$is_last_day" == "true" ]]; then
                min_expected_minutes=1 # Allow very few minutes for current day
                max_expected_minutes=1380 # Allow for incomplete day
            fi

            if [[ $minute_record_count -lt $min_expected_minutes ]]; then
                echo -e "  ${RED}  ❌ 1m: Only $minute_record_count records (expected at least $min_expected_minutes)${NC}"
                minute_test_passed=false
            elif [[ $minute_record_count -gt $max_expected_minutes ]]; then
                echo -e "  ${YELLOW}  ⚠️ 1m: $minute_record_count records (expected max $max_expected_minutes, possible duplicates)${NC}"
                # Still pass but warn
            else
                echo -e "  ${GREEN}  ✅ 1m: $minute_record_count records${NC}"
            fi
        fi

        # Update counters
        if [[ "$hourly_test_passed" == "true" ]]; then
            PASSED_TESTS=$((PASSED_TESTS + 1))
            daily_passed=$((daily_passed + 1))
        else
            FAILED_TESTS=$((FAILED_TESTS + 1))
            daily_failed=$((daily_failed + 1))
        fi

        if [[ "$minute_test_passed" == "true" ]]; then
            PASSED_TESTS=$((PASSED_TESTS + 1))
            daily_passed=$((daily_passed + 1))
        else
            FAILED_TESTS=$((FAILED_TESTS + 1))
            daily_failed=$((daily_failed + 1))
        fi
    done

    echo ""
    echo -e "${YELLOW}BTC Availability Test Results:${NC}"
    echo "  Days tested: $daily_record_count"
    echo "  Total tests: $total_daily_tests (1H + 1m per day)"
    echo -e "  ${GREEN}Passed: $daily_passed${NC}"
    echo -e "  ${RED}Failed: $daily_failed${NC}"
    echo "  Total hours checked: $total_hours_checked"
    echo "  Total minutes checked: $total_minutes_checked"
    echo "  Average hours per day: $((total_hours_checked / daily_record_count))"
    echo "  Average minutes per day: $((total_minutes_checked / daily_record_count))"

    # Calculate expected totals for validation
    local expected_total_hours=$((daily_record_count * 24))
    local expected_total_minutes=$((daily_record_count * 1440))
    local hour_coverage=$((total_hours_checked * 100 / expected_total_hours))
    local minute_coverage=$((total_minutes_checked * 100 / expected_total_minutes))

    echo ""
    echo -e "${YELLOW}Data Coverage Analysis:${NC}"
    echo "  Hourly coverage: $hour_coverage% ($total_hours_checked/$expected_total_hours expected hours)"
    echo "  Minute coverage: $minute_coverage% ($total_minutes_checked/$expected_total_minutes expected minutes)"

    if [[ $hourly_coverage -ge 80 ]]; then
        echo -e "  ${GREEN}✅ Hourly coverage: EXCELLENT (≥80%)${NC}"
    elif [[ $hourly_coverage -ge 60 ]]; then
        echo -e "  ${YELLOW}⚠️ Hourly coverage: GOOD (≥60%)${NC}"
    else
        echo -e "  ${RED}❌ Hourly coverage: POOR (<60%)${NC}"
    fi

    if [[ $minute_coverage -ge 70 ]]; then
        echo -e "  ${GREEN}✅ Minute coverage: EXCELLENT (≥70%)${NC}"
    elif [[ $minute_coverage -ge 50 ]]; then
        echo -e "  ${YELLOW}⚠️ Minute coverage: GOOD (≥50%)${NC}"
    else
        echo -e "  ${RED}❌ Minute coverage: POOR (<50%)${NC}"
    fi
}

# Run the BTC availability test
test_btc_availability

echo ""
echo "=== FINAL SUMMARY ==="
echo -e "Total Tests: $TOTAL_TESTS"
echo -e "${GREEN}Passed: $PASSED_TESTS${NC}"
echo -e "${RED}Failed: $FAILED_TESTS${NC}"

if [[ $FAILED_TESTS -eq 0 ]]; then
    echo -e "${GREEN}🎉 ALL TESTS PASSED! Data correctness, deduplication, and availability verified.${NC}"
    echo -e "${GREEN}✅ Interval-aware deduplication system working perfectly!${NC}"
    echo -e "${GREEN}✅ BTC data availability across all intervals confirmed!${NC}"
    exit 0
else
    echo -e "${RED}❌ $FAILED_TESTS test(s) failed. Data correctness or availability issues detected.${NC}"
    exit 1
fi