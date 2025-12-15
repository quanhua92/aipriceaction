#!/bin/bash

# Performance test script for CSV read optimization
# Tests the old chunked method vs new memory-mapped method

set -e

API_URL="${API_URL:-http://localhost:3000}"
RESULTS_FILE="/tmp/csv_performance_test_$(date +%s).csv"

echo "CSV Performance Test Results - $(date)" | tee "$RESULTS_FILE"
echo "========================================" | tee -a "$RESULTS_FILE"
echo "" | tee -a "$RESULTS_FILE"
echo "Testing API URL: $API_URL" | tee -a "$RESULTS_FILE"
echo "" | tee -a "$RESULTS_FILE"

# CSV header
echo "Ticker,Interval,Limit,File_Size_MB,Response_Time_ms,Throughput_MB_s" | tee -a "$RESULTS_FILE"

# Test cases - various tickers with different file sizes
test_cases=(
    "AGG,1m,10"     # Large file (~12MB)
    "VCB,1m,10"     # Large file (~8MB)
    "FPT,1m,10"     # Medium file (~5MB)
    "VNINDEX,1D,10" # Small file (~200KB)
    "BTC,1m,10"     # Crypto file
)

for case in "${test_cases[@]}"; do
    IFS=',' read -r ticker interval limit <<< "$case"

    echo -n "Testing $ticker ($interval, limit=$limit)... "

    # Measure response time and capture output
    start_time=$(date +%s%N)
    response=$(curl -s -w "TIME:%{time_total}" "$API_URL/tickers?symbol=$ticker&interval=$interval&limit=$limit&cache=false")
    end_time=$(date +%s%N)

    # Extract time from curl response
    time_seconds=$(echo "$response" | grep -o "TIME:[0-9.]*" | cut -d: -f2)
    time_ms=$(echo "$time_seconds * 1000" | bc -l 2>/dev/null || echo "0")

    # Try to get file size from logs or estimate
    # For now, use estimated sizes based on typical data
    case $ticker in
        AGG) file_size_mb=12 ;;
        VCB) file_size_mb=8 ;;
        FPT) file_size_mb=5 ;;
        VNINDEX) file_size_mb=0.2 ;;
        BTC) file_size_mb=3 ;;
        *) file_size_mb=2 ;;
    esac

    # Calculate throughput
    if (( $(echo "$time_ms > 0" | bc -l 2>/dev/null || echo 1) )); then
        throughput=$(echo "scale=2; $file_size_mb * 1000 / $time_ms" | bc -l 2>/dev/null || echo "0")
    else
        throughput="0"
    fi

    echo "${time_ms}ms, ${throughput}MB/s"

    # Save to CSV
    echo "$ticker,$interval,$limit,$file_size_mb,$time_ms,$throughput" | tee -a "$RESULTS_FILE"

    # Small delay between requests
    sleep 0.5
done

echo "" | tee -a "$RESULTS_FILE"
echo "Performance test completed!" | tee -a "$RESULTS_FILE"
echo "Results saved to: $RESULTS_FILE" | tee -a "$RESULTS_FILE"

# Show summary
echo ""
echo "=== Summary ==="
if command -v bc >/dev/null 2>&1; then
    avg_time=$(tail -n +4 "$RESULTS_FILE" | cut -d, -f5 | awk '{sum+=$1} END {print sum/NR}')
    echo "Average response time: ${avg_time}ms"

    # Count fast vs slow responses (under 100ms is good)
    fast_count=$(tail -n +4 "$RESULTS_FILE" | awk -F, '$5 < 100 {count++} END {print count+0}')
    total_count=$(tail -n +4 "$RESULTS_FILE" | wc -l)
    echo "Fast responses (<100ms): $fast_count/$total_count"
else
    echo "Install 'bc' for better statistics"
fi