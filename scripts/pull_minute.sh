#!/bin/bash
# Minute data sync script
# Uses smart default: 2 days resume (optimized for minute data volume)
# Expected time: ~5 minutes for 290 tickers with 3-ticker batches
#
# Note: Minute data has ~360 records per day, so 2 days = ~720 records per ticker
#       3-ticker batches keep API requests manageable (~900 records/batch)

echo "🚀 Starting minute data sync..."
echo "================================"
echo "Started at: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

time ./target/release/aipriceaction pull --intervals minute

EXIT_CODE=$?

echo ""
echo "Completed at: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Minute sync completed successfully!"
else
    echo "❌ Minute sync failed with exit code: $EXIT_CODE"
fi

exit $EXIT_CODE
