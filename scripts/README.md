# Data Sync Scripts

Convenient scripts for syncing market data. Run from the project root directory.

## Available Scripts

### 📊 VN Stock Market Scripts

#### pull_daily.sh
Syncs daily data for all 283 tickers (last 1 day)
```bash
./scripts/pull_daily.sh
```
**Expected time**: ~3 seconds (50-ticker batches)
**Data volume**: 1 record/ticker = very light

#### pull_hourly.sh
Syncs hourly data for all 283 tickers (last 5 days)
```bash
./scripts/pull_hourly.sh
```
**Expected time**: ~4 seconds (20-ticker batches)
**Data volume**: ~30 records/ticker = moderate

#### pull_minute.sh
Syncs minute data for all 283 tickers (last 2 days)
```bash
./scripts/pull_minute.sh
```
**Expected time**: ~5 minutes (3-ticker batches)
**Data volume**: ~400 records/ticker = heavy (optimized to avoid API overload)

#### pull_all.sh
Syncs all intervals (daily, hourly, minute) in sequence
```bash
./scripts/pull_all.sh
```
**Expected time**: ~5-6 minutes total
**Uses smart interval-specific defaults (no parameters needed)**
**Breakdown**: Daily (3s) + Hourly (4s) + Minute (5min) = ~5min 7s

### 🌐 Cryptocurrency Scripts

#### get_top_crypto.py
Fetches unique cryptocurrency trading pairs from CoinGecko (free API)
```bash
source venv/bin/activate
python scripts/get_top_crypto.py [unique_count]
```
**Default unique_count**: 100 unique trading pairs
**Data source**: CoinGecko API (no registration required)
**Smart deduplication**:
- Fetches 200+ coins to ensure unique trading pairs after deduplication
- Removes duplicate Binance trading pairs (e.g., multiple coins mapping to same USDT pair)
- Uses identical mapping logic as fetch_all_crypto.py for consistency
**Output**:
- `top_crypto.json` - Top unique trading pairs by market cap
- `binance_mapping.json` - Unique symbol mappings for Binance trading pairs

#### fetch_all_crypto.py
Downloads historical data for all top cryptocurrencies from Binance Vision API
```bash
source venv/bin/activate
python scripts/fetch_all_crypto.py
```
**Date range**: Automatically detects latest available data (checks today, yesterday, today-2) and goes backwards to 2017-08-17 (Binance launch)
**Smart 404 handling**: For each symbol, stops at first HTTP 404 error (symbol-specific start date)
- Example: USDTUSD stops around 2025-11-17 when it gets 404s
- Example: BTCUSDT goes all the way back to 2017-08-17
**Data coverage**: Symbol-specific start dates to yesterday for all timeframes:
- 1-day (1D): Daily candles
- 1-hour (1H): Hourly candles
- 1-minute (1m): Minute candles
**Output**: ZIP files saved to `spot/daily/klines/{symbol}/{timeframe}/`
**Data format**: OHLCV with timestamps, volume, and trade statistics (compressed)
**Download method**: ZIP files from Binance Vision API with `.downloading` → `.zip` safety pattern
**Performance**: 4 parallel workers per symbol (sequential symbol processing)
**Resume capability**: Skips existing files, removes partial downloads automatically
**Progress tracking**: Real-time progress with HTTP response codes and file sizes
**Ctrl+C support**: Graceful shutdown with instant worker termination
**Logging**: Shows HTTP status codes, file sizes, and download URLs for every request

## Usage

### VN Stock Market Scripts
```bash
# Daily sync (recommended for daily cron jobs)
./scripts/pull_daily.sh

# Hourly sync (run daily or weekly)
./scripts/pull_hourly.sh

# Minute sync (run daily or weekly)
./scripts/pull_minute.sh

# Full sync (all intervals)
./scripts/pull_all.sh
```

### Cryptocurrency Scripts
```bash
# Step 1: Get top cryptocurrencies by market cap
source venv/bin/activate
python scripts/get_top_crypto.py 100

# Step 2: Download all historical data from Binance Vision API
python scripts/fetch_all_crypto.py
# Press Ctrl+C anytime to stop gracefully - will resume where left off

# Step 3: (Optional) Test with limited data first
# Creates top_crypto_test.json with only 3 symbols and 7 days
python -c "
import json
with open('top_crypto.json', 'r') as f: data = json.load(f)
data['data'] = data['data'][:3]
with open('top_crypto_test.json', 'w') as f: json.dump(data, f, indent=2)
print('Test file created with 3 symbols')
"
python scripts/fetch_all_crypto.py
```

## Features

### VN Stock Scripts
- ✅ Shows start/end timestamps
- ✅ Displays execution time
- ✅ Exit codes for automation
- ✅ Clear success/failure messages

### Cryptocurrency Scripts
- ✅ Uses true market cap rankings (CoinGecko API)
- ✅ Maps symbols to Binance trading pairs automatically
- ✅ Downloads 3 timeframes (1D, 1H, 1m) per symbol via Binance Vision API
- ✅ High-performance parallel processing (4 workers per timeframe + 2 concurrent symbols)
- ✅ ZIP-only downloads (no extraction) for maximum speed
- ✅ Safety pattern (.downloading → .zip) prevents corrupted files
- ✅ Auto-resume capability (skips existing, removes partial downloads)
- ✅ Stream downloading for memory efficiency (8KB chunks)
- ✅ Graceful Ctrl+C support with instant worker termination
- ✅ Comprehensive error handling and cleanup on failures
- ✅ Real-time progress tracking (every 50 completed downloads)
- ✅ Test mode support (limited symbols/dates for verification)
- ✅ No API registration required (CoinGecko free tier + Binance Vision)

## Scheduling with Cron

### VN Stock Market
```bash
# Daily sync at 6 PM (after market closes at 3:45 PM Vietnam time)
0 18 * * 1-5 cd /path/to/aipriceaction && ./scripts/pull_daily.sh >> logs/daily.log 2>&1

# Weekly full sync on Sunday at 2 AM
0 2 * * 0 cd /path/to/aipriceaction && ./scripts/pull_all.sh >> logs/weekly.log 2>&1
```

### Cryptocurrency
```bash
# Weekly crypto data update (Sunday 3 AM)
0 3 * * 0 cd /path/to/aipriceaction && source venv/bin/activate && python scripts/get_top_crypto.py 100 && python scripts/fetch_all_crypto.py >> logs/crypto.log 2>&1
```

## Data Sources

### VN Stock Market
- **VCI API**: Real-time Vietnamese stock market data
- **Coverage**: 283 tickers including VN30, banking, tech, and other sectors
- **Formats**: Enhanced CSV with 20 columns including technical indicators

### Cryptocurrency
- **CoinGecko API**: Market cap rankings and coin information (free tier)
- **Binance Vision API**: Historical OHLCV data via direct ZIP downloads
- **Coverage**: Top 100+ cryptocurrencies by market cap
- **Timeframes**: Daily (1D), Hourly (1H), Minute (1m) from 2017-08-17 to present
- **Download method**: ZIP files extracted to CSV format with safety patterns

## Dependencies

### VN Stock Scripts
- `cargo build --release` (Rust compilation required)

### Cryptocurrency Scripts
- Python virtual environment: `python3 -m venv venv`
- Required packages:
  - `pip install requests`
- No API keys required (CoinGecko free tier + Binance Vision public API)

## File Structure

### Output Directories
```
market_data/           # VN stock data
├── VCB/1D.csv        # Daily data per ticker
├── FPT/1H.csv        # Hourly data per ticker
└── VNINDEX/1m.csv    # Minute data per ticker

spot/daily/klines/    # Cryptocurrency data (ZIP downloads only)
├── BTCUSDT/
│   ├── 1d/           # Daily BTC data (ZIP files)
│   ├── 1h/           # Hourly BTC data (ZIP files)
│   └── 1m/           # Minute BTC data (ZIP files)
├── ETHUSDT/
│   ├── 1d/           # Daily ETH data (ZIP files)
│   ├── 1h/           # Hourly ETH data (ZIP files)
│   └── 1m/           # Minute ETH data (ZIP files)
└── SOLUSDT/
    ├── 1d/           # Daily SOL data (ZIP files)
    ├── 1h/           # Hourly SOL data (ZIP files)
    └── 1m/           # Minute SOL data (ZIP files)
```

### Configuration Files
- `top_crypto.json` - Cryptocurrency market cap rankings
- `binance_mapping.json` - Symbol to Binance pair mappings
- `ticker_group.json` - VN stock sector classifications

## Notes

- VN stock scripts use resume mode by default (incremental updates)
- Cryptocurrency downloads are very large (~8.9M files: 100 symbols × 3 timeframes × 8+ years)
- All VN scripts require project to be built: `cargo build --release`
- Cryptocurrency scripts require Python environment setup
- Use test mode first (top_crypto_test.json) to verify setup before full download
- Cryptocurrency downloads are resume-friendly and can be stopped/restarted anytime
- High-performance parallel processing reduces download time from 12+ hours to 1-2 hours
- Ctrl+C support for graceful shutdown with immediate worker termination
- ZIP-only downloads save disk space and extraction time