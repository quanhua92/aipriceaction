#!/bin/bash

# quick_crypto_test.sh - Quick test to verify crypto endpoint behavior
# Usage: ./scripts/quick_crypto_test.sh [IP_ADDRESS] [HOST_HEADER]

set -e

IP=${1:-"127.0.0.1"}
HOST_HEADER=${2:-""}
BASE_URL="http://${IP}"

echo "Testing crypto endpoint on $BASE_URL"
if [ -n "$HOST_HEADER" ]; then
    echo "Host Header: $HOST_HEADER"
fi
echo ""

# Test 1: Health check
echo "1. Health Check:"
if [ -n "$HOST_HEADER" ]; then
    curl -s -H "Host: $HOST_HEADER" "$BASE_URL/health" | jq -r '.crypto_last_sync' || echo "Failed"
else
    curl -s "$BASE_URL/health" | jq -r '.crypto_last_sync' || echo "Failed"
fi
echo ""

# Test 2: Single crypto request with timeout
echo "2. Crypto request (5s timeout):"
if [ -n "$HOST_HEADER" ]; then
    response=$(timeout 5 curl -s -H "Host: $HOST_HEADER" "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1D&limit=1" 2>/dev/null || echo "TIMEOUT")
else
    response=$(timeout 5 curl -s "$BASE_URL/tickers?symbol=BTC&mode=crypto&interval=1D&limit=1" 2>/dev/null || echo "TIMEOUT")
fi

if [ "$response" = "TIMEOUT" ]; then
    echo "❌ TIMEOUT - crypto request is hanging"
else
    ticker=$(echo "$response" | jq -r 'keys[0]' 2>/dev/null)
    if [ "$ticker" = "BTC" ]; then
        echo "✅ SUCCESS - returned BTC data"
    elif [ -n "$ticker" ]; then
        echo "❌ WRONG TICKER - expected BTC, got $ticker"
    else
        echo "❌ INVALID RESPONSE - $response"
    fi
fi
echo ""

# Test 3: VN request for comparison
echo "3. VN request (should work):"
if [ -n "$HOST_HEADER" ]; then
    response=$(timeout 5 curl -s -H "Host: $HOST_HEADER" "$BASE_URL/tickers?symbol=FPT&mode=vn&interval=1D&limit=1" 2>/dev/null || echo "TIMEOUT")
else
    response=$(timeout 5 curl -s "$BASE_URL/tickers?symbol=FPT&mode=vn&interval=1D&limit=1" 2>/dev/null || echo "TIMEOUT")
fi

if [ "$response" = "TIMEOUT" ]; then
    echo "❌ TIMEOUT - VN request failed"
else
    ticker=$(echo "$response" | jq -r 'keys[0]' 2>/dev/null)
    if [ "$ticker" = "FPT" ]; then
        echo "✅ SUCCESS - returned FPT data"
    else
        echo "❌ WRONG TICKER - expected FPT, got $ticker"
    fi
fi