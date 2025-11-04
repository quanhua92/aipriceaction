#!/bin/bash
# Sync all intervals (daily, hourly, minute)
# Expected total time: ~10-15 minutes for 290 tickers

echo "🚀 Starting FULL data sync (all intervals)..."
echo "=============================================="
echo "Started at: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

TOTAL_START=$(date +%s)

# Run daily sync
echo "📊 [1/3] Daily sync (last 3 days)..."
echo "----------------------"
./target/release/aipriceaction pull --intervals daily --resume-days 3
DAILY_EXIT=$?
echo ""

# Run hourly sync
echo "📊 [2/3] Hourly sync (last 5 days)..."
echo "----------------------"
./target/release/aipriceaction pull --intervals hourly --resume-days 5
HOURLY_EXIT=$?
echo ""

# Run minute sync
echo "📊 [3/3] Minute sync (last 2 days)..."
echo "----------------------"
./target/release/aipriceaction pull --intervals minute --resume-days 2
MINUTE_EXIT=$?
echo ""

TOTAL_END=$(date +%s)
TOTAL_TIME=$((TOTAL_END - TOTAL_START))
TOTAL_MINUTES=$((TOTAL_TIME / 60))
TOTAL_SECONDS=$((TOTAL_TIME % 60))

echo "=============================================="
echo "🎉 ALL SYNCS COMPLETE!"
echo "=============================================="
echo "Completed at: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Total time: ${TOTAL_MINUTES}m ${TOTAL_SECONDS}s"
echo ""
echo "Results:"
echo "  Daily:  $([ $DAILY_EXIT -eq 0 ] && echo '✅ Success' || echo '❌ Failed')"
echo "  Hourly: $([ $HOURLY_EXIT -eq 0 ] && echo '✅ Success' || echo '❌ Failed')"
echo "  Minute: $([ $MINUTE_EXIT -eq 0 ] && echo '✅ Success' || echo '❌ Failed')"

# Exit with error if any sync failed
if [ $DAILY_EXIT -ne 0 ] || [ $HOURLY_EXIT -ne 0 ] || [ $MINUTE_EXIT -ne 0 ]; then
    exit 1
fi

exit 0
