#!/bin/bash

# Test script for upload API endpoints
# Usage: ./scripts/test-upload.sh [BASE_URL]
# Example: ./scripts/test-upload.sh http://localhost:3000
# Example: ./scripts/test-upload.sh https://api.aipriceaction.com

set -e  # Exit on error

# Configuration
BASE_URL="${1:-http://localhost:3000}"
PASSED=0
FAILED=0

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counter
TEST_NUM=0

# Function to print test result
print_result() {
    TEST_NUM=$((TEST_NUM + 1))
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓ Test $TEST_NUM PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}✗ Test $TEST_NUM FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

# Generate a UUID v7 (or fallback to v4)
generate_uuid() {
    # Try to use uuidgen (available on macOS)
    if command -v uuidgen &> /dev/null; then
        uuidgen | tr '[:upper:]' '[:lower:]'
    else
        # Fallback: generate a random UUID-like string
        cat /dev/urandom | LC_ALL=C tr -dc 'a-f0-9' | fold -w 32 | head -n 1 | sed 's/\(.\{8\}\)\(.\{4\}\)\(.\{4\}\)\(.\{4\}\)\(.\{12\}\)/\1-\2-\3-\4-\5/'
    fi
}

# Create test files
create_test_files() {
    # Create markdown test file
    cat > /tmp/test-upload.md <<EOF
# Test Markdown Document

This is a test markdown document for upload API testing.

## Features
- Markdown formatting
- Multiple lines
- **Bold text**
- *Italic text*

## Code Example
\`\`\`rust
fn main() {
    println!("Hello, world!");
}
\`\`\`
EOF

    # Create a small test image (1x1 PNG)
    printf '\x89\x50\x4e\x47\x0d\x0a\x1a\x0a\x00\x00\x00\x0d\x49\x48\x44\x52\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90\x77\x53\xde\x00\x00\x00\x0c\x49\x44\x41\x54\x08\xd7\x63\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00\x49\x45\x4e\x44\xae\x42\x60\x82' > /tmp/test-upload.png

    # Create a text file for markdown endpoint
    echo "This is a plain text file" > /tmp/test-upload.txt

    # Create an invalid file (binary for markdown endpoint)
    dd if=/dev/urandom of=/tmp/test-invalid.bin bs=1024 count=1 2>/dev/null
}

# Cleanup test files
cleanup_test_files() {
    rm -f /tmp/test-upload.md /tmp/test-upload.png /tmp/test-upload.txt /tmp/test-invalid.bin
    rm -f /tmp/large-file.md /tmp/duplicate.md
}

echo "========================================="
echo "Upload API Test Suite"
echo "========================================="
echo "Base URL: $BASE_URL"
echo ""

# Create test files
create_test_files

# Generate a session ID and secret for testing
SESSION_ID=$(generate_uuid)
SECRET=$(generate_uuid)
echo "Using session ID: $SESSION_ID"
echo "Using secret: $SECRET"
echo ""

# Test 1: Upload markdown file (success)
echo "=== Test 1: Upload markdown file (success) ==="
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/upload/markdown?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@/tmp/test-upload.md")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ] && echo "$BODY" | jq -e '.success == true' > /dev/null 2>&1; then
    print_result 0 "Upload markdown file"
    echo "$BODY" | jq '.'
else
    print_result 1 "Upload markdown file (HTTP $HTTP_CODE)"
    echo "$BODY"
fi
echo ""

# Test 2: Retrieve markdown file (success)
echo "=== Test 2: Retrieve markdown file (success) ==="
RESPONSE=$(curl -s -w "\n%{http_code}" "$BASE_URL/uploads/$SESSION_ID/markdown/test-upload.md")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ] && echo "$BODY" | grep -q "Test Markdown Document"; then
    print_result 0 "Retrieve markdown file"
    echo "Content preview: $(echo "$BODY" | head -n 3)"
else
    print_result 1 "Retrieve markdown file (HTTP $HTTP_CODE)"
fi
echo ""

# Test 3: Upload duplicate file (409 error expected)
echo "=== Test 3: Upload duplicate file (409 Conflict expected) ==="
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/upload/markdown?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@/tmp/test-upload.md")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "409" ] && echo "$BODY" | jq -e '.success == false' > /dev/null 2>&1; then
    print_result 0 "Duplicate file detection"
    echo "$BODY" | jq '.'
else
    print_result 1 "Duplicate file detection (expected 409, got HTTP $HTTP_CODE)"
    echo "$BODY"
fi
echo ""

# Test 4: Upload image file (success)
echo "=== Test 4: Upload image file (success) ==="
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/upload/image?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@/tmp/test-upload.png")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ] && echo "$BODY" | jq -e '.success == true' > /dev/null 2>&1; then
    print_result 0 "Upload image file"
    echo "$BODY" | jq '.'
else
    print_result 1 "Upload image file (HTTP $HTTP_CODE)"
    echo "$BODY"
fi
echo ""

# Test 5: Retrieve image file (success)
echo "=== Test 5: Retrieve image file (success) ==="
RESPONSE=$(curl -s -w "\n%{http_code}" "$BASE_URL/uploads/$SESSION_ID/images/test-upload.png")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)

if [ "$HTTP_CODE" = "200" ]; then
    print_result 0 "Retrieve image file"
    echo "Image retrieved successfully (size: $(echo "$RESPONSE" | sed '$d' | wc -c) bytes)"
else
    print_result 1 "Retrieve image file (HTTP $HTTP_CODE)"
fi
echo ""

# Test 6: Upload text file to markdown endpoint (success)
echo "=== Test 6: Upload .txt file to markdown endpoint (success) ==="
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/upload/markdown?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@/tmp/test-upload.txt")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ] && echo "$BODY" | jq -e '.success == true' > /dev/null 2>&1; then
    print_result 0 "Upload .txt file to markdown endpoint"
else
    print_result 1 "Upload .txt file to markdown endpoint (HTTP $HTTP_CODE)"
    echo "$BODY"
fi
echo ""

# Test 7: Wrong secret (403 error expected)
echo "=== Test 7: Wrong secret (403 Forbidden expected) ==="
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/upload/markdown?session_id=$SESSION_ID&secret=wrong-secret-12345" \
  -F "file=@/tmp/test-upload.txt")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "403" ] && echo "$BODY" | jq -e '.success == false' > /dev/null 2>&1; then
    print_result 0 "Wrong secret detection"
    echo "$BODY" | jq '.'
else
    print_result 1 "Wrong secret detection (expected 403, got HTTP $HTTP_CODE)"
    echo "$BODY"
fi
echo ""

# Test 8: Invalid session ID (400 error expected)
echo "=== Test 8: Invalid session ID (400 Bad Request expected) ==="
WRONG_SECRET=$(generate_uuid)
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/upload/markdown?session_id=invalid-uuid&secret=$WRONG_SECRET" \
  -F "file=@/tmp/test-upload.md")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "400" ] && echo "$BODY" | jq -e '.success == false' > /dev/null 2>&1; then
    print_result 0 "Invalid session ID detection"
    echo "$BODY" | jq '.'
else
    print_result 1 "Invalid session ID detection (expected 400, got HTTP $HTTP_CODE)"
    echo "$BODY"
fi
echo ""

# Test 9: Missing secret (400 error expected)
echo "=== Test 9: Missing secret (400 Bad Request expected) ==="
NEW_SESSION=$(generate_uuid)
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/upload/markdown?session_id=$NEW_SESSION" \
  -F "file=@/tmp/test-upload.md")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)

if [ "$HTTP_CODE" = "400" ]; then
    print_result 0 "Missing secret detection"
else
    print_result 1 "Missing secret detection (expected 400, got HTTP $HTTP_CODE)"
fi
echo ""

# Test 10: Missing session ID (400 error expected)
echo "=== Test 10: Missing session ID (400 Bad Request expected) ==="
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/upload/markdown" \
  -F "file=@/tmp/test-upload.md")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)

if [ "$HTTP_CODE" = "400" ]; then
    print_result 0 "Missing session ID detection"
else
    print_result 1 "Missing session ID detection (expected 400, got HTTP $HTTP_CODE)"
fi
echo ""

# Test 11: Path traversal attempt (400 error expected)
echo "=== Test 9: Path traversal attempt (400 Bad Request expected) ==="
RESPONSE=$(curl -s -w "\n%{http_code}" "$BASE_URL/uploads/$SESSION_ID/markdown/../../../etc/passwd")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)

if [ "$HTTP_CODE" = "400" ] || [ "$HTTP_CODE" = "404" ]; then
    print_result 0 "Path traversal prevention"
else
    print_result 1 "Path traversal prevention (expected 400/404, got HTTP $HTTP_CODE)"
fi
echo ""

# Test 12: Wrong file type - upload binary to markdown endpoint (415 error expected)
echo "=== Test 10: Wrong file type (415 Unsupported Media Type expected) ==="
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/upload/markdown?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@/tmp/test-invalid.bin")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "415" ] || [ "$HTTP_CODE" = "400" ]; then
    print_result 0 "Wrong file type detection"
    echo "$BODY" | jq '.' 2>/dev/null || echo "$BODY"
else
    print_result 1 "Wrong file type detection (expected 415, got HTTP $HTTP_CODE)"
    echo "$BODY"
fi
echo ""

# Test 13: File too large (413 or 500 error expected) - Create a 6MB file for markdown (limit is 5MB)
echo "=== Test 11: File too large (413/500 error expected) ==="
dd if=/dev/zero of=/tmp/large-file.md bs=1M count=6 2>/dev/null
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/upload/markdown?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@/tmp/large-file.md")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "413" ] || [ "$HTTP_CODE" = "500" ]; then
    print_result 0 "File size limit enforcement"
    echo "$BODY" | jq '.' 2>/dev/null || echo "$BODY"
else
    print_result 1 "File size limit enforcement (expected 413/500, got HTTP $HTTP_CODE)"
    echo "$BODY"
fi
echo ""

# Cleanup
cleanup_test_files

# Summary
echo "========================================="
echo "Test Summary"
echo "========================================="
echo -e "Total Tests: $TEST_NUM"
echo -e "${GREEN}Passed: $PASSED${NC}"
echo -e "${RED}Failed: $FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
