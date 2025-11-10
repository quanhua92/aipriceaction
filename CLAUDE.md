# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**aipriceaction** is a Vietnamese stock market data management system that fetches, stores, and serves market data from the VCI (Vietcap) API. It provides both a CLI for data synchronization and a REST API server with in-memory caching.

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
./scripts/test-integration.sh    # 13 integration tests
./scripts/test-analysis.sh        # 10 analysis API tests
./scripts/test-aggregated.sh      # 27 aggregated interval tests

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
- **Total: 13 tests**

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
```

#### Expected Results
- **Integration**: 13/13 tests passed
- **Analysis**: 10/10 tests passed
- **Aggregated**: 27/27 tests passed
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
- **DataStore** (`data_store.rs`): Dual-layer cache:
  - Memory cache: Last 1 year daily data (4GB limit, 60s TTL)
  - Disk cache: LRU cache for hourly/minute (500MB limit, 60s TTL)
- **TickerFetcher** (`ticker_fetcher.rs`): Handles API calls, categorizes tickers (resume vs full)
- **CSV Enhancer** (`csv_enhancer.rs`): Adds technical indicators during sync (single-phase enhancement)

**Server (`src/server/`)**
- **API** (`api.rs`): Axum REST API
  - `GET /tickers` - Query stock data
  - `GET /health` - System health/stats
  - `GET /tickers/group` - Ticker groupings
- **Background Workers** (`worker/`):
  - `daily_worker`: Syncs daily data (15s trading hours, 5min off-hours)
  - `slow_worker`: Syncs hourly/minute (5min trading, 30min off-hours)

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

**Background Worker Flow:**
1. Check trading hours (9:00-15:00 ICT)
2. Validate CSV files (auto-repair corruption)
3. Sync data via DataSync
4. Enhance CSVs with indicators
5. Reload into memory cache
6. Update health stats
7. Sleep until next iteration

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
market_data/
├── VCB/
│   ├── 1D.csv   # Daily data (20 columns)
│   ├── 1h.csv   # Hourly data
│   └── 1m.csv   # Minute data
├── FPT/
│   ├── 1D.csv
│   ├── 1h.csv
│   └── 1m.csv
└── VNINDEX/
    ├── 1D.csv
    ├── 1h.csv
    └── 1m.csv
```

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