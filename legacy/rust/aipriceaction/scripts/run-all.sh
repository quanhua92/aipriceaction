#!/usr/bin/env bash
set -euo pipefail

# Usage: ./scripts/run-all.sh [BASE_URL]
#
# Optional env vars (load from .env for full coverage):
#   SYNC_TOKEN      Required by test-sync.mjs for auth tests
#   REFRESH_SECRET  Enables full refresh-key tests in test-api.mjs
#
# Example with .env:
#   export SYNC_TOKEN REFRESH_SECRET && ./scripts/run-all.sh http://localhost:3000
#   set -a; source .env; set +a; ./scripts/run-all.sh http://localhost:3000

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
