#!/bin/bash

# Analyze timestamp patterns to confirm deduplication bug
# Focus on checking if records are at the right granularity

API_URL="http://localhost:3000"

echo "=== Timestamp Pattern Analysis ==="
echo ""

check_interval() {
    local ticker="$1"
    local interval="$2"
    local mode="$3"

    echo "=== $ticker $interval ($mode mode) ==="

    local response=$(curl -s "$API_URL/tickers?symbol=$ticker&interval=$interval&mode=$mode&format=csv" 2>/dev/null || echo "")
    if [[ -z "$response" || "$response" == *"symbol,time"* ]]; then
        echo "No data or error"
        echo ""
        return
    fi

    local record_count=$(($(echo "$response" | wc -l) - 1))
    echo "Records: $record_count"

    if [[ $record_count -eq 0 ]]; then
        echo "No data available"
        echo ""
        return
    fi

    # Show first 5 and last 5 timestamps
    echo "First 5 timestamps:"
    echo "$response" | tail -n +2 | head -5 | cut -d',' -f2

    echo "Last 5 timestamps:"
    echo "$response" | tail -6 | head -5 | cut -d',' -f2

    # Check date_naive distribution
    echo "Date distribution:"
    echo "$response" | tail -n +2 | cut -d',' -f2 | cut -d'T' -f1 | sort | uniq -c | sort -nr | head -10

    echo ""
}

# Check VCB stocks
check_interval "VCB" "1m" "vn"
check_interval "VCB" "15m" "vn"
check_interval "VCB" "1D" "vn"

# Check BTC crypto
check_interval "BTC" "1m" "crypto"
check_interval "BTC" "1D" "crypto"

echo "=== Analysis ==="
echo ""
echo "Looking for patterns:"
echo "1. 1m should show many different timestamps throughout the day"
echo "2. 15m should show timestamps at 15-minute intervals"
echo "3. 1D should show one timestamp per trading day"
echo ""
echo "Bug indicator:"
echo "- 1m showing only few timestamps with same date = deduplication bug"
echo "- 15m showing daily timestamps = aggregation bug"
echo "- 1D working correctly = deduplication is OK for daily"