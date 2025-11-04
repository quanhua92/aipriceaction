#!/bin/bash
# Daily data sync script
# Uses smart default: 3 days resume (optimized for daily data volume)
# Expected time: ~5 seconds for 290 tickers with 30-ticker batches

echo "🚀 Starting daily data sync..."
echo "================================"
echo "Started at: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

time ./target/release/aipriceaction pull --intervals daily

EXIT_CODE=$?

echo ""
echo "Completed at: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Daily sync completed successfully!"
else
    echo "❌ Daily sync failed with exit code: $EXIT_CODE"
fi

exit $EXIT_CODE
