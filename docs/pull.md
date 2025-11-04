# Pull Command

## Overview

The `pull` command fetches the latest market data from the VCI (Vietcap) API and updates the local `market_data/` directory. It supports smart incremental updates, dividend detection, and multiple data intervals (daily, hourly, minute).

## Usage

```bash
# Pull latest data (default: all intervals, resume mode)
./target/release/aipriceaction pull

# Pull specific interval only
./target/release/aipriceaction pull --intervals daily

# Pull multiple intervals
./target/release/aipriceaction pull --intervals daily,hourly

# Force full download from 2015-01-05
./target/release/aipriceaction pull --full

# Customize resume period (last 7 days instead of 30)
./target/release/aipriceaction pull --resume-days 7

# Custom start date for full downloads
./target/release/aipriceaction pull --full --start-date 2020-01-01
```

## Command Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--intervals` | `-i` | `all` | Intervals to sync: `all`, `daily`, `hourly`, `minute`, or comma-separated (e.g., `daily,hourly`) |
| `--full` | - | `false` | Force full download from start-date (disable resume mode) |
| `--resume-days` | - | `30` | Number of recent days to fetch in resume mode |
| `--start-date` | - | `2015-01-05` | Start date for historical data (YYYY-MM-DD) |

## How It Works

### 1. Ticker Categorization

The command pre-scans all tickers to determine their data needs:

- **Resume Tickers**: Have sufficient existing data (>5 rows), only fetch recent updates
- **Full History Tickers**: New or insufficient data, need complete download

```
🔍 Pre-scanning 290 tickers to categorize data needs for 1D...
   ✅ VNINDEX: 2,707 rows - can use resume mode
   ✅ VIC: 2,707 rows - can use resume mode
   🆕 AAA: No existing file - needs full history
   📉 BBB: Only 3 rows - needs full history

📊 Categorization results:
   Resume mode tickers: 285
   Full history tickers: 5
```

### 2. Batch Fetching Strategy

**For Daily Data:**
- Uses VCI batch API for efficiency (10 tickers per request)
- Resume tickers: fetch last 30 days (configurable)
- Full history tickers: fetch from start-date to today

**For Hourly/Minute Data:**
- Uses individual requests with chunking
- Hourly: Split by year (e.g., 2015, 2016, ..., 2025)
- Minute: Split by month (e.g., 2015-01, 2015-02, ...)

### 3. Dividend Detection

For resume tickers, the command checks for dividend adjustments:

1. Downloads last 60 days from API
2. Compares with existing data (3 weeks ago to 1 week ago window)
3. Checks price ratios
4. If ratio > 1.02 (2% difference) → dividend detected

**When dividend detected:**
- Re-downloads full history from start-date
- Ensures all historical prices are adjusted correctly

```
[253/290] VCB
   - Checking for dividend adjustments...
   - DEBUG: Enhanced dividend check: downloading 2025-09-04 to 2025-11-04
   - DEBUG: Recent window: 15 rows, Existing window: 15 rows
   - DEBUG: Date 2025-10-15: existing=98500, recent=97000, ratio=1.0155
   - 💰 DIVIDEND DETECTED for VCB on 2025-10-28: ratio=1.0250
   💰 Dividend detected, re-downloading full history...
   - Downloading full history from 2015-01-05 to 2025-11-04 using VCI [1D]...
```

### 4. Smart Data Merging

For resume tickers without dividends:

1. Load existing data
2. Find latest date in existing data
3. Remove rows >= latest date (to update last row)
4. Append all new data from API
5. Sort by time

**Why update last row?**
- Intraday data may be incomplete
- API provides final/corrected values

```
   📝 No dividend, merging with existing data...
   - DEBUG: Existing data has 2,707 rows, new data has 30 rows
   - DEBUG: Latest existing date: 2025-10-04
   - DEBUG: Adding 31 new/updated rows
   - DEBUG: Final merged data has 2,708 rows
```

### 5. Chunking for Large Datasets

**Hourly Data (1H):**
```
   - Starting chunked hourly download for VCB
   - Downloading 2015 chunk: 2015-01-05 to 2015-12-31
     - Downloaded 1,638 records for 2015
   - Downloading 2016 chunk: 2016-01-01 to 2016-12-31
     - Downloaded 1,652 records for 2016
   ...
   - Combined 17,842 total records from 11 yearly chunks
```

**Minute Data (1m):**
```
   - Starting chunked minute download for VCB
   - Downloading 2015-01 chunk: 2015-01-05 to 2015-01-31
     - Downloaded 8,235 records for 2015-01
   - Downloading 2015-02 chunk: 2015-02-01 to 2015-02-28
     - Downloaded 7,890 records for 2015-02
   ...
   - Combined 215,478 total records from 130 monthly chunks
```

### 6. Progress Tracking

Real-time progress with ETA calculation:

```
[123/290] VCB | 2.3s | Elapsed: 5.2min | ETA: 8.7min
```

- **Current/Total**: Current ticker number out of total
- **Ticker time**: Time spent on current ticker
- **Elapsed**: Total time since start
- **ETA**: Estimated time remaining (based on average time per ticker)

## Directory Structure

Data is saved in **ticker-first** structure:

```
market_data/
├── VNINDEX/
│   ├── daily.csv      # Daily OHLCV data
│   ├── 1h.csv         # Hourly OHLCV data
│   └── 1m.csv         # Minute OHLCV data
├── VIC/
│   ├── daily.csv
│   ├── 1h.csv
│   └── 1m.csv
├── FPT/
│   ├── daily.csv
│   ├── 1h.csv
│   └── 1m.csv
```

### CSV Format

All CSV files use the same format:

```csv
ticker,time,open,high,low,close,volume
VCB,2024-01-01,23.1,23.5,23.0,23.2,1000000
VCB,2024-01-02,23.2,23.8,23.1,23.6,1200000
```

**Important Price Format:**
- **Stock tickers** (VCB, FPT, HPG, etc.): Divided by 1000
  - API returns: `23200` → CSV stores: `23.2`
- **Market indices** (VNINDEX, VN30): No scaling
  - API returns: `1250.5` → CSV stores: `1250.5`

## Examples

### First Run (Fresh Start)

```bash
$ rm -rf market_data/  # Clean slate
$ ./target/release/aipriceaction pull --intervals daily

🚀 Starting data sync: 290 tickers, 1 intervals
📅 Date range: 2015-01-05 to 2025-11-04
📊 Mode: RESUME (incremental)

==================================================================
📊 Interval: 1D (Daily)
==================================================================

🔍 Pre-scanning 290 tickers to categorize data needs for 1D...
   🆕 VNINDEX: No existing file - needs full history
   🆕 VN30: No existing file - needs full history
   🆕 AAA: No existing file - needs full history
   ...

📊 Categorization results:
   Resume mode tickers: 0
   Full history tickers: 290

🚀 Processing 290 tickers needing full history...

--- Batch 1/29: 10 tickers ---
Tickers: VNINDEX, VN30, AAA, AAM, AAS, AAT, ABB, ABC, ABI, ABT
   ✅ Batch success: VNINDEX (2707 records)
   ✅ Batch success: VN30 (2707 records)
   ...

==================================================================
[001/290] VNINDEX
==================================================================
   ✅ Using full history batch result for VNINDEX
   - Data saved to: market_data/VNINDEX/daily.csv
   ✅ SUCCESS: VNINDEX - 2707 records saved

[001/290] VNINDEX | 1.2s | Elapsed: 0.0min | ETA: 5.8min
...

✨ Daily sync complete: 290 tickers in 6.2min

==================================================================
🎉 SYNC COMPLETE!
==================================================================
⏰ Finished at: 2025-11-04 19:50:00
⏱️  Total execution time: 6.23 minutes (373.8 seconds)
📊 Results: ✅290 successful, ❌0 failed, 📝0 updated, ⏭️ 0 skipped
📁 Files written: 290
📈 Total records: 785,030

✅ Data sync completed successfully!
```

### Subsequent Runs (Resume Mode)

```bash
$ ./target/release/aipriceaction pull --intervals daily

🚀 Starting data sync: 290 tickers, 1 intervals
📅 Date range: 2015-01-05 to 2025-11-04
📊 Mode: RESUME (incremental)

==================================================================
📊 Interval: 1D (Daily)
==================================================================

🔍 Pre-scanning 290 tickers to categorize data needs for 1D...
   ✅ VNINDEX: 2,707 rows - can use resume mode
   ✅ VN30: 2,707 rows - can use resume mode
   ✅ AAA: 2,707 rows - can use resume mode
   ...

📊 Categorization results:
   Resume mode tickers: 290
   Full history tickers: 0

⚡ Batch processing 290 tickers using resume mode...

--- Batch 1/29: 10 tickers ---
Tickers: VNINDEX, VN30, AAA, AAM, AAS, AAT, ABB, ABC, ABI, ABT
   ✅ Batch success: VNINDEX (30 records)
   ✅ Batch success: VN30 (30 records)
   ...

==================================================================
[001/290] VNINDEX
==================================================================
   ✅ Using batch result for VNINDEX
   - Checking for dividend adjustments...
   - DEBUG: Enhanced dividend check: downloading 2025-09-04 to 2025-11-04
   - DEBUG: Recent window: 15 rows, Existing window: 15 rows
   - No dividend detected for VNINDEX
   📝 No dividend, merging with existing data...
   - DEBUG: Existing data has 2,707 rows, new data has 30 rows
   - DEBUG: Adding 1 new/updated rows
   - Data saved to: market_data/VNINDEX/daily.csv
   ✅ SUCCESS: VNINDEX - 2708 records saved

[001/290] VNINDEX | 1.5s | Elapsed: 0.0min | ETA: 7.2min
...

✨ Daily sync complete: 290 tickers in 1.2min

⏱️  Total execution time: 1.18 minutes (70.8 seconds)
📊 Results: ✅290 successful, ❌0 failed, 📝290 updated, ⏭️ 0 skipped
```

### All Intervals (Daily + Hourly + Minute)

```bash
$ ./target/release/aipriceaction pull --intervals all

🚀 Starting data sync: 290 tickers, 3 intervals
📅 Date range: 2015-01-05 to 2025-11-04
📊 Mode: RESUME (incremental)

==================================================================
📊 Interval: 1D (Daily)
==================================================================
... (processes all daily data) ...
✨ Daily sync complete: 290 tickers in 1.2min

==================================================================
📊 Interval: 1H (Hourly)
==================================================================
... (processes all hourly data with chunking) ...
✨ Hourly sync complete: 290 tickers in 45.3min

==================================================================
📊 Interval: 1m (Minute)
==================================================================
... (processes all minute data with chunking) ...
✨ Minute sync complete: 290 tickers in 3.2hours

⏱️  Total execution time: 4.23 hours
```

## Performance

### Resume Mode (Last 30 Days)
- **Daily data**: ~1-2 minutes for 290 tickers
- **Batch API**: 10 tickers per request
- **Rate limiting**: 2s between batches
- **Average**: ~0.2-0.4s per ticker

### Full Download
- **Daily data**: ~6-8 minutes for 290 tickers
- **Batch size**: 2 tickers per request (more reliable)
- **Rate limiting**: 2s between batches
- **Average**: ~1.5-2s per ticker

### Hourly Data (Chunked)
- **Per ticker**: ~30-60s (11 yearly chunks)
- **Total (290 tickers)**: ~2.5-4 hours
- **Rate limiting**: 2s per chunk

### Minute Data (Chunked)
- **Per ticker**: ~6-10 minutes (130 monthly chunks)
- **Total (290 tickers)**: ~30-50 hours
- **Rate limiting**: 3s per chunk

## Rate Limiting

VCI client has built-in rate limiting:
- **API limit**: 30 calls per minute
- **Batch delays**: 2s between batches (daily)
- **Chunk delays**: 2s for hourly, 3s for minute
- **Retry logic**: Exponential backoff (max 5 retries)

## Error Handling

### Network Errors
```
   ❌ Batch request error: Network error: connection timeout
   🔄 Batch not available for VCB, fetching individually...
   ✅ Individual VCI success for VCB
```

### Missing Data
```
   ❌ FAILED: XYZ - Not found: No data returned for XYZ
```

### Parse Errors
```
   ❌ FAILED: ABC - Parse error: Invalid date format
```

## Troubleshooting

### Slow Performance

If sync is slower than expected:

```bash
# Check if running in full mode unintentionally
./target/release/aipriceaction pull --intervals daily  # Should be fast (resume mode)

# Force resume mode explicitly
./target/release/aipriceaction pull --intervals daily --resume-days 7
```

### Data Not Updating

If latest data isn't appearing:

```bash
# Check existing data
./target/release/aipriceaction status

# Force full re-download for specific ticker
rm -rf market_data/VCB/
./target/release/aipriceaction pull --intervals daily
```

### Dividend Detection Not Working

If you know a dividend occurred but wasn't detected:

```bash
# Delete the ticker directory to force full re-download
rm -rf market_data/VCB/
./target/release/aipriceaction pull --intervals daily
```

### API Rate Limiting

If you hit rate limits:

```bash
# Reduce batch size by using full mode (batch_size=2)
./target/release/aipriceaction pull --intervals daily --full

# Or just wait - the command has automatic retry with backoff
```

## Best Practices

### Daily Workflow

Run daily after market close (15:00 ICT):

```bash
# Quick daily update (1-2 minutes)
./target/release/aipriceaction pull --intervals daily
```

### Weekly Workflow

Update hourly data once a week:

```bash
# Daily + hourly update (~1 hour)
./target/release/aipriceaction pull --intervals daily,hourly
```

### Monthly Workflow

Full sync including minute data once a month:

```bash
# Complete data sync (several hours)
./target/release/aipriceaction pull --intervals all --resume-days 60
```

### After System Reinstall

```bash
# Fresh full download from 2015
./target/release/aipriceaction pull --intervals daily --full
```

## Integration with import-legacy

The `pull` command uses the **new ticker-first structure**, while `import-legacy` uses the **old structure**. They're compatible:

1. **First time**: Run `import-legacy` to get historical data
2. **Daily updates**: Run `pull` to fetch latest data
3. **Data structure**: Both write to `market_data/{TICKER}/`

The `pull` command will:
- Detect existing data from `import-legacy`
- Categorize as resume tickers (if >5 rows)
- Only fetch recent updates
- Merge smartly with existing data

## Related Commands

- `status` - View imported data summary
- `import-legacy` - Import historical data from reference project
- `serve` - Start REST API server (TODO)

## Source Code

- Implementation: `src/services/data_sync.rs`, `src/services/ticker_fetcher.rs`
- Configuration: `src/models/sync_config.rs`
- Command: `src/commands/pull.rs`
- Tests: (TODO)

---

**Last Updated**: 2025-11-04
**Version**: 0.1.0
