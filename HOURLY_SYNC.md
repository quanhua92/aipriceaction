# Hourly Data Sync Guide

## Overview

The hourly sync functionality downloads and maintains hourly OHLCV (Open, High, Low, Close, Volume) data for all tickers in `ticker_group.json`.

## Key Features

- **Smart Resume Mode**: Only downloads recent data if historical data exists
- **Yearly Chunking**: Splits large historical downloads into yearly chunks to avoid API timeouts
- **Dividend Detection**: Automatically detects price adjustments and re-downloads full history when needed
- **Timestamp Preservation**: Maintains full datetime information (`YYYY-MM-DD HH:MM:SS`)

## Usage

### Basic Hourly Sync
```bash
./target/release/aipriceaction pull --intervals hourly
```

### Resume Mode (Default)
Downloads only recent data (last 30 days by default):
```bash
./target/release/aipriceaction pull --intervals hourly --resume-days 30
```

### Custom Date Range
```bash
./target/release/aipriceaction pull --intervals hourly --resume-days 7  # Last 7 days
```

### Debug Mode (Test with 3 tickers)
```bash
./target/release/aipriceaction pull --intervals hourly --debug
```

### Full History Download
Force complete re-download (use sparingly):
```bash
./target/release/aipriceaction pull --intervals hourly --full
```

## Data Structure

### Directory Layout
```
market_data/
├── VNINDEX/
│   ├── daily.csv    # Daily data (YYYY-MM-DD)
│   ├── 1h.csv       # Hourly data (YYYY-MM-DD HH:MM:SS)
│   └── 1m.csv       # Minute data (YYYY-MM-DD HH:MM:SS)
├── VIC/
│   ├── daily.csv
│   ├── 1h.csv
│   └── 1m.csv
...
```

### CSV Format
```csv
ticker,time,open,high,low,close,volume
VNINDEX,2025-11-04 07:00:00,1635.99,1658.93,1632.67,1651.98,301717115
VNINDEX,2025-11-04 06:00:00,1608.99,1640.94,1600.56,1635.9,454740778
VNINDEX,2025-11-04 04:00:00,1615.05,1616.21,1608.8,1612.53,108957582
```

**Note**: Timestamps are in UTC and use 24-hour format.

## Performance

### Timing (290 tickers)
- **Per ticker**: ~3.8-4.2 seconds
- **Total time**: ~35-40 minutes for full sync
- **Breakdown**:
  - Dividend check: ~1.5s (94% of time) ⚠️ Bottleneck
  - API call: ~0.2s
  - File I/O: ~0.002s

### Optimization Tips
1. **Use resume mode**: Only download recent data unless needed
2. **Avoid --full**: Only use when data corruption detected
3. **Run during off-hours**: Reduces network congestion

## Technical Details

### Yearly Chunking
For tickers requiring full history, downloads are split by year:
```
2015-01-01 to 2015-12-31
2016-01-01 to 2016-12-31
...
2025-01-01 to 2025-11-04
```

This prevents API timeouts on large date ranges.

### Resume vs Full Mode Decision
The system automatically categorizes tickers:
- **Resume mode**: Existing file with >5 rows → download last N days
- **Full history**: No file or <5 rows → download from `start_date`

### Dividend Detection
For each ticker in resume mode:
1. Download last 60 days of data
2. Compare prices from 3 weeks ago vs existing data
3. If ratio > 1.02 (2%), assume dividend → re-download full history
4. Otherwise, merge new data with existing

### Data Merging
Smart merge strategy:
1. Remove last row from existing data (may be incomplete)
2. Add all new data from API (includes refreshed last row)
3. Sort by timestamp
4. Write to CSV

## Troubleshooting

### Error: "Invalid date: trailing input"
**Cause**: Old CSV files with wrong datetime format
**Fix**: Delete affected CSV file and re-sync:
```bash
rm market_data/TICKER/1h.csv
./target/release/aipriceaction pull --intervals hourly
```

### Error: "No data returned"
**Cause**: VCI API doesn't have hourly data for that ticker
**Expected**: Some tickers may not have hourly data available

### Slow Performance
**Check**:
- Network connection
- API rate limiting (60 calls/minute limit)
- Disk I/O performance

### Incomplete Data
**Solutions**:
1. Check hourly_sync.log for errors
2. Re-run with --full flag for affected tickers
3. Verify ticker exists in VCI system

## Examples

### Daily Incremental Update
Run this command daily to keep hourly data up-to-date:
```bash
./target/release/aipriceaction pull --intervals hourly --resume-days 3
```

### Weekly Full Check
Run weekly to catch any missing data:
```bash
./target/release/aipriceaction pull --intervals hourly --resume-days 7
```

### Background Sync
Run in background and log output:
```bash
nohup ./target/release/aipriceaction pull --intervals hourly > hourly_sync.log 2>&1 &
```

Monitor progress:
```bash
tail -f hourly_sync.log
```

### Sync Specific Tickers
Use debug mode or modify ticker_group.json temporarily:
```bash
# Debug mode uses: VNINDEX, VIC, VCB
./target/release/aipriceaction pull --intervals hourly --debug
```

## Integration with Daily Sync

### Sync All Timeframes
```bash
./target/release/aipriceaction pull --intervals all
```

This syncs daily, hourly, and minute data in sequence.

### Selective Sync
```bash
# Daily and hourly only
./target/release/aipriceaction pull --intervals daily,hourly

# Just hourly
./target/release/aipriceaction pull --intervals hourly
```

## Data Quality

### Validation Checks
The system performs these validations:
- ✅ Timestamp parsing (3 formats supported)
- ✅ Numeric field validation
- ✅ Duplicate detection (merging removes duplicates)
- ✅ Chronological ordering

### Known Limitations
1. **Timezone**: All timestamps stored in UTC
2. **Gaps**: VCI API may have data gaps (holidays, trading halts)
3. **Adjustments**: Dividend adjustments detected but not automatically applied to historical data

## Best Practices

1. **Regular Updates**: Run daily with `--resume-days 3`
2. **Monitoring**: Check logs for failures
3. **Backups**: Keep backups before running --full
4. **Verification**: Spot-check CSV files periodically

## API Limits

- **Rate limit**: 60 calls per minute
- **Concurrent requests**: Sequential processing (no parallelization)
- **Retry logic**: 3 retries with exponential backoff
- **Timeouts**: 30 seconds per request

## Future Enhancements

Potential improvements:
- [ ] Parallel dividend checks
- [ ] Skip dividend checks for indices
- [ ] Configurable dividend sleep time
- [ ] Resume from failed ticker
- [ ] Progress persistence
