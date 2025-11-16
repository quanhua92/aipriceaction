#!/bin/bash

# Script to generate market data breakdown markdown table
# Scans market_data/ directory and creates MARKET_DATA_BREAKDOWN.md

MARKET_DATA_DIR="market_data"
OUTPUT_FILE="MARKET_DATA_BREAKDOWN.md"
TICKER_JSON="ticker_group.json"

echo "🔍 Generating market data breakdown..."

# Start markdown file
cat > "$OUTPUT_FILE" << 'EOF'
# Market Data Breakdown

Auto-generated breakdown of all stock market data in the `market_data/` directory.

EOF

# Add timestamp
echo "**Last Updated:** $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# Count total tickers
total_tickers=$(ls -d $MARKET_DATA_DIR/*/ 2>/dev/null | wc -l | tr -d ' ')
echo "**Total Tickers:** $total_tickers" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# Get sector information from ticker_group.json
get_ticker_sector() {
    local ticker=$1
    # Check if it's an index
    if [ "$ticker" = "VNINDEX" ] || [ "$ticker" = "VN30" ]; then
        echo "INDEX"
        return
    fi
    # Get all keys from JSON and check if ticker is in any group
    local sector=$(jq -r --arg sym "$ticker" 'to_entries[] | select(.value | index($sym)) | .key' "$TICKER_JSON" 2>/dev/null | head -1)
    if [ -n "$sector" ]; then
        echo "$sector"
    else
        echo "OTHER"
    fi
}

# Table header
cat >> "$OUTPUT_FILE" << 'EOF'
## Data Breakdown by Ticker

| Symbol | Sector | Daily (1D) | Hourly (1H) | Minute (1m) | Total Records | Daily Size | Hourly Size | Minute Size |
|--------|--------|------------|-------------|-------------|---------------|------------|-------------|-------------|
EOF

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

# Temporary files for sector tracking
sector_temp=$(mktemp)
> "$sector_temp"

# Temporary file to sort alphabetically
temp_file=$(mktemp)

# Iterate through ticker directories
for dir in $MARKET_DATA_DIR/*/; do
    ticker=$(basename "$dir")

    # Get sector
    sector=$(get_ticker_sector "$ticker")

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

    # Calculate total for this ticker
    ticker_total=$(( daily_count + hourly_count + minute_count ))

    # Update grand totals
    total_daily=$(( total_daily + daily_count ))
    total_hourly=$(( total_hourly + hourly_count ))
    total_minute=$(( total_minute + minute_count ))
    total_records=$(( total_records + ticker_total ))

    # Track sector for this ticker
    echo "$sector" >> "$sector_temp"

    # Format sizes
    daily_size_fmt=$(format_size "$daily_size")
    hourly_size_fmt=$(format_size "$hourly_size")
    minute_size_fmt=$(format_size "$minute_size")

    # Write to temp file for sorting
    echo "$ticker|$sector|$daily_count|$hourly_count|$minute_count|$ticker_total|$daily_size_fmt|$hourly_size_fmt|$minute_size_fmt" >> "$temp_file"
done

# Sort alphabetically and append to output
sort -t'|' -k1 "$temp_file" | while IFS='|' read -r ticker sector daily hourly minute total daily_sz hourly_sz minute_sz; do
    echo "| $ticker | $sector | $daily | $hourly | $minute | $total | $daily_sz | $hourly_sz | $minute_sz |" >> "$OUTPUT_FILE"
done

# Clean up temp file
rm "$temp_file"

# Add summary section
cat >> "$OUTPUT_FILE" << EOF

## Summary Statistics

- **Total Tickers:** $total_tickers
- **Total Daily Records:** $(printf "%'d" $total_daily)
- **Total Hourly Records:** $(printf "%'d" $total_hourly)
- **Total Minute Records:** $(printf "%'d" $total_minute)
- **Grand Total Records:** $(printf "%'d" $total_records)

## Breakdown by Sector

EOF

# Count unique sectors and their frequencies
sort "$sector_temp" | uniq -c | sort -rn | while read count sector; do
    echo "- **$sector:** $count tickers" >> "$OUTPUT_FILE"
done

cat >> "$OUTPUT_FILE" << EOF

## Data Intervals

- **Daily (1D):** One record per trading day (OHLCV + indicators)
- **Hourly (1H):** One record per hour during trading hours (OHLCV + indicators)
- **Minute (1m):** One record per minute during trading hours (OHLCV + indicators)

## CSV Format

All CSV files contain 20 columns:
1. ticker, time, open, high, low, close, volume (7 OHLCV columns)
2. ma10, ma20, ma50, ma100, ma200 (5 moving average columns)
3. ma10_score, ma20_score, ma50_score, ma100_score, ma200_score (5 MA score columns)
4. close_changed, volume_changed, total_money_changed (3 change columns)

## Trading Hours

- **Market:** Vietnam Stock Exchange (VNX)
- **Trading Hours:** 09:00 - 15:00 ICT (UTC+7)
- **Trading Days:** Monday - Friday (excluding holidays)

## Notes

- Record counts exclude CSV header row
- File sizes are approximate
- Data is updated by background workers:
  - Daily: Every 15 seconds during trading hours, 5 minutes off-hours
  - Hourly/Minute: Every 5 minutes during trading hours, 30 minutes off-hours
- Missing data indicated by "-"
- Prices stored in full VND format (not divided by 1000)
EOF

echo "✅ Breakdown saved to $OUTPUT_FILE"
echo ""
echo "📊 Summary:"
echo "  - Total Tickers: $total_tickers"
echo "  - Total Records: $(printf "%'d" $total_records)"
echo "  - Daily: $(printf "%'d" $total_daily)"
echo "  - Hourly: $(printf "%'d" $total_hourly)"
echo "  - Minute: $(printf "%'d" $total_minute)"
echo ""
echo "📂 Sectors:"
sort "$sector_temp" | uniq -c | sort -rn | while read count sector; do
    echo "  - $sector: $count tickers"
done

# Clean up sector temp file
rm "$sector_temp"
