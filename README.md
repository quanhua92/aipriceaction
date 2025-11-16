# aipriceaction

Vietnamese stock market and cryptocurrency data management system. Fetches, stores, and serves market data with technical indicators via REST API.

## Features

- 📈 **Vietnamese Stock Market**: VCI API integration for HOSE/HNX/UPCOM markets
- 🪙 **Cryptocurrency Data**: CryptoCompare API integration for top 100 cryptocurrencies
- 🔄 **Smart Sync**: Adaptive resume mode, dividend detection, intelligent categorization
- 📊 **Technical Indicators**: 5 moving averages (MA10-200), MA scores, change percentages
- 🚀 **Fast API**: In-memory caching, LRU disk cache, background workers
- 🐳 **Docker Ready**: Multi-stage builds, health checks, volume mounting

## Quick Start

### Build & Run

```bash
# Build release binary
cargo build --release

# Sync Vietnamese stock data
./target/release/aipriceaction pull --intervals 1D

# Sync cryptocurrency data
./target/release/aipriceaction crypto-pull --symbol all --interval daily

# Start API server (with background workers)
./target/release/aipriceaction serve --port 3000
```

### Docker

```bash
# Start server (local development with existing data)
docker compose -f docker-compose.local.yml up -d

# View logs
docker logs aipriceaction -f
```

## Vietnamese Stock Market Data

### Sync Stock Data

```bash
# Single stock
./target/release/aipriceaction pull --ticker VCB --intervals 1D

# Multiple stocks (comma-separated)
./target/release/aipriceaction pull --ticker VCB,FPT,VNM --intervals 1D

# All stocks, all intervals
./target/release/aipriceaction pull --intervals 1D,1H,1m

# Force full history download
./target/release/aipriceaction pull --ticker VCB --intervals 1D --full
```

### Data Storage

Stock data is stored in `market_data/{TICKER}/` with CSV files:

```
market_data/
├── VCB/
│   ├── 1D.csv   # Daily data
│   ├── 1h.csv   # Hourly data
│   └── 1m.csv   # Minute data
├── FPT/
│   └── 1D.csv
└── VNINDEX/
    └── 1D.csv
```

## Cryptocurrency Data

### Sync Crypto Data

The `crypto-pull` command fetches cryptocurrency data from CryptoCompare API and stores it in the same 20-column CSV format as stock market data.

```bash
# Single cryptocurrency (daily data)
./target/release/aipriceaction crypto-pull --symbol BTC --interval daily

# Multiple cryptos (comma-separated)
./target/release/aipriceaction crypto-pull --symbol BTC,ETH,USDT --interval daily

# All 100 cryptocurrencies (default: daily data)
./target/release/aipriceaction crypto-pull --symbol all --interval daily

# All cryptos, all intervals (daily, hourly, minute)
./target/release/aipriceaction crypto-pull --symbol all --interval all

# Force full history download
./target/release/aipriceaction crypto-pull --symbol BTC --interval daily --full
```

### Supported Cryptocurrencies

The system supports the top 100 cryptocurrencies by market cap, defined in `crypto_top_100.json`. This includes:

- **Major coins**: BTC, ETH, USDT, XRP, BNB, SOL, USDC, ADA, DOGE, etc.
- **DeFi tokens**: UNI, AAVE, LINK, CRV, etc.
- **Layer 1/2**: AVAX, DOT, MATIC, ARB, OP, etc.
- **Stablecoins**: USDT, USDC, DAI, PYUSD, etc.

**Note**: 2 cryptos are currently unsupported (MNT, IOTA) due to API data unavailability.

### Data Intervals

- **Daily** (`1D`): Historical data from 2010-07-17 (Bitcoin inception)
- **Hourly** (`1H`): Historical data from 2010-07-17
- **Minute** (`1m`): Last 7 days only (CryptoCompare API limitation)

### Data Storage

Cryptocurrency data is stored in `crypto_data/{SYMBOL}/` with the same structure as stock data:

```
crypto_data/
├── BTC/
│   ├── 1D.csv   # Daily data (2010-07-17 to present)
│   ├── 1H.csv   # Hourly data
│   └── 1m.csv   # Minute data (last 7 days)
├── ETH/
│   ├── 1D.csv
│   ├── 1H.csv
│   └── 1m.csv
└── USDT/
    └── 1D.csv
```

### Performance

Based on Phase 5 testing with 98 cryptocurrencies:

- **Success rate**: 98/100 (98%)
- **Total time**: 253.5 seconds (~2.6s per crypto)
- **Data volume**: ~41MB for daily data across all cryptos
- **Rate limiting**: 200ms delay between cryptos (30 requests/min)

### Command Options

```bash
crypto-pull [OPTIONS]

Options:
  --symbol <SYMBOL>      Cryptocurrency symbol(s) or "all"
                         Examples: BTC, "BTC,ETH,USDT", all
                         Default: all

  --interval <INTERVAL>  Data interval: daily, hourly, minute, all
                         Aliases: 1d, 1h, 1m
                         Default: daily

  --full                 Force full history download (ignore existing data)
                         Default: false (resume mode)
```

### Resume Mode

The crypto sync uses smart resume mode:

1. **Pre-scan**: Reads last date from each crypto's CSV file
2. **Categorize**: Groups into "resume" (existing data) vs "full history" (new crypto)
3. **Fetch**: Only downloads data after the last date for resume cryptos
4. **Merge**: Appends new data to existing CSVs

This makes daily syncs very fast (~2-3s per crypto for resume mode).

## CSV Format (20 Columns)

All data (both stocks and cryptocurrencies) uses the same enhanced CSV format:

```csv
ticker,time,open,high,low,close,volume,
ma10,ma20,ma50,ma100,ma200,
ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,
close_changed,volume_changed,total_money_changed
```

### Column Details

- **Columns 1-7**: OHLCV data (ticker, timestamp, open, high, low, close, volume)
- **Columns 8-12**: Moving averages (MA10, MA20, MA50, MA100, MA200)
- **Columns 13-17**: MA scores (percentage deviation from each MA)
- **Column 18**: `close_changed` - Price change % from previous period
- **Column 19**: `volume_changed` - Volume change % from previous period
- **Column 20**: `total_money_changed` - Money flow: `(price_change × volume)`

**Note**: All technical indicators are calculated during sync (single-phase enhancement).

## API Server

### Start Server

```bash
# Start with default port (3000)
./target/release/aipriceaction serve

# Custom port
./target/release/aipriceaction serve --port 8080
```

### API Endpoints

```bash
# Health check
curl http://localhost:3000/health

# Get stock data
curl "http://localhost:3000/tickers?symbol=VCB&interval=1D&limit=100"

# Get cryptocurrency data
curl "http://localhost:3000/tickers?symbol=BTC&interval=1D&limit=100"

# Multiple tickers
curl "http://localhost:3000/tickers?symbol=VCB,FPT,BTC&interval=1D"

# Date range
curl "http://localhost:3000/tickers?symbol=ETH&interval=1D&from=2024-01-01&to=2024-12-31"

# CSV export
curl "http://localhost:3000/tickers?symbol=BTC&interval=1D&format=csv"
```

### Background Workers

The server runs two background workers:

- **Daily Worker**: Syncs daily data (15s during trading hours, 5min off-hours)
- **Slow Worker**: Syncs hourly/minute data (5min trading, 30min off-hours)

## Other Commands

```bash
# Show data statistics
./target/release/aipriceaction status

# Validate and repair CSV files
./target/release/aipriceaction doctor

# Get company information
./target/release/aipriceaction company VCB
```

## Development

See [CLAUDE.md](CLAUDE.md) for detailed development documentation including:

- Architecture overview (4-layer structure)
- Build & test commands
- Docker usage (local vs production)
- CSV format evolution
- Performance optimization notes
- Common issues and troubleshooting

## Documentation

- [API.md](docs/API.md) - REST API reference
- [PLAN_CRYPTO.md](docs/PLAN_CRYPTO.md) - Cryptocurrency integration plan (Phase 1-7)
- [CLAUDE.md](CLAUDE.md) - Development guide for Claude Code

## License

See [LICENSE](LICENSE) file for details.
