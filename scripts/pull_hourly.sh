#!/bin/bash
# Hourly data sync script
# Uses smart default: 5 days resume (optimized for hourly data volume)
# Expected time: ~20-30 seconds for 290 tickers with 10-ticker batches
#
# Note: 5 days × ~6 trading hours = ~30 records per ticker
#       Perfect balance between coverage and API load

echo "🚀 Starting hourly data sync..."
echo "================================"
echo "Started at: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

time ./target/release/aipriceaction pull --intervals hourly

EXIT_CODE=$?

echo ""
echo "Completed at: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Hourly sync completed successfully!"
else
    echo "❌ Hourly sync failed with exit code: $EXIT_CODE"
fi

exit $EXIT_CODE
