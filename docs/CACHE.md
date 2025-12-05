# Cache System Architecture

This document explains the dual-layer cache system used by aipriceaction to serve market data efficiently.

## Overview

The system implements a **two-tier cache architecture** with distinct purposes for each layer:

- **Memory Cache**: Hot data with auto-reload (recent daily/hourly data)
- **Disk Cache**: Cold data cache on-demand (historical data + minute data)

## Memory Cache (Primary Cache)

### Purpose
Fast access to frequently accessed recent data that is automatically kept fresh.

### Data Stored
- **Daily data**: Last 730 records (~2 years per ticker)
- **Hourly data**: Last 2160 records (~3 months per ticker for aggregated intervals)
- **Minute data**: Last 2160 records (1.5 days per ticker, required for aggregated intervals)
- **Memory limit**: 4GB per mode (VN + Crypto), actual usage ~40-63MB for VN data, ~23MB for crypto data

### Auto-Reload Mechanism
```rust
// Interval-specific background tasks
let _vn_daily_reload = shared_data_store_vn.spawn_auto_reload_task(Interval::Daily);
let _vn_hourly_reload = shared_data_store_vn.spawn_auto_reload_task(Interval::Hourly);
let _vn_minute_reload = shared_data_store_vn.spawn_auto_reload_task(Interval::Minute);
```

- **Frequency**: Interval-specific reload intervals
  - **Daily data**: Every 15 seconds
  - **Hourly data**: Every 30 seconds
  - **Minute data**: Every 300 seconds (5 minutes)
- **TTL**: 300 seconds (5 minutes), but continuously refreshed
- **File change detection**: Only reloads if CSV files have been modified via mtime tracking
- **Non-blocking**: File I/O happens in dedicated auto-reload runtime, separate from main server

### Performance
- **Cache hit**: ~1-2ms response time
- **Concurrent access**: Safe with RwLock (`data.read().await`)

### Emergency Cache Reload
```rust
// Triggered when cache is extremely stale (2x TTL)
if cache_age > CACHE_TTL_SECONDS * 2 {
    tracing::warn!("Cache extremely stale ({}s > {}s), forcing emergency reload", cache_age, CACHE_TTL_SECONDS * 2);
    // Force reload if background auto-reload task fails
}
```

- **Trigger**: Cache age exceeds 600 seconds (10 minutes, 2x TTL)
- **Purpose**: Recovery when background auto-reload tasks fail
- **Action**: Forces immediate file reload bypassing normal background schedule
- **Logging**: Warn-level logging for monitoring

### Dedicated Auto-Reload Runtime
```rust
// Separate runtime for background tasks (3 worker threads)
let auto_reload_runtime = tokio::runtime::Builder::new_multi_thread()
    .worker_threads(3)
    .thread_name("auto-reload")
    .build()
    .expect("Failed to create auto-reload runtime");
```

- **Isolation**: Runs in dedicated tokio runtime, separate from main server
- **Worker threads**: 3 dedicated threads for auto-reload operations
- **Coverage**: 6 total tasks (3 VN + 3 Crypto: daily, hourly, minute)
- **Non-blocking**: Server API performance unaffected by file I/O

### Usage Pattern
```rust
// API handler - fastest path
let store = self.data.read().await;  // Non-blocking read lock
if let Some(data) = store.get(ticker, interval) {
    return data; // Immediate response
}
```

## Disk Cache (Secondary Cache)

### Purpose
Cache expensive file reads for complex queries that don't fit in memory cache.

### Data Stored
- **Minute data**: All minute intervals (required for aggregated intervals with MA200)
- **Historical data**: Large date ranges beyond memory cache capacity
- **Cache bypass**: `cache=false` parameter requests
- **Complex queries**: Requests with specific start/end dates and limits

### Cache Entry Structure
```rust
struct CacheEntry {
    data: Vec<StockData>,           // The cached OHLCV data
    size_bytes: usize,               // Memory usage of this entry
    cached_at: DateTime<Utc>,       // When cached (for TTL)
}
```

### Cache Key Generation
Each cache entry is uniquely identified by:
```rust
(ticker_symbol, interval, start_date, end_date, limit)
```

Examples:
- `("VCB", 1m, None, None, Some(100))` - Last 100 minutes of VCB
- `("BTC", 1H, Some(2015-01-01), Some(2016-12-31), None)` - Historical hourly BTC data

### Size Management
- **Default limit**: 500MB (configurable via `MAX_CACHE_SIZE_MB`)
- **Auto-clear threshold**: 95% capacity
- **Eviction strategy**: LRU-style, removes oldest 50% when threshold reached
- **Individual item limit**: 100MB (prevents caching huge datasets)

### TTL and Expiration
- **TTL**: 30 seconds (same as memory cache)
- **Expiration**: Entries automatically expire after TTL
- **Eviction**: Oldest entries removed first when space needed

### Smart File Reading Strategies

The system uses three different CSV reading strategies based on query type:

#### 1. FromEnd Strategy
```rust
// For recent data queries - read backwards from file end
for line in file.lines().rev() {
    if collected_count >= needed_limit { break; }
    process_line(line);
}
```

#### 2. FromStart Strategy
```rust
// For historical data with limits - read forward with buffer
let smart_limit = match api_limit {
    Some(limit) => {
        if limit > 252 { limit * 10 }  // Allow buffer for aggregation
        else if limit > 100 { 3000 }
        else { 1000 }
    },
    None => match interval {
        Interval::Minute => 200,
        Interval::Hourly => 100,
        Interval::Daily => 50,
    }
};
```

#### 3. CompleteFile Strategy
```rust
// For small files or full dataset needs
let all_data = csv::Reader::from_reader(file)
    .deserialize()
    .collect::<Vec<_>>();
```

### Request Flow

```rust
async fn get_data_with_fallback(ticker, interval, start_date, end_date, limit) {
    // Step 1: Try memory cache first
    if let Some(data) = memory_cache.get(ticker, interval) {
        return Ok(data); // Fastest path
    }

    // Step 2: Check disk cache
    let cache_key = (ticker, interval, start_date, end_date, limit);
    if let Some(entry) = disk_cache.get(&cache_key) {
        // Check TTL
        if (Utc::now() - entry.cached_at).num_seconds() < 30 {
            return Ok(entry.data.clone()); // Cache hit
        }
    }

    // Step 3: Read from CSV file (expensive)
    let file_data = read_csv_file_smart(ticker, interval, start_date, end_date, limit)?;

    // Step 4: Store in disk cache
    disk_cache.insert(cache_key, CacheEntry {
        data: file_data.clone(),
        size_bytes: estimate_data_size(&file_data),
        cached_at: Utc::now(),
    });

    Ok(file_data)
}
```

## Dual-Mode Cache System

The system maintains separate cache instances for Vietnamese stocks and cryptocurrencies:

### Mode Detection
```rust
fn is_crypto_mode(&self) -> bool {
    self.market_data_dir.to_string_lossy().contains("crypto_data")
}

fn get_representative_tickers(&self) -> &[&str] {
    if self.is_crypto_mode() {
        &["BTC", "ETH", "XRP"] // Crypto tickers
    } else {
        &["VNINDEX", "VCB", "VIC"] // VN tickers
    }
}
```

### VN Mode (Vietnamese Stocks)
- **Data directory**: `market_data/`
- **Tickers**: 282 Vietnamese stocks + indices
- **Representative tickers**: VNINDEX, VCB, VIC
- **Memory usage**: ~40-63MB (daily data cache)
- **Background tasks**: 3 auto-reload tasks (daily, hourly, minute)
- **Trading hours**: 9:00-15:00 ICT affects worker frequency

### Crypto Mode (Cryptocurrencies)
- **Data directory**: `crypto_data/`
- **Tickers**: 98 cryptocurrencies (filtered from top 100)
- **Representative tickers**: BTC, ETH, XRP
- **Memory usage**: ~23MB (daily data cache)
- **Background tasks**: 3 auto-reload tasks (daily, hourly, minute)
- **24/7 operation**: No trading hour restrictions

### Dual-Mode Benefits
- **Isolation**: VN and crypto caches operate independently
- **Resource sharing**: Same cache algorithms and structures
- **Mode switching**: API mode parameter determines which cache to use
- **Independent scaling**: Each mode can have different retention policies

### API Mode Selection
```bash
# VN mode (default)
curl "http://localhost:3000/tickers?symbol=VCB&interval=1D"

# Crypto mode
curl "http://localhost:3000/tickers?symbol=BTC&mode=crypto&interval=1D"
```

## Critical Use Cases

### 1. Aggregated Intervals
```bash
# Requires minute data for MA200 calculations
curl "http://localhost:3000/tickers?symbol=BTC&interval=5m&limit=100"
```
- **Requirement**: Minute data as base for aggregation
- **Impact**: Cannot be served from memory cache alone
- **Solution**: Disk cache provides efficient minute data access

### 2. Historical Data Queries
```bash
# Large date range beyond memory cache capacity
curl "http://localhost:3000/tickers?symbol=VCB&start_date=2015-01-01&end_date=2016-12-31&interval=1H"
```
- **Issue**: Memory cache only holds recent data
- **Solution**: Disk cache caches expensive historical reads

### 3. Cache Bypass for Development
```bash
# Force fresh data read for debugging
curl "http://localhost:3000/tickers?symbol=VCB&cache=false"
```
- **Usage**: 17% of integration tests use this
- **Purpose**: Ensures data freshness for development
- **Impact**: Essential for troubleshooting

## Performance Characteristics

| Cache Layer | Hit Time | Miss Time | Use Case |
|-------------|----------|-----------|----------|
| Memory Cache | 1-2ms | 50-200ms (falls back to disk) | Recent data, high frequency |
| Disk Cache | 50-200ms | 500ms-2s (file read) | Historical data, minute data |
| File Read | 500ms-2s | N/A | Cold start, cache bypass |
| Emergency Reload | 1-5s | N/A | Cache extremely stale (>10min) |

### Performance Factors

**Memory Cache Performance:**
- **TTL impact**: 300-second TTL reduces cache misses vs 30-second TTL
- **Auto-reload frequency**: Interval-specific (15s, 30s, 300s) affects freshness
- **Dual-mode overhead**: Minimal, separate instances for VN and Crypto

**Disk Cache Performance:**
- **Cache size**: 500MB limit with 95% auto-clear threshold
- **Eviction cost**: LRU-style removal of 50% oldest entries
- **Smart reading**: Strategy selection (FromEnd, FromStart, CompleteFile)

**File Reading Performance:**
- **Change detection**: mtime tracking avoids unnecessary reads
- **CSV parsing**: Optimized for 20-column enhanced format
- **Atomic operations**: Copy-process-rename adds minimal overhead

### Memory Usage Breakdown

**VN Mode (282 tickers):**
- Daily data: ~40-63MB (730 records × 282 tickers × 20 columns)
- Hourly data: Cached on-demand (500MB disk limit)
- Minute data: Cached on-demand (500MB disk limit)

**Crypto Mode (98 tickers):**
- Daily data: ~23MB (730 records × 98 tickers × 20 columns)
- Hourly data: Cached on-demand (500MB disk limit)
- Minute data: Cached on-demand (500MB disk limit)

**Total System Memory:**
- Memory limit: 4GB per mode (8GB total theoretical)
- Actual usage: ~83-86MB for both modes combined
- Disk cache: Up to 1GB total (500MB per mode)

## Cache Management

### Memory Cache Auto-Reload
```rust
// Background auto-reload tasks (interval-specific timing)
async fn spawn_auto_reload_task(&self, interval: Interval) {
    let sleep_duration = match interval {
        Interval::Daily => Duration::from_secs(15),    // 15 seconds
        Interval::Hourly => Duration::from_secs(30),   // 30 seconds
        Interval::Minute => Duration::from_secs(300),  // 5 minutes
    };

    loop {
        // Check file modification times via mtime tracking
        // Only reload changed files to minimize I/O
        // Update in-memory cache with brief write lock
        tokio::time::sleep(sleep_duration).await;
    }
}
```

### Auto-Clear Cache Feature
```rust
// Automatic cache eviction when memory threshold exceeded
async fn auto_clear_cache(&self, current_size: usize, incoming_size: usize) -> bool {
    let threshold_size = (self.max_cache_size_bytes as f64 * 0.95) as usize; // 95% threshold

    if current_size + incoming_size <= threshold_size {
        return false; // No clearing needed
    }

    // Sort entries by cached_at (oldest first)
    let mut entries: Vec<_> = self.disk_cache.iter()
        .map(|(key, entry)| (key.clone(), entry.cached_at))
        .collect();
    entries.sort_by_key(|(_, cached_at)| *cached_at);

    // Remove oldest 50% of entries
    let entries_to_remove = (entries.len() as f64 * 0.5) as usize;
    // ... eviction logic
}
```

- **Trigger**: Cache reaches 95% of `MAX_CACHE_SIZE_MB` (default: 475MB of 500MB limit)
- **Eviction ratio**: Removes oldest 50% of entries when triggered
- **Strategy**: LRU-style based on `cached_at` timestamp
- **Environment variables**:
  - `CACHE_AUTO_CLEAR_ENABLED=true` (default)
  - `CACHE_AUTO_CLEAR_THRESHOLD=0.95` (95%)
  - `CACHE_AUTO_CLEAR_RATIO=0.5` (50% eviction)

### Disk Cache Eviction
```rust
async fn auto_clear_cache(&self, current_size: usize, incoming_size: usize) -> bool {
    let threshold_size = (self.max_cache_size_bytes as f64 * 0.95) as usize;

    if current_size + incoming_size <= threshold_size {
        return false;
    }

    // Sort entries by cached_at (oldest first)
    let mut entries: Vec<_> = disk_cache.iter()
        .map(|(key, entry)| (key.clone(), entry.cached_at))
        .collect();
    entries.sort_by_key(|(_, cached_at)| *cached_at);

    // Remove oldest 50% of entries
    let entries_to_remove = (entries.len() as f64 * 0.5) as usize;
    for ((symbol, interval, start_date, end_date, limit), _) in entries.iter().take(entries_to_remove) {
        let key = (symbol.clone(), *interval, *start_date, *end_date, *limit);
        if let Some(removed) = disk_cache.remove(&key) {
            total_freed_size += removed.size_bytes;
        }
    }
}
```

## Atomic File Operations and Concurrent Access

### Copy-Process-Rename Strategy
The system uses atomic file operations instead of traditional file locking:

```rust
// Background worker file processing pattern
fn save_enhanced_csv_to_dir(file_path: &Path, data: &[StockData]) -> Result<(), Error> {
    if file_path.exists() {
        // Step 1: Copy original to processing file
        let processing_path = file_path.with_extension(format!("{}.processing.{}",
            extension, timestamp));
        std::fs::copy(&file_path, &processing_path)?;

        // Step 2: Process data on the copy
        process_csv_data(&processing_path, data)?;

        // Step 3: Atomic rename on success
        std::fs::rename(&processing_path, &file_path)?;
    } else {
        // New files: Direct write (no atomic rename needed)
        write_new_csv(&file_path, data)?;
    }
}
```

### Atomic File Reading
```rust
pub fn open_file_atomic_read(csv_path: &Path) -> Result<File, Error> {
    let file = std::fs::OpenOptions::new()
        .read(true)  // Read-only access, no locking needed
        .open(csv_path)?;
    Ok(file)
}
```

### File Change Detection
```rust
// Sophisticated mtime tracking to avoid unnecessary file reads
let current_mtime = std::fs::metadata(&csv_path)?.modified()?;
if let Some(stored_mtime) = file_mtimes.get(&mtime_key) {
    if current_mtime != *stored_mtime {
        tracing::debug!("File modified, need to reload");
        read_and_cache_file();
    } else {
        tracing::debug!("File unchanged, skipping");
    }
} else {
    tracing::debug!("New file detected, loading");
    read_and_cache_file();
}
```

### Processing File Management
```rust
// Automatic cleanup of orphaned processing files
fn cleanup_orphaned_processing_files(data_dir: &Path) -> Result<(), Error> {
    for entry in std::fs::read_dir(data_dir)? {
        let path = entry?.path();
        if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
            if name.contains(".processing.") {
                tracing::warn!("Removing orphaned processing file: {:?}", path);
                std::fs::remove_file(&path)?;
            }
        }
    }
}
```

### Concurrent Access Safety
- **No Traditional Locking**: The system does NOT use fs2 or file locking
- **Atomic Rename**: Readers always see either complete old file or complete new file
- **Processing Isolation**: All modifications happen on temporary copies
- **Error Recovery**: Original files preserved if processing fails
- **Cleanup**: Orphaned processing files automatically removed

### Limitations
- **No Writer Coordination**: Multiple workers could theoretically write to same files
- **Read-Write Race**: API reads during writes may see either old or new data (never partial)
- **Best Effort**: System relies on atomic operations rather than explicit locking

## Configuration

### Environment Variables
```bash
# Disk cache size limit (default: 500MB)
export MAX_CACHE_SIZE_MB=500

# TTL for both cache layers (default: 300 seconds)
export CACHE_TTL_SECONDS=300

# Cache auto-clear settings
export CACHE_AUTO_CLEAR_ENABLED=true
export CACHE_AUTO_CLEAR_THRESHOLD=0.95  # 95%
export CACHE_AUTO_CLEAR_RATIO=0.5       # 50%
```

## Health Monitoring

### Cache Statistics API
```rust
// Health endpoint includes cache stats
pub struct HealthStats {
    pub disk_cache_entries: usize,
    pub disk_cache_size_bytes: usize,
    pub disk_cache_size_mb: f64,
    pub disk_cache_limit_mb: usize,
    pub disk_cache_usage_percent: f64,
    pub cache_last_updated: DateTime<Utc>,
}
```

### Monitoring Points
- Cache hit/miss ratios
- Memory usage vs limits
- File modification frequency
- Background worker sync status

## Why Both Caches Are Necessary

The two-tier architecture is not redundant - each serves distinct purposes:

**Memory Cache** = "Hot Data Refrigerator"
- Always fresh, auto-stocked essentials
- Sub-millisecond access for frequent requests
- Limited to what fits in RAM

**Disk Cache** = "Cold Data Pantry"
- Stores expensive-to-retrieve items on-demand
- Caches the results of complex file reads
- Handles datasets too large for RAM (minute data, historical ranges)

**Without Disk Cache:**
- Minute data requests would require full CSV reads every time (5-10+ seconds)
- Aggregated intervals (5m, 15m, 30m) would become unusable
- Historical queries would be extremely slow
- `cache=false` debugging feature would be impractical

The dual-cache system enables the API to serve both recent and historical market data efficiently while maintaining data freshness and handling concurrent requests safely.