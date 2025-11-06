# Import Legacy Command

## Overview

The `import-legacy` command intelligently imports historical market data from the legacy project structure into the new ticker-first format. It includes smart validation to skip unchanged files, dramatically reducing import time on subsequent runs.

## Usage

```bash
# Import from default location
./target/release/aipriceaction import-legacy

# Import from custom location
./target/release/aipriceaction import-legacy --source /path/to/reference/data
```

## What It Does

1. **Reads legacy data** from three directories:
   - `market_data/` → Daily OHLCV with old indicators (16 columns, converted to 15)
   - `market_data_hour/` → Hourly OHLCV (7 columns)
   - `market_data_minutes/` → Minute OHLCV (7 columns)

2. **Enhances data in-memory** with technical indicators (single-phase write)

3. **Converts to new structure**:
   ```
   market_data/
   ├── VCB/
   │   ├── 1D.csv     (11 columns with indicators)
   │   ├── 1h.csv     (11 columns with indicators)
   │   └── 1m.csv     (11 columns with indicators)
   ├── FPT/
   │   ├── 1D.csv
   │   ├── 1h.csv
   │   └── 1m.csv
   └── ...
   ```

4. **Applies price scaling** (CRITICAL for Vietnamese market):
   - **Stock tickers** (VCB, FPT, HPG, etc.): Multiply by 1000
     - Source: `23.2` → Destination: `23200.0`
   - **Market indices** (VNINDEX, VN30): No scaling
     - Source: `1250.5` → Destination: `1250.5`

5. **Validates existing data**:
   - Compares last 10 rows with source
   - Skips import if data matches
   - Reimports if changes detected (e.g., dividend adjustments)

## Smart Import Features

### 🚀 Fast Incremental Updates

When data hasn't changed:
```
⏱️  Total time: 1.02s (290 tickers, 866 files)
   Average: 0.004s per ticker
```

### 🔍 Change Detection

The import validates data by comparing the last 10 rows from each file:

| Scenario | Behavior |
|----------|----------|
| Data matches | ⏭️ Skip (data up to date) |
| Data differs | 🔄 Reimport (data changed) |
| File missing | ✅ Import from scratch |

**Why last 10 rows?**
- Recent data most likely to change (dividend adjustments)
- Fast validation without reading entire file
- Catches most data quality issues

### 📊 Clear Progress Reporting

```
[1/290] AAA: ⏭️  Skipped 3 files (data up to date)
[2/290] ACB: 🔄 Reimported 1, ⏭️  Skipped 2 files
[3/290] ACL: ✅ Imported 3 files

✨ Import complete!
   ✅ Success: 290 tickers
   ⏭️  Skipped: 863 files (already up to date)
   🔄 Reimported: 3 files (data changed)

⏱️  Total time: 1.11s (290 tickers)
   Average: 0.004s per ticker
```

## Price Format Rules

### Stock Tickers

Vietnamese stock prices in CSV are stored in "short format" (divided by 1000):

```csv
# Source (legacy format)
VCB,2024-01-01,23.1,23.5,23.0,23.2,1000000

# Destination (new format)
VCB,2024-01-01,23100.0,23500.0,23000.0,23200.0,1000000
```

**Why multiply by 1000?**
- APIs return full format: `23200 VND`
- Legacy CSV stores short format: `23.2`
- We standardize on full format for consistency

### Market Indices (VNINDEX, VN30)

Index prices are NOT scaled:

```csv
# Source (legacy format)
VNINDEX,2024-01-01,1250.5,1260.3,1245.2,1255.8,0

# Destination (new format) - SAME VALUES
VNINDEX,2024-01-01,1250.5,1260.3,1245.2,1255.8,0
```

## Data Validation Details

### Validation Algorithm

For each file:
1. Check if destination exists → No? **Import**
2. Check if source has enough rows → No? **Reimport**
3. Compare last 10 rows:
   - Extract OHLC prices (columns 2-5)
   - Apply scaling factor (1000x for stocks, 1x for indices)
   - Compare with 0.01 tolerance for floating point
   - Compare volume (column 6) exactly
4. All match? **Skip** | Any differ? **Reimport**

### Edge Cases Handled

| Case | Behavior |
|------|----------|
| Empty source file | Skip (no data) |
| Empty destination file | Reimport |
| Malformed CSV | Error (stops import) |
| Fewer than 10 rows | Compare all available rows |
| File read error | Assume changed, reimport |

## Examples

### First Import (Fresh Start)

```bash
$ rm -rf market_data/  # Clean slate
$ ./target/release/aipriceaction import-legacy

🚀 Starting smart import from: ./references/aipriceaction-data
⏱️  Start time: 19:00:00

📊 Found 290 tickers to process

[1/290] AAA: ✅ Imported 3 files
[2/290] ACB: ✅ Imported 3 files
...
[290/290] VN30: ✅ Imported 3 files

✨ Import complete!
   ✅ Success: 290 tickers
   ⏭️  Skipped: 0 files (already up to date)
   🔄 Reimported: 0 files (data changed)

⏱️  Total time: 45.32s (290 tickers)
   Average: 0.156s per ticker
```

### Subsequent Runs (Data Unchanged)

```bash
$ ./target/release/aipriceaction import-legacy

🚀 Starting smart import from: ./references/aipriceaction-data
⏱️  Start time: 19:01:00

📊 Found 290 tickers to process

[1/290] AAA: ⏭️  Skipped 3 files (data up to date)
[2/290] ACB: ⏭️  Skipped 3 files (data up to date)
...
[290/290] VN30: ⏭️  Skipped 3 files (data up to date)

✨ Import complete!
   ✅ Success: 290 tickers
   ⏭️  Skipped: 866 files (already up to date)
   🔄 Reimported: 0 files (data changed)

⏱️  Total time: 1.02s (290 tickers)
   Average: 0.004s per ticker
```

### Detecting Changes (Dividend Adjustment)

```bash
$ ./target/release/aipriceaction import-legacy

🚀 Starting smart import from: ./references/aipriceaction-data
⏱️  Start time: 19:02:00

📊 Found 290 tickers to process

[1/290] AAA: ⏭️  Skipped 3 files (data up to date)
...
[253/290] VCB: 🔄 Reimported 1, ⏭️  Skipped 2 files  # Daily data changed!
...
[290/290] VN30: ⏭️  Skipped 3 files (data up to date)

✨ Import complete!
   ✅ Success: 290 tickers
   ⏭️  Skipped: 865 files (already up to date)
   🔄 Reimported: 1 files (data changed)

⏱️  Total time: 1.15s (290 tickers)
   Average: 0.004s per ticker
```

## When to Run Import

### Regular Schedule
- **Daily**: To get latest trading data
- **After market close**: Vietnamese market closes at 15:00 ICT

### Special Circumstances
- **After dividend announcements**: Prices may be adjusted retroactively
- **Data quality issues**: If you suspect incorrect data
- **New ticker added**: When ticker_group.json is updated
- **Fresh setup**: Initial installation or after data loss

## Troubleshooting

### Import is Slow

If import takes longer than expected:

```bash
# Check if data actually changed
ls -lt market_data/VCB/  # Check file timestamps

# Force full reimport by deleting existing data
rm -rf market_data/
./target/release/aipriceaction import-legacy
```

### Reimport Not Detected

If you know data changed but import skips:

```bash
# Delete specific ticker to force reimport
rm -rf market_data/VCB/
./target/release/aipriceaction import-legacy
```

### Price Format Issues

If prices look wrong (100x or 1000x off):

1. Check ticker type:
   ```bash
   # Is it a stock or index?
   grep "VCB" ticker_group.json   # Stock (should be scaled)
   grep "VNINDEX" ticker_group.json  # Index (no scaling)
   ```

2. Verify source data:
   ```bash
   head -n 3 references/aipriceaction-data/market_data/VCB.csv
   ```

3. Verify destination:
   ```bash
   head -n 3 market_data/VCB/daily.csv
   ```

### Missing Tickers

If some tickers aren't imported:

```bash
# Check source files exist
ls references/aipriceaction-data/market_data/ | wc -l

# Check ticker_group.json
cat ticker_group.json | jq '.groups[].tickers | .[]' | sort | uniq | wc -l

# Run with error details
./target/release/aipriceaction import-legacy 2>&1 | grep "❌"
```

## Technical Details

### CSV Format (NEW 11-COLUMN FORMAT)

All intervals use the same enhanced format with technical indicators:

```csv
ticker,time,open,high,low,close,volume,ma10,ma20,ma50,ma10_score,ma20_score,ma50_score,close_changed,volume_changed
VCB,2024-01-01,23100.0,23500.0,23000.0,23200.0,1000000,22800.0,22500.0,22000.0,1.75,3.11,5.45,1.52,-10.23
VCB,2024-01-01 09:00:00,23100.0,23200.0,23000.0,23150.0,100000,22850.0,22550.0,22050.0,1.34,2.66,4.99,0.43,5.21
```

**Columns:**
- 1-7: OHLCV data (ticker, time, open, high, low, close, volume)
- 8-10: Moving averages (ma10, ma20, ma50)
- 11-13: MA scores (percentage deviation)
- 14-15: Percentage changes (close_changed, volume_changed)

### Performance Characteristics

| Operation | Time (290 tickers) | Notes |
|-----------|-------------------|-------|
| First import | ~45s | Parsing & converting all files |
| Validation only | ~1s | Comparing last 10 rows |
| Mixed (3 reimports) | ~1.1s | Only changed files processed |
| Per ticker average | 0.004s | When data unchanged |

### Memory Usage

- **Peak memory**: ~50MB
- **Per file overhead**: ~10KB for validation
- **Temporary buffers**: Released after each ticker

## Related Commands

- `status` - View imported data summary
- `pull` - Fetch latest data from API (TODO)
- `serve` - Start REST API server (TODO)

## Source Code

- Implementation: `src/services/importer.rs`
- CSV parsing: `src/services/csv_parser.rs`
- Tests: `src/services/importer.rs` (lines 258-521)

## Testing

Run the test suite:

```bash
# All tests
cargo test

# Just importer tests
cargo test services::importer::tests

# Specific test
cargo test test_is_data_up_to_date_files_match
```

11 tests cover:
- Price scaling (stocks vs indices)
- Change detection (prices, volume)
- Edge cases (empty files, missing data)
- Validation behavior (last N rows only)

---

**Last Updated**: 2024-11-04
**Version**: 0.1.0
