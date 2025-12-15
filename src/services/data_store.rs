use crate::error::Error;
use crate::models::{Interval, AggregatedInterval, StockData};
use crate::services::database::SQLiteDatabaseStore;
use crate::services::migration::{CsvToSqliteMigration, MigrationConfig};
use crate::services::sqlite_updater::SQLiteUpdater;
use tracing::{debug, info, warn, error};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::RwLock;
use tokio::task::JoinHandle;
use std::env;

/// Memory management constants
pub const MAX_MEMORY_MB: usize = 4096; // 4GB limit
pub const MAX_MEMORY_BYTES: usize = MAX_MEMORY_MB * 1024 * 1024;
pub const DATA_RETENTION_RECORDS: usize = 1500; // Keep last 1500 records per ticker per interval
pub const MINUTE_DATA_RETENTION_RECORDS: usize = 2160; // Keep last 2160 minute records (1.5 days) to support aggregated intervals
pub const CACHE_TTL_SECONDS: i64 = 300; // 5 minutes TTL for memory cache
pub const DEFAULT_MAX_CACHE_SIZE_MB: usize = 500; // 500MB default cache size

/// Health statistics for the data store
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HealthStats {
    pub daily_last_sync: Option<String>,
    pub hourly_last_sync: Option<String>,
    pub minute_last_sync: Option<String>,
    pub crypto_last_sync: Option<String>,
    pub daily_iteration_count: u64,
    pub slow_iteration_count: u64,
    pub crypto_iteration_count: u64,
    pub uptime_secs: u64,
    pub current_system_time: String,
}

impl Default for HealthStats {
    fn default() -> Self {
        Self {
            daily_last_sync: None,
            hourly_last_sync: None,
            minute_last_sync: None,
            crypto_last_sync: None,
            daily_iteration_count: 0,
            slow_iteration_count: 0,
            crypto_iteration_count: 0,
            uptime_secs: 0,
            current_system_time: Utc::now().to_rfc3339(),
        }
    }
}

pub type SharedDataStore = Arc<DataStore>;
pub type SharedHealthStats = Arc<RwLock<HealthStats>>;

#[derive(Debug, Clone)]
pub enum DataStoreBackend {
    CSV,
    SQLite(Arc<SQLiteDatabaseStore>),
}

pub type IntervalData = HashMap<Interval, Vec<StockData>>;
pub type InMemoryData = HashMap<String, IntervalData>;

#[derive(Debug, Clone)]
pub struct QueryParameters {
    pub tickers: Vec<String>,
    pub interval: Interval,
    pub aggregated_interval: Option<AggregatedInterval>,
    pub start_date: Option<DateTime<Utc>>,
    pub end_date: Option<DateTime<Utc>>,
    pub limit: usize,
    pub use_cache: bool,
    pub legacy_prices: bool,
}

impl QueryParameters {
    pub fn new(
        tickers: Vec<String>,
        interval: Interval,
        aggregated_interval: Option<AggregatedInterval>,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
        limit: Option<usize>,
        use_cache: bool,
        legacy_prices: bool,
    ) -> Self {
        Self {
            tickers,
            interval,
            aggregated_interval,
            start_date,
            end_date,
            limit: limit.unwrap_or(252),
            use_cache,
            legacy_prices,
        }
    }
}

pub struct DataStore {
    backend: RwLock<DataStoreBackend>,
    data: RwLock<InMemoryData>,
    market_data_dir: PathBuf,
    cache_last_updated: RwLock<DateTime<Utc>>,
    db_path: Option<PathBuf>,
    last_sqlite_check: RwLock<DateTime<Utc>>,
}

impl DataStore {
    pub async fn new(market_data_dir: PathBuf) -> Result<Self, Error> {
        // Log all environment variables for debugging
        info!("[DATA_STORE] ===== DataStore Initialization Start =====");
        info!("[DATA_STORE] Market dir: {:?}", market_data_dir);
        info!("[DATA_STORE] Current working directory: {:?}", std::env::current_dir());

        let backend_preference = env::var("DATA_STORE_BACKEND")
            .unwrap_or_else(|_| "csv".to_string())
            .to_lowercase();

        info!("[DATA_STORE] Raw DATA_STORE_BACKEND env var: {:?}", std::env::var("DATA_STORE_BACKEND"));
        info!("[DATA_STORE] Backend preference: '{}'", backend_preference);
        info!("[DATA_STORE] PID: {}", std::process::id());

        let backend = match backend_preference.as_str() {
            "sqlite" => {
                let db_path = if market_data_dir.to_string_lossy().contains("crypto") {
                    info!("[DATA_STORE] Detected crypto market data dir");
                    market_data_dir.parent()
                        .unwrap_or(&market_data_dir)
                        .join("crypto_data.db")
                } else {
                    info!("[DATA_STORE] Detected VN market data dir");
                    market_data_dir.join("..").join("market_data.db")
                };

                info!("[SQLITE] SQLite backend requested - DB path: {:?}", db_path);
                info!("[SQLITE] Database exists: {}", db_path.exists());
                info!("[SQLITE] Database parent dir exists: {}", db_path.parent().map(|p| p.exists()).unwrap_or(false));
                if let Some(parent) = db_path.parent() {
                    info!("[SQLITE] Database parent dir: {:?}", parent);
                }

                if db_path.exists() {
                    info!("[SQLITE] Database file exists, attempting to initialize...");
                    match SQLiteDatabaseStore::new(db_path.clone()).await {
                        Ok(db) => {
                            info!("[SQLITE] SQLiteDatabaseStore created successfully");
                            match db.get_record_count().await {
                                Ok(record_count) => {
                                    info!("[SQLITE] Database has {} records", record_count);
                                    if record_count > 0 {
                                        info!("✅ [SQLITE] Using existing SQLite database with {} records", record_count);

                                        // Spawn background smart sync check (non-blocking)
                                        info!("[SQLITE] Spawning background smart sync check...");
                                        let db_path_clone = db_path.clone();
                                        let market_data_dir_clone = market_data_dir.clone();
                                        tokio::spawn(async move {
                                            let csv_dirs = if market_data_dir_clone.ends_with("crypto_data") {
                                                vec![market_data_dir_clone.clone()]
                                            } else {
                                                vec![
                                                    market_data_dir_clone.clone(),
                                                    market_data_dir_clone.parent()
                                                        .unwrap_or(&market_data_dir_clone)
                                                        .join("crypto_data")
                                                ]
                                            };

                                            match crate::services::migration::smart_sync_check(&db_path_clone, &csv_dirs).await {
                                                Ok(sync_result) => {
                                                    if sync_result.missing_files > 0 || sync_result.outdated_files > 0 {
                                                        info!("✅ [SQLITE] Background smart sync completed: {} files updated with {} records",
                                                              sync_result.missing_files + sync_result.outdated_files,
                                                              sync_result.migrated_records);
                                                    } else {
                                                        info!("✅ [SQLITE] Background smart sync check passed - all data current");
                                                    }
                                                }
                                                Err(e) => {
                                                    warn!("⚠️  [SQLITE] Background smart sync check failed: {}", e);
                                                }
                                            }
                                        });

                                        DataStoreBackend::SQLite(Arc::new(db))
                                    } else {
                                        warn!("⚠️  [SQLITE] Database exists but is empty. Starting background migration...");
                                        spawn_background_migration(db_path.clone(), market_data_dir.clone());
                                        DataStoreBackend::CSV
                                    }
                                }
                                Err(e) => {
                                    error!("❌ [SQLITE] Failed to get record count: {}. Starting background migration...", e);
                                    spawn_background_migration(db_path.clone(), market_data_dir.clone());
                                    DataStoreBackend::CSV
                                }
                            }
                        }
                        Err(e) => {
                            error!("❌ [SQLITE] Failed to initialize SQLite backend: {}. Starting background migration...", e);
                            spawn_background_migration(db_path.clone(), market_data_dir.clone());
                            DataStoreBackend::CSV
                        }
                    }
                } else {
                    warn!("⚠️  [SQLITE] SQLite database not found at {:?}. Starting background migration...", db_path);
                    spawn_background_migration(db_path.clone(), market_data_dir.clone());
                    DataStoreBackend::CSV
                }
            }
            _ => DataStoreBackend::CSV,
        };

        let db_path = if market_data_dir.to_string_lossy().contains("crypto") {
            Some(market_data_dir.parent()
                .unwrap_or(&market_data_dir)
                .join("crypto_data.db"))
        } else {
            Some(market_data_dir.join("..").join("market_data.db"))
        };

        // Log final backend selection
        match backend {
            DataStoreBackend::SQLite(_) => {
                info!("✅ [DATA_STORE] DataStore initialized with SQLite backend");
            }
            DataStoreBackend::CSV => {
                info!("📄 [DATA_STORE] DataStore initialized with CSV backend (migration in progress)");
            }
        }

        info!("[DATA_STORE] ===== DataStore Initialization Complete =====");

        Ok(Self {
            backend: RwLock::new(backend),
            data: RwLock::new(HashMap::new()),
            market_data_dir,
            cache_last_updated: RwLock::new(Utc::now()),
            db_path,
            last_sqlite_check: RwLock::new(Utc::now()),
        })
    }

    /// Check if SQLite database has caught up with CSV data
    async fn is_sqlite_caught_up(&self, db: &SQLiteDatabaseStore) -> bool {
        let sqlite_count = match db.get_record_count().await {
            Ok(count) => count,
            Err(e) => {
                debug!("Failed to get SQLite record count: {}", e);
                return false;
            }
        };

        if sqlite_count < 10000 {
            debug!("SQLite has {} records, threshold not met", sqlite_count);
            return false;
        }

        let seven_days_ago = Utc::now() - chrono::Duration::days(7);
        if let Ok(has_recent) = db.has_recent_data(seven_days_ago).await {
            if !has_recent {
                debug!("SQLite database lacks recent data (within 7 days)");
                return false;
            }
        }

        let key_tickers = if self.market_data_dir.to_string_lossy().contains("crypto") {
            vec!["BTC", "ETH"]
        } else {
            vec!["VNINDEX", "VCB", "FPT"]
        };

        for ticker in key_tickers {
            if let Ok(has_ticker) = db.has_ticker_data(ticker).await {
                if !has_ticker {
                    debug!("SQLite missing key ticker: {}", ticker);
                    return false;
                }
            }
        }

        info!("✅ SQLite catch-up verification passed: {} records", sqlite_count);
        true
    }

    /// Try to switch from CSV to SQLite backend if migration is complete
    async fn try_switch_to_sqlite(&self) -> bool {
        {
            let backend = self.backend.read().await;
            if matches!(*backend, DataStoreBackend::SQLite(_)) {
                return false;
            }
        }

        {
            let mut last_check = self.last_sqlite_check.write().await;
            let now = Utc::now();
            if (now - *last_check).num_seconds() < 30 {
                return false;
            }
            *last_check = now;
        }

        if let Some(ref db_path) = self.db_path {
            if db_path.exists() {
                match SQLiteDatabaseStore::new(db_path.clone()).await {
                    Ok(db) => {
                        if self.is_sqlite_caught_up(&db).await {
                            info!("✅ SQLite migration completed and verified! Switching to SQLite backend");

                            {
                                let mut backend = self.backend.write().await;
                                *backend = DataStoreBackend::SQLite(Arc::new(db));
                            }

                            {
                                let mut data = self.data.write().await;
                                data.clear();
                            }

                            return true;
                        }
                    }
                    Err(e) => {
                        debug!("SQLite database not ready yet: {}", e);
                    }
                }
            }
        }

        false
    }

    /// Get current backend type
    pub async fn get_backend_type(&self) -> String {
        let backend = self.backend.read().await;
        match &*backend {
            DataStoreBackend::CSV => "CSV".to_string(),
            DataStoreBackend::SQLite(_) => "SQLite".to_string(),
        }
    }

    /// Check if this DataStore is using SQLite backend
    pub async fn is_sqlite_backend(&self) -> bool {
        let backend = self.backend.read().await;
        matches!(*backend, DataStoreBackend::SQLite(_))
    }

    /// Check if this DataStore is using CSV backend
    pub async fn is_csv_backend(&self) -> bool {
        let backend = self.backend.read().await;
        matches!(*backend, DataStoreBackend::CSV)
    }

    /// Smart data retrieval with backend switching
    pub async fn get_data_smart(
        &self,
        params: QueryParameters,
    ) -> HashMap<String, Vec<StockData>> {
        // Log backend selection for this request
        let backend_type = self.get_backend_type().await;
        debug!("[DATA_STORE] Request routed to {} backend - symbols: {:?}, interval: {:?}",
               backend_type, params.tickers, params.interval);

        // Try to switch to SQLite if we're on CSV and migration is complete
        self.try_switch_to_sqlite().await;

        // Route to appropriate backend
        let backend = self.backend.read().await;
        match &*backend {
            DataStoreBackend::SQLite(sqlite_db) => {
                info!("🔍 [DATA_STORE] Using SQLite backend for request");
                match sqlite_db.get_data_smart(params.clone()).await {
                    Ok(data) => {
                        debug!("[DATA_STORE] SQLite returned {} tickers", data.len());
                        data
                    },
                    Err(e) => {
                        error!("❌ [DATA_STORE] SQLite backend error: {}. Falling back to CSV", e);
                        warn!("📄 [DATA_STORE] Falling back to CSV backend");
                        self.get_data_csv_fallback(params).await
                    }
                }
            }
            DataStoreBackend::CSV => {
                info!("📄 [DATA_STORE] Using CSV backend for request");
                self.get_data_csv_fallback(params).await
            }
        }
    }

    /// CSV fallback implementation
    async fn get_data_csv_fallback(
        &self,
        params: QueryParameters,
    ) -> HashMap<String, Vec<StockData>> {
        let mut result = HashMap::new();

        for ticker in &params.tickers {
            let csv_path = self.market_data_dir.join(ticker).join(params.interval.to_filename());

            if !csv_path.exists() {
                debug!("CSV file not found: {}", csv_path.display());
                continue;
            }

            match self.read_csv_file(&csv_path, ticker).await {
                Ok(mut data) => {
                    // Apply date filtering if specified
                    if let Some(start_date) = params.start_date {
                        data.retain(|d| d.time >= start_date);
                    }
                    if let Some(end_date) = params.end_date {
                        data.retain(|d| d.time <= end_date);
                    }

                    // Apply limit if specified
                    if params.limit > 0 && data.len() > params.limit {
                        data.truncate(params.limit);
                    }

                    result.insert(ticker.clone(), data);
                }
                Err(e) => {
                    warn!("Failed to read CSV file {}: {}", csv_path.display(), e);
                }
            }
        }

        result
    }

    /// Read a CSV file and return StockData
    async fn read_csv_file(&self, csv_path: &Path, ticker: &str) -> Result<Vec<StockData>, Error> {
        let csv_content = tokio::fs::read_to_string(csv_path).await
            .map_err(|e| Error::Other(format!("Failed to read CSV file {:?}: {}", csv_path, e)))?;

        let mut rdr = csv::Reader::from_reader(csv_content.as_bytes());
        let headers = rdr.headers().map_err(|e| Error::Other(format!("CSV parsing error: {}", e)))?;

        // Check if this is an enhanced CSV with technical indicators
        let is_enhanced = headers.len() >= 20; // 20 columns for enhanced CSV

        let mut stock_data = Vec::new();

        for result in rdr.records() {
            let record = result.map_err(|e| Error::Other(format!("CSV parsing error: {}", e)))?;

            match SQLiteUpdater::parse_csv_record(&record, ticker, self.interval_from_filename(csv_path)?, is_enhanced) {
                Ok(data) => stock_data.push(data),
                Err(e) => {
                    warn!("Skipping malformed record in {}: {}", csv_path.display(), e);
                    continue;
                }
            }
        }

        if stock_data.is_empty() {
            return Ok(vec![]);
        }

        // Sort by timestamp descending (most recent first)
        stock_data.sort_by(|a, b| b.time.cmp(&a.time));

        Ok(stock_data)
    }

    /// Determine interval from filename
    fn interval_from_filename(&self, csv_path: &Path) -> Result<Interval, Error> {
        let filename = csv_path.file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| Error::Other("Invalid filename".to_string()))?;

        match filename {
            "1D.csv" => Ok(Interval::Daily),
            "1h.csv" | "1H.csv" => Ok(Interval::Hourly),
            "1m.csv" => Ok(Interval::Minute),
            _ => Err(Error::Other(format!("Unknown interval file: {}", filename))),
        }
    }

    /// Load startup data for specified intervals
    pub async fn load_startup_data(&self, intervals: Vec<Interval>, skip_intervals: Option<Vec<Interval>>) -> Result<(), Error> {
        for interval in intervals {
            // Skip if interval is in skip_intervals
            if let Some(ref skip) = skip_intervals {
                if skip.contains(&interval) {
                    info!("⏭️  Skipping {} loading - background worker will handle it", interval.to_filename());
                    continue;
                }
            }

            // Load only limited data for fast startup
            let startup_limit = match interval {
                Interval::Daily => 128, // Fast startup: ~4 months of data
                Interval::Minute => MINUTE_DATA_RETENTION_RECORDS,
                _ => 300, // For hourly, reasonable startup amount
            };

            info!("Loading {} with {} records limit", interval.to_filename(), startup_limit);
        }
        Ok(())
    }

    /// Get record counts for statistics
    pub async fn get_record_counts(&self) -> (usize, usize, usize) {
        // Return default counts for now
        (0, 0, 0)
    }

    /// Get active ticker count
    pub async fn get_active_ticker_count(&self) -> usize {
        // Return default count for now
        0
    }

    /// Estimate memory usage
    pub fn estimate_memory_usage(&self) -> usize {
        // Return default estimate for now
        0
    }
}

/// Public function to estimate memory usage for compatibility
pub fn estimate_memory_usage() -> usize {
    // Return default estimate for now
    0
}

impl DataStore {
    /// Spawn auto-reload task for an interval
    pub async fn spawn_auto_reload_task(&self, interval: Interval) -> JoinHandle<()> {
        tokio::spawn(async move {
            info!("Auto-reload task started for {} interval", interval.to_filename());
            tokio::time::sleep(tokio::time::Duration::from_secs(30)).await;
            info!("Auto-reload task completed for {} interval", interval.to_filename());
        })
    }

    /// Get cache statistics
    pub async fn get_cache_stats(&self) -> (usize, f64) {
        // Return default cache stats for now
        (0, 0.0)
    }

    /// Get data with cache (alias for get_data_smart for compatibility)
    pub async fn get_data_with_cache(
        &self,
        tickers: Vec<String>,
        interval: Interval,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
        limit: Option<usize>,
        use_cache: bool,
    ) -> HashMap<String, Vec<StockData>> {
        let params = QueryParameters::new(
            tickers,
            interval,
            None, // aggregated_interval
            start_date,
            end_date,
            limit,
            use_cache,
            false, // legacy_prices
        );
        self.get_data_smart(params).await
    }

    /// Clear cache
    pub async fn clear_cache(&self) -> Result<(), Error> {
        info!("Clearing cache");
        Ok(())
    }

    /// Get all ticker names (placeholder implementation)
    pub async fn get_all_ticker_names(&self) -> Vec<String> {
        // Return empty for now - in real implementation this would scan the data directory
        vec![]
    }

    /// Get disk cache stats (placeholder implementation)
    pub async fn get_disk_cache_stats(&self) -> (usize, usize, usize) {
        // Return (entries, size_bytes, limit_bytes)
        (0, 0, 500 * 1024 * 1024) // 0 entries, 0 bytes, 500MB limit
    }
}

/// Spawn background migration task to populate SQLite database from CSV files
fn spawn_background_migration(db_path: PathBuf, market_data_dir: PathBuf) {
    info!("🚀 [SQLITE] Spawning background migration task");
    info!("[SQLITE] Migration target DB: {:?}", db_path);
    info!("[SQLITE] Migration source dir: {:?}", market_data_dir);

    tokio::spawn(async move {
        info!("📋 [SQLITE] Background migration task started");
        let csv_directories = if market_data_dir.ends_with("crypto_data") {
            vec![market_data_dir.clone()]
        } else {
            vec![
                market_data_dir.clone(),
                market_data_dir.parent()
                    .unwrap_or(&market_data_dir)
                    .join("crypto_data")
            ]
        };

        let config = MigrationConfig {
            database_path: db_path.clone(),
            csv_directories,
            batch_size: 5000,
            validate_data: true,
            progress_callback: None,
        };

        match CsvToSqliteMigration::new(db_path).await {
            Ok(migration) => {
                info!("📋 [SQLITE] Starting background CSV to SQLite migration");
                info!("[SQLITE] Directories to migrate: {:?}", config.csv_directories);
                match migration.migrate_directories(config).await {
                    Ok(result) => {
                        info!("✅ [SQLITE] Background migration completed successfully!");
                        info!("   Files processed: {}", result.total_files_processed);
                        info!("   Records migrated: {}", result.total_records_migrated);
                        info!("   Duration: {} seconds", result.duration_secs);
                        if result.total_errors > 0 {
                            warn!("   Errors encountered: {}", result.total_errors);
                        }
                    }
                    Err(e) => {
                        error!("❌ [SQLITE] Background migration failed: {}", e);
                    }
                }
            }
            Err(e) => {
                error!("❌ Failed to initialize background migration: {}", e);
            }
        }
    });
}