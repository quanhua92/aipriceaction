# CSV Enhancement with Technical Indicators

## Overview

The CSV enhancement feature automatically adds technical indicator columns to raw OHLCV data **during sync**, transforming data into comprehensive 20-column files with moving averages, MA scores, and percentage change indicators in a **single write operation**.

## Quick Start

Enhancement happens automatically during every `pull` command (single-phase write):

```bash
# Sync and enhance daily data (single write)
cargo run -- pull --intervals daily

# Output:
# ✅ Data sync completed successfully!
# 💡 Note: CSV files are already enhanced with technical indicators (single-phase write)
```

## CSV Format

### Enhanced Format (20 columns)
```csv
ticker,time,open,high,low,close,volume,ma10,ma20,ma50,ma100,ma200,ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,close_changed,volume_changed,total_money_changed
VCB,2025-11-04,59200.00,60400.00,59100.00,60100.00,2952400,59840.00,61160.00,63017.80,64500.00,67800.00,0.4345,-1.7332,-4.6301,-6.8201,-11.3445,1.5234,-10.2341,8955400000
```

**Column Breakdown:**
- **Columns 1-7**: Basic OHLCV data (ticker, time, open, high, low, close, volume)
- **Columns 8-12**: Moving averages (ma10, ma20, ma50, ma100, ma200)
- **Columns 13-17**: MA scores (percentage deviation from MAs)
- **Columns 18-19**: Percentage changes (close_changed, volume_changed)
- **Column 20**: Money flow indicator (total_money_changed in VND)

## Technical Indicators

### 1. Moving Averages (MA10, MA20, MA50)

**Description**: Simple moving averages over 10, 20, and 50 periods.

**Formula**:
```
MA_N[i] = (close[i] + close[i-1] + ... + close[i-N+1]) / N
```

**Example**:
```
MA10 = (60100 + 59300 + 59600 + ... + 60700) / 10 = 59840.00
```

**Properties**:
- Calculated for all tickers (including indices like VNINDEX, VN30)
- Empty for first N-1 records (not enough data)
- Rounded to 2 decimal places

### 2. MA Scores (ma10_score, ma20_score, ma50_score)

**Description**: Percentage deviation of current price from its moving average.

**Formula**:
```
MA_Score = ((close - MA) / MA) × 100
```

**Example**:
```
ma10_score = ((60100 - 59840) / 59840) × 100 = +0.4345%
ma20_score = ((60100 - 61160) / 61160) × 100 = -1.7332%
```

**Interpretation**:
- **Positive**: Price is above the moving average (bullish signal)
- **Negative**: Price is below the moving average (bearish signal)
- **Magnitude**: How far price has deviated from average

**Properties**:
- Calculated for all tickers (including indices)
- Empty when MA is not available
- Rounded to 4 decimal places

### 3. Close Changed

**Description**: Percentage change in closing price from the previous row.

**Formula**:
```
close_changed[i] = ((close[i] - close[i-1]) / close[i-1]) × 100
```

**Example**:
```
Previous close: 59200
Current close: 60100
close_changed = ((60100 - 59200) / 59200) × 100 = +1.5234%
```

**Interpretation**:
- **Positive**: Price increased from previous period
- **Negative**: Price decreased from previous period
- **Magnitude**: Percentage change

**Properties**:
- Calculated for all tickers
- Empty for first record (no previous data)
- Rounded to 4 decimal places

### 4. Volume Changed

**Description**: Percentage change in volume from the previous row.

**Formula**:
```
volume_changed[i] = ((volume[i] - volume[i-1]) / volume[i-1]) × 100
```

**Example**:
```
Previous volume: 3290000
Current volume: 2952400
volume_changed = ((2952400 - 3290000) / 3290000) × 100 = -10.2341%
```

**Interpretation**:
- **Positive**: Volume increased from previous period
- **Negative**: Volume decreased from previous period
- **Magnitude**: Percentage change in trading activity

**Properties**:
- Calculated for all tickers
- Empty for first record (no previous data)
- Rounded to 4 decimal places

## Index vs Stock Tickers

### All Tickers (Stocks + Indices)
✅ **Calculated**:
- MA10, MA20, MA50
- ma10_score, ma20_score, ma50_score
- close_changed, volume_changed

**Example (Stock)**:
```csv
VCB,2025-11-04,59200.00,60400.00,59100.00,60100.00,2952400,59840.00,61160.00,63017.80,0.4345,-1.7332,-4.6301,1.5234,-10.2341
```

**Example (Index)**:
```csv
VNINDEX,2025-11-04,1618.02,1658.93,1600.56,1651.98,1198941757,1664.58,1694.48,1675.38,-0.7570,-2.5084,-1.3967,2.0123,5.3421
```

## Implementation Details

### Architecture (NEW SINGLE-PHASE FLOW)

```
┌─────────────────┐
│  Pull Command   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Sync OHLCV    │  ← Download raw data from VCI API
│   (282 tickers) │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  OhlcvData → CSV Enhancer (Memory)  │  ← IN-MEMORY ENHANCEMENT
│                                      │
│  1. Calculate MAs for all tickers   │
│  2. Calculate MA scores             │
│  3. Calculate close_changed         │
│  4. Calculate volume_changed        │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  Smart Save (Single Write)          │
│                                      │
│  • File locking (no race conditions)│
│  • Truncate to cutoff date          │
│  • Append enhanced data (11 cols)   │
└─────────────────────────────────────┘
```

**Key Improvements:**
- ✅ Single write operation (no double-write)
- ✅ Data passed directly in memory (OhlcvData → StockData)
- ✅ API can read CSV safely during writes (file locking)
- ✅ Simplified indicators (removed cross-ticker complexity)

### File Structure

```
market_data/
├── VNINDEX/
│   ├── 1D.csv     (20 columns)
│   ├── 1h.csv     (20 columns)
│   └── 1m.csv     (20 columns)
├── VCB/
│   ├── 1D.csv     (20 columns)
│   ├── 1h.csv     (20 columns)
│   └── 1m.csv     (20 columns)
└── ...
```

### Performance

**Daily Interval (283 tickers, ~685,000 records)**:
- Sync + Enhancement: ~166 seconds (2.77 minutes)
- Enhancement is **integrated** into sync (single-phase)
- **No separate enhancement overhead**

### Processing Strategy

**Current: Integrated Single-Phase**
- Fetches data from VCI API
- Enhances in-memory immediately
- Writes enhanced CSV once
- File locking prevents race conditions

**Advantages**:
- Fast and efficient (single write)
- No temporary files
- API-safe (tickers API can read during writes)
- Simple and reliable

## Code Examples

### Using Enhanced Data

```rust
use csv::Reader;

// Read enhanced CSV
let mut reader = Reader::from_path("market_data/VCB/1D.csv")?;

for result in reader.records() {
    let record = result?;

    let ticker = &record[0];
    let time = &record[1];
    let close: f64 = record[5].parse()?;
    let ma10: Option<f64> = record[7].parse().ok();
    let ma10_score: Option<f64> = record[10].parse().ok();
    let close_changed: Option<f64> = record[13].parse().ok();

    if let Some(score) = ma10_score {
        if score > 5.0 {
            println!("{} on {} is {:.2}% above MA10", ticker, time, score);
        }
    }

    if let Some(change) = close_changed {
        if change > 3.0 {
            println!("{} on {} increased {:.2}% from previous day", ticker, time, change);
        }
    }
}
```

### Manual Enhancement (Legacy Function)

For workers that don't have direct access to OhlcvData:

```rust
use aipriceaction::services::csv_enhancer;
use aipriceaction::models::Interval;
use std::path::Path;

// Enhance specific interval (reads CSV, enhances, writes back)
let market_data_dir = Path::new("market_data");
let stats = csv_enhancer::enhance_interval(Interval::Daily, market_data_dir)?;

println!("Enhanced {} tickers, {} records in {:.2}s",
    stats.tickers,
    stats.records,
    stats.duration.as_secs_f64()
);
```

## Troubleshooting

### Issue: Empty MA columns

**Symptom**:
```csv
VCB,2015-01-05,9320.00,9430.00,9230.00,9380.00,310010,,,,,,,1.2345,5.6789
```

**Cause**: Not enough historical data (need 10/20/50 records for MA10/20/50)

**Solution**: This is expected for early records. MA values appear after N periods.

### Issue: Empty close_changed/volume_changed

**Symptom**: First row has empty change indicators

**Cause**: No previous data to compare against

**Solution**: This is expected. Change indicators start from the second row.

## Technical Reference

### Building Block Functions

All calculations are implemented in `src/models/indicators.rs`:

```rust
// Moving average
pub fn calculate_sma(closes: &[f64], period: usize) -> Vec<f64>

// MA score
pub fn calculate_ma_score(close: f64, ma: f64) -> f64
```

### Enhancement Pipeline

Main orchestration in `src/services/csv_enhancer.rs`:

```rust
// Direct data enhancement (NEW)
pub fn enhance_data(
    data: HashMap<String, Vec<OhlcvData>>,
) -> HashMap<String, Vec<StockData>>

// Smart save with file locking
pub fn save_enhanced_csv(
    ticker: &str,
    data: &[StockData],
    interval: Interval,
    cutoff_date: DateTime<Utc>,
) -> Result<(), Error>

// Legacy function (for workers)
pub fn enhance_interval(
    interval: Interval,
    market_data_dir: &Path,
) -> Result<EnhancementStats, Error>
```

**Steps**:
1. `enhance_data()` - In-memory enhancement of OhlcvData → StockData
2. `save_enhanced_csv()` - Smart save with file locking and cutoff strategy

## Related Documentation

- [Pull Command](pull.md) - Syncing OHLCV data with integrated enhancement
- [Import Legacy](import-legacy.md) - Importing historical data

## Version History

- **v0.2.0** (2025-11-06): **BREAKING CHANGE** - Single-phase enhancement
  - Removed: `money_flow`, `dollar_flow`, `trend_score` (cross-ticker complexity)
  - Added: `close_changed`, `volume_changed` (simple per-row indicators)
  - Changed: 16 columns → 20 columns
  - Changed: Double-write → Single-write
  - Changed: Data passed in memory (no intermediate CSV)
- **v0.1.0** (2025-11-05): Initial implementation
  - 9 technical indicators
  - Per-ticker CSV enhancement
  - 16-column format
