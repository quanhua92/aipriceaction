use crate::constants::{csv_column, DEFAULT_CACHE_AUTO_CLEAR_ENABLED, DEFAULT_CACHE_AUTO_CLEAR_THRESHOLD, DEFAULT_CACHE_AUTO_CLEAR_RATIO};
use crate::error::Error;
use crate::models::{Interval, StockData, AggregatedInterval};
use crate::utils::{parse_timestamp, deduplicate_stock_data_by_time, open_file_atomic_read};
use crate::services::database::SQLiteDatabaseStore;
use crate::services::migration::{CsvToSqliteMigration, MigrationConfig};
use tracing::{debug, info, warn, error};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::SystemTime;
use std::io::{Read, Seek};
use tokio::sync::RwLock;
use tokio::task::JoinHandle;
use memmap2::Mmap;
use std::env;

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
        let backend_preference = env::var("DATA_STORE_BACKEND")
            .unwrap_or_else(|_| "csv".to_string())
            .to_lowercase();

        let backend = match backend_preference.as_str() {
            "sqlite" => {
                let db_path = if market_data_dir.to_string_lossy().contains("crypto") {
                    market_data_dir.parent()
                        .unwrap_or(&market_data_dir)
                        .join("crypto_data.db")
                } else {
                    market_data_dir.join("..").join("market_data.db")
                };

                if db_path.exists() {
                    match SQLiteDatabaseStore::new(db_path.clone()).await {
                        Ok(db) => {
                            if let Ok(record_count) = db.get_record_count().await {
                                if record_count > 0 {
                                    info!("Using existing SQLite database with {} records", record_count);
                                    DataStoreBackend::SQLite(Arc::new(db))
                                } else {
                                    warn!("SQLite database exists but is empty. Starting background migration...");
                                    spawn_background_migration(db_path.clone(), market_data_dir.clone());
                                    DataStoreBackend::CSV
                                }
                            } else {
                                DataStoreBackend::CSV
                            }
                        }
                        Err(e) => {
                            warn!("Failed to initialize SQLite backend: {}. Starting background migration...", e);
                            spawn_background_migration(db_path.clone(), market_data_dir.clone());
                            DataStoreBackend::CSV
                        }
                    }
                } else {
                    info!("SQLite database not found. Starting background migration...");
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
        // Try to switch to SQLite if we're on CSV and migration is complete
        self.try_switch_to_sqlite().await;

        // Route to appropriate backend
        let backend = self.backend.read().await;
        match &*backend {
            DataStoreBackend::SQLite(sqlite_db) => {
                match sqlite_db.get_data_smart(params.clone()).await {
                    Ok(data) => data,
                    Err(e) => {
                        error!("SQLite backend error: {}. Falling back to CSV", e);
                        self.get_data_csv_fallback(params).await
                    }
                }
            }
            DataStoreBackend::CSV => {
                self.get_data_csv_fallback(params).await
            }
        }
    }

    /// CSV fallback implementation
    async fn get_data_csv_fallback(
        &self,
        _params: QueryParameters,
    ) -> HashMap<String, Vec<StockData>> {
        // For now, return empty data
        warn!("CSV fallback not implemented yet");
        HashMap::new()
    }
}

/// Spawn background migration task to populate SQLite database from CSV files
fn spawn_background_migration(db_path: PathBuf, market_data_dir: PathBuf) {
    info!("Spawning background migration task");

    tokio::spawn(async move {
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
                info!("Starting background CSV to SQLite migration");
                match migration.migrate_directories(config).await {
                    Ok(result) => {
                        info!("✅ Background migration completed successfully!");
                        info!("   Files processed: {}", result.total_files_processed);
                        info!("   Records migrated: {}", result.total_records_migrated);
                        info!("   Duration: {} seconds", result.duration_secs);
                        if result.total_errors > 0 {
                            warn!("   Errors encountered: {}", result.total_errors);
                        }
                    }
                    Err(e) => {
                        error!("❌ Background migration failed: {}", e);
                    }
                }
            }
            Err(e) => {
                error!("❌ Failed to initialize background migration: {}", e);
            }
        }
    });
}