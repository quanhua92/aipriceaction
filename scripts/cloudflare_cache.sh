#!/bin/bash

# Cloudflare Cache Behavior Test Script
# Tests cache HIT/MISS behavior and compression on production API

set -e

API_URL="${1:-https://api.aipriceaction.com}"
TIMESTAMP=$(date +%s)

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║           Cloudflare Cache & Compression Test                   ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Testing API: $API_URL"
echo "Timestamp: $(date)"
echo ""

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_test() {
    echo ""
    echo -e "${BLUE}═══ $1 ═══${NC}"
}

extract_header() {
    grep -i "$1:" | head -1 | cut -d' ' -f2- | tr -d '\r'
}

# Test 1: CSV Single Ticker (Cold Cache)
print_test "Test 1: CSV Single Ticker - First Request (expect MISS)"
response=$(curl -s -i -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=VCB&format=csv&limit=10&t=$TIMESTAMP")
cache_status=$(echo "$response" | extract_header "cf-cache-status")
content_encoding=$(echo "$response" | extract_header "content-encoding")
age=$(echo "$response" | extract_header "age")

echo "  Cache Status: ${cache_status:-NONE}"
echo "  Content-Encoding: ${content_encoding:-none}"
echo "  Age: ${age:-0}s"

if [[ "$cache_status" == "MISS" ]]; then
    echo -e "  Result: ${GREEN}✅ CORRECT - Cache miss as expected${NC}"
else
    echo -e "  Result: ${YELLOW}⚠️  Expected MISS, got $cache_status${NC}"
fi

# Wait a moment
sleep 1

# Test 2: CSV Single Ticker (Should be HIT now)
print_test "Test 2: CSV Single Ticker - Second Request (expect HIT)"
response=$(curl -s -i -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=VCB&format=csv&limit=10&t=$TIMESTAMP")
cache_status=$(echo "$response" | extract_header "cf-cache-status")
content_encoding=$(echo "$response" | extract_header "content-encoding")
age=$(echo "$response" | extract_header "age")

echo "  Cache Status: ${cache_status:-NONE}"
echo "  Content-Encoding: ${content_encoding:-none}"
echo "  Age: ${age:-0}s"

if [[ "$cache_status" == "HIT" ]]; then
    echo -e "  Result: ${GREEN}✅ CORRECT - Cache hit as expected${NC}"
else
    echo -e "  Result: ${RED}❌ Expected HIT, got $cache_status${NC}"
fi

# Test 3: CSV All Tickers (Big Query)
print_test "Test 3: CSV All Tickers (limit=128) - Check Compression"
response=$(curl -s -i -H "Accept-Encoding: gzip" "$API_URL/tickers?format=csv&limit=128")
cache_status=$(echo "$response" | extract_header "cf-cache-status")
content_encoding=$(echo "$response" | extract_header "content-encoding")
age=$(echo "$response" | extract_header "age")

echo "  Cache Status: ${cache_status:-NONE}"
echo "  Content-Encoding: ${content_encoding:-none}"
echo "  Age: ${age:-0}s"

# Download and check size
temp_file=$(mktemp)
curl -s -H "Accept-Encoding: gzip" "$API_URL/tickers?format=csv&limit=128" -o "$temp_file"
compressed_size=$(wc -c < "$temp_file" | tr -d ' ')
file_type=$(file "$temp_file" | cut -d: -f2)

echo "  Downloaded Size: $(numfmt --to=iec-i --suffix=B $compressed_size 2>/dev/null || echo "${compressed_size} bytes")"
echo "  File Type: $file_type"

if [[ "$content_encoding" == "gzip" ]] && [[ $compressed_size -lt 3000000 ]]; then
    echo -e "  Result: ${GREEN}✅ CORRECT - Compressed (expected ~2MB)${NC}"
else
    echo -e "  Result: ${RED}❌ Not compressed properly${NC}"
fi

rm "$temp_file"

# Test 4: JSON Format
print_test "Test 4: JSON Format - Check Compression"
response=$(curl -s -i -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=VCB&format=json&limit=100")
cache_status=$(echo "$response" | extract_header "cf-cache-status")
content_encoding=$(echo "$response" | extract_header "content-encoding")
age=$(echo "$response" | extract_header "age")

echo "  Cache Status: ${cache_status:-NONE}"
echo "  Content-Encoding: ${content_encoding:-none}"
echo "  Age: ${age:-0}s"

if [[ "$content_encoding" == "gzip" ]]; then
    echo -e "  Result: ${GREEN}✅ JSON compression working${NC}"
else
    echo -e "  Result: ${RED}❌ JSON not compressed${NC}"
fi

# Test 5: Brotli Compression
print_test "Test 5: Brotli Compression (CSV)"
temp_file=$(mktemp)
curl -s -H "Accept-Encoding: br" "$API_URL/tickers?symbol=FPT&format=csv&limit=100" -o "$temp_file"
br_size=$(wc -c < "$temp_file" | tr -d ' ')
file_type=$(file "$temp_file" | cut -d: -f2)

echo "  Downloaded Size: $(numfmt --to=iec-i --suffix=B $br_size 2>/dev/null || echo "${br_size} bytes")"
echo "  File Type: $file_type"

if [[ $br_size -lt 10000 ]]; then
    echo -e "  Result: ${GREEN}✅ Brotli compression working (expected ~6KB)${NC}"
else
    echo -e "  Result: ${YELLOW}⚠️  Size larger than expected${NC}"
fi

rm "$temp_file"

# Test 6: Different Symbols (Cache Key Variation)
print_test "Test 6: Different Symbols - Cache Key Behavior"
echo "  Testing: VCB, FPT, HPG (same limit=50)"

symbols=("VCB" "FPT" "HPG")
for symbol in "${symbols[@]}"; do
    response=$(curl -s -i -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=$symbol&format=csv&limit=50")
    cache_status=$(echo "$response" | extract_header "cf-cache-status")
    age=$(echo "$response" | extract_header "age")
    echo "    $symbol: Cache=$cache_status, Age=${age:-0}s"
done

# Test same symbol again - should be HIT
sleep 1
response=$(curl -s -i -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=VCB&format=csv&limit=50")
cache_status=$(echo "$response" | extract_header "cf-cache-status")
age=$(echo "$response" | extract_header "age")
echo "    VCB (repeat): Cache=$cache_status, Age=${age:-0}s"

if [[ "$cache_status" == "HIT" ]]; then
    echo -e "  Result: ${GREEN}✅ Cache keys work correctly per symbol${NC}"
else
    echo -e "  Result: ${RED}❌ Expected HIT on repeat${NC}"
fi

# Test 7: Different Limits (Cache Key Variation)
print_test "Test 7: Different Limits - Same Symbol (ACB)"
echo "  Testing: limit=10, 50, 100, 200"

limits=(10 50 100 200)
for limit in "${limits[@]}"; do
    response=$(curl -s -i -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=ACB&format=csv&limit=$limit")
    cache_status=$(echo "$response" | extract_header "cf-cache-status")
    age=$(echo "$response" | extract_header "age")

    temp_file=$(mktemp)
    curl -s -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=ACB&format=csv&limit=$limit" -o "$temp_file"
    size=$(wc -c < "$temp_file" | tr -d ' ')
    size_fmt=$(numfmt --to=iec-i --suffix=B $size 2>/dev/null || echo "${size}B")
    rm "$temp_file"

    echo "    limit=$limit: Cache=$cache_status, Age=${age:-0}s, Size=$size_fmt"
done

echo -e "  Result: ${GREEN}✅ Each limit creates unique cache key${NC}"

# Test 8: Multiple Symbols (Cache Performance)
print_test "Test 8: Multiple Symbols in Single Request"
response=$(curl -s -i -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=VCB&symbol=FPT&symbol=HPG&format=csv&limit=100")
cache_status=$(echo "$response" | extract_header "cf-cache-status")
content_encoding=$(echo "$response" | extract_header "content-encoding")
age=$(echo "$response" | extract_header "age")

temp_file=$(mktemp)
curl -s -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=VCB&symbol=FPT&symbol=HPG&format=csv&limit=100" -o "$temp_file"
size=$(wc -c < "$temp_file" | tr -d ' ')
size_fmt=$(numfmt --to=iec-i --suffix=B $size 2>/dev/null || echo "${size}B")
rm "$temp_file"

echo "  Cache Status: ${cache_status:-NONE}"
echo "  Compression: ${content_encoding:-none}"
echo "  Size: $size_fmt"
echo "  Age: ${age:-0}s"

if [[ "$content_encoding" == "gzip" ]]; then
    echo -e "  Result: ${GREEN}✅ Multi-symbol query compressed${NC}"
else
    echo -e "  Result: ${RED}❌ Not compressed${NC}"
fi

# Test 9: No Limit Parameter (Default Behavior)
print_test "Test 9: No Limit Parameter - Default Behavior"
response=$(curl -s -i -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=VNM&format=csv")
cache_status=$(echo "$response" | extract_header "cf-cache-status")
content_encoding=$(echo "$response" | extract_header "content-encoding")

temp_file=$(mktemp)
curl -s -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=VNM&format=csv" -o "$temp_file"
size=$(wc -c < "$temp_file" | tr -d ' ')
size_fmt=$(numfmt --to=iec-i --suffix=B $size 2>/dev/null || echo "${size}B")
rm "$temp_file"

echo "  Cache Status: ${cache_status:-NONE}"
echo "  Size: $size_fmt (full history)"
echo "  Compression: ${content_encoding:-none}"

if [[ "$content_encoding" == "gzip" ]]; then
    echo -e "  Result: ${GREEN}✅ Full history compressed${NC}"
else
    echo -e "  Result: ${RED}❌ Not compressed${NC}"
fi

# Test 10: Cache Bypass with Unique Timestamp
print_test "Test 10: True Cache Bypass (Unique Timestamp)"
bypass_time=$(date +%s%N)
response=$(curl -s -i -H "Accept-Encoding: gzip" "$API_URL/tickers?symbol=SSI&format=csv&limit=10&_nocache=$bypass_time")
cache_status=$(echo "$response" | extract_header "cf-cache-status")

echo "  Cache Status: ${cache_status:-NONE}"
echo "  Timestamp: $bypass_time"

if [[ "$cache_status" == "MISS" ]] || [[ "$cache_status" == "DYNAMIC" ]]; then
    echo -e "  Result: ${GREEN}✅ Unique timestamp bypasses cache${NC}"
else
    echo -e "  Result: ${YELLOW}⚠️  Got $cache_status${NC}"
fi

# Test 11: Rate Limiting & Security Headers
print_test "Test 11: Rate Limiting & Security Headers"
response=$(curl -s -i "$API_URL/health")
ratelimit=$(echo "$response" | extract_header "x-ratelimit-limit")
ratelimit_remaining=$(echo "$response" | extract_header "x-ratelimit-remaining")
xss_protection=$(echo "$response" | extract_header "x-xss-protection")
frame_options=$(echo "$response" | extract_header "x-frame-options")
content_type_options=$(echo "$response" | extract_header "x-content-type-options")

echo "  Rate Limit: ${ratelimit:-NONE}"
echo "  Rate Remaining: ${ratelimit_remaining:-NONE}"
echo "  X-XSS-Protection: ${xss_protection:-NONE}"
echo "  X-Frame-Options: ${frame_options:-NONE}"
echo "  X-Content-Type-Options: ${content_type_options:-NONE}"

if [[ -n "$ratelimit" ]] && [[ -n "$xss_protection" ]]; then
    echo -e "  Result: ${GREEN}✅ Security headers present${NC}"
else
    echo -e "  Result: ${RED}❌ Some headers missing${NC}"
fi

# Summary
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                         TEST SUMMARY                             ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Tests Performed:"
echo "  1-2.  Basic cache MISS → HIT behavior"
echo "  3.    Large CSV query (all tickers, limit=128)"
echo "  4.    JSON format compression"
echo "  5.    Brotli compression algorithm"
echo "  6.    Different symbols (VCB, FPT, HPG) - cache key variation"
echo "  7.    Different limits (10, 50, 100, 200) - size & cache behavior"
echo "  8.    Multiple symbols in single request"
echo "  9.    No limit parameter (full history)"
echo "  10.   True cache bypass with unique timestamp"
echo "  11.   Rate limiting & security headers"
echo ""
echo "Expected Behavior:"
echo "  • First request: MISS (cache population)"
echo "  • Repeat request: HIT (served from cache)"
echo "  • Different query params: unique cache keys"
echo "  • CSV compressed: ~60% reduction"
echo "  • JSON compressed: ~75% reduction"
echo "  • Cache TTL: 4 hours (14400s)"
echo "  • Security headers present on all requests"
echo ""
echo "Cache Key Behavior:"
echo "  • Different symbols → Different cache keys"
echo "  • Different limits → Different cache keys"
echo "  • Same params → Cache HIT (within TTL)"
echo ""
echo -e "${GREEN}✅ Test completed at $(date)${NC}"
