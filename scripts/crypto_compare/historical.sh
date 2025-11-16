#!/bin/bash

# CryptoCompare API Historical Data Explorer
# This script demonstrates all features documented in docs/CRYPTO.md

BASE_URL="https://min-api.cryptocompare.com"
CRYPTO=${1:-BTC}  # Default to BTC if no argument provided
CURRENCY=${2:-USD}  # Default to USD

echo "=========================================="
echo "CryptoCompare API Explorer"
echo "Cryptocurrency: $CRYPTO"
echo "Currency: $CURRENCY"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print section headers
print_header() {
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Function to make API call and display result
api_call() {
    local description=$1
    local url=$2

    echo -e "${YELLOW}$description${NC}"
    echo "URL: $url"
    echo ""

    response=$(curl -s "$url")

    # Check if response is successful
    if echo "$response" | jq -e '.Response == "Success"' > /dev/null 2>&1; then
        echo "$response" | jq '.'
    else
        # Show error details
        echo "❌ Error Response:"
        echo "$response" | jq '.'
    fi

    echo ""
    sleep 0.3  # Rate limit protection (5 calls/sec max)
}

# 1. DAILY ENDPOINT TESTS
print_header "1. DAILY HISTORICAL DATA (/data/v2/histoday)"

api_call "1.1 Basic daily data (last 10 days)" \
    "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=10"

api_call "1.2 Daily data with specific end date (toTs parameter)" \
    "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=5&toTs=1640000000"

api_call "1.3 Weekly aggregation (aggregate=7)" \
    "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=10&aggregate=7"

api_call "1.4 All available daily data (allData=true)" \
    "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&allData=true&limit=5"

# 2. HOURLY ENDPOINT TESTS
print_header "2. HOURLY HISTORICAL DATA (/data/v2/histohour)"

api_call "2.1 Basic hourly data (last 24 hours)" \
    "$BASE_URL/data/v2/histohour?fsym=$CRYPTO&tsym=$CURRENCY&limit=24"

api_call "2.2 4-hour candles (aggregate=4)" \
    "$BASE_URL/data/v2/histohour?fsym=$CRYPTO&tsym=$CURRENCY&limit=10&aggregate=4"

api_call "2.3 Hourly data from specific exchange (e=Binance)" \
    "$BASE_URL/data/v2/histohour?fsym=$CRYPTO&tsym=$CURRENCY&limit=10&e=Binance"

# 3. MINUTE ENDPOINT TESTS
print_header "3. MINUTE HISTORICAL DATA (/data/v2/histominute)"

api_call "3.1 Basic minute data (last 60 minutes)" \
    "$BASE_URL/data/v2/histominute?fsym=$CRYPTO&tsym=$CURRENCY&limit=60"

api_call "3.2 5-minute candles (aggregate=5)" \
    "$BASE_URL/data/v2/histominute?fsym=$CRYPTO&tsym=$CURRENCY&limit=12&aggregate=5"

api_call "3.3 15-minute candles (aggregate=15)" \
    "$BASE_URL/data/v2/histominute?fsym=$CRYPTO&tsym=$CURRENCY&limit=8&aggregate=15"

api_call "3.4 30-minute candles (aggregate=30)" \
    "$BASE_URL/data/v2/histominute?fsym=$CRYPTO&tsym=$CURRENCY&limit=6&aggregate=30"

# 4. ADVANCED PARAMETER TESTS
print_header "4. ADVANCED PARAMETERS"

api_call "4.1 Direct trading only (tryConversion=false)" \
    "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=5&tryConversion=false"

api_call "4.2 Explain conversion path (explainPath=true)" \
    "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=5&explainPath=true"

api_call "4.3 Non-predictable time periods (aggregatePredictableTimePeriods=false)" \
    "$BASE_URL/data/v2/histohour?fsym=$CRYPTO&tsym=$CURRENCY&limit=10&aggregate=2&aggregatePredictableTimePeriods=false"

api_call "4.4 With application identifier (extraParams)" \
    "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=5&extraParams=aipriceaction"

# 5. MULTI-CURRENCY TESTS
print_header "5. MULTI-CURRENCY SUPPORT"

api_call "5.1 BTC to EUR" \
    "$BASE_URL/data/v2/histoday?fsym=BTC&tsym=EUR&limit=5"

api_call "5.2 BTC to VND (Vietnamese Dong)" \
    "$BASE_URL/data/v2/histoday?fsym=BTC&tsym=VND&limit=5"

api_call "5.3 BTC to JPY (Japanese Yen)" \
    "$BASE_URL/data/v2/histoday?fsym=BTC&tsym=JPY&limit=5"

# 6. DIFFERENT CRYPTOCURRENCIES
print_header "6. DIFFERENT CRYPTOCURRENCIES"

api_call "6.1 Ethereum (ETH) daily data" \
    "$BASE_URL/data/v2/histoday?fsym=ETH&tsym=USD&limit=5"

api_call "6.2 Solana (SOL) daily data" \
    "$BASE_URL/data/v2/histoday?fsym=SOL&tsym=USD&limit=5"

api_call "6.3 XRP daily data" \
    "$BASE_URL/data/v2/histoday?fsym=XRP&tsym=USD&limit=5"

# 7. PAGINATION DEMONSTRATION
print_header "7. PAGINATION FOR FULL HISTORY"

echo -e "${YELLOW}7.1 First batch (limit=2000)${NC}"
echo "URL: $BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=10"
echo ""

response1=$(curl -s "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=10")
echo "$response1" | jq '{Response, TimeFrom: .Data.TimeFrom, TimeTo: .Data.TimeTo, DataPoints: (.Data.Data | length)}'

# Extract earliest timestamp for pagination
earliest_ts=$(echo "$response1" | jq -r '.Data.Data[0].time')
echo ""
echo "Earliest timestamp from first batch: $earliest_ts"
echo ""

sleep 0.3

echo -e "${YELLOW}7.2 Second batch using toTs (pagination)${NC}"
echo "URL: $BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=10&toTs=$earliest_ts"
echo ""

response2=$(curl -s "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=10&toTs=$earliest_ts")
echo "$response2" | jq '{Response, TimeFrom: .Data.TimeFrom, TimeTo: .Data.TimeTo, DataPoints: (.Data.Data | length)}'
echo ""

# 8. ERROR HANDLING TESTS
print_header "8. ERROR HANDLING"

api_call "8.1 Invalid cryptocurrency symbol" \
    "$BASE_URL/data/v2/histoday?fsym=INVALID123&tsym=USD&limit=5"

api_call "8.2 Invalid currency symbol" \
    "$BASE_URL/data/v2/histoday?fsym=BTC&tsym=INVALID&limit=5"

# 9. RESPONSE FIELD INSPECTION
print_header "9. RESPONSE FIELD DETAILS"

echo -e "${YELLOW}9.1 Detailed field inspection${NC}"
echo "URL: $BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=2"
echo ""

response=$(curl -s "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=2")

echo "Full Response Structure:"
echo "$response" | jq '{
    Response,
    Message,
    HasWarning,
    Type,
    RateLimit,
    Data: {
        Aggregated: .Data.Aggregated,
        TimeFrom: .Data.TimeFrom,
        TimeTo: .Data.TimeTo,
        FirstDataPoint: .Data.Data[0],
        FieldsAvailable: (.Data.Data[0] | keys)
    }
}'
echo ""

echo "Volume Fields Detail:"
echo "$response" | jq '.Data.Data[0] | {
    time,
    close,
    "volumefrom (crypto volume)": .volumefrom,
    "volumeto (currency volume)": .volumeto,
    conversionType,
    conversionSymbol
}'
echo ""

# 10. RATE LIMIT INFORMATION
print_header "10. RATE LIMIT MONITORING"

echo "Making rapid consecutive calls to check rate limit response..."
echo ""

for i in {1..6}; do
    echo "Call $i/6..."
    response=$(curl -s "$BASE_URL/data/v2/histoday?fsym=$CRYPTO&tsym=$CURRENCY&limit=1")

    if echo "$response" | jq -e '.Response == "Error"' > /dev/null 2>&1; then
        echo "❌ Rate limit hit!"
        echo "$response" | jq '{
            Response,
            Message,
            Type,
            RateLimit: .RateLimit.calls_made,
            RateLimitLeft: .RateLimit.calls_left
        }'
        break
    else
        echo "✅ Success"
    fi

    sleep 0.1  # Very short delay to potentially trigger rate limit
done

echo ""

# SUMMARY
print_header "SUMMARY"

echo "✅ Tested all three endpoints: daily, hourly, minute"
echo "✅ Demonstrated common parameters: limit, aggregate, toTs, etc."
echo "✅ Tested daily-only parameter: allData"
echo "✅ Explored multi-currency support: USD, EUR, VND, JPY"
echo "✅ Tested different cryptocurrencies: BTC, ETH, SOL, XRP"
echo "✅ Demonstrated pagination pattern for full history"
echo "✅ Tested error handling for invalid inputs"
echo "✅ Inspected response field structure"
echo "✅ Monitored rate limit behavior"
echo ""
echo "For full documentation, see: docs/CRYPTO.md"
echo ""
echo "=========================================="
echo "Exploration Complete!"
echo "=========================================="
