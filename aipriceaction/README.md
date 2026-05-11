# aipriceaction

**Live site:** [aipriceaction.com](https://aipriceaction.com) | **GitHub:** [aipriceaction](https://github.com/quanhua92/aipriceaction) | **Frontend:** [aipriceaction-web](https://github.com/quanhua92/aipriceaction-web) | **Docker image:** [`quanhua92/aipriceaction:latest`](https://hub.docker.com/r/quanhua92/aipriceaction) | **Python SDK:** [`aipriceaction` on PyPI](https://pypi.org/project/aipriceaction/) | **AIPA Terminal:** [`aipa-cli` on PyPI](https://pypi.org/project/aipa-cli/)

Vietnamese stock, US stock, cryptocurrency, and commodity data management system with PostgreSQL backend and Redis edge cache. Fetches, stores, and serves OHLCV market data with technical indicators via REST API. All endpoints serve from Redis first for low latency, with automatic fallback to PostgreSQL when Redis is unavailable.

## Quick Start

### Docker (recommended)

```bash
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

### Production (HAProxy + rolling updates)

For zero-downtime deployments with multiple API replicas:

```bash
# Create .env from template
cp .env.example .env

# Build and start (HAProxy + 3 API replicas + 1 worker + PostgreSQL + Redis)
docker compose -f docker-compose.prod.yml up -d
```

This starts five services:
- **haproxy** -- Load balancer on port 3000, routes traffic across API replicas
- **aipriceaction-api** (x3) -- API servers with workers disabled, health-checked by HAProxy
- **aipriceaction-worker** (x1) -- Background sync workers (VCI, Binance, Yahoo, SJC)
- **aipriceaction-postgres** -- PostgreSQL 18 with pgvector on port 5432
- **aipriceaction-redis** -- Redis 8 with AOF persistence on port 6379

Rolling updates: when you pull and redeploy, `start-first` order ensures a new container is healthy before the old one is removed -- zero downtime.

#### Day-2 operations

```bash
# Rolling update (pull latest image and redeploy with zero downtime)
docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d

# Scale API replicas up or down
docker compose -f docker-compose.prod.yml up -d --scale aipriceaction-api=5

# View running containers
docker compose -f docker-compose.prod.yml ps

# View HAProxy stats (from inside the network)
docker compose -f docker-compose.prod.yml exec haproxy wget -qO- http://localhost:8404/stats

# View logs
docker compose -f docker-compose.prod.yml logs aipriceaction-api -f
docker compose -f docker-compose.prod.yml logs aipriceaction-worker -f
docker compose -f docker-compose.prod.yml logs haproxy -f

# Restart a single service
docker compose -f docker-compose.prod.yml restart aipriceaction-worker

# Stop everything (preserves data volumes)
docker compose -f docker-compose.prod.yml down

# Stop everything and delete volumes (destroys all data)
docker compose -f docker-compose.prod.yml down -v

# Database backup (uses script mounted into postgres container)
docker compose -f docker-compose.prod.yml exec postgres /app/scripts/backup-db.sh

# Database restore
docker compose -f docker-compose.prod.yml exec postgres /app/scripts/restore-db.sh /app/backups/<backup-file>
```

### Build from source

Requires a running PostgreSQL instance.

```bash
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

# Test TradingView UDF providers (Vietstock, VNDirect, DNSE, VPS)
./target/release/aipriceaction test-udf --ticker VNINDEX --source all
./target/release/aipriceaction test-udf --ticker VCB --source vietstock

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

# Use EMA instead of SMA for moving averages
curl "http://localhost:3000/tickers?symbol=VCB&interval=1D&limit=10&ema=true"
curl "http://localhost:3000/tickers?symbol=BTCUSDT&mode=crypto&interval=1D&ema=true"

# Analysis endpoints also support ema=true
curl "http://localhost:3000/analysis/top-performers?ema=true"
curl "http://localhost:3000/analysis/ma-scores-by-sector?ema=true"
curl "http://localhost:3000/analysis/rrg?ema=true"

# Redis snapshot cache (skip ZSET parsing + SMA computation)
curl "http://localhost:3000/tickers?interval=1D&limit=1&snap=true"     # use snapshot (default)
curl "http://localhost:3000/tickers?interval=1D&limit=1&snap=false"    # skip snapshot, recompute from ZSET
curl "http://localhost:3000/analysis/top-performers?snap=false"
curl "http://localhost:3000/analysis/ma-scores-by-sector?snap=false"
curl "http://localhost:3000/analysis/rrg?algorithm=mascore&snap=false"

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

# Sync KV-store (cross-device JSON object syncing)
curl -X POST http://localhost:3000/sync/550e8400-e29b-41d4-a716-446655440000 \
  -H "Authorization: Bearer <SYNC_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"secret": "my-secret", "value": {"bookmarks": ["BTCUSDT", "VCB"]}}'

curl http://localhost:3000/sync/550e8400-e29b-41d4-a716-446655440000?secret=my-secret \
  -H "Authorization: Bearer <SYNC_TOKEN>"
```

## Environment Variables

| Variable                      | Required | Default                     | Description                                       |
| ----------------------------- | -------- | --------------------------- | ------------------------------------------------- |
| `DATABASE_URL`                | Yes      | --                          | PostgreSQL connection string                      |
| `PORT`                        | No       | `3000`                      | Server port                                       |
| `RUST_LOG`                    | No       | `info`                      | Log level                                         |
| `VCI_WORKERS`                 | No       | `true`                      | Enable VN stock data workers                      |
| `VCI_DIVIDEND_WORKER`         | No       | `true`                      | Enable dividend detection worker                  |
| `BINANCE_WORKERS`             | No       | `true`                      | Enable crypto data workers                        |
| `SJC_WORKERS`                 | No       | `true`                      | Enable SJC gold price workers                     |
| `YAHOO_WORKERS`               | No       | `true`                      | Enable Yahoo Finance data workers                 |
| `HTTP_PROXIES`                | No       | --                          | Comma-separated SOCKS5 proxy list                 |
| `CORS_ORIGINS`                | No       | `https://aipriceaction.com` | Comma-separated allowed CORS origins              |
| `REDIS_URL`                   | No       | --                          | Redis connection URL (auto-configured in Docker)  |
| `REDIS_PASSWORD`              | No       | --                          | Redis password (auto-configured in Docker)        |
| `REDIS_WORKERS`               | No       | `false`                     | Enable Redis ZSET backfill worker                 |
| `REDIS_OP_TIMEOUT_SECS`       | No       | `5`                         | Timeout for all Redis operations (seconds)        |
| `API_MAX_LIMIT`               | No       | `40`                        | Max ?limit= rows per ticker for /tickers endpoint |
| `REDIS_DAILY_MAX_SIZE`        | No       | `5000`                      | Max Redis ZSET members for daily interval         |
| `REDIS_HOURLY_MAX_SIZE`       | No       | `30000`                     | Max Redis ZSET members for hourly interval        |
| `REDIS_MINUTE_MAX_SIZE`       | No       | `20000`                     | Max Redis ZSET members for minute interval        |
| `REDIS_DAILY_BACKFILL_LIMIT`  | No       | `5000`                      | Rows fetched during backfill (daily)              |
| `REDIS_HOURLY_BACKFILL_LIMIT` | No       | `30000`                     | Rows fetched during backfill (hourly)             |
| `REDIS_MINUTE_BACKFILL_LIMIT` | No       | `20000`                     | Rows fetched during backfill (minute)             |
| `REFRESH_SECRET`              | No       | --                          | Secret key required for POST /tickers/refresh. Endpoint returns 403 if unset. |
| `SYNC_TOKEN`                  | No       | --                          | Bearer token for /sync KV-store endpoint. Comma-separated for key rotation. Endpoint returns 403 if unset. |

## Redis Cache

OHLCV data is cached in Redis ZSETs for fast reads. All API endpoints try Redis first and fall back to PostgreSQL automatically.

- **1 ZSET per ticker/interval**: `ohlcv:{source}:{ticker}:{interval}`
- **Snapshot HASH per ticker/interval**: `snap:{source}:{ticker}:{interval}` -- pre-computed JSON responses for common queries (limit=1, limit=N), avoids ZSET parsing + SMA computation
- **Retention**: 1D (5,000 bars / ~20yr), 1h (30,000 / ~3yr), 1m (20,000 / ~14 days)
- **Snapshot TTL**: 30 seconds (invalidated immediately by workers on data updates)
- **Backfill**: periodic full backfill from PostgreSQL every 15 minutes
- **Write path**: fire-and-forget ZADD from all data workers after PG upsert
- **Read path**: pipelined ZREVRANGE -- 1 network round-trip per ticker batch
- **PG-outage resilience**: all `/tickers` and `/analysis/*` endpoints serve from Redis when PostgreSQL is down
- **`?snap=true/false`**: toggle snapshot cache on `/tickers` and `/analysis/*` endpoints

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
- **S3 archive worker** -- Exports OHLCV data from PostgreSQL to S3 as per-day CSV files with enriched ticker metadata (`meta/tickers.json`). Runs a full historical scan on startup and every 24h, plus an incremental check every 1h for the last 7 days. Uses fingerprint-based skip-if-unchanged and concurrent uploads. See [S3_ARCHIVE_WORKER.md](S3_ARCHIVE_WORKER.md) for detailed documentation.

**S3 bucket policy** (required for public-read access): the bucket must allow anonymous `s3:GetObject`. In the rustfs console, go to Buckets → `aipriceaction-archive` → Access Rules → Add:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PublicReadGetObject",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::aipriceaction-archive/*"
        }
    ]
}
```

## UDF Providers (TradingView-compatible)

The `src/providers/udf.rs` module implements a generic TradingView UDF (Universal Data Feed) provider that connects to multiple Vietnamese broker endpoints. Unlike the default VCI provider (`src/providers/vci.rs`) which uses Vietcap's proprietary POST-based gap-chart API, UDF providers use the standard TradingView GET-based protocol with columnar JSON responses (`t`, `o`, `h`, `l`, `c`, `v` arrays).

### VCI vs UDF comparison

| Feature | VCI Provider (`vci.rs`) | UDF Provider (`udf.rs`) |
|---------|------------------------|------------------------|
| Protocol | POST to `/chart/OHLCChart/gap-chart` | GET to `/history?symbol=...&resolution=...` |
| Response format | Wrapped in array: `[{o:[], h:[], ...}]` | Flat: `{t:[], o:[], h:[], s:"ok"}` |
| Source | Vietcap only | 4 brokers (Vietstock, VNDirect, DNSE, VPS) |
| Authentication | Browser-like headers + origin/referer | Browser-like headers + referer per broker |
| Use case | Primary production worker | Alternative/fallback data sources |

### Supported sources

| Source | Base URL | History path | Stock path | UDF protocol |
|--------|----------|-------------|------------|-------------|
| Vietstock | `api.vietstock.vn/tvnew` | `/history` | `/history` | Full (/config, /symbols, /search, /time) |
| VNDirect | `dchart-api.vndirect.com.vn/dchart` | `/history` | `/history` | Full (/config, /symbols, /search, /time) |
| DNSE | `api.dnse.com.vn/chart-api/v2` | `/ohlcs/index` | `/ohlcs/stock` | None (OHLCV only) |
| VPS | `histdatafeed.vps.com.vn/tradingview` | `/history` | `/history` | Partial (/symbols, /search, /time; no /config) |

DNSE uses separate endpoints for index (`/ohlcs/index`) and stock (`/ohlcs/stock`) tickers. The provider auto-detects by trying the stock endpoint first and falling back to index.

### Price normalization

All UDF providers return prices normalized to match VCI's raw convention (e.g. VCB: 60,400). Providers that return face-value prices (VNDirect, DNSE, VPS) are automatically multiplied by 1000. Index tickers (VNINDEX, VN30, etc.) are detected via `is_index_ticker()` and skip normalization since their prices are already correct across all providers.

### Test command

```bash
# Test all 4 providers at once
./target/release/aipriceaction test-udf --ticker VNINDEX --source all --count-back 10

# Test a single provider
./target/release/aipriceaction test-udf --ticker VCB --source vietstock

# Test a specific stock across all providers
./target/release/aipriceaction test-udf --ticker FPT --source all
```

The `test-udf` command tests OHLCV fetching (1D, 1H, 1m intervals) plus optional UDF protocol endpoints (`/config`, `/symbols`, `/search`, `/time`) for each provider, showing which endpoints each broker supports. There is a 1s sleep between each interval test to respect rate limits.

## AIPA Terminal

See [aipriceaction-terminal/](../aipriceaction-terminal/) for the Python TUI/CLI tool.

## Python SDK

See [sdk/aipriceaction-python/](../sdk/aipriceaction-python/) for the Python SDK.

## TypeScript SDK

See [sdk/aipriceaction-js/](../sdk/aipriceaction-js/) for the TypeScript SDK.

## Development

See [CLAUDE.md](../CLAUDE.md) for detailed development documentation.

## License

See [LICENSE](../LICENSE) file for details.
