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
- **Total memory usage**: ~40-63MB for VN data, ~23MB for crypto data

### Auto-Reload Mechanism
```rust
// Background tasks run every 30 seconds
let _vn_daily_reload = shared_data_store_vn.spawn_auto_reload_task(Interval::Daily);
let _vn_hourly_reload = shared_data_store_vn.spawn_auto_reload_task(Interval::Hourly);
```

- **Frequency**: Every 30 seconds by background workers
- **TTL**: 30 seconds, but continuously refreshed
- **File change detection**: Only reloads if CSV files have been modified
- **Non-blocking**: File I/O happens without locks, brief write locks only for final update

### Performance
- **Cache hit**: ~1-2ms response time
- **Concurrent access**: Safe with RwLock (`data.read().await`)

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

## Cache Management

### Memory Cache Auto-Reload
```rust
// Background auto-reload tasks (spawn every 30 seconds)
async fn spawn_auto_reload_task(&self, interval: Interval) {
    loop {
        // Check file modification times
        // Only reload changed files
        // Update in-memory cache with brief write lock
        tokio::time::sleep(Duration::from_secs(30)).await;
    }
}
```

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

## File Locking and Concurrent Access

### Atomic File Reading
```rust
pub fn open_file_atomic_read(csv_path: &Path) -> Result<File, Error> {
    let file = std::fs::OpenOptions::new()
        .read(true)  // Read-only, no locking needed
        .open(csv_path)?;
    Ok(file)
}
```

### File Change Detection
```rust
// Track file modification times to avoid unnecessary reads
let current_mtime = std::fs::metadata(&csv_path)?.modified()?;
if let Some(stored_mtime) = file_mtimes.get(&mtime_key) {
    if current_mtime != *stored_mtime {
        // File modified, need to reload
        read_and_cache_file();
    }
} else {
    // New file, need to load
    read_and_cache_file();
}
```

## Configuration

### Environment Variables
```bash
# Disk cache size limit (default: 500MB)
export MAX_CACHE_SIZE_MB=500

# TTL for both cache layers (default: 30 seconds)
export CACHE_TTL_SECONDS=30

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