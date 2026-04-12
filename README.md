# aipriceaction

**Live site:** [aipriceaction.com](https://aipriceaction.com) | **Frontend:** [aipriceaction-web](https://github.com/quanhua92/aipriceaction-web)

Vietnamese stock, US stock, cryptocurrency, and commodity data management system with PostgreSQL backend and Redis edge cache. Fetches, stores, and serves OHLCV market data with technical indicators via REST API. All endpoints serve from Redis first for low latency, with automatic fallback to PostgreSQL when Redis is unavailable.

## Quick Start

### Docker (recommended)

```bash
cd aipriceaction

# Create .env from template
cp .env.example .env

# Build and start (includes PostgreSQL 18 + pgvector, Redis 7)
docker compose up -d

# View logs
docker logs aipriceaction -f
docker logs aipriceaction-postgres -f
docker logs aipriceaction-redis -f
```

This starts three containers:
- **aipriceaction** -- API server on port 3000, runs migrations on startup
- **aipriceaction-postgres** -- PostgreSQL 18 with pgvector on port 5432
- **aipriceaction-redis** -- Redis 8 with AOF persistence on port 6379 (edge cache for OHLCV data)

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

# Manually backfill Redis ZSETs from PostgreSQL
./target/release/aipriceaction backfill-redis
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

# SJC gold (served under yahoo/global mode as a commodity)
curl "http://localhost:3000/tickers?symbol=SJC-GOLD&mode=yahoo&interval=1D&limit=100"

# All sources at once (vn + yahoo + crypto + sjc)
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
| `SJC_WORKERS` | No | `true` | Enable SJC gold price workers |
| `YAHOO_WORKERS` | No | `true` | Enable Yahoo Finance data workers |
| `HTTP_PROXIES` | No | -- | Comma-separated SOCKS5 proxy list |
| `CORS_ORIGINS` | No | `https://aipriceaction.com` | Comma-separated allowed CORS origins |
| `REDIS_URL` | No | -- | Redis connection URL (auto-configured in Docker) |
| `REDIS_PASSWORD` | No | -- | Redis password (auto-configured in Docker) |
| `REDIS_WORKERS` | No | `false` | Enable Redis ZSET backfill worker |
| `API_MAX_LIMIT` | No | `40` | Max ?limit= rows per ticker for /tickers endpoint |
| `REDIS_DAILY_MAX_SIZE` | No | `5000` | Max Redis ZSET members for daily interval |
| `REDIS_HOURLY_MAX_SIZE` | No | `30000` | Max Redis ZSET members for hourly interval |
| `REDIS_MINUTE_MAX_SIZE` | No | `20000` | Max Redis ZSET members for minute interval |
| `REDIS_DAILY_BACKFILL_LIMIT` | No | `5000` | Rows fetched during backfill (daily) |
| `REDIS_HOURLY_BACKFILL_LIMIT` | No | `30000` | Rows fetched during backfill (hourly) |
| `REDIS_MINUTE_BACKFILL_LIMIT` | No | `20000` | Rows fetched during backfill (minute) |

## Redis Cache

OHLCV data is cached in Redis ZSETs for fast reads. All API endpoints try Redis first and fall back to PostgreSQL automatically.

- **1 ZSET per ticker/interval**: `ohlcv:{source}:{ticker}:{interval}`
- **Retention**: 1D (5,000 bars / ~20yr), 1h (30,000 / ~3yr), 1m (20,000 / ~14 days)
- **Backfill**: periodic full backfill from PostgreSQL every 15 minutes
- **Write path**: fire-and-forget ZADD from all data workers after PG upsert
- **Read path**: pipelined ZREVRANGE — 1 network round-trip per ticker batch
- **PG-outage resilience**: all `/tickers` and `/analysis/*` endpoints serve from Redis when PostgreSQL is down

See [REDIS.md](REDIS.md) for detailed documentation.

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
- **SJC gold workers** -- Syncs SJC gold bar prices (HCM branch) via sjc.com.vn API; bootstrap imports historical CSV, then live syncs every 5min during trading hours. SJC-GOLD appears under `mode=yahoo` as a commodity alongside GC=F, CL=F, etc.

## Development

See [CLAUDE.md](CLAUDE.md) for detailed development documentation.

## License

See [LICENSE](LICENSE) file for details.
