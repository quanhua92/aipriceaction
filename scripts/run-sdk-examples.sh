#!/bin/bash

# Run all SDK TypeScript examples
# Usage: ./scripts/run-sdk-examples.sh [API_URL]

set -e

API_URL="${1:-http://localhost:3000}"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo "=========================================="
echo "Running All SDK TypeScript Examples"
echo "API URL: $API_URL"
echo "=========================================="
echo ""

total=0
success=0

cd "$(dirname "$0")/../sdk/aipriceaction-js"

for file in examples/*.ts; do
    example_name=$(basename "$file" .ts)
    echo -e "${BLUE}Running: $example_name${NC}"

    if env API_URL="$API_URL" timeout 30 pnpx tsx "$file" > /dev/null 2>&1; then
        echo -e "${GREEN}✅ SUCCESS: $example_name${NC}"
        ((success++))
    else
        echo -e "${RED}❌ FAILED: $example_name${NC}"
    fi
    ((total++))
    echo ""
done

echo "=========================================="
echo "SDK Examples Summary"
echo "=========================================="
echo -e "Total examples: $total"
echo -e "${GREEN}Successful: $success${NC}"
echo -e "${RED}Failed: $((total - success))${NC}"
echo ""

if [ $success -eq $total ]; then
    echo -e "${GREEN}🎉 All SDK examples completed successfully!${NC}"
else
    echo -e "${RED}❌ Some examples failed.${NC}"
    exit 1
fi