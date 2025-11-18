#!/bin/bash

# Debug performance for CSV endpoint against any server
# Usage: ./scripts/debug_perf_csv.sh [url]
# Default: http://localhost:3000

BASE_URL=${1:-http://localhost:3000}

echo "=== CSV Performance Test ==="
echo "Server: $BASE_URL"
echo ""

# Test if server is running
if ! curl -s "$BASE_URL/health" > /dev/null 2>&1; then
  echo "❌ Server not responding at $BASE_URL"
  exit 1
fi

echo "✅ Server is responding"
echo ""

# Test 1: All tickers with limit=100 (the fix target)
echo "=== Test 1: All tickers, 1D, limit=100 (baseline - should use cache) ==="
TOTAL_TIME=0
for i in 1 2 3 4 5; do
  TIME=$(curl -s -o /tmp/perf_test_1_$i.csv -w "%{time_total}" "$BASE_URL/tickers?interval=1D&format=csv&limit=100")
  echo "Request $i: ${TIME}s"
  TOTAL_TIME=$(echo "$TOTAL_TIME + $TIME" | bc)
  sleep 0.2
done
AVG_TIME=$(echo "scale=3; $TOTAL_TIME / 5" | bc)
echo "Average: ${AVG_TIME}s"
echo ""

# Test 2: Single ticker (should be fast)
echo "=== Test 2: Single ticker (VCB), 1D, limit=100 ==="
TOTAL_TIME=0
for i in 1 2 3 4 5; do
  TIME=$(curl -s -o /tmp/perf_test_2_$i.csv -w "%{time_total}" "$BASE_URL/tickers?symbol=VCB&interval=1D&format=csv&limit=100")
  echo "Request $i: ${TIME}s"
  TOTAL_TIME=$(echo "$TOTAL_TIME + $TIME" | bc)
  sleep 0.2
done
AVG_TIME=$(echo "scale=3; $TOTAL_TIME / 5" | bc)
echo "Average: ${AVG_TIME}s"
echo ""

# Test 3: All tickers with limit=500 (larger limit)
echo "=== Test 3: All tickers, 1D, limit=500 ==="
TOTAL_TIME=0
for i in 1 2 3 4 5; do
  TIME=$(curl -s -o /tmp/perf_test_3_$i.csv -w "%{time_total}" "$BASE_URL/tickers?interval=1D&format=csv&limit=500")
  echo "Request $i: ${TIME}s"
  TOTAL_TIME=$(echo "$TOTAL_TIME + $TIME" | bc)
  sleep 0.2
done
AVG_TIME=$(echo "scale=3; $TOTAL_TIME / 5" | bc)
echo "Average: ${AVG_TIME}s"
echo ""

# Test 4: Representative tickers used in cache check (VNINDEX, VCB, VIC)
echo "=== Test 4: Representative tickers (VNINDEX, VCB, VIC) - Cache Check Validation ==="
TOTAL_TIME=0
for i in 1 2 3 4 5; do
  TIME=$(curl -s -o /tmp/perf_test_4_$i.csv -w "%{time_total}" "$BASE_URL/tickers?symbol=VNINDEX&symbol=VCB&symbol=VIC&interval=1D&format=csv&limit=100")
  echo "Request $i: ${TIME}s"
  TOTAL_TIME=$(echo "$TOTAL_TIME + $TIME" | bc)
  sleep 0.2
done
AVG_TIME=$(echo "scale=3; $TOTAL_TIME / 5" | bc)
echo "Average: ${AVG_TIME}s"
echo ""

# Test 5: All tickers with limit=50 (smaller limit)
echo "=== Test 5: All tickers, 1D, limit=50 (smaller limit) ==="
TOTAL_TIME=0
for i in 1 2 3 4 5; do
  TIME=$(curl -s -o /tmp/perf_test_5_$i.csv -w "%{time_total}" "$BASE_URL/tickers?interval=1D&format=csv&limit=50")
  echo "Request $i: ${TIME}s"
  TOTAL_TIME=$(echo "$TOTAL_TIME + $TIME" | bc)
  sleep 0.2
done
AVG_TIME=$(echo "scale=3; $TOTAL_TIME / 5" | bc)
echo "Average: ${AVG_TIME}s"
echo ""

# Test 6: All tickers with date range (should use cache binary search)
echo "=== Test 6: All tickers, 1D, date range 2024-01-01 to 2024-12-31 ==="
TOTAL_TIME=0
for i in 1 2 3 4 5; do
  TIME=$(curl -s -o /tmp/perf_test_6_$i.csv -w "%{time_total}" "$BASE_URL/tickers?interval=1D&format=csv&start_date=2024-01-01&end_date=2024-12-31")
  echo "Request $i: ${TIME}s"
  TOTAL_TIME=$(echo "$TOTAL_TIME + $TIME" | bc)
  sleep 0.2
done
AVG_TIME=$(echo "scale=3; $TOTAL_TIME / 5" | bc)
echo "Average: ${AVG_TIME}s"
echo ""

# Test 7: All tickers no limit (should use full cache)
echo "=== Test 7: All tickers, 1D, no limit (full cache) ==="
TOTAL_TIME=0
for i in 1 2 3; do
  TIME=$(curl -s -o /tmp/perf_test_7_$i.csv -w "%{time_total}" "$BASE_URL/tickers?interval=1D&format=csv")
  echo "Request $i: ${TIME}s"
  TOTAL_TIME=$(echo "$TOTAL_TIME + $TIME" | bc)
  sleep 0.2
done
AVG_TIME=$(echo "scale=3; $TOTAL_TIME / 3" | bc)
echo "Average: ${AVG_TIME}s"
echo ""

echo "=== File Size Summary ==="
echo "Test 1 (limit=100):"
ls -lh /tmp/perf_test_1_1.csv | awk '{print $5}'
wc -l /tmp/perf_test_1_1.csv | awk '{print $1 " lines"}'
echo ""
echo "Test 7 (no limit):"
ls -lh /tmp/perf_test_7_1.csv | awk '{print $5}'
wc -l /tmp/perf_test_7_1.csv | awk '{print $1 " lines"}'
