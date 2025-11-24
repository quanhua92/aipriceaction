# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**aipriceaction** is a dual-mode market data management system that:
- **VN Mode (default)**: Fetches, stores, and serves Vietnamese stock market data from the VCI (Vietcap) API
- **Crypto Mode**: Fetches, stores, and serves cryptocurrency data from CoinDesk API

It provides both a CLI for data synchronization and a REST API server with dual in-memory caching (VN + Crypto).

## Build & Development Commands

### Build
```bash
# Debug build
cargo build

# Release build (optimized)
cargo build --release

# Check compilation without building
cargo check
```

### Run CLI Commands
```bash
# Fetch latest market data (adaptive resume mode)
cargo run -- pull

# Start API server with background workers
cargo run -- serve --port 3000

# Show data statistics
cargo run -- status

# Validate and repair CSV files
cargo run -- doctor

# Import historical data from reference project
cargo run -- import-legacy

# Get company information
cargo run -- company <TICKER>
```

### Run with Release Binary
```bash
# Build once
cargo build --release

# Then use the optimized binary
./target/release/aipriceaction pull
./target/release/aipriceaction serve --port 3000
```

### Testing

Complete test flow covering all aspects of the system:

#### Quick Test Flow (Recommended)
```bash
# 1. Start server
./target/release/aipriceaction serve --port 3000

# 2. Run all tests (in another terminal)
./scripts/test-integration.sh    # 17 integration tests (13 VN + 4 crypto)
./scripts/test-analysis.sh        # 10 analysis API tests
./scripts/test-aggregated.sh      # 27 aggregated interval tests
./scripts/test-upload.sh          # 13 upload API tests (includes secret validation)

# 3. Run all SDK examples
cd sdk/aipriceaction-js
pnpx tsx examples/01-basic-tickers.ts
pnpx tsx examples/02-health-check.ts
pnpx tsx examples/03-ticker-groups.ts
pnpx tsx examples/04-top-performers.ts
pnpx tsx examples/05-ma-scores-by-sector.ts
pnpx tsx examples/06-csv-export.ts
pnpx tsx examples/07-error-handling.ts
pnpx tsx examples/08-batch-requests.ts
pnpx tsx examples/09-analysis-dashboard.ts
pnpx tsx examples/10-aggregated-intervals.ts
```

#### One-Liner Test All SDK Examples
```bash
cd sdk/aipriceaction-js && for file in examples/*.ts; do echo "=== $file ===" && pnpx tsx "$file" || exit 1; done
```

#### Test Details

**Integration Tests** (`./scripts/test-integration.sh`):
- ✅ Server health check
- ✅ Basic tickers API (cache=true/false)
- ✅ Multiple tickers API
- ✅ Ticker groups API
- ✅ Cache behavior
- ✅ Error handling (invalid ticker, invalid date)
- ✅ Raw GitHub proxy (legacy endpoint)
- ✅ Indicators completeness (VCB, VIC)
- ✅ CSV export performance (1D, 1m single/multiple)
- ✅ Limit parameter behavior
- ✅ Historical data range (2023-2024)
- ✅ Crypto mode - basic query (BTC)
- ✅ Crypto mode - ticker groups
- ✅ Crypto mode - CSV format
- ✅ Crypto mode - multiple tickers
- **Total: 17 tests**

**Analysis API Tests** (`./scripts/test-analysis.sh`):
- ✅ Top performers endpoint (basic, sort by close/volume/MA scores)
- ✅ MA scores by sector (basic, MA50, threshold, invalid period)
- ✅ Response format validation
- ✅ Sorting validation (descending/ascending)
- **Total: 10 tests**

**Aggregated Intervals Tests** (`./scripts/test-aggregated.sh`):
- ✅ 5m, 15m, 30m interval aggregation
- ✅ 1W, 2W, 1M interval aggregation
- ✅ Multiple tickers aggregation
- ✅ Date range filtering
- ✅ Limit parameter
- ✅ CSV export for aggregated data
- **Total: 27 tests**

**Upload API Tests** (`./scripts/test-upload.sh`):
- ✅ Upload markdown file (with secret - success)
- ✅ Retrieve markdown file (public access - success)
- ✅ Duplicate file detection (409 Conflict)
- ✅ Upload image file (with secret - success)
- ✅ Retrieve image file (public access - success)
- ✅ Upload .txt file to markdown endpoint
- ✅ Wrong secret detection (403 Forbidden)
- ✅ Invalid session ID (400 Bad Request)
- ✅ Missing secret (400 Bad Request)
- ✅ Missing session ID (400 Bad Request)
- ✅ Path traversal prevention (400/404)
- ✅ Wrong file type (415 Unsupported Media Type)
- ✅ File size limit enforcement (413/500)
- **Total: 13 tests**

**SDK Examples** (10 TypeScript examples):
1. `01-basic-tickers.ts` - Single/multiple tickers, historical data, hourly data
2. `02-health-check.ts` - Server status, memory, disk cache, worker stats
3. `03-ticker-groups.ts` - Sector groups, sector data fetching
4. `04-top-performers.ts` - Top/bottom performers, volume leaders, MA momentum
5. `05-ma-scores-by-sector.ts` - Sector analysis, MA periods, thresholds
6. `06-csv-export.ts` - CSV format export, legacy format, parsing
7. `07-error-handling.ts` - Validation errors, network errors, graceful handling
8. `08-batch-requests.ts` - Parallel requests, concurrency control, caching
9. `09-analysis-dashboard.ts` - Complete market analysis workflow
10. `10-aggregated-intervals.ts` - Aggregated intervals (5m, 15m, 30m, 1W, 2W, 1M)

#### Custom Test URLs
```bash
# Test against different server URL
./scripts/test-integration.sh http://localhost:3001
./scripts/test-analysis.sh http://api.aipriceaction.com
./scripts/test-aggregated.sh https://api.aipriceaction.com
./scripts/test-upload.sh https://api.aipriceaction.com
```

#### Expected Results
- **Integration**: 17/17 tests passed (13 VN + 4 crypto)
- **Analysis**: 10/10 tests passed
- **Aggregated**: 27/27 tests passed
- **Upload**: 13/13 tests passed
- **SDK Examples**: All 10 examples run successfully without errors

#### Troubleshooting Tests
```bash
# If tests fail due to server not running
./target/release/aipriceaction serve --port 3000

# If SDK examples fail, rebuild the SDK
cd sdk/aipriceaction-js && pnpm build

# If CSV format errors occur
cargo run -- doctor
```

### Docker

**Important:** Use `docker-compose.local.yml` for local development (absolute path to market_data).

**⚠️ WARNING:** Never run CLI and Docker simultaneously - causes CSV corruption. Stop Docker before running CLI commands.

```bash
# Start server (local development with existing data)
docker compose -f docker-compose.local.yml up -d

# Start server (production - empty market_data, will sync from scratch)
docker compose up -d

# Rebuild and start
docker compose -f docker-compose.local.yml up -d --build

# View logs
docker logs aipriceaction --tail 50
docker logs aipriceaction -f

# Stop and remove
docker compose -f docker-compose.local.yml down

# Check container stats
docker stats aipriceaction
```

### Useful Scripts
```bash
# Pull all intervals (daily, hourly, minute)
./scripts/pull_all.sh

# Pull specific intervals
./scripts/pull_daily.sh
./scripts/pull_hourly.sh
./scripts/pull_minute.sh

# Start server
./scripts/serve.sh

# Verify API endpoints
./scripts/api_verification.sh

# Fix corrupted CSV files
./scripts/fix_corrupted_csvs.sh
```

## API Usage Examples

### Mode Parameter

The API supports two modes via the `mode` query parameter:
- **`vn`** (default): Vietnamese stocks (market_data/)
- **`crypto`**: Cryptocurrencies (crypto_data/)

### VN Mode (Vietnamese Stocks)

```bash
# Get VN stock data (default mode)
curl "http://localhost:3000/tickers?symbol=VCB&interval=1D&limit=10"

# Explicit VN mode
curl "http://localhost:3000/tickers?symbol=VCB&mode=vn&interval=1D&limit=10"

# VN ticker groups
curl "http://localhost:3000/tickers/group"
curl "http://localhost:3000/tickers/group?mode=vn"
# Returns: {"BANKING": ["VCB", "BID", "CTG"], "TECH": ["FPT", "CMG"], ...}
```

### Crypto Mode

```bash
# Get crypto data
curl "http://localhost:3000/tickers?symbol=BTC&mode=crypto&interval=1D&limit=10"

# Multiple cryptos
curl "http://localhost:3000/tickers?symbol=BTC&symbol=ETH&symbol=XRP&mode=crypto&interval=1D&limit=5"

# Crypto ticker groups
curl "http://localhost:3000/tickers/group?mode=crypto"
# Returns: {"CRYPTO_TOP_100": ["BTC", "ETH", "USDT", ...]}

# CSV format
curl "http://localhost:3000/tickers?symbol=ETH&mode=crypto&interval=1D&limit=100&format=csv"
```

### Common Parameters (Both Modes)

```bash
# Interval options: 1D (daily), 1H (hourly), 1m (minute)
curl "http://localhost:3000/tickers?symbol=VCB&interval=1H&limit=24"

# Date range filtering
curl "http://localhost:3000/tickers?symbol=BTC&mode=crypto&start_date=2024-01-01&end_date=2024-12-31"

# CSV format (both modes)
curl "http://localhost:3000/tickers?symbol=VCB&format=csv&limit=100"

# Force disk read (bypass cache)
curl "http://localhost:3000/tickers?symbol=VCB&cache=false"

# Aggregated intervals (5m, 15m, 30m, 1W, 2W, 1M)
curl "http://localhost:3000/tickers?symbol=BTC&mode=crypto&interval=5m&limit=100"
```

### Legacy Price Format (VN Mode Only)

```bash
# Legacy format: divide VN stock prices by 1000 (ignored for crypto)
curl "http://localhost:3000/tickers?symbol=VCB&legacy=true"
# VCB close: 60.3 (instead of 60300)

# Crypto mode ignores legacy parameter
curl "http://localhost:3000/tickers?symbol=BTC&mode=crypto&legacy=true"
# BTC price: 95555.28 (unchanged)
```

### Upload API (File Storage)

**Session-based file storage for markdown documents and images.**

For complete API documentation, see [docs/UPLOAD_API.md](docs/UPLOAD_API.md)

```bash
# Generate a session ID and secret (client-side)
SESSION_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
SECRET=$(uuidgen | tr '[:upper:]' '[:lower:]')

# Upload markdown file (secret required)
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@notes.md"

# Upload image file (secret required)
curl -X POST "http://localhost:3000/upload/image?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@screenshot.png"

# Retrieve markdown file (public by default - no secret needed)
curl "http://localhost:3000/uploads/$SESSION_ID/markdown/notes.md"

# Retrieve image file (public by default - no secret needed)
curl "http://localhost:3000/uploads/$SESSION_ID/images/screenshot.png"
```

**Directory Structure:**
```
uploads/
└── {session-uuid}/
    ├── metadata.json  # Session config: secret, is_public, created_at
    ├── markdown/      # .md, .markdown, .txt files (max 5MB)
    └── images/        # .jpg, .png, .gif, .webp, .svg files (max 10MB)
```

**Key Features:**
- **Secret-based authentication**: All uploads require a client-generated secret (min 8 chars, recommended 32+)
- **Public/private sessions**: `is_public: true` (default) allows public read access without secret
- Session-based isolation using UUID identifiers
- File type validation (extension + MIME type)
- Security: filename sanitization, path traversal prevention, SVG script blocking
- Duplicate file detection (returns 409 Conflict)
- Rate limiting via existing middleware (5000 req/s per IP)

**Testing:**
```bash
# Run upload API tests (13 tests)
./scripts/test-upload.sh

# Test against different server
./scripts/test-upload.sh https://api.aipriceaction.com
```

**Docker Volume:**
The `uploads/` directory is mounted as a volume for persistence:
- `docker-compose.yml`: `./uploads:/app/uploads`
- `docker-compose.local.yml`: `/Volumes/data/workspace/aipriceaction/uploads:/app/uploads`

## Architecture

### 4-Layer Structure

```
┌─────────────────────────────────────────────────────┐
│  CLI Layer (commands/)                              │
│  - pull, serve, status, doctor, import-legacy       │
└─────────────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Service Layer (services/)                          │
│  - VciClient: VCI API integration                   │
│  - DataSync: Sync orchestration                     │
│  - DataStore: Dual-layer caching                    │
│  - CSV Enhancement: Technical indicators            │
└─────────────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Server Layer (server/)                             │
│  - Axum REST API with background workers            │
└─────────────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Storage: market_data/{TICKER}/{INTERVAL}.csv       │
│  - 20-column format with technical indicators       │
└─────────────────────────────────────────────────────┘
```

### Key Components

**Services (`src/services/`)**
- **VciClient** (`vci.rs`): VCI API client with rate limiting (30 req/min), retry logic, batch fetching
- **DataSync** (`data_sync.rs`): Orchestrates syncing - batch fetching, dividend detection, data merging
- **DataStore** (`data_store.rs`): Dual-layer cache with dual-mode support:
  - **VN DataStore**: Memory cache for last 2 years daily data (~40MB), disk cache for hourly/minute (500MB limit, 15s TTL)
  - **Crypto DataStore**: Memory cache for last 2 years daily data (~23MB), disk cache for hourly/minute (500MB limit, 15s TTL)
  - Total memory: ~63MB for daily data, 1GB disk cache (500MB per mode)
- **TickerFetcher** (`ticker_fetcher.rs`): Handles API calls, categorizes tickers (resume vs full)
- **CSV Enhancer** (`csv_enhancer.rs`): Adds technical indicators during sync (single-phase enhancement)

**Server (`src/server/`)**
- **API** (`api.rs`): Axum REST API with mode parameter support
  - `GET /tickers?mode=vn|crypto` - Query stock/crypto data
  - `GET /health` - System health/stats (separate VN + crypto stats)
  - `GET /tickers/group?mode=vn|crypto` - Ticker/crypto groupings
- **Background Workers** (`worker/`):
  - `daily_worker`: Syncs daily stock data (15s trading hours, 5min off-hours)
  - `slow_worker`: Syncs hourly/minute stock data (5min trading, 30min off-hours)
  - `crypto_worker`: Syncs all crypto intervals sequentially (15min, 24/7)

**Models (`src/models/`)**
- **StockData**: OHLCV + 10 technical indicators (ma10-200, scores, changes)
- **Interval**: Daily (1D), Hourly (1H), Minute (1m)

### Data Flow Patterns

**Sync Flow (CLI `pull`):**
1. Pre-scan: Read last dates from CSV files
2. Categorize: Resume tickers vs Full history tickers
3. Batch fetch: VCI API (adaptive start date)
4. Dividend check: Detect price adjustments (daily only)
5. Merge: Smart merging with existing data
6. Enhance: Calculate indicators in-memory (single-phase)
7. Write: Write enhanced CSV once (20 columns)

**API Request Flow:**
1. Request: `GET /tickers?symbol=VCB&interval=1D`
2. Cache check: Memory (daily) or Disk (hourly/minute)
3. If expired/missing: Read from disk, update cache
4. Filter: Apply date range and limit
5. Transform: Apply legacy scaling if needed
6. Response: JSON or CSV format

**Background Worker Flow (Stock Workers):**
1. Check trading hours (9:00-15:00 ICT)
2. Validate CSV files (auto-repair corruption)
3. Sync data via DataSync
4. Enhance CSVs with indicators
5. Reload into memory cache
6. Update health stats
7. Sleep until next iteration

**Crypto Worker Flow:**
1. Load crypto symbols from crypto_top_100.json (filter ignored cryptos)
2. For each interval (Daily → Hourly → Minute):
   - Sync all 98 cryptos via CryptoSync (resume mode)
   - Enhance CSVs with technical indicators
3. Update health stats (crypto_last_sync, crypto_iteration_count)
4. Write log to crypto_data/crypto_worker.log
5. Sleep 15 minutes (fixed interval, 24/7)

## Critical Implementation Details

### Price Format Convention (CRITICAL)

**Stock prices are stored in FULL VND format** (not divided by 1000):
- Stock tickers (VCB, FPT): 60300.0 (not 60.3)
- Market indices (VNINDEX, VN30): 1642.64 (actual value)
- API returns full prices → CSV stores full prices → Memory stores full prices
- Only divide by 1000 when `legacy=true` flag is used (backward compatibility)
- Index tickers defined in `INDEX_TICKERS` constant in `src/constants.rs`

### CSV Format (20 columns)

```
ticker,time,open,high,low,close,volume,
ma10,ma20,ma50,ma100,ma200,
ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,
close_changed,volume_changed,total_money_changed
```

**Column Details:**
- **Columns 1-7**: OHLCV data (ticker, time, open, high, low, close, volume)
- **Columns 8-12**: Moving averages (MA10, MA20, MA50, MA100, MA200)
- **Columns 13-17**: MA scores (percentage deviation from each MA)
- **Column 18**: `close_changed` - Price change percentage from previous period
- **Column 19**: `volume_changed` - Volume change percentage from previous period
- **Column 20**: `total_money_changed` - Money flow in VND: `(price_change × volume)`

- All CSV files follow this enhanced format (added in v0.3.0, updated to 20 columns in v0.3.1)
- Technical indicators calculated during sync (single-phase enhancement)
- Time format: "YYYY-MM-DD" (daily) or "YYYY-MM-DD HH:MM:SS" (hourly/minute)
- `total_money_changed` represents the absolute money flow in Vietnamese Dong

### File Structure

```
market_data/                # VN stocks (282 tickers)
├── VCB/
│   ├── 1D.csv             # Daily data (20 columns)
│   ├── 1h.csv             # Hourly data
│   └── 1m.csv             # Minute data
├── FPT/
│   ├── 1D.csv
│   ├── 1h.csv
│   └── 1m.csv
└── VNINDEX/
    ├── 1D.csv
    ├── 1h.csv
    └── 1m.csv

crypto_data/                # Cryptocurrencies (98 cryptos)
├── BTC/
│   ├── 1D.csv             # Daily data (20 columns, same format)
│   ├── 1H.csv             # Hourly data
│   └── 1m.csv             # Minute data
├── ETH/
│   ├── 1D.csv
│   ├── 1H.csv
│   └── 1m.csv
└── XRP/
    ├── 1D.csv
    ├── 1H.csv
    └── 1m.csv
```

**Note:** Both `market_data/` and `crypto_data/` use identical 20-column CSV format, enabling 95%+ code reuse.

### Adaptive Resume Mode

- Reads last date from each ticker's CSV file
- Uses minimum (earliest) date across all tickers as batch fetch start
- Automatically scales: 1 day if run daily, 7 days if missed a week
- No fixed `resume_days` configuration needed
- Fallback: 2 days if CSV read fails

### Dividend Detection

When a dividend is issued, VCI API returns price-adjusted historical data:
- Compares recent API data (3 weeks to 1 week ago) with existing CSV
- Threshold: >2% price difference
- Action: Delete ALL CSV files (1D, 1H, 1m) and re-download full history
- Ensures data consistency across all intervals

### Single-Phase CSV Enhancement

**Old approach:** Fetch → Write raw → Read → Enhance → Write enhanced (2 writes)
**New approach:** Fetch → Enhance in-memory → Write enhanced once (1 write)

Benefits:
- Faster sync times
- Safe with file locking during API writes
- No redundant disk I/O

### Batch API Strategy

- Resume mode: 50 tickers/batch (daily), 20 (hourly), 3 (minute)
- Full history: 2 tickers/batch (more reliable for large data)
- Rate limiting: 30 calls/min, 2-3s between batches
- Retry: Exponential backoff, max 5 retries

### Trading Hours

- Hardcoded in `src/services/trading_hours.rs`
- Vietnam stock market: 9:00-15:00 ICT (UTC+7)
- Affects worker sync frequency

## Common Patterns

### Adding a New API Endpoint

1. Add handler function in `src/server/api.rs`
2. Register route in `src/server/mod.rs`
3. Update `scripts/test-integration.sh` to test it
4. Document in `docs/API.md`

### Adding a New Technical Indicator

1. Add fields to `StockData` in `src/models/stock_data.rs`
2. Update `CSV_ENHANCED_COLUMNS` count in `src/constants.rs`
3. Add column indices in `csv_column` module
4. Implement calculation in `src/services/csv_enhancer.rs`
5. Update CSV parser in `src/services/csv_parser.rs`
6. Update API response in `src/server/api.rs` (StockDataResponse)

### Working with Index Tickers

Index tickers (VNINDEX, VN30) are treated differently:
- No dividend detection (indices don't have dividends)
- No legacy price scaling (keep original values)
- Defined in `INDEX_TICKERS` constant in `src/constants.rs`

To add a new index:
```rust
// src/constants.rs
pub const INDEX_TICKERS: &[&str] = &["VNINDEX", "VN30", "NEW_INDEX"];
```

### File Locking

The codebase uses `fs2` for cross-process file locking:
- Enables safe concurrent access during background sync
- API reads are safe even during worker writes
- Always use `OpenOptions::new().read(true).open()` pattern

## Configuration

### Environment Variables

- `MAX_CACHE_SIZE_MB`: Disk cache limit (default: 500MB)
- `RUST_LOG`: Logging level (`info`, `debug`, `trace`)
- `PORT`: Server port (default: 3000)
- `CRYPTO_WORKER_TARGET_URL`: Alternative API URL for crypto data (optional). If set, the crypto worker will fetch data from this API instead of CryptoCompare. Example: `https://api.aipriceaction.com`
- `CRYPTO_WORKER_TARGET_HOST`: Host header for crypto API requests (optional). Used with `CRYPTO_WORKER_TARGET_URL` for CDN/proxy bypass. Example: `api.aipriceaction.com`

#### Crypto Data Source Configuration

By default, the crypto worker fetches data directly from CryptoCompare API. However, if your server IP is blocked by CryptoCompare, you can configure it to fetch data from another aipriceaction instance instead:

**Usage:**
```bash
# Set environment variables
export CRYPTO_WORKER_TARGET_URL="https://api.aipriceaction.com"
export CRYPTO_WORKER_TARGET_HOST="api.aipriceaction.com"  # Optional, for CDN/proxy bypass

# Start server (worker-only feature)
./target/release/aipriceaction serve
```

**How it works:**
- The crypto worker will fetch all crypto data in a single API call: `/tickers?mode=crypto&interval={interval}&start_date={date}`
- Supports all intervals: 1D (daily), 1H (hourly), 1m (minute)
- Automatic fallback: If the alternative API fails, it retries with CryptoCompare
- Worker-only: CLI commands (`crypto-pull`) always use CryptoCompare directly

**Example scenario:**
1. Server A: Has access to CryptoCompare, runs API server
2. Server B: Blocked by CryptoCompare, runs worker pointing to Server A
3. Server B worker fetches crypto data from Server A's `/tickers` endpoint
4. Server B serves the synced data via its own API

### Key Files

- `ticker_group.json`: Ticker groupings (VN30, BANKING, etc.)
- `market_data/`: CSV storage directory
- `Cargo.toml`: Dependencies and build config
- `Dockerfile`: Multi-stage build for production
- `docker-compose.local.yml`: Local development (absolute path to market_data)
- `docker-compose.yml`: Production deployment (relative path)

## Performance Notes

- **Full sync**: Daily 6-8min | Hourly 2.5-4hrs | Minute 30-50hrs (for 282 tickers)
- **Resume sync**: Daily 4-6s | Hourly 20-25s | Minute 50-60s (CLI) / 90-100s (Docker)
- **Memory**: ~32MB (Docker) / ~19MB (daily data cache)
- **API throughput**: ~30 requests/minute (VCI rate limit)
- **Optimization (v0.3.1)**: Smart buffer slicing (200 records + cutoff) → 43% faster minute sync

## Common Issues

### Concurrent Access (CLI + Docker)

**⚠️ CRITICAL:** Never run CLI and Docker simultaneously - both write to `market_data/` causing CSV corruption (race conditions, file locking conflicts). Always stop Docker before running CLI commands:
```bash
docker compose -f docker-compose.local.yml down
./target/release/aipriceaction pull --intervals 1m
docker compose -f docker-compose.local.yml up -d
```

### Docker Volume Mounting

Use `docker-compose.local.yml` for local development because:
- `docker-compose.yml` uses relative path `./market_data`
- May not work properly with symlinks or Colima on macOS
- `docker-compose.local.yml` uses absolute path `/Volumes/data/workspace/aipriceaction/market_data`

### CSV Corruption

If CSV files get corrupted:
```bash
# Auto-detect and repair
cargo run -- doctor

# Or manually fix with script
./scripts/fix_corrupted_csvs.sh
```

### API Rate Limiting

VCI API limits: ~30 requests/minute
- Built-in rate limiting in `VciClient`
- Automatic retry with exponential backoff
- If rate limited, wait 60 seconds before retry

### Cache Not Updating

Cache TTL is 60 seconds. To force refresh:
- Add `cache=false` query parameter: `/tickers?symbol=VCB&cache=false`
- Or restart the server to clear memory cache
- run pull without filtering in background so we can check shell outputs