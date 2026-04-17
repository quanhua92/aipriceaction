#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_URL="${1:-http://localhost:3000}"
FAILED=0
PASSED=0
TOTAL=0

for script in "$SCRIPT_DIR"/*.mjs; do
    [ -f "$script" ] || continue
    name="$(basename "$script")"
    TOTAL=$((TOTAL + 1))
    echo ""
    echo "===== [$TOTAL] Running $name against $BASE_URL ====="
    if node "$script" "$BASE_URL"; then
        PASSED=$((PASSED + 1))
        echo "===== [PASS] $name ====="
    else
        FAILED=$((FAILED + 1))
        echo "===== [FAIL] $name ====="
    fi
done

echo ""
echo "=============================="
echo "Results: $PASSED/$TOTAL passed, $FAILED failed"
echo "=============================="

[ "$FAILED" -eq 0 ]
