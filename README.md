# aipriceaction

**Live site:** [aipriceaction.com](https://aipriceaction.com) | **Frontend:** [aipriceaction-web](https://github.com/quanhua92/aipriceaction-web)

Vietnamese stock, US stock, and cryptocurrency data management system with PostgreSQL backend. Fetches, stores, and serves OHLCV market data with technical indicators via REST API.

## Quick Start

### Docker (recommended)

```bash
cd aipriceaction

# Create .env from template
cp .env.example .env

# Build and start (includes PostgreSQL 18 + pgvector)
docker compose up -d

# View logs
docker logs aipriceaction -f
docker logs aipriceaction-postgres -f
```

This starts two containers:
- **aipriceaction** -- API server on port 3000, runs migrations on startup
- **aipriceaction-postgres** -- PostgreSQL 18 with pgvector on port 5432

Edit `.env` to configure `DATABASE_URL` (required by the API server) and enable or disable background workers:

```bash
# Disable crypto workers
# BINANCE_WORKERS=false
```

After changing `.env`, restart: `docker compose up -d`.

### Build from source

Requires a running PostgreSQL instance.

```bash
cd aipriceaction

# Create .env and set DATABASE_URL to your PostgreSQL instance
cp .env.example .env
# Edit .env — update DATABASE_URL, e.g.:
#   DATABASE_URL=postgresql://user:pass@localhost:5432/aipriceaction

# Build and run
cargo build --release
./target/release/aipriceaction serve --port 3000
```

If using the Docker PostgreSQL container for local dev, you can start it standalone:

```bash
cd aipriceaction
docker compose up -d postgres
```

## CLI Commands

```bash
# Start API server with background workers
./target/release/aipriceaction serve --port 3000

# Show database status
./target/release/aipriceaction status

# Import CSV data from legacy market_data directory
./target/release/aipriceaction import --market-data ./market_data

# Run benchmark queries
./target/release/aipriceaction stats --tickers VCB,FPT --intervals 1D

# Test VCI provider connectivity (Vietnamese stocks)
./target/release/aipriceaction test-vci --ticker VNINDEX

# Test Binance provider connectivity (crypto)
./target/release/aipriceaction test-binance --ticker BTCUSDT --interval all

# Test Yahoo Finance provider connectivity (US/international stocks)
./target/release/aipriceaction test-yahoo --ticker AAPL

# Test SOCKS5 proxy connectivity against Yahoo Finance API
./target/release/aipriceaction test-proxy

# Benchmark database query performance
./target/release/aipriceaction test-perf
```

## API Endpoints

```bash
# Health check
curl http://localhost:3000/health

# Vietnamese stock (default mode)
curl "http://localhost:3000/tickers?symbol=VCB&interval=1D&limit=100"

# US/international stock (Yahoo Finance)
curl "http://localhost:3000/tickers?symbol=AAPL&mode=yahoo&interval=1D&limit=100"

# Cryptocurrency
curl "http://localhost:3000/tickers?symbol=BTCUSDT&mode=crypto&interval=1D&limit=100"

# Multiple tickers
curl "http://localhost:3000/tickers?symbol=VCB&symbol=FPT&interval=1D"

# All sources at once (vn + yahoo + crypto)
curl "http://localhost:3000/tickers?mode=all&interval=1D&limit=100"
curl "http://localhost:3000/tickers?mode=all&symbol=CL=F&symbol=BTCUSDT&interval=1D"

# Date range
curl "http://localhost:3000/tickers?symbol=BTCUSDT&mode=crypto&start_date=2024-01-01&end_date=2024-12-31"

# Aggregated intervals (5m, 15m, 30m, 4h, 1W, 2W, 1M)
curl "http://localhost:3000/tickers?symbol=ETHUSDT&mode=crypto&interval=5m&limit=100"

# CSV export
curl "http://localhost:3000/tickers?symbol=VCB&interval=1D&format=csv"

# Ticker groups
curl "http://localhost:3000/tickers/group"              # VN sectors
curl "http://localhost:3000/tickers/group?mode=crypto"   # Crypto groups
curl "http://localhost:3000/tickers/group?mode=yahoo"    # Yahoo symbols
curl "http://localhost:3000/tickers/group?mode=all"      # All sources

# Ticker name lookup (symbol -> human-readable name)
curl "http://localhost:3000/tickers/name"              # VN: ticker maps to itself
curl "http://localhost:3000/tickers/name?mode=crypto"   # e.g. BTCUSDT -> Bitcoin
curl "http://localhost:3000/tickers/name?mode=yahoo"    # e.g. GC=F -> Gold Futures
curl "http://localhost:3000/tickers/name?mode=all"      # All sources

# Company info (from company_info.json)
curl "http://localhost:3000/tickers/info"              # All tickers
curl "http://localhost:3000/tickers/info?ticker=VCB"   # Single ticker
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | -- | PostgreSQL connection string |
| `PORT` | No | `3000` | Server port |
| `RUST_LOG` | No | `info` | Log level |
| `VCI_WORKERS` | No | `true` | Enable VN stock data workers |
| `VCI_DIVIDEND_WORKER` | No | `true` | Enable dividend detection worker |
| `BINANCE_WORKERS` | No | `true` | Enable crypto data workers |
| `YAHOO_WORKERS` | No | `true` | Enable Yahoo Finance data workers |
| `HTTP_PROXIES` | No | -- | Comma-separated SOCKS5 proxy list |
| `CORS_ORIGINS` | No | `https://aipriceaction.com` | Comma-separated allowed CORS origins |

## Database

- **PostgreSQL 18** with pgvector extension
- Migrations are embedded in the binary and run automatically on startup
- Data is stored in partitioned tables (by interval) with yearly sub-partitions for minute/hourly data
- Backup/restore scripts available in `scripts/`

## Background Workers

When enabled via environment variables, the server runs background sync workers:

- **VCI daily worker** -- Syncs daily VN stock data every 15s during trading hours (9:00-15:00 ICT)
- **VCI hourly/minute worker** -- Syncs hourly and minute data every minute during trading hours
- **VCI dividend worker** -- Detects dividend-adjusted prices and re-downloads full history
- **Binance workers** -- Syncs cryptocurrency data for all intervals (24/7)
- **Yahoo Finance workers** -- Syncs US/international stock data for daily, hourly, and minute intervals

## Development

See [CLAUDE.md](CLAUDE.md) for detailed development documentation.

## License

See [LICENSE](LICENSE) file for details.
