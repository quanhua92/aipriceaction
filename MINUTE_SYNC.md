# Minute Data Sync Guide

## Overview

The minute sync functionality downloads and maintains 1-minute interval OHLCV (Open, High, Low, Close, Volume) data for all tickers in `ticker_group.json`. This provides the highest granularity data for detailed intraday analysis.

## Key Features

- **Smart Resume Mode**: Only downloads recent data if historical data exists (significantly faster)
- **Monthly Chunking**: Splits large downloads into monthly chunks to avoid API timeouts
- **Dividend Detection**: Automatically detects price adjustments and re-downloads when needed
- **High-Precision Timestamps**: Maintains full datetime information (`YYYY-MM-DD HH:MM:SS`)
- **Large Dataset Handling**: Efficiently processes 100K+ records per ticker

## Usage

### Basic Minute Sync
```bash
./target/release/aipriceaction pull --intervals minute
```

### Resume Mode (Recommended)
Downloads only recent data (last 7 days recommended for minute data):
```bash
./target/release/aipriceaction pull --intervals minute --resume-days 7
```

### Custom Date Range
```bash
./target/release/aipriceaction pull --intervals minute --resume-days 3   # Last 3 days
./target/release/aipriceaction pull --intervals minute --resume-days 14  # Last 2 weeks
```

### Debug Mode (Test with 3 tickers)
```bash
./target/release/aipriceaction pull --intervals minute --debug
```

### Full History Download
Force complete re-download (use very sparingly - takes hours):
```bash
./target/release/aipriceaction pull --intervals minute --full
```

## Data Structure

### Directory Layout
```
market_data/
├── VNINDEX/
│   ├── daily.csv    # ~2,700 rows
│   ├── 1h.csv       # ~2,300 rows
│   └── 1m.csv       # ~120,000 rows (large!)
├── VIC/
│   ├── daily.csv
│   ├── 1h.csv
│   └── 1m.csv       # ~114,000 rows
...
```

### CSV Format
```csv
ticker,time,open,high,low,close,volume
VNINDEX,2025-11-04 07:45:00,1651.98,1656.45,1651.98,1651.98,59124770
VNINDEX,2025-11-04 07:30:00,1656.41,1656.41,1656.41,1656.41,916715
VNINDEX,2025-11-04 07:29:00,1656.81,1657.16,1655.44,1656.31,7761907
```

**Note**:
- Timestamps are in UTC (Vietnam is UTC+7)
- 24-hour format
- 1-minute intervals during trading hours only

## Performance

### Timing (290 tickers, 7-day resume)
- **Per ticker**: ~9-12 seconds
- **Total time**: ~40-60 minutes for full sync
- **Breakdown**:
  - Monthly chunking: ~6s (2 months × 3s each)
  - Dividend check: ~2s (API 0.4s + sleep 1.5s)
  - File I/O: ~0.05s (larger files than hourly)

### File Sizes
- **Per ticker**: 5-15 MB (CSV text)
- **Total (290 tickers)**: ~2-4 GB
- **VNINDEX**: ~120,000 rows (~10 MB)

### Optimization Tips
1. **Use --resume-days 7** (not 30): Minute data grows very fast
2. **Monitor disk space**: Ensure 5-10 GB available
3. **Run during off-peak hours**: Large data transfers
4. **Consider cleanup**: Old minute data may not be needed beyond 1-2 months

## Technical Details

### Monthly Chunking
For large date ranges, downloads are split by month:
```
2025-10-01 to 2025-10-31  (~20,000 records)
2025-11-01 to 2025-11-04  (~1,500 records)
```

This prevents API timeouts and memory issues.

### Resume vs Full Mode Decision
Automatic categorization:
- **Resume mode**: Existing file with >5 rows → download last N days
- **Full history**: No file or <5 rows → download from `start_date`

**Important**: Minute data full history takes 10-20x longer than daily!

### Dividend Detection
Same as hourly:
1. Download last 60 days
2. Compare 3-week-old data vs existing
3. If ratio > 1.02, re-download full history
4. Otherwise, merge incrementally

### Data Merging
Smart merge for large datasets:
1. Load existing data (may be 100K+ rows)
2. Remove last row (potentially incomplete)
3. Append new data from API
4. Sort by timestamp (efficient for mostly-sorted data)
5. Write to CSV

### Memory Management
- Streaming CSV writes for large datasets
- Efficient sorting for chronological data
- No full dataset loading in memory during writes

## Troubleshooting

### Error: "Out of memory"
**Cause**: Full history download for too many tickers simultaneously
**Fix**:
- Use resume mode instead of --full
- Process tickers in smaller batches
- Increase available RAM

### Error: "Disk full"
**Cause**: Minute data requires significant storage
**Fix**:
- Free up 5-10 GB of disk space
- Clean up old data files
- Use external storage

### Slow Performance (>2 min/ticker)
**Check**:
- Network bandwidth
- Disk I/O speed (SSD recommended)
- Monthly chunking working correctly

### Missing Recent Data
**Cause**: Resume mode window too small
**Fix**:
```bash
# Increase resume window
./target/release/aipriceaction pull --intervals minute --resume-days 14
```

### Incomplete Files (less than expected rows)
**Solutions**:
1. Check minute_sync.log for API errors
2. Re-run for specific ticker
3. Use --full for that ticker only
4. Verify VCI has minute data for that ticker

## Examples

### Daily Incremental Update
Run this daily to keep minute data current:
```bash
./target/release/aipriceaction pull --intervals minute --resume-days 3
```

### Weekly Full Sync
Run weekly to catch any gaps:
```bash
./target/release/aipriceaction pull --intervals minute --resume-days 7
```

### Background Sync with Progress Monitoring
```bash
# Start in background
nohup ./target/release/aipriceaction pull --intervals minute --resume-days 7 > minute_sync.log 2>&1 &

# Monitor progress (another terminal)
tail -f minute_sync.log | grep -E "SUCCESS|FAILED|\[0[0-9][0-9]/290\]"

# Check completion
grep "SYNC COMPLETE" minute_sync.log
```

### Sync All Timeframes Together
```bash
# Daily, hourly, and minute
./target/release/aipriceaction pull --intervals all

# Takes ~1-2 hours for 290 tickers
```

### Emergency Re-sync Single Ticker
```bash
# Delete corrupted file
rm market_data/VIC/1m.csv

# Re-download (use debug mode workaround)
# Edit ticker_group.json temporarily to only include VIC
./target/release/aipriceaction pull --intervals minute
```

## Data Quality

### Validation Checks
- ✅ Timestamp parsing (minute precision)
- ✅ Trading hours validation (2:15-7:45 UTC)
- ✅ Duplicate removal during merge
- ✅ Chronological ordering
- ✅ Volume sanity checks

### Known Limitations
1. **Trading hours only**: No data outside market hours
2. **Gaps**: Market halts, lunch breaks show as gaps
3. **Timezone**: Stored in UTC (Vietnam is UTC+7)
4. **File size**: Large files may slow down some tools

## Best Practices

1. **Regular Updates**: Run daily with `--resume-days 3-7`
2. **Disk Management**: Monitor and clean old data
3. **Backup Before Full Sync**: Preserve existing data
4. **Check Logs**: Review errors after each sync
5. **Spot Checks**: Verify random tickers periodically

## Performance Comparison

| Interval | Rows/Ticker | File Size | Sync Time (290 tickers) |
|----------|-------------|-----------|-------------------------|
| Daily    | ~2,700      | ~200 KB   | ~8 minutes              |
| Hourly   | ~2,300      | ~180 KB   | ~35 minutes             |
| Minute   | ~120,000    | ~10 MB    | ~45-60 minutes          |

## API Limits

- **Rate limit**: 60 calls per minute (same as daily/hourly)
- **Chunking**: Monthly (vs yearly for hourly)
- **Timeout**: 30 seconds per chunk
- **Retries**: 3 attempts with exponential backoff

## Storage Requirements

### Per Ticker (approximate)
- 1 month: ~25,000 rows (~2 MB)
- 3 months: ~75,000 rows (~6 MB)
- 1 year: ~300,000 rows (~25 MB)
- Full history (2023-present): ~120,000 rows (~10 MB)

### Total (290 tickers)
- 7-day sync: ~290 × 2 MB = **580 MB**
- 30-day sync: ~290 × 8 MB = **2.3 GB**
- Full history: ~290 × 10 MB = **2.9 GB**

## Integration

### With Other Intervals
```bash
# Sync all three intervals in sequence
./target/release/aipriceaction pull --intervals all --resume-days 7

# Selective sync
./target/release/aipriceaction pull --intervals daily,minute
```

### Programmatic Access
```rust
// Read minute data from CSV
let data = read_csv("market_data/VNINDEX/1m.csv")?;

// Filter by time range
let today_data: Vec<_> = data.into_iter()
    .filter(|row| row.time >= start && row.time < end)
    .collect();
```

## Monitoring

### During Sync
```bash
# Count completed tickers
grep "SUCCESS" minute_sync.log | wc -l

# Check errors
grep "FAILED" minute_sync.log

# Current ticker
tail -1 minute_sync.log | grep "\[0[0-9][0-9]/290\]"
```

### After Sync
```bash
# Check all file sizes
du -sh market_data/*/1m.csv | sort -h

# Count total rows
wc -l market_data/*/1m.csv | tail -1

# Verify no corrupted files
for f in market_data/*/1m.csv; do
    if ! head -1 "$f" | grep -q "ticker,time"; then
        echo "Corrupted: $f"
    fi
done
```

## Future Enhancements

Potential improvements:
- [ ] Parallel chunk downloads
- [ ] Compression (gzip CSV files)
- [ ] Incremental append-only writes
- [ ] Data cleanup utilities
- [ ] Parquet format export
- [ ] Configurable chunk size

## Common Issues

### 1. Sync Takes Too Long
**Problem**: Full sync taking 2+ hours
**Solution**: Use resume mode, not --full

### 2. Files Too Large
**Problem**: Individual CSV files > 50 MB
**Solution**: This is normal for active tickers with long history

### 3. Missing Chunks
**Problem**: Gaps in data timeline
**Solution**: VCI API may not have complete minute data for all periods

### 4. Memory Spike
**Problem**: Process using 1GB+ RAM
**Solution**: Normal for minute data processing, ensure adequate RAM

## Summary

Minute data provides highest granularity but requires:
- ⚠️ More storage (~3 GB)
- ⚠️ Longer sync times (45-60 min)
- ⚠️ Regular maintenance
- ✅ Best for detailed intraday analysis
- ✅ Essential for backtesting strategies
- ✅ Valuable for pattern detection

Recommended: **Run daily with --resume-days 7** for optimal balance.
