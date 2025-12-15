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

/// Auto-reconstruct database from CSV if missing or empty
pub async fn ensure_database_exists(
    database_path: &Path,
    csv_directories: &[PathBuf],
) -> Result<bool, AppError> {
    if is_database_ready(database_path).await? {
        info!("Database exists and contains data");
        return Ok(false); // No reconstruction needed
    }

    warn!("Database missing or empty, reconstructing from CSV files...");

    let result = migrate_csv_to_sqlite(
        database_path.to_path_buf(),
        csv_directories.to_vec(),
    ).await?;

    info!("Database reconstruction complete: {} records", result.total_records_migrated);
    Ok(true) // Reconstruction was performed
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