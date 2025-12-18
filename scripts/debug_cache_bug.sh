#!/bin/bash

# Debug script to investigate cache duplication bug
# This script will test various scenarios to identify where duplicates are coming from

API_URL="${1:-https://api.aipriceaction.com}"
OUTPUT_DIR="/tmp/cache_debug_$(date +%s)"

echo "=== Cache Bug Debugging Script ==="
echo "API URL: $API_URL"
echo "Output Dir: $OUTPUT_DIR"
echo ""

mkdir -p "$OUTPUT_DIR"

# Function to make a request and save output
test_request() {
    local name="$1"
    local url="$2"
    local output_file="$OUTPUT_DIR/${name}.csv"

    echo "Testing: $name"
    echo "URL: $url"

    # Make request and get response
    local response=$(curl -s -w "HTTP_CODE:%{http_code};TIME:%{time_total};SIZE:%{size_download}" "$url")

    # Extract metrics
    local http_code=$(echo "$response" | grep -o "HTTP_CODE:[0-9]*" | cut -d: -f2)
    local time=$(echo "$response" | grep -o "TIME:[0-9.]*" | cut -d: -f2)
    local size=$(echo "$response" | grep -o "SIZE:[0-9]*" | cut -d: -f2)

    # Extract CSV content
    local csv_content=$(echo "$response" | sed 's/HTTP_CODE:.*//')

    # Save to file
    echo "$csv_content" > "$output_file"

    # Count rows (excluding header)
    local rows=$(tail -n +2 "$output_file" | wc -l)
    local total_rows=$(wc -l < "$output_file")

    # Check for duplicates
    local duplicates=0
    if [ "$rows" -gt 0 ]; then
        # Remove header, sort, and count duplicates
        duplicates=$(tail -n +2 "$output_file" | sort | uniq -d | wc -l)
    fi

    echo "  HTTP: $http_code | Time: ${time}s | Size: ${size} bytes | Rows: $rows/$total_rows | Duplicates: $duplicates"
    echo ""

    # Return metrics for later analysis
    echo "$name,$http_code,$time,$size,$rows,$total_rows,$duplicates" >> "$OUTPUT_DIR/metrics.csv"
}

# Initialize metrics file
echo "Test,HTTP,Time,Size,Rows,TotalRows,Duplicates" > "$OUTPUT_DIR/metrics.csv"

echo "=== Test Cases ==="

# Test 1: Cache=true (default) - CSV format
test_request "cache_true_csv" \
    "$API_URL/tickers?symbol=BTC&interval=1D&mode=crypto&format=csv&limit=30"

# Test 2: Cache=false - CSV format
test_request "cache_false_csv" \
    "$API_URL/tickers?symbol=BTC&interval=1D&mode=crypto&format=csv&limit=30&cache=false"

# Test 3: Cache=true - JSON format
test_request "cache_true_json" \
    "$API_URL/tickers?symbol=BTC&interval=1D&mode=crypto&limit=30"

# Test 4: Cache=false - JSON format
test_request "cache_false_json" \
    "$API_URL/tickers?symbol=BTC&interval=1D&mode=crypto&limit=30&cache=false"

# Test 5: Multiple requests with cache=true (to test cache consistency)
echo "=== Testing Cache Consistency (3 consecutive requests) ==="
for i in {1..3}; do
    test_request "cache_consistent_$i" \
        "$API_URL/tickers?symbol=BTC&interval=1D&mode=crypto&format=csv&limit=30"
done

# Test 6: Different symbols to see if it affects all crypto data
test_request "cache_true_ETH" \
    "$API_URL/tickers?symbol=ETH&interval=1D&mode=crypto&format=csv&limit=30"

test_request "cache_false_ETH" \
    "$API_URL/tickers?symbol=ETH&interval=1D&mode=crypto&format=csv&limit=30&cache=false"

# Test 7: Different intervals
test_request "cache_true_1h" \
    "$API_URL/tickers?symbol=BTC&interval=1H&mode=crypto&format=csv&limit=30"

test_request "cache_false_1h" \
    "$API_URL/tickers?symbol=BTC&interval=1H&mode=crypto&format=csv&limit=30&cache=false"

echo "=== Analysis ==="
echo "Metrics saved to: $OUTPUT_DIR/metrics.csv"
echo ""

# Analyze results
echo "=== Duplicate Analysis ==="
while IFS=',' read -r test http time size rows total_rows duplicates; do
    if [ "$test" != "Test" ] && [ "$duplicates" -gt 0 ]; then
        echo "DUPLICATES FOUND in $test: $duplicates duplicate rows"
        echo "  File: $OUTPUT_DIR/${test}.csv"
        echo "  First 5 duplicate lines:"
        tail -n +2 "$OUTPUT_DIR/${test}.csv" | sort | uniq -d | head -5
        echo ""
    fi
done < "$OUTPUT_DIR/metrics.csv"

echo "=== Cache Performance Comparison ==="
echo "Cache=true vs Cache=false performance (CSV format):"
cache_true_time=$(grep "cache_true_csv" "$OUTPUT_DIR/metrics.csv" | cut -d, -f3)
cache_false_time=$(grep "cache_false_csv" "$OUTPUT_DIR/metrics.csv" | cut -d, -f3)
cache_true_rows=$(grep "cache_true_csv" "$OUTPUT_DIR/metrics.csv" | cut -d, -f5)
cache_false_rows=$(grep "cache_false_csv" "$OUTPUT_DIR/metrics.csv" | cut -d, -f5)

printf "%-15s | %-10s | %-10s\n" "Test" "Time (s)" "Rows"
printf "%-15s | %-10s | %-10s\n" "Cache=true" "$cache_true_time" "$cache_true_rows"
printf "%-15s | %-10s | %-10s\n" "Cache=false" "$cache_false_time" "$cache_false_rows"

echo ""
echo "Files saved in: $OUTPUT_DIR"
echo "Review CSV files to see duplicate patterns"
echo ""

# Check if the issue is specifically with CSV format or cache in general
echo "=== Root Cause Analysis ==="
cache_csv_rows=$(grep "cache_true_csv" "$OUTPUT_DIR/metrics.csv" | cut -d, -f5)
no_cache_csv_rows=$(grep "cache_false_csv" "$OUTPUT_DIR/metrics.csv" | cut -d, -f5)
cache_json_rows=$(grep "cache_true_json" "$OUTPUT_DIR/metrics.csv" | cut -d, -f5)

if [ "$cache_csv_rows" -gt "$no_cache_csv_rows" ]; then
    echo "🔍 ISSUE: Cache=true returns more rows than cache=false for CSV"
    echo "   This suggests a cache duplication issue specific to CSV format"
elif [ "$cache_csv_rows" -eq "$no_cache_csv_rows" ]; then
    echo "✅ No row count difference between cache=true and cache=false for CSV"
    echo "   Issue might be in data quality, not cache"
fi

if [ "$cache_json_rows" -gt 0 ]; then
    echo "✅ JSON format cache works correctly"
else
    echo "❌ JSON format also has issues"
fi

echo ""
echo "=== Recommendations ==="
echo "1. Check disk cache implementation for CSV serialization"
echo "2. Verify cache key generation includes format parameter"
echo "3. Review if cache entries are being properly invalidated"
echo "4. Check for concurrent cache update race conditions"
echo "5. Consider clearing disk cache on production server"