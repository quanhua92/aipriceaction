use std::path::{Path, PathBuf};
use std::sync::Arc;
use tracing::{info, error, warn, debug};
use notify::{Watcher, RecursiveMode, Event, EventKind, RecommendedWatcher};
use tokio::sync::mpsc;
use crate::models::StockData;
use crate::models::Interval;
use crate::error::AppError;
use crate::services::database::{SQLiteDatabaseStore, database_exists};

/// SQLite updater for real-time CSV synchronization
pub struct SQLiteUpdater {
    /// Main SQLite database for market data
    database: Arc<SQLiteDatabaseStore>,
    /// File watcher for CSV changes
    _watcher: RecommendedWatcher,
    /// Channel for receiving file change events
    _receiver: mpsc::UnboundedReceiver<PathBuf>,
}

/// Configuration for SQLiteUpdater
pub struct SQLiteUpdaterConfig {
    /// Path to SQLite database
    pub database_path: PathBuf,
    /// CSV directories to watch
    pub csv_directories: Vec<PathBuf>,
    /// Enable/disable real-time synchronization
    pub enable_realtime_sync: bool,
}

impl SQLiteUpdater {
    /// Create new SQLite updater with real-time monitoring
    pub async fn new(config: SQLiteUpdaterConfig) -> Result<Self, AppError> {
        info!("Initializing SQLite updater for database: {:?}", config.database_path);

        // Initialize or load SQLite database
        let database_exists = database_exists(&config.database_path).await;
        let database = if !database_exists {
            info!("Database not found, creating new database at: {:?}", config.database_path);
            SQLiteDatabaseStore::new(config.database_path.clone()).await?
        } else {
            info!("Loading existing database from: {:?}", config.database_path);
            SQLiteDatabaseStore::new(config.database_path.clone()).await?
        };

        let database = Arc::new(database);

        // Initial full sync from CSV directories
        if !config.csv_directories.is_empty() {
            info!("Performing initial sync from CSV directories...");
            Self::initial_sync(&database, &config.csv_directories).await?;
        }

        // Setup file watcher if real-time sync is enabled
        let (_watcher, _receiver) = if config.enable_realtime_sync {
            let (tx, rx) = mpsc::unbounded_channel();
            let mut watcher = notify::recommended_watcher(move |res| {
                match res {
                    Ok(event) => {
                        if let Event { kind: EventKind::Modify(_), paths, .. } = event {
                            for path in paths {
                                if let Some(ext) = path.extension() {
                                    if ext == "csv" {
                                        let _ = tx.send(path);
                                    }
                                }
                            }
                        }
                    }
                    Err(e) => error!("File watcher error: {:?}", e),
                }
            }).map_err(|e| AppError::Other(format!("Failed to create file watcher: {}", e)))?;

            // Watch CSV directories
            for csv_dir in &config.csv_directories {
                if csv_dir.exists() {
                    watcher.watch(csv_dir, RecursiveMode::Recursive)
                        .map_err(|e| AppError::Other(format!("Failed to watch directory {:?}: {}", csv_dir, e)))?;
                    info!("Watching CSV directory: {:?}", csv_dir);
                } else {
                    warn!("CSV directory does not exist: {:?}", csv_dir);
                }
            }

            (Some(watcher), Some(rx))
        } else {
            (None, None)
        };

        let updater = Self {
            database,
            _watcher: _watcher.unwrap(),
            _receiver: _receiver.unwrap(),
        };

        info!("SQLite updater initialized successfully");
        Ok(updater)
    }

    /// Perform initial full sync from CSV directories
    async fn initial_sync(database: &SQLiteDatabaseStore, csv_directories: &[PathBuf]) -> Result<(), AppError> {
        let mut total_records = 0;
        let mut total_files = 0;

        for csv_dir in csv_directories {
            if !csv_dir.exists() {
                warn!("CSV directory does not exist, skipping: {:?}", csv_dir);
                continue;
            }

            info!("Syncing CSV directory: {:?}", csv_dir);

            // Walk through directory and process CSV files
            let mut entries = tokio::fs::read_dir(csv_dir).await
                .map_err(|e| AppError::Io(format!("Failed to read CSV directory {:?}: {}", csv_dir, e)))?;

            while let Ok(Some(entry)) = entries.next_entry().await {
                let path = entry.path();

                if path.is_dir() {
                    // This is a ticker directory (e.g., market_data/VCB/)
                    Self::sync_ticker_directory(database, &path, &mut total_records, &mut total_files).await?;
                }
            }
        }

        info!("Initial sync complete: {} files, {} records synced", total_files, total_records);
        Ok(())
    }

    /// Sync a single ticker directory (e.g., market_data/VCB/)
    async fn sync_ticker_directory(
        database: &SQLiteDatabaseStore,
        ticker_dir: &Path,
        total_records: &mut i64,
        total_files: &mut i64,
    ) -> Result<(), AppError> {
        let ticker = ticker_dir.file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| AppError::InvalidInput(format!("Invalid ticker directory: {:?}", ticker_dir)))?;

        debug!("Syncing ticker directory: {} ({})", ticker, ticker_dir.display());

        // Process each CSV file in the ticker directory
        let mut entries = tokio::fs::read_dir(ticker_dir).await
            .map_err(|e| AppError::Io(format!("Failed to read ticker directory {:?}: {}", ticker_dir, e)))?;

        while let Ok(Some(entry)) = entries.next_entry().await {
            let path = entry.path();

            if let Some("csv") = path.extension().and_then(|s| s.to_str()) {
                let interval = Self::interval_from_filename(&path)?;

                match Self::sync_csv_file(database, &path, ticker, interval).await {
                    Ok(records) => {
                        *total_records += records as i64;
                        *total_files += 1;
                        debug!("Synced {}: {} records", path.display(), records);
                    }
                    Err(e) => {
                        warn!("Failed to sync {}: {}", path.display(), e);
                    }
                }
            }
        }

        Ok(())
    }

    /// Sync a single CSV file to SQLite
    async fn sync_csv_file(
        database: &SQLiteDatabaseStore,
        csv_path: &Path,
        ticker: &str,
        interval: Interval,
    ) -> Result<usize, AppError> {
        debug!("Syncing CSV file: {} (ticker: {}, interval: {:?})", csv_path.display(), ticker, interval);

        // Read and parse CSV file
        let csv_content = tokio::fs::read_to_string(csv_path).await
            .map_err(|e| AppError::Io(format!("Failed to read CSV file {:?}: {}", csv_path, e)))?;

        let mut rdr = csv::Reader::from_reader(csv_content.as_bytes());
        let headers = rdr.headers()?;

        // Check if this is an enhanced CSV with technical indicators
        let is_enhanced = headers.len() >= 20; // 20 columns for enhanced CSV

        let mut stock_data = Vec::new();
        let mut records_processed = 0;

        for result in rdr.records() {
            let record = result.map_err(|e| AppError::Io(format!("CSV parsing error: {}", e)))?;
            records_processed += 1;

            match Self::parse_csv_record(&record, ticker, interval, is_enhanced) {
                Ok(data) => stock_data.push(data),
                Err(e) => {
                    warn!("Skipping malformed record in {}: {}", csv_path.display(), e);
                    continue;
                }
            }
        }

        if stock_data.is_empty() {
            warn!("No valid records found in CSV file: {}", csv_path.display());
            return Ok(0);
        }

        // Clear existing data for this ticker/interval to ensure clean sync
        database.delete_ticker_interval(ticker, interval).await
            .map_err(|e| AppError::Database(e.to_string()))?;

        // Insert new data
        let inserted = database.upsert_market_data(&stock_data).await
            .map_err(|e| AppError::Database(e.to_string()))?;

        info!("Synced {}: {} records processed, {} records inserted",
              csv_path.display(), records_processed, inserted);

        Ok(inserted)
    }

    /// Parse a CSV record into StockData
    pub fn parse_csv_record(
        record: &csv::StringRecord,
        ticker: &str,
        interval: Interval,
        is_enhanced: bool,
    ) -> Result<StockData, AppError> {
        if record.len() < 7 {
            return Err(AppError::InvalidInput("CSV record has insufficient columns".to_string()));
        }

        // Parse basic OHLCV data
        let time = match interval {
            Interval::Daily => {
                // Format: "2024-01-01"
                let date_str = record.get(1).ok_or_else(|| AppError::Parse("Missing date column".to_string()))?;
                let date = chrono::NaiveDate::parse_from_str(date_str, "%Y-%m-%d")
                    .map_err(|e| AppError::Parse(format!("Invalid date '{}': {}", date_str, e)))?;
                date.and_hms_opt(0, 0, 0).unwrap().and_utc()
            }
            Interval::Hourly | Interval::Minute => {
                // Format: "2024-01-01 09:00:00"
                let datetime_str = record.get(1).ok_or_else(|| AppError::Parse("Missing datetime column".to_string()))?;
                chrono::NaiveDateTime::parse_from_str(datetime_str, "%Y-%m-%d %H:%M:%S")
                    .map(|dt| dt.and_utc())
                    .map_err(|e| AppError::Parse(format!("Invalid datetime '{}': {}", datetime_str, e)))?
            }
        };

        let stock_data = if is_enhanced && record.len() >= 20 {
            // Enhanced CSV with technical indicators
            StockData {
                ticker: ticker.to_string(),
                time,
                open: record.get(2).unwrap_or("0").parse().unwrap_or(0.0),
                high: record.get(3).unwrap_or("0").parse().unwrap_or(0.0),
                low: record.get(4).unwrap_or("0").parse().unwrap_or(0.0),
                close: record.get(5).unwrap_or("0").parse().unwrap_or(0.0),
                volume: record.get(6).unwrap_or("0").parse().unwrap_or(0u64),
                ma10: Self::parse_optional_f64(record.get(7)),
                ma20: Self::parse_optional_f64(record.get(8)),
                ma50: Self::parse_optional_f64(record.get(9)),
                ma100: Self::parse_optional_f64(record.get(10)),
                ma200: Self::parse_optional_f64(record.get(11)),
                ma10_score: Self::parse_optional_f64(record.get(12)),
                ma20_score: Self::parse_optional_f64(record.get(13)),
                ma50_score: Self::parse_optional_f64(record.get(14)),
                ma100_score: Self::parse_optional_f64(record.get(15)),
                ma200_score: Self::parse_optional_f64(record.get(16)),
                close_changed: Self::parse_optional_f64(record.get(17)),
                volume_changed: Self::parse_optional_f64(record.get(18)),
                total_money_changed: Self::parse_optional_f64(record.get(19)),
            }
        } else {
            // Basic CSV without technical indicators
            StockData {
                ticker: ticker.to_string(),
                time,
                open: record.get(2).unwrap_or("0").parse().unwrap_or(0.0),
                high: record.get(3).unwrap_or("0").parse().unwrap_or(0.0),
                low: record.get(4).unwrap_or("0").parse().unwrap_or(0.0),
                close: record.get(5).unwrap_or("0").parse().unwrap_or(0.0),
                volume: record.get(6).unwrap_or("0").parse().unwrap_or(0u64),
                ma10: None,
                ma20: None,
                ma50: None,
                ma100: None,
                ma200: None,
                ma10_score: None,
                ma20_score: None,
                ma50_score: None,
                ma100_score: None,
                ma200_score: None,
                close_changed: None,
                volume_changed: None,
                total_money_changed: None,
            }
        };

        Ok(stock_data)
    }

    /// Get interval from filename (1D.csv, 1H.csv, 1m.csv)
    pub fn interval_from_filename(path: &Path) -> Result<Interval, AppError> {
        let filename = path.file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| AppError::InvalidInput("Invalid filename".to_string()))?;

        match filename {
            "1D.csv" => Ok(Interval::Daily),
            "1H.csv" => Ok(Interval::Hourly),
            "1m.csv" => Ok(Interval::Minute),
            _ => Err(AppError::InvalidInput(format!("Unknown interval file: {}", filename))),
        }
    }

    /// Parse optional f64 value from CSV record
    fn parse_optional_f64(value: Option<&str>) -> Option<f64> {
        match value {
            Some(v) if !v.is_empty() => v.parse().ok(),
            _ => None,
        }
    }

    /// Get statistics about the database
    pub async fn get_stats(&self) -> Result<SQLiteStats, AppError> {
        let total_records = self.database.get_record_count().await
            .map_err(|e| AppError::Database(e.to_string()))?;

        let stats = SQLiteStats {
            total_records,
            is_connected: true,
        };

        Ok(stats)
    }

    /// Force sync a specific CSV file
    pub async fn sync_file(&self, csv_path: &Path) -> Result<usize, AppError> {
        let ticker = csv_path.parent()
            .and_then(|p| p.file_name())
            .and_then(|n| n.to_str())
            .ok_or_else(|| AppError::InvalidInput(format!("Cannot extract ticker from path: {:?}", csv_path)))?;

        let interval = Self::interval_from_filename(csv_path)?;

        Self::sync_csv_file(&self.database, csv_path, ticker, interval).await
    }
}

/// SQLite updater statistics
#[derive(Debug, Clone)]
pub struct SQLiteStats {
    pub total_records: i64,
    pub is_connected: bool,
}

/// Create default SQLite updater configuration
impl Default for SQLiteUpdaterConfig {
    fn default() -> Self {
        Self {
            database_path: PathBuf::from("market_data.db"),
            csv_directories: vec![
                PathBuf::from("market_data"),
                PathBuf::from("crypto_data"),
            ],
            enable_realtime_sync: true,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;
    use std::fs::File;
    use std::io::Write;

    #[tokio::test]
    async fn test_interval_from_filename() {
        assert_eq!(
            SQLiteUpdater::interval_from_filename(Path::new("1D.csv")).unwrap(),
            Interval::Daily
        );
        assert_eq!(
            SQLiteUpdater::interval_from_filename(Path::new("1H.csv")).unwrap(),
            Interval::Hourly
        );
        assert_eq!(
            SQLiteUpdater::interval_from_filename(Path::new("1m.csv")).unwrap(),
            Interval::Minute
        );
        assert!(SQLiteUpdater::interval_from_filename(Path::new("invalid.csv")).is_err());
    }

    #[tokio::test]
    async fn test_parse_csv_record() {
        let record = csv::StringRecord::from(vec![
            "VCB", "2024-01-01", "60000", "61000", "59000", "60500", "1000000",
            "60000", "59500", "59000", "58500", "58000",  // MA values
            "0.8", "1.7", "2.5", "3.4", "4.3",            // MA scores
            "0.5", "2.0", "500000000"                     // Changes
        ]);

        let stock_data = SQLiteUpdater::parse_csv_record(&record, "VCB", Interval::Daily, true).unwrap();

        assert_eq!(stock_data.ticker, "VCB");
        assert_eq!(stock_data.open, 60000.0);
        assert_eq!(stock_data.close, 60500.0);
        assert_eq!(stock_data.volume, 1000000);
        assert_eq!(stock_data.ma10, Some(60000.0));
        assert_eq!(stock_data.close_changed, Some(0.5));
    }

    #[test]
    fn test_parse_optional_f64() {
        assert_eq!(SQLiteUpdater::parse_optional_f64(Some("123.45")), Some(123.45));
        assert_eq!(SQLiteUpdater::parse_optional_f64(Some("")), None);
        assert_eq!(SQLiteUpdater::parse_optional_f64(None), None);
        assert_eq!(SQLiteUpdater::parse_optional_f64(Some("invalid")), None);
    }
}