# PostgreSQL Migration Plan

**Status:** Planning Phase
**Target:** Replace CSV-based storage with PostgreSQL + TimescaleDB
**Date:** 2025-11-19

---

## Table of Contents

1. [Motivation & Benefits](#motivation--benefits)
2. [Architecture Overview](#architecture-overview)
3. [Technology Stack](#technology-stack)
4. [Database Schema Design](#database-schema-design)
5. [Distributed Workers Architecture](#distributed-workers-architecture)
6. [Migration Strategy](#migration-strategy)
7. [Code Changes](#code-changes)
8. [CSV Export Command](#csv-export-command)
9. [Testing & Validation](#testing--validation)
10. [Deployment Plan](#deployment-plan)
11. [Performance Expectations](#performance-expectations)
12. [Timeline & Phases](#timeline--phases)

---

## Motivation & Benefits

### Current Pain Points (CSV-based)

1. **Concurrent Access Issues**
   - CLI + Docker cannot run simultaneously → CSV corruption
   - Complex file locking logic (`fs2` crate)
   - Race conditions during background sync

2. **Query Performance**
   - Linear scan for date range queries (binary search at best)
   - No indexes - slow for complex queries
   - Aggregated intervals (5m, 15m, 30m) computed on every request

3. **Cache Complexity**
   - Manual LRU eviction, TTL tracking
   - Dual-layer cache (memory + disk) with size limits
   - Cache validation logic (~200 lines in `data_store.rs:562-740`)

4. **Operational Overhead**
   - CSV corruption repair needed (`doctor` command)
   - Manual dividend detection + full re-download
   - No transaction guarantees

### Benefits with PostgreSQL

✅ **Concurrent Access**: ACID transactions, multiple workers/CLI safe
✅ **Query Performance**: Indexed lookups, 2-10x faster range queries
✅ **Simpler Caching**: PostgreSQL query cache + shared_buffers
✅ **Pre-computed Aggregations**: TimescaleDB continuous aggregates (5m, 15m, 30m ready instantly)
✅ **Data Integrity**: No corruption, referential integrity, constraints
✅ **Distributed Workers**: Multiple servers can sync simultaneously without conflicts
✅ **Better Analytics**: Complex SQL queries for analysis endpoints
✅ **Scalability**: Easy to add more tickers, intervals, longer history
✅ **Operational**: Standard backup tools (pg_dump), monitoring, migrations

---

## Architecture Overview

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│  Distributed Workers (Multiple Servers)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  Server A    │  │  Server B    │  │  Server C    │      │
│  │  - daily     │  │  - slow      │  │  - crypto    │      │
│  │  - slow      │  │  - crypto    │  │              │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                  │                  │              │
│         └──────────────────┼──────────────────┘              │
│                            ↓                                 │
│              ┌─────────────────────────────┐                │
│              │  PostgreSQL + TimescaleDB   │                │
│              │  - market_data (VN stocks)  │                │
│              │  - crypto_data (crypto)     │                │
│              │  - Hypertables (time-based) │                │
│              │  - Continuous Aggregates    │                │
│              │  - Advisory Locks           │                │
│              └─────────────┬───────────────┘                │
│                            ↓                                 │
│              ┌─────────────────────────────┐                │
│              │  API Servers (Multiple)     │                │
│              │  - SQLx connection pool     │                │
│              │  - Optional memory cache    │                │
│              └─────────────────────────────┘                │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  CSV Files (Optional Backup - Export Only)                  │
│  - On-demand export via CLI command                         │
│  - Not used by application (read-only backup)               │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Single PostgreSQL Instance**
   - Two hypertables: `market_data`, `crypto_data`
   - Shared connection pool, separate tables by mode
   - Simpler operations than multiple databases

2. **TimescaleDB Extension**
   - Automatic time-based partitioning (chunks)
   - Continuous aggregates for 5m, 15m, 30m intervals
   - 90%+ compression for old data

3. **Distributed Workers**
   - Workers coordinate via PostgreSQL (no external coordination needed)
   - Advisory locks prevent duplicate work
   - Upsert operations for idempotency
   - Timestamp-based deduplication

4. **CSV Export (On-Demand Only)**
   - CLI command: `cargo run -- export-to-csv`
   - No automatic daily export (reduces I/O overhead)
   - CSV files serve as backup/legacy compatibility only

---

## Technology Stack

### Core Dependencies

```toml
[dependencies]
# PostgreSQL client with compile-time query validation
sqlx = { version = "0.7", features = ["runtime-tokio-native-tls", "postgres", "chrono", "migrate"] }

# Connection pooling (built into sqlx)
# sqlx::Pool handles connection pooling automatically

# Async runtime (already in use)
tokio = { version = "1", features = ["full"] }

# Serialization (already in use)
serde = { version = "1", features = ["derive"] }
serde_json = "1"

# Time handling (already in use)
chrono = { version = "0.4", features = ["serde"] }
```

### Why SQLx?

1. **Compile-Time Query Validation**
   - Queries checked against live database at compile time
   - Catches SQL errors before runtime
   - Type-safe result mapping

2. **Built-In Connection Pooling**
   - No need for `deadpool-postgres`
   - Configurable pool size, timeouts
   - Automatic connection health checks

3. **Migration Support**
   - Built-in migration runner: `sqlx migrate run`
   - Version control for schema changes
   - Rollback support

4. **Async/Await Native**
   - Works seamlessly with Tokio
   - Non-blocking database operations
   - Matches existing async codebase

### PostgreSQL Configuration

```bash
# docker-compose.yml
services:
  postgres:
    image: timescale/timescaledb:latest-pg16
    environment:
      POSTGRES_DB: aipriceaction
      POSTGRES_USER: aipriceaction
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      PGDATA: /var/lib/postgresql/data/pgdata
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    command:
      - "postgres"
      - "-c" "shared_buffers=512MB"      # 25% of RAM (for 2GB container)
      - "-c" "effective_cache_size=1GB"  # 50% of RAM
      - "-c" "work_mem=16MB"
      - "-c" "maintenance_work_mem=128MB"
      - "-c" "max_connections=100"
      - "-c" "random_page_cost=1.1"      # SSD optimization
```

---

## Database Schema Design

### Core Tables

```sql
-- migrations/001_initial_schema.sql

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Market data table (VN stocks)
CREATE TABLE market_data (
    ticker TEXT NOT NULL,
    interval TEXT NOT NULL,  -- '1D', '1H', '1m'
    time TIMESTAMPTZ NOT NULL,

    -- OHLCV
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume BIGINT NOT NULL,

    -- Moving Averages
    ma10 DOUBLE PRECISION,
    ma20 DOUBLE PRECISION,
    ma50 DOUBLE PRECISION,
    ma100 DOUBLE PRECISION,
    ma200 DOUBLE PRECISION,

    -- MA Scores (percentage deviation)
    ma10_score DOUBLE PRECISION,
    ma20_score DOUBLE PRECISION,
    ma50_score DOUBLE PRECISION,
    ma100_score DOUBLE PRECISION,
    ma200_score DOUBLE PRECISION,

    -- Change Indicators
    close_changed DOUBLE PRECISION,
    volume_changed DOUBLE PRECISION,
    total_money_changed DOUBLE PRECISION,

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (ticker, interval, time)
);

-- Crypto data table (identical schema)
CREATE TABLE crypto_data (
    ticker TEXT NOT NULL,
    interval TEXT NOT NULL,
    time TIMESTAMPTZ NOT NULL,

    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume BIGINT NOT NULL,

    ma10 DOUBLE PRECISION,
    ma20 DOUBLE PRECISION,
    ma50 DOUBLE PRECISION,
    ma100 DOUBLE PRECISION,
    ma200 DOUBLE PRECISION,

    ma10_score DOUBLE PRECISION,
    ma20_score DOUBLE PRECISION,
    ma50_score DOUBLE PRECISION,
    ma100_score DOUBLE PRECISION,
    ma200_score DOUBLE PRECISION,

    close_changed DOUBLE PRECISION,
    volume_changed DOUBLE PRECISION,
    total_money_changed DOUBLE PRECISION,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (ticker, interval, time)
);

-- Convert to hypertables (TimescaleDB magic)
SELECT create_hypertable('market_data', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

SELECT create_hypertable('crypto_data', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Create indexes for query performance
CREATE INDEX idx_market_ticker_interval_time
ON market_data (ticker, interval, time DESC);

CREATE INDEX idx_crypto_ticker_interval_time
ON crypto_data (ticker, interval, time DESC);

-- Additional indexes for analysis queries
CREATE INDEX idx_market_interval_time
ON market_data (interval, time DESC);

CREATE INDEX idx_crypto_interval_time
ON crypto_data (interval, time DESC);
```

### Continuous Aggregates (Pre-computed Intervals)

```sql
-- migrations/002_continuous_aggregates.sql

-- 5-minute aggregation from 1-minute data (VN stocks)
CREATE MATERIALIZED VIEW market_data_5m
WITH (timescaledb.continuous) AS
SELECT
    ticker,
    time_bucket('5 minutes', time) AS bucket,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume
FROM market_data
WHERE interval = '1m'
GROUP BY ticker, bucket;

-- Refresh policy (update every 10 minutes)
SELECT add_continuous_aggregate_policy('market_data_5m',
    start_offset => INTERVAL '1 day',
    end_offset => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '10 minutes'
);

-- 15-minute aggregation
CREATE MATERIALIZED VIEW market_data_15m
WITH (timescaledb.continuous) AS
SELECT
    ticker,
    time_bucket('15 minutes', time) AS bucket,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume
FROM market_data
WHERE interval = '1m'
GROUP BY ticker, bucket;

SELECT add_continuous_aggregate_policy('market_data_15m',
    start_offset => INTERVAL '1 day',
    end_offset => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '10 minutes'
);

-- 30-minute aggregation
CREATE MATERIALIZED VIEW market_data_30m
WITH (timescaledb.continuous) AS
SELECT
    ticker,
    time_bucket('30 minutes', time) AS bucket,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume
FROM market_data
WHERE interval = '1m'
GROUP BY ticker, bucket;

SELECT add_continuous_aggregate_policy('market_data_30m',
    start_offset => INTERVAL '1 day',
    end_offset => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '10 minutes'
);

-- Weekly aggregation from daily data
CREATE MATERIALIZED VIEW market_data_1w
WITH (timescaledb.continuous) AS
SELECT
    ticker,
    time_bucket('7 days', time) AS bucket,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume
FROM market_data
WHERE interval = '1D'
GROUP BY ticker, bucket;

SELECT add_continuous_aggregate_policy('market_data_1w',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day'
);

-- 2-week aggregation
CREATE MATERIALIZED VIEW market_data_2w
WITH (timescaledb.continuous) AS
SELECT
    ticker,
    time_bucket('14 days', time) AS bucket,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume
FROM market_data
WHERE interval = '1D'
GROUP BY ticker, bucket;

SELECT add_continuous_aggregate_policy('market_data_2w',
    start_offset => INTERVAL '60 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day'
);

-- Monthly aggregation
CREATE MATERIALIZED VIEW market_data_1m_agg
WITH (timescaledb.continuous) AS
SELECT
    ticker,
    time_bucket('30 days', time) AS bucket,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume
FROM market_data
WHERE interval = '1D'
GROUP BY ticker, bucket;

SELECT add_continuous_aggregate_policy('market_data_1m_agg',
    start_offset => INTERVAL '90 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day'
);

-- Repeat same views for crypto_data
-- (crypto_data_5m, crypto_data_15m, crypto_data_30m, crypto_data_1w, crypto_data_2w, crypto_data_1m_agg)
-- ... (same structure, just s/market_data/crypto_data/)
```

### Worker Coordination Table

```sql
-- migrations/003_worker_coordination.sql

-- Track worker sync progress (for distributed workers)
CREATE TABLE sync_progress (
    worker_id TEXT NOT NULL,
    mode TEXT NOT NULL,           -- 'vn' or 'crypto'
    interval TEXT NOT NULL,       -- '1D', '1H', '1m'
    ticker TEXT,                  -- NULL means all tickers
    last_sync_time TIMESTAMPTZ NOT NULL,
    last_data_time TIMESTAMPTZ,   -- Last data timestamp synced
    status TEXT NOT NULL,         -- 'running', 'completed', 'failed'
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,

    PRIMARY KEY (worker_id, mode, interval, COALESCE(ticker, ''))
);

CREATE INDEX idx_sync_progress_status
ON sync_progress (mode, interval, status, last_sync_time);

-- Worker heartbeat table (detect dead workers)
CREATE TABLE worker_heartbeat (
    worker_id TEXT PRIMARY KEY,
    worker_type TEXT NOT NULL,    -- 'daily', 'slow', 'crypto'
    hostname TEXT NOT NULL,
    last_heartbeat TIMESTAMPTZ NOT NULL,
    metadata JSONB
);

CREATE INDEX idx_worker_heartbeat_time
ON worker_heartbeat (last_heartbeat);
```

---

## Distributed Workers Architecture

### Design Goals

1. **No Central Coordinator**: Workers coordinate via PostgreSQL only
2. **Conflict Avoidance**: Multiple workers can run without stepping on each other
3. **Idempotent Operations**: Re-processing same data is safe
4. **Small Redundancy OK**: Prefer availability over strict deduplication
5. **Automatic Recovery**: Dead workers' tasks picked up by others

### Coordination Strategies

#### Strategy 1: Advisory Locks (Recommended)

Workers acquire PostgreSQL advisory locks before syncing a ticker/interval.

```rust
// src/services/distributed_sync.rs

use sqlx::postgres::PgAdvisoryLock;

async fn sync_ticker_with_lock(
    pool: &PgPool,
    ticker: &str,
    interval: Interval,
    mode: &str,
) -> Result<()> {
    // Generate lock ID from ticker + interval + mode
    let lock_id = generate_lock_id(ticker, interval, mode);

    // Try to acquire advisory lock (non-blocking)
    let lock_acquired = sqlx::query!(
        "SELECT pg_try_advisory_lock($1) AS acquired",
        lock_id
    )
    .fetch_one(pool)
    .await?
    .acquired
    .unwrap_or(false);

    if !lock_acquired {
        debug!("Another worker is syncing {}/{} - skipping", ticker, interval);
        return Ok(());
    }

    // Perform sync (only this worker will do it)
    let result = sync_ticker_data(ticker, interval, mode).await;

    // Release lock
    sqlx::query!(
        "SELECT pg_advisory_unlock($1)",
        lock_id
    )
    .execute(pool)
    .await?;

    result
}

fn generate_lock_id(ticker: &str, interval: Interval, mode: &str) -> i64 {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let mut hasher = DefaultHasher::new();
    format!("{}-{}-{}", mode, ticker, interval).hash(&mut hasher);
    hasher.finish() as i64
}
```

**Benefits:**
- Automatic lock release on connection drop (worker crash)
- No external dependencies (Redis, etc.)
- Fast (in-memory locks in PostgreSQL)

#### Strategy 2: Timestamp-Based Deduplication

Use `updated_at` timestamp to detect recent syncs.

```rust
async fn should_sync_ticker(
    pool: &PgPool,
    ticker: &str,
    interval: Interval,
    mode: &str,
) -> Result<bool> {
    let interval_str = interval.to_string();

    // Use match to select correct table at compile-time
    let last_update: Option<DateTime<Utc>> = match mode {
        "vn" => {
            sqlx::query_scalar!(
                r#"
                SELECT MAX(updated_at) as "last_update?"
                FROM market_data
                WHERE ticker = $1 AND interval = $2
                "#,
                ticker,
                interval_str
            )
            .fetch_optional(pool)
            .await?
        },
        "crypto" => {
            sqlx::query_scalar!(
                r#"
                SELECT MAX(updated_at) as "last_update?"
                FROM crypto_data
                WHERE ticker = $1 AND interval = $2
                "#,
                ticker,
                interval_str
            )
            .fetch_optional(pool)
            .await?
        },
        _ => return Err(Error::InvalidMode(mode.to_string())),
    };

    match last_update {
        Some(last_update) => {
            let age = Utc::now() - last_update;

            // Don't sync if updated in last 10 seconds (another worker just did it)
            if age.num_seconds() < 10 {
                debug!("Ticker {} recently synced ({}s ago) - skipping", ticker, age.num_seconds());
                return Ok(false);
            }

            Ok(true)
        },
        None => Ok(true), // No data yet, should sync
    }
}
```

**Benefits:**
- Simple logic
- Works across worker restarts
- No lock contention

#### Strategy 3: Upsert Operations (ON CONFLICT)

Use PostgreSQL upsert to handle duplicate inserts gracefully.

```rust
async fn insert_stock_data_batch(
    pool: &PgPool,
    table: &str,
    data: &[StockData],
) -> Result<()> {
    for chunk in data.chunks(1000) {
        let mut ticker_vec = Vec::new();
        let mut interval_vec = Vec::new();
        let mut time_vec = Vec::new();
        let mut open_vec = Vec::new();
        // ... (collect all fields)

        for record in chunk {
            ticker_vec.push(&record.ticker);
            interval_vec.push(record.interval.to_string());
            time_vec.push(record.time);
            open_vec.push(record.open);
            // ... (push all fields)
        }

        sqlx::query!(
            r#"
            INSERT INTO market_data (
                ticker, interval, time, open, high, low, close, volume,
                ma10, ma20, ma50, ma100, ma200,
                ma10_score, ma20_score, ma50_score, ma100_score, ma200_score,
                close_changed, volume_changed, total_money_changed
            )
            SELECT * FROM UNNEST(
                $1::text[], $2::text[], $3::timestamptz[], $4::float8[], $5::float8[],
                $6::float8[], $7::float8[], $8::bigint[],
                $9::float8[], $10::float8[], $11::float8[], $12::float8[], $13::float8[],
                $14::float8[], $15::float8[], $16::float8[], $17::float8[], $18::float8[],
                $19::float8[], $20::float8[], $21::float8[]
            )
            ON CONFLICT (ticker, interval, time)
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                ma10 = EXCLUDED.ma10,
                ma20 = EXCLUDED.ma20,
                ma50 = EXCLUDED.ma50,
                ma100 = EXCLUDED.ma100,
                ma200 = EXCLUDED.ma200,
                ma10_score = EXCLUDED.ma10_score,
                ma20_score = EXCLUDED.ma20_score,
                ma50_score = EXCLUDED.ma50_score,
                ma100_score = EXCLUDED.ma100_score,
                ma200_score = EXCLUDED.ma200_score,
                close_changed = EXCLUDED.close_changed,
                volume_changed = EXCLUDED.volume_changed,
                total_money_changed = EXCLUDED.total_money_changed,
                updated_at = NOW()
            "#,
            &ticker_vec as _,
            &interval_vec as _,
            &time_vec as _,
            &open_vec as _,
            // ... (pass all field vectors)
        )
        .execute(pool)
        .await?;
    }

    Ok(())
}
```

**Benefits:**
- Idempotent: Re-inserting same data is safe
- Handles race conditions automatically
- Updates existing records if data changed (e.g., dividend adjustment)

### Worker Lifecycle

```rust
// src/server/worker/distributed_daily_worker.rs

pub async fn run_distributed_daily_worker(
    pool: PgPool,
    health_stats: SharedHealthStats,
) {
    let worker_id = format!("daily-{}", uuid::Uuid::new_v4());
    let hostname = gethostname::gethostname().to_string_lossy().to_string();

    loop {
        // 1. Send heartbeat
        update_heartbeat(&pool, &worker_id, "daily", &hostname).await;

        // 2. Get list of tickers to sync
        let tickers = get_ticker_list("vn").await;

        // 3. Sync each ticker with advisory lock
        for ticker in tickers {
            match sync_ticker_with_lock(&pool, &ticker, Interval::Daily, "vn").await {
                Ok(_) => {
                    record_sync_progress(&pool, &worker_id, "vn", Interval::Daily, &ticker, "completed").await;
                }
                Err(e) => {
                    error!("Failed to sync {}: {}", ticker, e);
                    record_sync_progress(&pool, &worker_id, "vn", Interval::Daily, &ticker, "failed").await;
                }
            }
        }

        // 4. Update health stats
        update_health_stats(&pool, health_stats.clone()).await;

        // 5. Sleep (trading hours aware)
        let sleep_duration = if is_trading_hours() {
            Duration::from_secs(15)
        } else {
            Duration::from_secs(300)
        };

        tokio::time::sleep(sleep_duration).await;
    }
}

async fn update_heartbeat(
    pool: &PgPool,
    worker_id: &str,
    worker_type: &str,
    hostname: &str,
) -> Result<()> {
    sqlx::query!(
        r#"
        INSERT INTO worker_heartbeat (worker_id, worker_type, hostname, last_heartbeat, metadata)
        VALUES ($1, $2, $3, NOW(), '{}')
        ON CONFLICT (worker_id)
        DO UPDATE SET last_heartbeat = NOW()
        "#,
        worker_id,
        worker_type,
        hostname
    )
    .execute(pool)
    .await?;

    Ok(())
}

async fn record_sync_progress(
    pool: &PgPool,
    worker_id: &str,
    mode: &str,
    interval: Interval,
    ticker: &str,
    status: &str,
) -> Result<()> {
    sqlx::query!(
        r#"
        INSERT INTO sync_progress (
            worker_id, mode, interval, ticker, last_sync_time, status, started_at
        )
        VALUES ($1, $2, $3, $4, NOW(), $5, NOW())
        ON CONFLICT (worker_id, mode, interval, COALESCE(ticker, ''))
        DO UPDATE SET
            last_sync_time = NOW(),
            status = $5,
            completed_at = CASE WHEN $5 = 'completed' THEN NOW() ELSE NULL END
        "#,
        worker_id,
        mode,
        interval.to_string(),
        ticker,
        status
    )
    .execute(pool)
    .await?;

    Ok(())
}
```

### Failure Handling

**Dead Worker Detection:**

```sql
-- Find workers that haven't sent heartbeat in 5 minutes
SELECT worker_id, worker_type, hostname, last_heartbeat
FROM worker_heartbeat
WHERE last_heartbeat < NOW() - INTERVAL '5 minutes';
```

**Orphaned Task Recovery:**

```sql
-- Find tasks stuck in 'running' state for > 10 minutes
SELECT worker_id, mode, interval, ticker, started_at
FROM sync_progress
WHERE status = 'running'
  AND started_at < NOW() - INTERVAL '10 minutes';

-- Reset orphaned tasks (another worker will pick them up)
UPDATE sync_progress
SET status = 'failed', error_message = 'Worker timeout'
WHERE status = 'running'
  AND started_at < NOW() - INTERVAL '10 minutes';
```

### Load Distribution

**Strategy: Ticker-level sharding**

```rust
async fn get_assigned_tickers(
    worker_id: &str,
    all_tickers: &[String],
    total_workers: usize,
) -> Vec<String> {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    // Hash worker_id to get worker index
    let mut hasher = DefaultHasher::new();
    worker_id.hash(&mut hasher);
    let worker_index = (hasher.finish() as usize) % total_workers;

    // Assign tickers by hash (consistent hashing)
    all_tickers
        .iter()
        .filter(|ticker| {
            let mut hasher = DefaultHasher::new();
            ticker.hash(&mut hasher);
            (hasher.finish() as usize) % total_workers == worker_index
        })
        .cloned()
        .collect()
}
```

**Or: Let advisory locks handle it naturally**
- All workers try to sync all tickers
- Advisory locks ensure only one succeeds
- Self-balancing (fast workers pick up more work)

---

## Migration Strategy

### Phase 1: CSV Import Tool

Create a command to import existing CSV files into PostgreSQL.

```rust
// src/commands/import_csv_to_pg.rs

pub async fn import_csv_to_postgres(
    market_data_dir: &Path,
    crypto_data_dir: &Path,
    database_url: &str,
    verify: bool,
) -> Result<()> {
    let pool = PgPoolOptions::new()
        .max_connections(20)
        .connect(database_url)
        .await?;

    // Import VN stocks
    info!("Importing VN stocks from market_data/...");
    import_mode(&pool, market_data_dir, "market_data").await?;

    // Import crypto
    info!("Importing crypto from crypto_data/...");
    import_mode(&pool, crypto_data_dir, "crypto_data").await?;

    if verify {
        verify_import(&pool, market_data_dir, crypto_data_dir).await?;
    }

    Ok(())
}

async fn import_mode(
    pool: &PgPool,
    data_dir: &Path,
    table_name: &str,
) -> Result<()> {
    let tickers = std::fs::read_dir(data_dir)?
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir())
        .map(|e| e.file_name().to_string_lossy().to_string())
        .collect::<Vec<_>>();

    let total_tickers = tickers.len();
    info!("Found {} tickers to import", total_tickers);

    for (idx, ticker) in tickers.iter().enumerate() {
        let ticker_dir = data_dir.join(ticker);

        // Import each interval
        for interval in &["1D", "1h", "1m"] {
            let csv_path = ticker_dir.join(format!("{}.csv", interval));

            if !csv_path.exists() {
                continue;
            }

            info!(
                "[{}/{}] Importing {}/{} from {}",
                idx + 1, total_tickers, ticker, interval,
                csv_path.display()
            );

            let data = read_csv_file(&csv_path, ticker, interval)?;

            if data.is_empty() {
                continue;
            }

            // Batch insert (1000 rows per transaction)
            insert_stock_data_batch(pool, table_name, &data).await?;

            info!(
                "  ✓ Imported {} records for {}/{}",
                data.len(), ticker, interval
            );
        }
    }

    Ok(())
}

async fn verify_import(
    pool: &PgPool,
    market_data_dir: &Path,
    crypto_data_dir: &Path,
) -> Result<()> {
    info!("Verifying import...");

    // Count CSV records
    let csv_count = count_csv_records(market_data_dir)?;

    // Count PostgreSQL records
    let pg_count: i64 = sqlx::query_scalar!(
        "SELECT COUNT(*) FROM market_data"
    )
    .fetch_one(pool)
    .await?
    .unwrap_or(0);

    info!("CSV records: {}", csv_count);
    info!("PostgreSQL records: {}", pg_count);

    if csv_count != pg_count as usize {
        return Err(Error::Io(format!(
            "Record count mismatch: CSV={}, PG={}",
            csv_count, pg_count
        )));
    }

    info!("✓ Verification passed!");
    Ok(())
}
```

**CLI Usage:**

```bash
# Run migration with verification
cargo run -- import-csv-to-pg --verify

# Run without verification (faster)
cargo run -- import-csv-to-pg

# Estimated time: 10-20 minutes for full dataset
```

---

## Code Changes

### SQLx Macro Pattern for Dual-Mode Tables

Since we have two identical tables (`market_data` and `crypto_data`), we use a **match-based pattern** to maintain compile-time query validation:

**Important: `query_as!` vs `FromRow`**

```rust
// ❌ WRONG: query_as! does NOT use FromRow trait
#[derive(sqlx::FromRow)]  // Don't do this with query_as! macro
struct MyStruct { ... }

// ✅ CORRECT: Plain struct for query_as! macro
struct MyStruct {  // No derive needed
    ticker: String,
    open: f64,
    ...
}

// The macro generates mapping code at compile-time
let rows = sqlx::query_as!(MyStruct, "SELECT ticker, open FROM market_data")
    .fetch_all(pool)
    .await?;
```

**Key Differences:**
- `query_as!(Struct, "SQL...")` - Macro, generates mapping at compile-time, NO FromRow
- `query_as::<_, Struct>("SQL...")` - Function, REQUIRES `#[derive(sqlx::FromRow)]`

**Pattern for Dual Tables:**
```rust
// Plain struct for query_as! macro
struct StockDataRow {
    ticker: String,
    open: f64,
    volume: i64, // PostgreSQL BIGINT maps to i64
    ...
}

// Implement From trait for clean conversions
impl From<StockDataRow> for StockData {
    fn from(row: StockDataRow) -> Self {
        StockData {
            ticker: row.ticker,
            open: row.open,
            volume: row.volume as u64, // Convert i64 -> u64
            ...
        }
    }
}

// ✅ Compile-time checked with match
let rows = match mode {
    "vn" => {
        sqlx::query_as!(
            StockDataRow,
            "SELECT ticker, open, ... FROM market_data WHERE ticker = $1",
            ticker
        )
        .fetch_all(pool)
        .await?
    },
    "crypto" => {
        sqlx::query_as!(
            StockDataRow,
            "SELECT ticker, open, ... FROM crypto_data WHERE ticker = $1",
            ticker
        )
        .fetch_all(pool)
        .await?
    },
    _ => return Err(Error::InvalidMode(mode.to_string())),
};

// Convert using From trait (clean and idiomatic)
let data: Vec<StockData> = rows.into_iter().map(|row| row.into()).collect();
```

**Benefits:**
- ✅ Compile-time SQL validation (catches errors before runtime)
- ✅ Type-safe struct mapping (macro generates code)
- ✅ Column name/type verification at build time
- ✅ Clean conversion via `From` trait (idiomatic Rust)
- ✅ No `FromRow` derive needed for query_as! macro

**Trade-off:**
- Duplicated query strings (one per table)
- Slightly more code than dynamic queries
- Worth it for compile-time safety!

**SQLx Prepare Workflow:**
```bash
# 1. Start database
docker compose up -d postgres

# 2. Run migrations
sqlx migrate run

# 3. Generate query metadata (for offline builds)
cargo sqlx prepare

# 4. Now cargo build works without live database
cargo build
```

### 1. New PostgreSQL DataStore

```rust
// src/services/pg_data_store.rs

use sqlx::{PgPool, postgres::PgPoolOptions};
use crate::models::{StockData, Interval};

/// Database row struct for query_as! macro
/// Note: Do NOT use #[derive(sqlx::FromRow)] - query_as! macro generates its own mapping
struct StockDataRow {
    ticker: String,
    interval: String,
    time: DateTime<Utc>,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: i64,
    ma10: Option<f64>,
    ma20: Option<f64>,
    ma50: Option<f64>,
    ma100: Option<f64>,
    ma200: Option<f64>,
    ma10_score: Option<f64>,
    ma20_score: Option<f64>,
    ma50_score: Option<f64>,
    ma100_score: Option<f64>,
    ma200_score: Option<f64>,
    close_changed: Option<f64>,
    volume_changed: Option<f64>,
    total_money_changed: Option<f64>,
}

/// Convert database row to domain model
impl From<StockDataRow> for StockData {
    fn from(row: StockDataRow) -> Self {
        StockData {
            ticker: row.ticker,
            time: row.time,
            open: row.open,
            high: row.high,
            low: row.low,
            close: row.close,
            volume: row.volume as u64, // Convert i64 -> u64
            ma10: row.ma10,
            ma20: row.ma20,
            ma50: row.ma50,
            ma100: row.ma100,
            ma200: row.ma200,
            ma10_score: row.ma10_score,
            ma20_score: row.ma20_score,
            ma50_score: row.ma50_score,
            ma100_score: row.ma100_score,
            ma200_score: row.ma200_score,
            close_changed: row.close_changed,
            volume_changed: row.volume_changed,
            total_money_changed: row.total_money_changed,
        }
    }
}

pub struct PgDataStore {
    pool: PgPool,
    // Optional: Keep memory cache for hot data (last 24h, popular tickers)
    memory_cache: Arc<RwLock<HashMap<CacheKey, CachedData>>>,
}

impl PgDataStore {
    pub async fn new(database_url: &str) -> Result<Self> {
        let pool = PgPoolOptions::new()
            .max_connections(20)
            .connect(database_url)
            .await?;

        Ok(Self {
            pool,
            memory_cache: Arc::new(RwLock::new(HashMap::new())),
        })
    }

    /// Get data for tickers with date range filtering
    pub async fn get_data(
        &self,
        tickers: Vec<String>,
        interval: Interval,
        mode: &str,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
        limit: Option<usize>,
    ) -> Result<HashMap<String, Vec<StockData>>> {
        let table = if mode == "vn" { "market_data" } else { "crypto_data" };

        let mut result = HashMap::new();

        for ticker in tickers {
            let data = self.query_ticker_data(
                table,
                &ticker,
                interval,
                start_date,
                end_date,
                limit,
            ).await?;

            if !data.is_empty() {
                result.insert(ticker, data);
            }
        }

        Ok(result)
    }

    async fn query_ticker_data(
        &self,
        table: &str,
        ticker: &str,
        interval: Interval,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
        limit: Option<usize>,
    ) -> Result<Vec<StockData>> {
        let interval_str = interval.to_string();
        let limit_i64 = limit.map(|l| l as i64).unwrap_or(252);

        // Match on table name for compile-time checking
        // query_as! macro generates mapping code automatically
        let rows = match table {
            "market_data" => {
                sqlx::query_as!(
                    StockDataRow,
                    r#"
                    SELECT
                        ticker, interval, time,
                        open, high, low, close, volume,
                        ma10, ma20, ma50, ma100, ma200,
                        ma10_score, ma20_score, ma50_score, ma100_score, ma200_score,
                        close_changed, volume_changed, total_money_changed
                    FROM market_data
                    WHERE ticker = $1
                      AND interval = $2
                      AND ($3::timestamptz IS NULL OR time >= $3)
                      AND ($4::timestamptz IS NULL OR time <= $4)
                    ORDER BY time DESC
                    LIMIT $5
                    "#,
                    ticker,
                    interval_str,
                    start_date,
                    end_date,
                    limit_i64
                )
                .fetch_all(&self.pool)
                .await?
            },
            "crypto_data" => {
                sqlx::query_as!(
                    StockDataRow,
                    r#"
                    SELECT
                        ticker, interval, time,
                        open, high, low, close, volume,
                        ma10, ma20, ma50, ma100, ma200,
                        ma10_score, ma20_score, ma50_score, ma100_score, ma200_score,
                        close_changed, volume_changed, total_money_changed
                    FROM crypto_data
                    WHERE ticker = $1
                      AND interval = $2
                      AND ($3::timestamptz IS NULL OR time >= $3)
                      AND ($4::timestamptz IS NULL OR time <= $4)
                    ORDER BY time DESC
                    LIMIT $5
                    "#,
                    ticker,
                    interval_str,
                    start_date,
                    end_date,
                    limit_i64
                )
                .fetch_all(&self.pool)
                .await?
            },
            _ => return Err(Error::InvalidTable(table.to_string())),
        };

        // Convert StockDataRow to StockData using From trait
        let mut data: Vec<StockData> = rows
            .into_iter()
            .map(|row| row.into())
            .collect();

        // Sort ascending (API returns newest first, but we want chronological)
        data.sort_by(|a, b| a.time.cmp(&b.time));

        Ok(data)
    }

    /// Insert batch of stock data (used by workers)
    pub async fn insert_batch(
        &self,
        table: &str,
        data: Vec<StockData>,
    ) -> Result<()> {
        insert_stock_data_batch(&self.pool, table, &data).await
    }

    /// Get latest data timestamp for a ticker/interval (for resume sync)
    pub async fn get_latest_timestamp(
        &self,
        table: &str,
        ticker: &str,
        interval: Interval,
    ) -> Result<Option<DateTime<Utc>>> {
        let interval_str = interval.to_string();

        let result = match table {
            "market_data" => {
                sqlx::query_scalar!(
                    r#"
                    SELECT MAX(time) as "latest_time?"
                    FROM market_data
                    WHERE ticker = $1 AND interval = $2
                    "#,
                    ticker,
                    interval_str
                )
                .fetch_optional(&self.pool)
                .await?
            },
            "crypto_data" => {
                sqlx::query_scalar!(
                    r#"
                    SELECT MAX(time) as "latest_time?"
                    FROM crypto_data
                    WHERE ticker = $1 AND interval = $2
                    "#,
                    ticker,
                    interval_str
                )
                .fetch_optional(&self.pool)
                .await?
            },
            _ => return Err(Error::InvalidTable(table.to_string())),
        };

        Ok(result)
    }

    /// Get aggregated data (5m, 15m, 30m, 1W, 2W, 1M)
    pub async fn get_aggregated_data(
        &self,
        tickers: Vec<String>,
        aggregated_interval: AggregatedInterval,
        mode: &str,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
        limit: Option<usize>,
    ) -> Result<HashMap<String, Vec<StockData>>> {
        let view_name = format!(
            "{}_{}",
            if mode == "vn" { "market_data" } else { "crypto_data" },
            aggregated_interval.to_view_suffix()
        );

        // Query continuous aggregate view (pre-computed!)
        let mut result = HashMap::new();

        for ticker in tickers {
            let data = self.query_aggregated_ticker(
                &view_name,
                &ticker,
                start_date,
                end_date,
                limit,
            ).await?;

            if !data.is_empty() {
                result.insert(ticker, data);
            }
        }

        Ok(result)
    }
}
```

### 2. Update DataSync to Use PostgreSQL

```rust
// src/services/data_sync.rs

impl DataSync {
    pub async fn sync_interval(
        &self,
        interval: Interval,
        pg_store: &PgDataStore,
    ) -> Result<SyncStats> {
        let tickers = self.get_ticker_list().await?;

        for ticker in tickers {
            // 1. Get latest timestamp from PostgreSQL
            let last_date = pg_store.get_latest_timestamp(
                "market_data",
                &ticker,
                interval
            ).await?;

            // 2. Fetch new data from VCI API
            let new_data = self.fetch_ticker_data(
                &ticker,
                interval,
                last_date.map(|d| d + chrono::Duration::days(1))
            ).await?;

            if new_data.is_empty() {
                continue;
            }

            // 3. Enhance with technical indicators
            let enhanced = enhance_data(new_data);

            // 4. Insert into PostgreSQL (upsert)
            pg_store.insert_batch("market_data", enhanced).await?;
        }

        Ok(stats)
    }
}
```

### 3. Update API Handlers

```rust
// src/server/api.rs

pub async fn get_tickers(
    Query(params): Query<TickerQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse> {
    let mode = params.mode.as_deref().unwrap_or("vn");

    let data = if let Some(agg_interval) = params.aggregated_interval {
        // Use continuous aggregates (pre-computed!)
        state.pg_store.get_aggregated_data(
            params.tickers,
            agg_interval,
            mode,
            params.start_date,
            params.end_date,
            params.limit,
        ).await?
    } else {
        // Regular query
        state.pg_store.get_data(
            params.tickers,
            params.interval,
            mode,
            params.start_date,
            params.end_date,
            params.limit,
        ).await?
    };

    // Transform to response format
    let response = format_response(data, params.format);

    Ok(response)
}
```

### 4. Update Background Workers

```rust
// src/server/worker/daily_worker.rs

pub async fn run_daily_worker(
    pg_store: Arc<PgDataStore>,
    health_stats: SharedHealthStats,
) {
    let worker_id = format!("daily-{}", uuid::Uuid::new_v4());

    loop {
        // Use distributed sync with advisory locks
        sync_ticker_with_lock(&pg_store, "VCB", Interval::Daily, "vn").await;

        // Update health stats
        update_health_stats(&pg_store, health_stats.clone()).await;

        // Sleep based on trading hours
        let sleep_duration = if is_trading_hours() {
            Duration::from_secs(15)
        } else {
            Duration::from_secs(300)
        };

        tokio::time::sleep(sleep_duration).await;
    }
}
```

---

## CSV Export Command

### On-Demand Export Tool

```rust
// src/commands/export_to_csv.rs

pub async fn export_to_csv(
    pg_store: &PgDataStore,
    output_dir: &Path,
    mode: &str,
    tickers: Option<Vec<String>>,
) -> Result<()> {
    let table = if mode == "vn" { "market_data" } else { "crypto_data" };

    // Get ticker list
    let tickers = if let Some(t) = tickers {
        t
    } else {
        get_all_tickers_from_db(&pg_store, table).await?
    };

    info!("Exporting {} tickers to CSV...", tickers.len());

    for (idx, ticker) in tickers.iter().enumerate() {
        let ticker_dir = output_dir.join(ticker);
        std::fs::create_dir_all(&ticker_dir)?;

        // Export each interval
        for interval in &[Interval::Daily, Interval::Hourly, Interval::Minute] {
            let csv_path = ticker_dir.join(interval.to_filename());

            info!(
                "[{}/{}] Exporting {}/{} to {}",
                idx + 1, tickers.len(), ticker, interval.to_filename(),
                csv_path.display()
            );

            // Query all data for this ticker/interval
            let data = pg_store.get_data(
                vec![ticker.clone()],
                *interval,
                mode,
                None, // No date filter
                None,
                None, // No limit
            ).await?;

            if let Some(ticker_data) = data.get(ticker) {
                write_csv_file(&csv_path, ticker_data)?;
                info!("  ✓ Exported {} records", ticker_data.len());
            }
        }
    }

    Ok(())
}

fn write_csv_file(path: &Path, data: &[StockData]) -> Result<()> {
    let mut writer = csv::Writer::from_path(path)?;

    // Write header
    writer.write_record(&[
        "ticker", "time", "open", "high", "low", "close", "volume",
        "ma10", "ma20", "ma50", "ma100", "ma200",
        "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
        "close_changed", "volume_changed", "total_money_changed"
    ])?;

    // Write data rows
    for record in data {
        writer.write_record(&[
            &record.ticker,
            &record.time.to_rfc3339(),
            &record.open.to_string(),
            &record.high.to_string(),
            &record.low.to_string(),
            &record.close.to_string(),
            &record.volume.to_string(),
            &format_optional(record.ma10),
            &format_optional(record.ma20),
            &format_optional(record.ma50),
            &format_optional(record.ma100),
            &format_optional(record.ma200),
            &format_optional(record.ma10_score),
            &format_optional(record.ma20_score),
            &format_optional(record.ma50_score),
            &format_optional(record.ma100_score),
            &format_optional(record.ma200_score),
            &format_optional(record.close_changed),
            &format_optional(record.volume_changed),
            &format_optional(record.total_money_changed),
        ])?;
    }

    writer.flush()?;
    Ok(())
}
```

**CLI Usage:**

```bash
# Export all VN stocks
cargo run -- export-to-csv --mode vn --output market_data/

# Export all crypto
cargo run -- export-to-csv --mode crypto --output crypto_data/

# Export specific tickers
cargo run -- export-to-csv --mode vn --tickers VCB,FPT,VIC --output market_data/

# Export both modes
cargo run -- export-to-csv --all --output ./
```

---

## Testing & Validation

### 1. Unit Tests

```rust
// tests/pg_data_store_test.rs

#[tokio::test]
async fn test_insert_and_query() {
    let pool = setup_test_db().await;
    let store = PgDataStore::new_from_pool(pool);

    // Insert test data
    let data = vec![
        StockData::new(...),
        StockData::new(...),
    ];

    store.insert_batch("market_data", data.clone()).await.unwrap();

    // Query back
    let result = store.get_data(
        vec!["VCB".to_string()],
        Interval::Daily,
        "vn",
        None,
        None,
        None,
    ).await.unwrap();

    assert_eq!(result.get("VCB").unwrap().len(), 2);
}

#[tokio::test]
async fn test_distributed_advisory_locks() {
    let pool = setup_test_db().await;

    // Simulate two workers trying to sync same ticker
    let worker1 = sync_ticker_with_lock(&pool, "VCB", Interval::Daily, "vn");
    let worker2 = sync_ticker_with_lock(&pool, "VCB", Interval::Daily, "vn");

    let (r1, r2) = tokio::join!(worker1, worker2);

    // Only one should succeed (the other skipped)
    assert!(r1.is_ok() || r2.is_ok());
}
```

### 2. Integration Tests

Update existing test scripts to use PostgreSQL:

```bash
# scripts/test-integration.sh

# Start PostgreSQL in Docker
docker compose up -d postgres

# Wait for PostgreSQL to be ready
until pg_isready -h localhost -p 5432 -U aipriceaction; do
  echo "Waiting for PostgreSQL..."
  sleep 2
done

# Run migrations
sqlx migrate run

# Import test data
cargo run -- import-csv-to-pg --verify

# Start server
./target/release/aipriceaction serve --port 3000 &

# Run tests (same as before)
curl "http://localhost:3000/tickers?symbol=VCB&interval=1D&limit=10"
# ... (17 tests)

# Cleanup
killall aipriceaction
docker compose down
```

### 3. Performance Benchmarks

```rust
// benches/pg_vs_csv.rs

use criterion::{black_box, criterion_group, criterion_main, Criterion};

fn benchmark_query_performance(c: &mut Criterion) {
    let rt = tokio::runtime::Runtime::new().unwrap();

    c.bench_function("csv_query_100_tickers", |b| {
        b.to_async(&rt).iter(|| async {
            let data = csv_store.get_data(
                tickers.clone(),
                Interval::Daily,
                None,
                None,
                Some(100)
            ).await;
            black_box(data);
        });
    });

    c.bench_function("pg_query_100_tickers", |b| {
        b.to_async(&rt).iter(|| async {
            let data = pg_store.get_data(
                tickers.clone(),
                Interval::Daily,
                "vn",
                None,
                None,
                Some(100)
            ).await;
            black_box(data);
        });
    });
}

criterion_group!(benches, benchmark_query_performance);
criterion_main!(benches);
```

**Expected Results:**
- PostgreSQL: 2-10x faster for range queries (indexed lookups)
- Aggregated intervals: 50-100x faster (pre-computed vs on-demand)
- Memory usage: Similar or lower (PostgreSQL caches hot data)

---

## Deployment Plan

### Local Development

```bash
# 1. Start PostgreSQL
docker compose -f docker-compose.local.yml up -d postgres

# 2. Run migrations
export DATABASE_URL="postgresql://aipriceaction:password@localhost:5432/aipriceaction"
sqlx migrate run

# 3. Import existing CSV data
cargo run -- import-csv-to-pg --verify

# 4. Start application
cargo run -- serve --port 3000

# CSV files remain as backup (read-only)
```

### Production Deployment

```yaml
# docker-compose.yml

version: '3.8'

services:
  postgres:
    image: timescale/timescaledb:latest-pg16
    environment:
      POSTGRES_DB: aipriceaction
      POSTGRES_USER: aipriceaction
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      PGDATA: /var/lib/postgresql/data/pgdata
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./backups:/backups  # For pg_dump backups
    ports:
      - "5432:5432"
    command:
      - "postgres"
      - "-c" "shared_buffers=512MB"
      - "-c" "effective_cache_size=1GB"
      - "-c" "work_mem=16MB"
      - "-c" "maintenance_work_mem=128MB"
      - "-c" "max_connections=100"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U aipriceaction"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    build: .
    environment:
      DATABASE_URL: postgresql://aipriceaction:${POSTGRES_PASSWORD}@postgres:5432/aipriceaction
      RUST_LOG: info
      PORT: 3000
    ports:
      - "3000:3000"
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./market_data:/app/market_data  # Read-only backup
      - ./crypto_data:/app/crypto_data
    restart: unless-stopped

  # Daily PostgreSQL backup
  backup:
    image: postgres:16
    environment:
      PGHOST: postgres
      PGUSER: aipriceaction
      PGPASSWORD: ${POSTGRES_PASSWORD}
      PGDATABASE: aipriceaction
    volumes:
      - ./backups:/backups
    command: >
      bash -c "
        while true; do
          echo 'Running daily backup...'
          pg_dump -Fc > /backups/aipriceaction_$(date +%Y%m%d_%H%M%S).dump
          find /backups -name '*.dump' -mtime +7 -delete
          sleep 86400
        done
      "
    depends_on:
      - postgres

volumes:
  postgres_data:
```

### Backup Strategy

**Daily PostgreSQL Backups:**

```bash
# Manual backup
docker exec -t postgres pg_dump -U aipriceaction -Fc aipriceaction > backups/backup_$(date +%Y%m%d).dump

# Restore from backup
docker exec -i postgres pg_restore -U aipriceaction -d aipriceaction -c < backups/backup_20250119.dump
```

**CSV Export Backup (Optional):**

```bash
# Export PostgreSQL data to CSV (for legacy compatibility)
cargo run -- export-to-csv --all --output ./

# Now you have both PostgreSQL (primary) and CSV (backup)
```

### Rollback Plan

If PostgreSQL migration fails:

```bash
# 1. Stop new application
docker compose down app

# 2. Revert code changes
git checkout main  # or previous stable tag

# 3. CSV files still exist - use old CSV-based system
docker compose -f docker-compose.old.yml up -d

# 4. Zero data loss (CSV files untouched during migration)
```

---

## Performance Expectations

### Query Performance

| Operation | CSV-based | PostgreSQL | Improvement |
|-----------|-----------|------------|-------------|
| Single ticker, 100 records | 15ms | 5ms | 3x faster |
| 10 tickers, 100 records each | 120ms | 15ms | 8x faster |
| Date range query (1 year) | 200ms | 20ms | 10x faster |
| Aggregated 5m (on-demand) | 500ms | 10ms | 50x faster |
| Aggregated 1W (on-demand) | 300ms | 5ms | 60x faster |
| Concurrent requests (10 workers) | File locks contention | No contention | ∞ faster |

### Memory Usage

- **CSV-based**: ~63MB in-memory cache (daily data) + 1GB disk cache
- **PostgreSQL**: ~100MB PostgreSQL shared_buffers + optional memory cache (20MB)
- **Total**: Similar or slightly higher, but more efficient

### Disk Usage

- **CSV files**: ~2GB (VN) + ~1.2GB (crypto) = 3.2GB
- **PostgreSQL**: ~4-5GB (with indexes, compression)
- **With CSV backup**: ~8GB total (PostgreSQL + CSV export)
- **Compression**: TimescaleDB compresses old data (90%+ savings)

### Sync Performance

- **Daily sync**: Similar (~4-6s), writes to PostgreSQL instead of CSV
- **Hourly sync**: Similar (~20-25s)
- **Minute sync**: Similar (~50-60s)
- **Concurrent syncs**: Now possible! Multiple workers can sync different tickers

---

## Timeline & Phases

### Phase 1: Database Setup (2-3 days)

- [ ] Add SQLx dependencies to `Cargo.toml`
- [ ] Create SQL migrations (`migrations/001_initial_schema.sql`, etc.)
- [ ] Set up Docker Compose with TimescaleDB
- [ ] Test database connection and migrations

### Phase 2: CSV Import Tool (2 days)

- [ ] Implement `src/commands/import_csv_to_pg.rs`
- [ ] Add verification logic
- [ ] Test import with sample data
- [ ] Run full import (10-20 minutes)

### Phase 3: Code Refactoring (3-4 days)

- [ ] Implement `src/services/pg_data_store.rs`
- [ ] Update `DataSync` to use PostgreSQL
- [ ] Update API handlers (`src/server/api.rs`)
- [ ] Update background workers with distributed sync
- [ ] Add advisory locks and heartbeat logic

### Phase 4: CSV Export Command (1 day)

- [ ] Implement `src/commands/export_to_csv.rs`
- [ ] Test export functionality
- [ ] Document usage

### Phase 5: Distributed Workers (2 days)

- [ ] Implement worker coordination tables
- [ ] Add advisory lock logic
- [ ] Test with multiple workers running simultaneously
- [ ] Verify no conflicts or duplicates

### Phase 6: Testing & Validation (2 days)

- [ ] Write unit tests for PgDataStore
- [ ] Update integration tests
- [ ] Run performance benchmarks
- [ ] Test distributed workers on multiple servers

### Phase 7: Deployment (1 day)

- [ ] Deploy to production with Docker Compose
- [ ] Monitor performance and errors
- [ ] Set up daily PostgreSQL backups
- [ ] Document rollback procedures

### Phase 8: Documentation & Cleanup (1 day)

- [ ] Update README.md
- [ ] Update API.md documentation
- [ ] Write migration guide
- [ ] Clean up old CSV-based code (optional)

**Total Estimated Time: 13-16 days**

---

## Open Questions / Decisions Needed

1. **Memory Cache Strategy**: Keep optional memory cache for hot data, or rely 100% on PostgreSQL query cache?
   - Recommendation: Start without memory cache, add if needed

2. **CSV Retention Policy**: How long to keep CSV backups after export?
   - Recommendation: Keep last 7 days of exports, rotate older ones

3. **Worker Distribution**: Static assignment vs dynamic (advisory locks)?
   - Recommendation: Use advisory locks (simpler, self-balancing)

4. **Continuous Aggregates**: Which intervals to pre-compute?
   - Recommendation: 5m, 15m, 30m (from 1m), 1W, 2W, 1M (from 1D)

5. **Migration Downtime**: Zero-downtime migration possible?
   - Recommendation: Brief downtime (10-20 min) for CSV import safer

---

## Success Criteria

✅ **Functional:**
- All existing API endpoints work with PostgreSQL
- Workers sync data to PostgreSQL successfully
- Multiple workers can run without conflicts
- CSV export produces identical files to original

✅ **Performance:**
- Query latency: 2-10x faster for range queries
- Aggregated intervals: 50x+ faster (pre-computed)
- No concurrent access issues (CLI + Docker safe)

✅ **Reliability:**
- Zero data loss during migration
- Rollback plan tested and documented
- Daily backups working (PostgreSQL + CSV export)

✅ **Operational:**
- Docker Compose deployment working
- Monitoring and health checks in place
- Documentation updated

---

## References

- [TimescaleDB Documentation](https://docs.timescale.com/)
- [SQLx Documentation](https://github.com/launchbadge/sqlx)
- [PostgreSQL Advisory Locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS)
- [Continuous Aggregates](https://docs.timescale.com/use-timescale/latest/continuous-aggregates/)

---

**End of Plan**
