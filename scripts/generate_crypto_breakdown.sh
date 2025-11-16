#!/bin/bash

# Script to generate crypto data breakdown markdown table
# Scans crypto_data/ directory and creates CRYPTO_DATA_BREAKDOWN.md

CRYPTO_DATA_DIR="crypto_data"
OUTPUT_FILE="CRYPTO_DATA_BREAKDOWN.md"
CRYPTO_JSON="crypto_top_100.json"

echo "🔍 Generating crypto data breakdown..."

# Start markdown file
cat > "$OUTPUT_FILE" << 'EOF'
# Cryptocurrency Data Breakdown

Auto-generated breakdown of all cryptocurrency data in the `crypto_data/` directory.

EOF

# Add timestamp
echo "**Last Updated:** $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# Count total cryptos
total_cryptos=$(ls -d $CRYPTO_DATA_DIR/*/ 2>/dev/null | wc -l | tr -d ' ')
echo "**Total Cryptocurrencies:** $total_cryptos" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# Table header
cat >> "$OUTPUT_FILE" << 'EOF'
## Data Breakdown by Ticker

| Rank | Symbol | Name | Daily (1D) | Hourly (1H) | Minute (1m) | Total Records | Daily Size | Hourly Size | Minute Size |
|------|--------|------|------------|-------------|-------------|---------------|------------|-------------|-------------|
EOF

# Function to get crypto info from JSON
get_crypto_info() {
    local symbol=$1
    local field=$2
    jq -r --arg sym "$symbol" '.data[] | select(.symbol == $sym) | .'"$field" "$CRYPTO_JSON" 2>/dev/null || echo "N/A"
}

# Function to format file size
format_size() {
    local size=$1
    if [ "$size" = "0" ] || [ -z "$size" ]; then
        echo "-"
    elif [ "$size" -lt 1024 ]; then
        echo "${size}B"
    elif [ "$size" -lt 1048576 ]; then
        echo "$(( size / 1024 ))KB"
    else
        echo "$(( size / 1048576 ))MB"
    fi
}

# Initialize counters
total_daily=0
total_hourly=0
total_minute=0
total_records=0

# Temporary file to sort by rank
temp_file=$(mktemp)

# Iterate through crypto directories
for dir in $CRYPTO_DATA_DIR/*/; do
    ticker=$(basename "$dir")

    # Get crypto metadata
    rank=$(get_crypto_info "$ticker" "rank")
    name=$(get_crypto_info "$ticker" "name")

    # Count records (subtract 1 for header)
    if [ -f "$dir/1D.csv" ]; then
        daily_count=$(( $(wc -l < "$dir/1D.csv") - 1 ))
        daily_size=$(stat -f%z "$dir/1D.csv" 2>/dev/null || stat -c%s "$dir/1D.csv" 2>/dev/null || echo "0")
    else
        daily_count=0
        daily_size=0
    fi

    if [ -f "$dir/1H.csv" ]; then
        hourly_count=$(( $(wc -l < "$dir/1H.csv") - 1 ))
        hourly_size=$(stat -f%z "$dir/1H.csv" 2>/dev/null || stat -c%s "$dir/1H.csv" 2>/dev/null || echo "0")
    else
        hourly_count=0
        hourly_size=0
    fi

    if [ -f "$dir/1m.csv" ]; then
        minute_count=$(( $(wc -l < "$dir/1m.csv") - 1 ))
        minute_size=$(stat -f%z "$dir/1m.csv" 2>/dev/null || stat -c%s "$dir/1m.csv" 2>/dev/null || echo "0")
    else
        minute_count=0
        minute_size=0
    fi

    # Calculate total for this crypto
    crypto_total=$(( daily_count + hourly_count + minute_count ))

    # Update grand totals
    total_daily=$(( total_daily + daily_count ))
    total_hourly=$(( total_hourly + hourly_count ))
    total_minute=$(( total_minute + minute_count ))
    total_records=$(( total_records + crypto_total ))

    # Format sizes
    daily_size_fmt=$(format_size "$daily_size")
    hourly_size_fmt=$(format_size "$hourly_size")
    minute_size_fmt=$(format_size "$minute_size")

    # Write to temp file for sorting
    echo "$rank|$ticker|$name|$daily_count|$hourly_count|$minute_count|$crypto_total|$daily_size_fmt|$hourly_size_fmt|$minute_size_fmt" >> "$temp_file"
done

# Sort by rank and append to output
sort -t'|' -k1 -n "$temp_file" | while IFS='|' read -r rank ticker name daily hourly minute total daily_sz hourly_sz minute_sz; do
    echo "| $rank | $ticker | $name | $daily | $hourly | $minute | $total | $daily_sz | $hourly_sz | $minute_sz |" >> "$OUTPUT_FILE"
done

# Clean up temp file
rm "$temp_file"

# Add summary section
cat >> "$OUTPUT_FILE" << EOF

## Summary Statistics

- **Total Cryptocurrencies:** $total_cryptos
- **Total Daily Records:** $(printf "%'d" $total_daily)
- **Total Hourly Records:** $(printf "%'d" $total_hourly)
- **Total Minute Records:** $(printf "%'d" $total_minute)
- **Grand Total Records:** $(printf "%'d" $total_records)

## Data Intervals

- **Daily (1D):** One record per day (OHLCV + indicators)
- **Hourly (1H):** One record per hour (OHLCV + indicators)
- **Minute (1m):** One record per minute (OHLCV + indicators)

## CSV Format

All CSV files contain 20 columns:
1. ticker, time, open, high, low, close, volume (7 OHLCV columns)
2. ma10, ma20, ma50, ma100, ma200 (5 moving average columns)
3. ma10_score, ma20_score, ma50_score, ma100_score, ma200_score (5 MA score columns)
4. close_changed, volume_changed, total_money_changed (3 change columns)

## Notes

- Record counts exclude CSV header row
- File sizes are approximate
- Data is updated every 15 minutes by crypto_worker
- Missing data indicated by "-"
EOF

echo "✅ Breakdown saved to $OUTPUT_FILE"
echo ""
echo "📊 Summary:"
echo "  - Total Cryptos: $total_cryptos"
echo "  - Total Records: $(printf "%'d" $total_records)"
echo "  - Daily: $(printf "%'d" $total_daily)"
echo "  - Hourly: $(printf "%'d" $total_hourly)"
echo "  - Minute: $(printf "%'d" $total_minute)"
