# SQLite Migration Plan

## Overview
Migrate from CSV-only storage to a dual-write system maintaining both CSV files and SQLite database, with SQLite as the primary read source and CSV as backup/fallback.

## Requirements Summary
- **Dual-write system**: Keep CSV files + add SQLite writes in parallel
- **Environment fallback**: `FALLBACK_TO_CSV=true` for automatic CSV fallback
- **Separate tables**: vn_daily, vn_hourly, vn_minute for performance
- **SQLite cache only**: Remove memory cache, rely on SQLite's caching
- **Workers continue CSV writes** + add SQLite saves
- **API reads from SQLite** with CSV fallback

## Implementation Strategy

### Phase 1: Infrastructure Setup

#### 1.1 Dependencies
```toml
# Add to Cargo.toml
rusqlite = { version = "0.37", features = ["bundled", "chrono"] }
```

#### 1.2 Database Schema Design

**Vietnamese Stocks Tables:**
```sql
CREATE TABLE vn_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    time INTEGER NOT NULL,  -- Unix timestamp
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume INTEGER NOT NULL,
    ma10 REAL, ma20 REAL, ma50 REAL, ma100 REAL, ma200 REAL,
    ma10_score REAL, ma20_score REAL, ma50_score REAL, ma100_score REAL, ma200_score REAL,
    close_changed REAL, volume_changed REAL, total_money_changed REAL,
    created_at INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(ticker, time)
);

CREATE TABLE vn_hourly (...same schema...);
CREATE TABLE vn_minute (...same schema...);
```

**Crypto Tables:**
```sql
CREATE TABLE crypto_daily (...same schema...);
CREATE TABLE crypto_hourly (...same schema...);
CREATE TABLE crypto_minute (...same schema...);
```

**Performance Indexes:**
```sql
CREATE INDEX idx_vn_daily_ticker_time ON vn_daily(ticker, time DESC);
CREATE UNIQUE INDEX idx_vn_daily_unique ON vn_daily(ticker, time);
```

#### 1.3 Constants to Add to `src/constants.rs`
```rust
/// SQLite Database Configuration
pub const FALLBACK_TO_CSV_ENV: &str = "FALLBACK_TO_CSV";
pub const DEFAULT_FALLBACK_TO_CSV: bool = true;
pub const VN_DATABASE_PATH: &str = "vn_data.db";
pub const CRYPTO_DATABASE_PATH: &str = "crypto_data.db";
pub const SQLITE_BATCH_SIZE: usize = 1000;
```

#### 1.4 Error Handling Updates
Add to `src/error.rs`:
```rust
impl From<rusqlite::Error> for AppError {
    fn from(err: rusqlite::Error) -> Self {
        AppError::Io(format!("SQLite error: {}", err))
    }
}
```

### Phase 2: Core Database Components

#### 2.1 Create `src/services/database_manager.rs`
- SQLite connection management
- Database initialization and schema creation
- Batch insert operations
- Query operations with performance optimization
- Health monitoring functions

#### 2.2 Create `src/services/sqlite_store.rs`
- Low-level SQLite utilities
- Data conversion helpers
- Query builders
- Database optimization functions
- Backup and maintenance utilities

#### 2.3 Update `src/services/mod.rs`
```rust
pub mod database_manager;
pub mod sqlite_store;
pub use database_manager::{DatabaseManager, DatabaseStats};
pub use sqlite_store::{SqliteStore, TableInfo, DatabaseUtils};
```

### Phase 3: Dual-Write Integration

#### 3.1 Modify CSV Enhancement Service

**Key Function to Update:** `src/services/csv_enhancer.rs:save_enhanced_csv_to_dir()`

Add new functions:
```rust
pub async fn save_enhanced_csv_and_sqlite(
    ticker: &str,
    data: &[StockData],
    interval: Interval,
    cutoff_date: DateTime<Utc>,
    rewrite_all: bool,
    base_dir: &Path,
    mode: &Mode,
    db_manager: &DatabaseManager,
) -> Result<(), Error>

pub async fn enhance_interval_filtered_with_sqlite(
    interval: Interval,
    base_dir: &Path,
    tickers_filter: Option<&[String]>,
    mode: &Mode,
    db_manager: &DatabaseManager,
) -> Result<EnhancementStats, Error>
```

**Integration Pattern:**
1. Save to CSV (existing logic)
2. Save to SQLite (new batch insert)
3. Handle SQLite errors gracefully
4. Fallback to CSV-only if configured

#### 3.2 Update Worker Files

**Daily Worker (`src/worker/daily_worker.rs`):**
- Add DatabaseManager parameter to run() function
- Replace `enhance_interval()` call with `enhance_interval_filtered_with_sqlite()`
- Maintain existing timing and error handling

**Slow Worker (`src/worker/slow_worker.rs`):**
- Same pattern for hourly and minute intervals
- Two independent async tasks for hourly/minute

**Crypto Worker (`src/worker/crypto_worker.rs`):**
- Add DatabaseManager parameter
- Update enhancement calls for all intervals
- Maintain crypto-specific filtering logic

### Phase 4: API Layer Migration

#### 4.1 Modify DataStore (`src/services/data_store.rs`)

**New Query Method:**
```rust
pub async fn get_data_smart_sqlite(
    &self,
    params: QueryParameters,
) -> HashMap<String, Vec<StockData>> {
    // Try SQLite first
    match self.query_from_sqlite(&params).await {
        Ok(data) if !data.is_empty() => data,
        Ok(_) | Err(_) if self.fallback_to_csv => {
            warn!("SQLite query failed, falling back to CSV");
            self.get_data_smart(params).await
        }
        Err(e) => {
            error!("SQLite query failed and CSV fallback disabled: {}", e);
            HashMap::new()
        }
    }
}
```

**Key Changes:**
- Add DatabaseManager field to DataStore struct
- Remove memory cache (rely on SQLite's built-in caching)
- Implement SQLite-first queries with CSV fallback
- Maintain existing query parameters and response format

#### 4.2 Update API Handler (`src/server/api.rs`)

**Minimal changes needed:**
- Update `get_tickers_handler()` to use `get_data_smart_sqlite()`
- Maintain existing response format and error handling
- Add performance logging for SQLite vs CSV queries

### Phase 5: Server Integration

#### 5.1 Update Server Initialization (`src/commands/serve.rs`)

```rust
pub async fn serve(args: ServeArgs) -> Result<(), Error> {
    // ... existing setup ...

    // Initialize database manager
    let db_manager = Arc::new(DatabaseManager::new().await?);

    // Create app state with database manager
    let app_state = AppState {
        vn_store: Arc::new(DataStore::new_with_db(market_data_dir.clone(), db_manager.clone()).await?),
        crypto_store: Arc::new(DataStore::new_with_db(crypto_data_dir.clone(), db_manager.clone()).await?),
        health_stats,
        db_manager,
        // ... existing fields ...
    };

    // ... rest of server setup ...
}
```

## Performance Optimizations

### SQLite Configuration
```sql
PRAGMA journal_mode = WAL;           -- Better concurrent access
PRAGMA synchronous = NORMAL;         -- Balance safety/performance
PRAGMA cache_size = -64000;          -- 64MB cache
PRAGMA temp_store = MEMORY;          -- Temp tables in memory
PRAGMA mmap_size = 268435456;        -- 256MB memory-mapped I/O
```

### Batch Operations
- 1000 records per transaction
- Prepared statements for reusability
- INSERT OR REPLACE for upserts

### Index Strategy
- Composite indexes on `(ticker, time DESC)`
- UNIQUE constraints for duplicate prevention

## Testing Strategy

### 1. Unit Tests
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sqlite_connection() { /* ... */ }

    #[test]
    fn test_batch_insert() { /* ... */ }

    #[test]
    fn test_fallback_mechanism() { /* ... */ }
}
```

### 2. Integration Tests
- Complete workflow: API → SQLite → CSV fallback
- Data consistency verification between CSV and SQLite
- Performance comparison testing

### 3. Load Testing
- Query performance benchmarks
- Memory usage comparison
- Concurrent access testing

## Environment Variables

```bash
# Enable/disable CSV fallback
FALLBACK_TO_CSV=true          # Default: true

# Custom database paths (optional)
VN_DATABASE_PATH="vn_data.db" # Default: vn_data.db
CRYPTO_DATABASE_PATH="crypto_data.db" # Default: crypto_data.db
```

## Migration Timeline

### Week 1: Infrastructure
- [ ] Add dependencies
- [ ] Create database modules
- [ ] Set up database schema
- [ ] Add error handling

### Week 2: Dual-Write Implementation
- [ ] Modify CSV enhancement service
- [ ] Update worker integration
- [ ] Test data consistency

### Week 3: API Migration
- [ ] Update DataStore for SQLite queries
- [ ] Modify API layer
- [ ] Implement fallback mechanism

### Week 4: Server Integration & Testing
- [ ] Update server initialization
- [ ] Comprehensive testing
- [ ] Performance optimization

### Week 5: Deployment
- [ ] Production deployment with feature flags
- [ ] Monitoring and rollback plan
- [ ] Documentation updates

## Risk Mitigation

### Data Loss Prevention
- **Dual-write**: CSV files remain as backup
- **Transactional**: SQLite operations use ACID transactions
- **Fallback**: Automatic CSV fallback on SQLite failures

### Performance Risks
- **Gradual rollout**: Feature flags for controlled deployment
- **Monitoring**: Track query performance and memory usage
- **Optimization**: SQLite configuration and indexing

### Migration Complexity
- **Incremental**: Phase-by-phase implementation
- **Backward compatibility**: API remains unchanged
- **Testing**: Comprehensive test coverage before production

## Expected Benefits

### Performance Improvements
- **5-10x faster** date-filtered queries with indexes
- **60-80% reduction** in memory usage (no in-memory cache)
- **Better concurrent access** with WAL mode

### Operational Benefits
- **Data integrity**: ACID transactions prevent partial writes
- **Scalability**: Better performance with growing datasets
- **Maintenance**: Simpler cache management

### Development Benefits
- **Cleaner code**: SQLite handles caching and optimization
- **Better queries**: SQL provides powerful filtering and aggregation
- **Reliability**: Built-in data validation and constraints

## Critical Files for Implementation

1. `Cargo.toml` - Add rusqlite dependency
2. `src/constants.rs` - Add database constants
3. `src/error.rs` - Add SQLite error handling
4. `src/services/database_manager.rs` - NEW: SQLite connection management
5. `src/services/sqlite_store.rs` - NEW: Low-level SQLite operations
6. `src/services/mod.rs` - Export new modules
7. `src/services/csv_enhancer.rs` - MOD: Add dual-write functions
8. `src/worker/daily_worker.rs` - MOD: Add DatabaseManager parameter
9. `src/worker/slow_worker.rs` - MOD: Add DatabaseManager parameter
10. `src/worker/crypto_worker.rs` - MOD: Add DatabaseManager parameter
11. `src/services/data_store.rs` - MOD: SQLite-first queries, remove memory cache
12. `src/server/api.rs` - MOD: Use SQLite queries with fallback
13. `src/commands/serve.rs` - MOD: Initialize DatabaseManager

## Rollback Plan

If issues arise:
1. **Disable SQLite**: Set `FALLBACK_TO_CSV=true` (default)
2. **Remove dual-write**: Revert worker calls to original functions
3. **Keep CSV**: CSV files remain intact as backup
4. **Gradual**: Can rollback phase by phase

## Success Metrics

- **Query performance**: 5x+ improvement for date-filtered queries
- **Memory usage**: 50%+ reduction in RAM consumption
- **Data consistency**: 100% parity between CSV and SQLite
- **Reliability**: Zero data loss during migration
- **API compatibility**: No breaking changes to existing endpoints

---

This plan provides a comprehensive roadmap for SQLite migration while maintaining data safety and dramatically improving performance. The dual-write approach ensures zero data loss risk while the fallback mechanism guarantees system reliability during the transition period.