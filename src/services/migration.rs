use std::path::{Path, PathBuf};
use std::sync::Arc;
use tracing::{info, warn, error, debug};
use crate::models::Interval;
use crate::error::AppError;
use crate::services::database::{SQLiteDatabaseStore, database_exists};
use crate::services::sqlite_updater::SQLiteUpdater;
use indicatif::{ProgressBar, ProgressStyle};
use std::time::Instant;

/// Migration progress callback
pub type MigrationProgressCallback = Box<dyn Fn(usize, usize) + Send + Sync>;

/// Migration configuration
pub struct MigrationConfig {
    /// Path to SQLite database
    pub database_path: PathBuf,
    /// CSV directories to migrate from
    pub csv_directories: Vec<PathBuf>,
    /// Batch size for database inserts
    pub batch_size: usize,
    /// Whether to validate data integrity during migration
    pub validate_data: bool,
    /// Progress callback
    pub progress_callback: Option<MigrationProgressCallback>,
}

/// Migration result statistics
#[derive(Debug, Clone)]
pub struct MigrationResult {
    pub total_files_processed: usize,
    pub total_records_migrated: usize,
    pub total_errors: usize,
    pub errors: Vec<String>,
    pub duration_secs: u64,
}

/// CSV to SQLite migration tool
pub struct CsvToSqliteMigration {
    pub database: Arc<SQLiteDatabaseStore>,
}

impl CsvToSqliteMigration {
    /// Create new migration instance
    pub async fn new(database_path: PathBuf) -> Result<Self, AppError> {
        info!("Initializing CSV to SQLite migration");

        let database = SQLiteDatabaseStore::new(database_path).await?;
        let database = Arc::new(database);

        Ok(Self { database })
    }

    /// Perform full migration from CSV directories to SQLite
    pub async fn migrate_directories(&self, config: MigrationConfig) -> Result<MigrationResult, AppError> {
        let start_time = Instant::now();
        info!("Starting CSV to SQLite migration");
        info!("Database: {:?}", config.database_path);
        info!("CSV directories: {:?}", config.csv_directories);

        let mut result = MigrationResult {
            total_files_processed: 0,
            total_records_migrated: 0,
            total_errors: 0,
            errors: Vec::new(),
            duration_secs: 0,
        };

        // Count total CSV files for progress tracking
        let total_files = self.count_csv_files(&config.csv_directories).await?;
        info!("Found {} CSV files to migrate", total_files);

        let pb = if total_files > 0 {
            let pb = ProgressBar::new(total_files as u64);
            pb.set_style(
                ProgressStyle::default_bar()
                    .template("{spinner:.green} [{elapsed_precise}] [{wide_bar:.cyan/blue}] {pos}/{len} ({eta})")
                    .unwrap()
                    .progress_chars("#>-")
            );
            Some(pb)
        } else {
            None
        };

        // Migrate each directory
        for csv_dir in &config.csv_directories {
            if !csv_dir.exists() {
                warn!("CSV directory does not exist, skipping: {:?}", csv_dir);
                continue;
            }

            let dir_result = self.migrate_directory(csv_dir, &config, &mut result).await?;
            result.total_records_migrated += dir_result;
        }

        if let Some(pb) = pb {
            pb.finish_with_message("Migration complete");
        }

        result.duration_secs = start_time.elapsed().as_secs();

        // Print summary
        info!("Migration completed in {} seconds", result.duration_secs);
        info!("Files processed: {}", result.total_files_processed);
        info!("Records migrated: {}", result.total_records_migrated);
        if result.total_errors > 0 {
            warn!("Errors encountered: {}", result.total_errors);
            for error in &result.errors {
                warn!("  {}", error);
            }
        }

        Ok(result)
    }

    /// Migrate a single CSV directory
    async fn migrate_directory(
        &self,
        csv_dir: &Path,
        config: &MigrationConfig,
        result: &mut MigrationResult,
    ) -> Result<usize, AppError> {
        let mut total_records = 0;

        // Read ticker directories
        let mut entries = tokio::fs::read_dir(csv_dir).await
            .map_err(|e| AppError::Io(format!("Failed to read directory {:?}: {}", csv_dir, e)))?;

        while let Ok(Some(entry)) = entries.next_entry().await {
            let path = entry.path();

            if path.is_dir() {
                // Process ticker directory
                let dir_result = self.migrate_ticker_directory(&path, config, &mut total_records, result).await;
                match dir_result {
                    Ok(records) => {
                        debug!("Migrated ticker directory: {} ({}) records", path.display(), records);
                    }
                    Err(e) => {
                        error!("Failed to migrate directory {}: {}", path.display(), e);
                        result.total_errors += 1;
                        result.errors.push(format!("{}: {}", path.display(), e));
                    }
                }
            }
        }

        Ok(total_records)
    }

    /// Migrate a single ticker directory
    async fn migrate_ticker_directory(
        &self,
        ticker_dir: &Path,
        config: &MigrationConfig,
        total_records: &mut usize,
        result: &mut MigrationResult,
    ) -> Result<usize, AppError> {
        let ticker = ticker_dir.file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| AppError::InvalidInput(format!("Invalid ticker directory: {:?}", ticker_dir)))?;

        // Process CSV files in ticker directory
        let mut entries = tokio::fs::read_dir(ticker_dir).await
            .map_err(|e| AppError::Io(format!("Failed to read ticker directory {:?}: {}", ticker_dir, e)))?;

        let mut ticker_records = 0;

        while let Ok(Some(entry)) = entries.next_entry().await {
            let path = entry.path();

            if let Some("csv") = path.extension().and_then(|s| s.to_str()) {
                let interval = SQLiteUpdater::interval_from_filename(&path)?;

                match self.migrate_csv_file(&path, ticker, interval, config).await {
                    Ok(records) => {
                        *total_records += records;
                        ticker_records += records;
                        result.total_files_processed += 1;

                        // Call progress callback if provided
                        if let Some(ref callback) = config.progress_callback {
                            callback(result.total_files_processed, records);
                        }
                    }
                    Err(e) => {
                        error!("Failed to migrate {}: {}", path.display(), e);
                        result.total_errors += 1;
                        result.errors.push(format!("{}: {}", path.display(), e));
                    }
                }
            }
        }

        Ok(ticker_records)
    }

    /// Migrate a single CSV file
    async fn migrate_csv_file(
        &self,
        csv_path: &Path,
        ticker: &str,
        interval: Interval,
        config: &MigrationConfig,
    ) -> Result<usize, AppError> {
        debug!("Migrating CSV file: {} (ticker: {}, interval: {:?})", csv_path.display(), ticker, interval);

        // Read CSV file
        let csv_content = tokio::fs::read_to_string(csv_path).await
            .map_err(|e| AppError::Io(format!("Failed to read CSV file {:?}: {}", csv_path, e)))?;

        let mut rdr = csv::Reader::from_reader(csv_content.as_bytes());
        let headers = rdr.headers()?;

        // Check if this is an enhanced CSV with technical indicators
        let is_enhanced = headers.len() >= 20; // 20 columns for enhanced CSV

        let mut records_processed = 0;

        // Process CSV records in batches
        let mut batch = Vec::new();

        for result in rdr.records() {
            let record = result.map_err(|e| AppError::Io(format!("CSV parsing error: {}", e)))?;
            records_processed += 1;

            match SQLiteUpdater::parse_csv_record(&record, ticker, interval, is_enhanced) {
                Ok(data) => {
                    batch.push(data);

                    // Process batch when it reaches the configured size
                    if batch.len() >= config.batch_size {
                        let inserted = self.database.upsert_market_data(&batch).await
                            .map_err(|e| AppError::Database(e.to_string()))?;
                        debug!("Inserted batch of {} records", inserted);
                        batch.clear();
                    }
                }
                Err(e) => {
                    warn!("Skipping malformed record in {}: {}", csv_path.display(), e);
                    continue;
                }
            }
        }

        // Insert remaining records in the last batch
        if !batch.is_empty() {
            let inserted = self.database.upsert_market_data(&batch).await
                .map_err(|e| AppError::Database(e.to_string()))?;
            debug!("Inserted final batch of {} records", inserted);
        }

        info!("Migrated {}: {} records processed", csv_path.display(), records_processed);
        Ok(records_processed)
    }

    /// Count total CSV files in directories
    async fn count_csv_files(&self, csv_directories: &[PathBuf]) -> Result<usize, AppError> {
        let mut total_files = 0;

        for csv_dir in csv_directories {
            if !csv_dir.exists() {
                continue;
            }

            let mut entries = tokio::fs::read_dir(csv_dir).await
                .map_err(|e| AppError::Io(format!("Failed to read directory {:?}: {}", csv_dir, e)))?;

            while let Ok(Some(entry)) = entries.next_entry().await {
                                let path = entry.path();

                if path.is_dir() {
                    // Count CSV files in ticker directory
                    if let Ok(mut sub_entries) = tokio::fs::read_dir(&path).await {
                        while let Ok(Some(sub_entry)) = sub_entries.next_entry().await {
                                                        let sub_path = sub_entry.path();

                            if sub_path.extension().map(|s| s == "csv").unwrap_or(false) {
                                total_files += 1;
                            }
                        }
                    }
                }
            }
        }

        Ok(total_files)
    }

    /// Validate migration by comparing CSV and SQLite data counts
    pub async fn validate_migration(&self, csv_directories: &[PathBuf]) -> Result<ValidationResult, AppError> {
        info!("Validating migration results");

        let mut validation = ValidationResult {
            total_csv_records: 0,
            total_sqlite_records: 0,
            validation_errors: Vec::new(),
        };

        // Count CSV records
        for csv_dir in csv_directories {
            validation.total_csv_records += self.count_csv_records(csv_dir).await?;
        }

        // Count SQLite records
        validation.total_sqlite_records = self.database.get_record_count().await
            .map_err(|e| AppError::Database(e.to_string()))? as usize;

        // Check for discrepancies
        if validation.total_csv_records != validation.total_sqlite_records {
            validation.validation_errors.push(format!(
                "Record count mismatch: CSV={}, SQLite={}",
                validation.total_csv_records, validation.total_sqlite_records
            ));
        }

        if validation.validation_errors.is_empty() {
            info!("Migration validation passed: {} records", validation.total_csv_records);
        } else {
            warn!("Migration validation failed with {} errors", validation.validation_errors.len());
            for error in &validation.validation_errors {
                warn!("  {}", error);
            }
        }

        Ok(validation)
    }

    /// Count total records in CSV files
    async fn count_csv_records(&self, csv_dir: &Path) -> Result<usize, AppError> {
        let mut total_records = 0;

        if !csv_dir.exists() {
            return Ok(0);
        }

        let mut entries = tokio::fs::read_dir(csv_dir).await
            .map_err(|e| AppError::Io(format!("Failed to read directory {:?}: {}", csv_dir, e)))?;

        while let Ok(Some(entry)) = entries.next_entry().await {
                        let path = entry.path();

            if path.is_dir() {
                // Count records in ticker directory CSV files
                if let Ok(mut sub_entries) = tokio::fs::read_dir(&path).await {
                    while let Ok(Some(sub_entry)) = sub_entries.next_entry().await {
                                                let sub_path = sub_entry.path();

                        if sub_path.extension().map(|s| s == "csv").unwrap_or(false) {
                            let content = tokio::fs::read_to_string(&sub_path).await?;
                            let lines = content.lines().count();
                            if lines > 1 { // Subtract header row
                                total_records += lines - 1;
                            }
                        }
                    }
                }
            }
        }

        Ok(total_records)
    }
}

/// Migration validation result
#[derive(Debug, Clone)]
pub struct ValidationResult {
    pub total_csv_records: usize,
    pub total_sqlite_records: usize,
    pub validation_errors: Vec<String>,
}

/// Convenience function to perform a complete migration
pub async fn migrate_csv_to_sqlite(
    database_path: PathBuf,
    csv_directories: Vec<PathBuf>,
) -> Result<MigrationResult, AppError> {
    let migration = CsvToSqliteMigration::new(database_path).await?;

    let config = MigrationConfig {
        database_path: PathBuf::new(), // Not used in this context
        csv_directories,
        batch_size: 1000,
        validate_data: true,
        progress_callback: None,
    };

    migration.migrate_directories(config).await
}

/// Check if database exists and has data
pub async fn is_database_ready(database_path: &Path) -> Result<bool, AppError> {
    if !database_exists(&database_path.to_path_buf()).await {
        return Ok(false);
    }

    let database = SQLiteDatabaseStore::new(database_path.to_path_buf()).await?;
    let record_count = database.get_record_count().await
        .map_err(|e| AppError::Database(e.to_string()))?;

    Ok(record_count > 0)
}

/// Smart sync: Check CSV files against SQLite and migrate missing data (optimized for performance)
pub async fn smart_sync_check(
    database_path: &Path,
    csv_directories: &[PathBuf],
) -> Result<SmartSyncResult, AppError> {
    info!("Starting smart sync check for database: {:?}", database_path);

    let database = SQLiteDatabaseStore::new(database_path.to_path_buf()).await?;
    let mut result = SmartSyncResult {
        total_csv_files: 0,
        missing_files: 0,
        outdated_files: 0,
        migrated_records: 0,
        errors: Vec::new(),
    };

    // Limit to first 20 ticker directories per run to avoid long blocking
    let mut ticker_count = 0;
    const MAX_TICKERS_PER_RUN: usize = 20;

    for csv_dir in csv_directories {
        if !csv_dir.exists() {
            continue;
        }

        // Read ticker directories
        let mut entries = tokio::fs::read_dir(csv_dir).await
            .map_err(|e| AppError::Io(format!("Failed to read directory {:?}: {}", csv_dir, e)))?;

        while let Ok(Some(entry)) = entries.next_entry().await {
            if ticker_count >= MAX_TICKERS_PER_RUN {
                info!("Reached smart sync limit ({} tickers), continuing in background", MAX_TICKERS_PER_RUN);
                break;
            }

            let ticker_dir = entry.path();
            if ticker_dir.is_dir() {
                ticker_count += 1;

                // Quick check: only process daily (1D) files for performance
                let daily_csv = ticker_dir.join("1D.csv");
                if daily_csv.exists() {
                    result.total_csv_files += 1;

                    // Quick timestamp check
                    match quick_csv_sqlite_check(&database, &daily_csv, ticker_dir.file_name().unwrap().to_str().unwrap()).await {
                        Ok(CsvSyncStatus::Missing) => {
                            result.missing_files += 1;
                            info!("Migrating missing daily file: {:?}", daily_csv);
                            match migrate_single_csv_file(&database, &daily_csv).await {
                                Ok(records) => {
                                    result.migrated_records += records;
                                    info!("Migrated {} records from {:?}", records, daily_csv);
                                }
                                Err(e) => {
                                    error!("Failed to migrate {:?}: {}", daily_csv, e);
                                    result.errors.push(format!("{}: {}", daily_csv.display(), e));
                                }
                            }
                        }
                        Ok(CsvSyncStatus::Outdated) => {
                            result.outdated_files += 1;
                            info!("Migrating outdated daily file: {:?}", daily_csv);
                            match migrate_single_csv_file(&database, &daily_csv).await {
                                Ok(records) => {
                                    result.migrated_records += records;
                                    info!("Migrated {} records from {:?}", records, daily_csv);
                                }
                                Err(e) => {
                                    error!("Failed to migrate {:?}: {}", daily_csv, e);
                                    result.errors.push(format!("{}: {}", daily_csv.display(), e));
                                }
                            }
                        }
                        Ok(CsvSyncStatus::InSync) => {
                            // File is in sync, no action needed
                        }
                        Err(_) => {
                            // Error during check, treat as missing to be safe
                            result.missing_files += 1;
                            info!("Migrating unchecked daily file: {:?}", daily_csv);
                            match migrate_single_csv_file(&database, &daily_csv).await {
                                Ok(records) => {
                                    result.migrated_records += records;
                                    info!("Migrated {} records from {:?}", records, daily_csv);
                                }
                                Err(e) => {
                                    error!("Failed to migrate {:?}: {}", daily_csv, e);
                                    result.errors.push(format!("{}: {}", daily_csv.display(), e));
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    info!("Smart sync check completed: {} tickers checked, {} files processed, {} migrated records",
          ticker_count, result.total_csv_files, result.migrated_records);

    Ok(result)
}

/// Quick check if a single daily CSV file is in sync with SQLite
async fn quick_csv_sqlite_check(
    database: &SQLiteDatabaseStore,
    csv_path: &Path,
    ticker: &str,
) -> Result<CsvSyncStatus, AppError> {
    // Get last line from CSV file
    let csv_last_line = read_csv_last_line(csv_path).await?;
    if csv_last_line.is_empty() {
        return Ok(CsvSyncStatus::Missing);
    }

    // Parse CSV line to get timestamp
    let csv_timestamp = parse_csv_timestamp(&csv_last_line, csv_path)?;

    // Get latest timestamp from SQLite for this ticker (daily only)
    match database.get_latest_timestamp(ticker, "1D").await {
        Ok(Some(sqlite_ts)) => {
            // Compare timestamps (allow 1 day tolerance for daily data)
            let time_diff = (csv_timestamp - sqlite_ts).abs();
            if time_diff.num_hours() > 24 {
                Ok(CsvSyncStatus::Outdated)
            } else {
                Ok(CsvSyncStatus::InSync)
            }
        }
        Ok(None) => Ok(CsvSyncStatus::Missing), // No data in SQLite
        Err(_) => Ok(CsvSyncStatus::Missing), // Error, treat as missing
    }
}

/// Check sync status for a single ticker directory
async fn check_ticker_sync(
    database: &SQLiteDatabaseStore,
    ticker_dir: &Path,
) -> Result<TickerSyncResult, AppError> {
    let ticker = ticker_dir.file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| AppError::InvalidInput(format!("Invalid ticker directory: {:?}", ticker_dir)))?;

    let mut result = TickerSyncResult {
        total_files: 0,
        missing_files: 0,
        outdated_files: 0,
        missing_files_list: Vec::new(),
    };

    // Process CSV files in ticker directory
    let mut entries = tokio::fs::read_dir(ticker_dir).await
        .map_err(|e| AppError::Io(format!("Failed to read ticker directory {:?}: {}", ticker_dir, e)))?;

    while let Ok(Some(entry)) = entries.next_entry().await {
        let path = entry.path();
        if let Some("csv") = path.extension().and_then(|s| s.to_str()) {
            result.total_files += 1;

            match check_csv_file_sync(database, &path, ticker).await {
                Ok(sync_status) => {
                    match sync_status {
                        CsvSyncStatus::Missing => {
                            result.missing_files += 1;
                            result.missing_files_list.push(path);
                        }
                        CsvSyncStatus::Outdated => {
                            result.outdated_files += 1;
                            result.missing_files_list.push(path);
                        }
                        CsvSyncStatus::InSync => {
                            // File is in sync, no action needed
                        }
                    }
                }
                Err(e) => {
                    warn!("Error checking CSV sync for {:?}: {}", path, e);
                    result.missing_files += 1;
                    result.missing_files_list.push(path);
                }
            }
        }
    }

    Ok(result)
}

/// Check if a single CSV file is in sync with SQLite
async fn check_csv_file_sync(
    database: &SQLiteDatabaseStore,
    csv_path: &Path,
    ticker: &str,
) -> Result<CsvSyncStatus, AppError> {
    // Get last line from CSV file
    let csv_last_line = read_csv_last_line(csv_path).await?;
    if csv_last_line.is_empty() {
        return Ok(CsvSyncStatus::Missing); // Empty file or no data
    }

    // Parse CSV line to get timestamp
    let csv_timestamp = parse_csv_timestamp(&csv_last_line, csv_path)?;

    // Get latest timestamp from SQLite for this ticker/interval
    let interval = SQLiteUpdater::interval_from_filename(csv_path)?;
    let sqlite_timestamp = get_latest_sqlite_timestamp(database, ticker, interval).await?;

    match sqlite_timestamp {
        None => Ok(CsvSyncStatus::Missing), // No data in SQLite
        Some(sqlite_ts) => {
            // Compare timestamps (allow 1 day tolerance for daily data)
            let time_diff = (csv_timestamp - sqlite_ts).abs();

            let tolerance_hours = match interval {
                Interval::Daily => 48, // 2 days tolerance for daily data
                Interval::Hourly => 2, // 2 hours tolerance for hourly data
                Interval::Minute => 1, // 1 hour tolerance for minute data
            };

            if time_diff.num_hours() > tolerance_hours {
                Ok(CsvSyncStatus::Outdated)
            } else {
                Ok(CsvSyncStatus::InSync)
            }
        }
    }
}

/// Read the last non-empty line from a CSV file
async fn read_csv_last_line(csv_path: &Path) -> Result<String, AppError> {
    let content = tokio::fs::read_to_string(csv_path).await
        .map_err(|e| AppError::Io(format!("Failed to read CSV file {:?}: {}", csv_path, e)))?;

    // Split by lines and find the last non-empty, non-header line
    let lines: Vec<&str> = content.lines().collect();
    for line in lines.iter().rev() {
        let line = line.trim();
        if !line.is_empty() && !line.starts_with("ticker,") {
            return Ok(line.to_string());
        }
    }

    Ok(String::new()) // No data lines found
}

/// Parse timestamp from CSV line
fn parse_csv_timestamp(csv_line: &str, csv_path: &Path) -> Result<chrono::DateTime<chrono::Utc>, AppError> {
    use csv::Reader;
    let mut rdr = Reader::from_reader(csv_line.as_bytes());
    let record = rdr.records().next()
        .ok_or_else(|| AppError::Parse("No records in CSV line".to_string()))?
        .map_err(|e| AppError::Parse(format!("CSV parsing error: {}", e)))?;

    let timestamp_str = record.get(1).ok_or_else(|| AppError::Parse("Missing timestamp column".to_string()))?;

    // Parse timestamp based on filename (interval)
    let interval = SQLiteUpdater::interval_from_filename(csv_path)?;

    match interval {
        Interval::Daily => {
            // Daily: "2024-01-01"
            let date = chrono::NaiveDate::parse_from_str(timestamp_str, "%Y-%m-%d")
                .map_err(|e| AppError::Parse(format!("Invalid date '{}': {}", timestamp_str, e)))?;
            Ok(date.and_hms_opt(0, 0, 0).unwrap().and_utc())
        }
        Interval::Hourly | Interval::Minute => {
            // Hourly/Minute: "2024-01-01 09:00:00" or "2024-01-01T09:00:00"
            if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(timestamp_str, "%Y-%m-%d %H:%M:%S") {
                Ok(dt.and_utc())
            } else if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(timestamp_str, "%Y-%m-%dT%H:%M:%S") {
                Ok(dt.and_utc())
            } else {
                Err(AppError::Parse(format!("Invalid datetime '{}': expected YYYY-MM-DD HH:MM:SS or YYYY-MM-DDTHH:MM:SS", timestamp_str)))
            }
        }
    }
}

/// Get latest timestamp from SQLite for ticker/interval
async fn get_latest_sqlite_timestamp(
    database: &SQLiteDatabaseStore,
    ticker: &str,
    interval: Interval,
) -> Result<Option<chrono::DateTime<chrono::Utc>>, AppError> {
    let interval_str = match interval {
        Interval::Daily => "1D",
        Interval::Hourly => "1H",
        Interval::Minute => "1m",
    };

    database
        .get_latest_timestamp(ticker, interval_str)
        .await
        .map_err(|e| AppError::Database(format!("Failed to get latest timestamp: {}", e)))
}

/// Migrate a single CSV file to SQLite
async fn migrate_single_csv_file(
    database: &SQLiteDatabaseStore,
    csv_path: &Path,
) -> Result<usize, AppError> {
    let ticker = csv_path.parent()
        .and_then(|p| p.file_name())
        .and_then(|n| n.to_str())
        .ok_or_else(|| AppError::InvalidInput(format!("Cannot extract ticker from path: {:?}", csv_path)))?;

    let interval = SQLiteUpdater::interval_from_filename(csv_path)?;

    // Read CSV file
    let csv_content = tokio::fs::read_to_string(csv_path).await
        .map_err(|e| AppError::Io(format!("Failed to read CSV file {:?}: {}", csv_path, e)))?;

    let mut rdr = csv::Reader::from_reader(csv_content.as_bytes());
    let headers = rdr.headers()?;

    // Check if this is an enhanced CSV with technical indicators
    let is_enhanced = headers.len() >= 20; // 20 columns for enhanced CSV

    let mut records_processed = 0;
    let mut batch = Vec::new();

    for result in rdr.records() {
        let record = result.map_err(|e| AppError::Io(format!("CSV parsing error: {}", e)))?;
        records_processed += 1;

        match SQLiteUpdater::parse_csv_record(&record, ticker, interval, is_enhanced) {
            Ok(data) => {
                batch.push(data);

                // Process batch when it reaches a reasonable size
                if batch.len() >= 1000 {
                    let inserted = database.upsert_market_data(&batch).await
                        .map_err(|e| AppError::Database(e.to_string()))?;
                    debug!("Inserted batch of {} records", inserted);
                    batch.clear();
                }
            }
            Err(e) => {
                warn!("Skipping malformed record in {}: {}", csv_path.display(), e);
                continue;
            }
        }
    }

    // Insert remaining records in the last batch
    if !batch.is_empty() {
        let inserted = database.upsert_market_data(&batch).await
            .map_err(|e| AppError::Database(e.to_string()))?;
        debug!("Inserted final batch of {} records", inserted);
    }

    info!("Migrated {}: {} records processed", csv_path.display(), records_processed);
    Ok(records_processed)
}

/// Auto-reconstruct database from CSV if missing or empty
pub async fn ensure_database_exists(
    database_path: &Path,
    csv_directories: &[PathBuf],
) -> Result<bool, AppError> {
    if is_database_ready(database_path).await? {
        info!("Database exists and contains data");
        // Run smart sync check to ensure it's fully up to date
        let sync_result = smart_sync_check(database_path, csv_directories).await?;
        if sync_result.missing_files > 0 || sync_result.outdated_files > 0 {
            info!("Smart sync updated {} files with {} records",
                  sync_result.missing_files + sync_result.outdated_files,
                  sync_result.migrated_records);
        }
        return Ok(false); // No full reconstruction needed
    }

    warn!("Database missing or empty, reconstructing from CSV files...");

    let result = migrate_csv_to_sqlite(
        database_path.to_path_buf(),
        csv_directories.to_vec(),
    ).await?;

    info!("Database reconstruction complete: {} records", result.total_records_migrated);
    Ok(true) // Reconstruction was performed
}

/// Smart sync result
#[derive(Debug, Clone)]
pub struct SmartSyncResult {
    pub total_csv_files: usize,
    pub missing_files: usize,
    pub outdated_files: usize,
    pub migrated_records: usize,
    pub errors: Vec<String>,
}

/// Ticker sync result
#[derive(Debug, Clone)]
struct TickerSyncResult {
    pub total_files: usize,
    pub missing_files: usize,
    pub outdated_files: usize,
    pub missing_files_list: Vec<PathBuf>,
}

/// CSV sync status
#[derive(Debug, Clone, PartialEq)]
enum CsvSyncStatus {
    Missing,    // No data in SQLite
    Outdated,   // SQLite data is older than CSV
    InSync,     // SQLite and CSV are in sync
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;
    use std::fs::File;
    use std::io::Write;

    #[tokio::test]
    async fn test_migration_creation() {
        let temp_dir = TempDir::new().unwrap();
        let db_path = temp_dir.path().join("test.db");

        let migration = CsvToSqliteMigration::new(db_path).await.unwrap();
        assert!(database_exists(&migration.database.database_path).await);
    }

    #[tokio::test]
    async fn test_database_ready_check() {
        let temp_dir = TempDir::new().unwrap();
        let db_path = temp_dir.path().join("test.db");

        // Test non-existent database
        assert!(!is_database_ready(&db_path).await.unwrap());

        // Create empty database
        SQLiteDatabaseStore::new(db_path.clone()).await.unwrap();
        assert!(!is_database_ready(&db_path).await.unwrap());

        // Add some data
        let data = vec![StockData {
            ticker: "TEST".to_string(),
            time: chrono::Utc::now(),
            open: 100.0,
            high: 105.0,
            low: 95.0,
            close: 102.0,
            volume: 1000000,
            ma10: Some(101.0),
            ma20: Some(100.5),
            ma50: None,
            ma100: None,
            ma200: None,
            ma10_score: Some(1.0),
            ma20_score: Some(1.5),
            ma50_score: None,
            ma100_score: None,
            ma200_score: None,
            close_changed: Some(2.0),
            volume_changed: Some(5.0),
            total_money_changed: Some(2000000.0),
        }];

        let db = SQLiteDatabaseStore::new(db_path.clone()).await.unwrap();
        db.upsert_market_data(&data).await.unwrap();

        assert!(is_database_ready(&db_path).await.unwrap());
    }
}