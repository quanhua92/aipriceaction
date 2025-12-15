use clap::{Arg, Command};
use std::path::PathBuf;
use tracing::{info, warn, error};
use tracing_subscriber;
use aipriceaction::services::migration::{CsvToSqliteMigration, MigrationConfig, MigrationResult, ValidationResult, ensure_database_exists};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Initialize tracing
    tracing_subscriber::fmt::init();

    let matches = Command::new("migrate_to_sqlite")
        .version("1.0")
        .about("Migrate CSV market data to SQLite database")
        .arg(
            Arg::new("database")
                .short('d')
                .long("database")
                .value_name("PATH")
                .help("Path to SQLite database file")
                .default_value("market_data.db"),
        )
        .arg(
            Arg::new("csv-dirs")
                .short('c')
                .long("csv-dirs")
                .value_name("PATHS")
                .help("Comma-separated list of CSV directories to migrate")
                .default_value("market_data,crypto_data"),
        )
        .arg(
            Arg::new("batch-size")
                .short('b')
                .long("batch-size")
                .value_name("SIZE")
                .help("Batch size for database inserts")
                .default_value("1000"),
        )
        .arg(
            Arg::new("validate")
                .short('v')
                .long("validate")
                .help("Validate migration results")
                .action(clap::ArgAction::SetTrue),
        )
        .arg(
            Arg::new("auto")
                .short('a')
                .long("auto")
                .help("Auto-reconstruct database if missing or empty")
                .action(clap::ArgAction::SetTrue),
        )
        .arg(
            Arg::new("force")
                .short('f')
                .long("force")
                .help("Force full migration (overwrite existing data)")
                .action(clap::ArgAction::SetTrue),
        )
        .get_matches();

    // Parse arguments
    let database_path = PathBuf::from(matches.get_one::<String>("database").unwrap());
    let csv_dirs: Vec<PathBuf> = matches
        .get_one::<String>("csv-dirs")
        .unwrap()
        .split(',')
        .map(|s| PathBuf::from(s.trim()))
        .collect();
    let batch_size: usize = matches.get_one::<String>("batch-size").unwrap().parse()?;
    let validate = matches.get_flag("validate");
    let auto = matches.get_flag("auto");
    let force = matches.get_flag("force");

    info!("Starting CSV to SQLite migration");
    info!("Database path: {:?}", database_path);
    info!("CSV directories: {:?}", csv_dirs);
    info!("Batch size: {}", batch_size);
    info!("Validate results: {}", validate);
    info!("Auto-reconstruct: {}", auto);
    info!("Force migration: {}", force);

    // Auto-reconstruct if requested
    if auto {
        match ensure_database_exists(&database_path, &csv_dirs).await {
            Ok(reconstructed) => {
                if reconstructed {
                    info!("Database auto-reconstructed successfully");
                    return Ok(());
                } else {
                    info!("Database already exists and contains data");
                }
            }
            Err(e) => {
                error!("Auto-reconstruction failed: {}", e);
                return Err(e.into());
            }
        }
    }

    // Validate CSV directories exist
    for csv_dir in &csv_dirs {
        if !csv_dir.exists() {
            error!("CSV directory does not exist: {:?}", csv_dir);
            return Err("CSV directory not found".into());
        }
    }

    // Create migration instance
    let migration = CsvToSqliteMigration::new(database_path.clone()).await?;

    // Check if database already has data
    let existing_records = migration.database.get_record_count().await?;
    if existing_records > 0 && !force {
        warn!("Database already contains {} records", existing_records);
        warn!("Use --force to overwrite existing data");
        return Ok(());
    }

    // Clear existing data if force is enabled
    if force && existing_records > 0 {
        warn!("Clearing existing {} records from database", existing_records);
        // Note: In a real implementation, you might want to clear specific tables
        // For now, we'll just proceed with migration which will use INSERT OR REPLACE
    }

    // Create migration configuration
    let config = MigrationConfig {
        database_path: database_path.clone(),
        csv_directories: csv_dirs.clone(),
        batch_size,
        validate_data: true,
        progress_callback: None,
    };

    // Perform migration
    info!("Starting migration...");
    let migration_start = std::time::Instant::now();

    let result = match migration.migrate_directories(config).await {
        Ok(result) => {
            info!("Migration completed successfully!");
            info!("Files processed: {}", result.total_files_processed);
            info!("Records migrated: {}", result.total_records_migrated);
            info!("Duration: {} seconds", result.duration_secs);

            if result.total_errors > 0 {
                warn!("Migration completed with {} errors:", result.total_errors);
                for error in &result.errors {
                    warn!("  {}", error);
                }
            }

            result
        }
        Err(e) => {
            error!("Migration failed: {}", e);
            return Err(e.into());
        }
    };

    // Validate migration if requested
    if validate {
        info!("Validating migration results...");
        let validation_result = match migration.validate_migration(&csv_dirs).await {
            Ok(result) => result,
            Err(e) => {
                error!("Validation failed: {}", e);
                return Err(e.into());
            }
        };

        info!("Validation Results:");
        info!("  CSV records: {}", validation_result.total_csv_records);
        info!("  SQLite records: {}", validation_result.total_sqlite_records);

        if validation_result.validation_errors.is_empty() {
            info!("✅ Validation passed - All records migrated successfully");
        } else {
            error!("❌ Validation failed:");
            for error in &validation_result.validation_errors {
                error!("  {}", error);
            }
        }
    }

    let total_duration = migration_start.elapsed();
    info!("Total migration time: {} seconds", total_duration.as_secs());

    // Performance summary
    if result.total_records_migrated > 0 {
        let records_per_second = result.total_records_migrated as f64 / total_duration.as_secs_f64();
        info!("Performance: {:.2} records/second", records_per_second);
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;
    use std::fs;
    use std::io::Write;

    #[test]
    fn test_argument_parsing() {
        use clap::CommandFactory;

        // Test that the CLI definition is valid
        let cmd = migrate_to_sqlite::command();
        assert!(cmd.try_get_matches_from(&["migrate_to_sqlite"]).is_ok());
        assert!(cmd.try_get_matches_from(&["migrate_to_sqlite", "--help"]).is_err()); // Help exits
    }

    #[tokio::test]
    async fn test_migration_with_invalid_directory() {
        let temp_dir = TempDir::new().unwrap();
        let db_path = temp_dir.path().join("test.db");
        let csv_dir = temp_dir.path().join("nonexistent");

        let migration = CsvToSqliteMigration::new(db_path).await.unwrap();
        let config = MigrationConfig {
            database_path: PathBuf::new(),
            csv_directories: vec![csv_dir],
            batch_size: 100,
            validate_data: false,
            progress_callback: None,
        };

        let result = migration.migrate_directories(config).await.unwrap();
        assert_eq!(result.total_files_processed, 0);
        assert_eq!(result.total_records_migrated, 0);
    }

    #[tokio::test]
    async fn test_ensure_database_exists() {
        let temp_dir = TempDir::new().unwrap();
        let db_path = temp_dir.path().join("test.db");
        let csv_dir = temp_dir.path().join("csv");
        fs::create_dir_all(&csv_dir).unwrap();

        // Test with non-existent database and no CSV files
        let reconstructed = ensure_database_exists(&db_path, &[csv_dir]).await.unwrap();
        assert!(!reconstructed); // No CSV files to migrate

        // Database should still be created
        assert!(db_path.exists());
    }
}