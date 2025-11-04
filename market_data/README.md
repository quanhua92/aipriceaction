# Market Data Directory Structure

This directory contains historical stock market data organized by ticker symbol.

## Structure

```
market_data/
└── {TICKER}/
    ├── daily.csv    # Daily OHLCV data
    ├── 1h.csv       # Hourly OHLCV data
    ├── 1m.csv       # 1-minute OHLCV data
    ├── 5m.csv       # 5-minute OHLCV data
    ├── 15m.csv      # 15-minute OHLCV data
    ├── 30m.csv      # 30-minute OHLCV data
    ├── weekly.csv   # Weekly OHLCV data
    └── monthly.csv  # Monthly OHLCV data
```

## Examples

```
market_data/VCB/daily.csv
market_data/FPT/1h.csv
market_data/VNINDEX/daily.csv
market_data/VN30/daily.csv
```

## Timeframe Files

| Timeframe | Filename | Description |
|-----------|----------|-------------|
| 1 minute | `1m.csv` | 1-minute candles |
| 5 minutes | `5m.csv` | 5-minute candles |
| 15 minutes | `15m.csv` | 15-minute candles |
| 30 minutes | `30m.csv` | 30-minute candles |
| 1 hour | `1h.csv` | Hourly candles |
| Daily | `daily.csv` | Daily candles |
| Weekly | `weekly.csv` | Weekly candles |
| Monthly | `monthly.csv` | Monthly candles |

## Usage in Code

```rust
use aipriceaction::models::Timeframe;

// Get path for VCB daily data
let path = Timeframe::Day1.get_data_path("VCB");
// Returns: "market_data/VCB/daily.csv"

// Get path for FPT hourly data
let path = Timeframe::Hour1.get_data_path("FPT");
// Returns: "market_data/FPT/1h.csv"
```

## CSV Format

All CSV files follow this format:

### Stock Tickers (VCB, FPT, HPG, etc.)
```csv
ticker,time,open,high,low,close,volume,ma10,ma20,ma50,ma10_score,ma20_score,ma50_score,money_flow,dollar_flow,trend_score
VCB,2025-01-10,23.2,23.7,22.6,23.7,4559900,22.5,21.8,20.3,5.33,8.72,16.75,...
```

**Note**: Prices in CSV are stored as `price/1000`. When loading, multiply by 1000.
- CSV: 23.2 → Memory: 23200.0

### Market Indices (VNINDEX, VN30)
```csv
ticker,time,open,high,low,close,volume,...
VNINDEX,2025-01-10,1250.5,1260.3,1245.2,1258.7,500000000,...
```

**Note**: Index prices are stored as-is. No conversion needed.

## Data Sources

Data is imported from:
- Reference project: `references/aipriceaction-data/market_data/`
- Proxy API responses
- External data providers

## Git Ignore

CSV files are not tracked by git (too large). Only the directory structure is tracked.
See `.gitignore` for details.
