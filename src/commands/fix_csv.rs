//! Fix CSV Command
//!
//! Removes the last N rows from CSV files with safety features.
//! This command can remove rows from both market data (VN) and crypto data
//! with dry-run mode, backup support, and comprehensive error handling.

use crate::error::Error;
use crate::models::Interval;
use crate::utils::{get_market_data_dir, get_crypto_data_dir};
use chrono::Utc;
use std::fs;
use std::path::{Path, PathBuf};
use tracing::{info, warn};

/// Statistics for fix CSV operation
#[derive(Debug)]
pub struct FixCsvStats {
    pub tickers_processed: usize,
    pub files_modified: usize,
    pub rows_removed: usize,
    pub files_backed_up: usize,
    pub files_skipped: usize,
    pub errors: Vec<String>,
}

impl FixCsvStats {
    pub fn new() -> Self {
        Self {
            tickers_processed: 0,
            files_modified: 0,
            rows_removed: 0,
            files_backed_up: 0,
            files_skipped: 0,
            errors: Vec::new(),
        }
    }

    pub fn print_summary(&self, dry_run: bool) {
        let mode_str = if dry_run { " (Dry Run)" } else { "" };
        println!("\n📊 Fix CSV Summary{}:", mode_str);
        println!("  ✅ Tickers processed: {}", self.tickers_processed);
        if dry_run {
            println!("  ⏭️  Files modified: {} (dry run)", self.files_modified);
            println!("  ⏭️  Rows removed: {} (dry run)", self.rows_removed);
            println!("  ⏭️  Files backed up: {} (dry run)", self.files_backed_up);
        } else {
            println!("  ✅ Files modified: {}", self.files_modified);
            println!("  ✅ Rows removed: {}", self.rows_removed);
            println!("  ✅ Files backed up: {}", self.files_backed_up);
        }
        println!("  ⏭️  Files skipped: {}", self.files_skipped);

        if !self.errors.is_empty() {
            println!("  ⚠️  Errors encountered: {}", self.errors.len());
            for error in &self.errors[0..std::cmp::min(5, self.errors.len())] {
                println!("    ❌ {}", error);
            }
            if self.errors.len() > 5 {
                println!("    ... and {} more errors", self.errors.len() - 5);
            }
        }

        if dry_run && (self.files_modified > 0 || self.rows_removed > 0) {
            println!("\n💡 To execute changes, add --execute flag");
        }
    }
}

/// Run the fix-csv command
pub fn run(
    mode: String,
    intervals_arg: String,
    rows: usize,
    tickers_arg: Option<String>,
    verbose: bool,
    execute: bool,
    backup: bool,
) -> Result<(), Error> {
    let start_time = std::time::Instant::now();

    // Validate parameters
    if rows == 0 {
        return Err(Error::InvalidInput(
            "Number of rows must be greater than 0".to_string(),
        ));
    }

    // Determine which data directory to use
    let (data_dir, data_type) = match mode.as_str() {
        "crypto" => (get_crypto_data_dir(), "crypto"),
        "vn" | "market" => (get_market_data_dir(), "vn"),
        other => {
            return Err(Error::InvalidInput(format!(
                "Invalid mode '{}'. Must be 'vn' or 'crypto'",
                other
            )));
        }
    };

    if verbose {
        println!("🔧 Starting CSV fix process...");
        println!("  Mode: {}", data_type);
        println!("  Directory: {:?}", data_dir);
        println!("  Intervals: {}", intervals_arg);
        println!("  Rows to remove: {}", rows);
        println!("  Execute: {}", execute);
        println!("  Backup: {}", backup);
        if let Some(ref tickers) = tickers_arg {
            println!("  Tickers: {}", tickers);
        } else {
            println!("  Tickers: ALL");
        }
        if !execute {
            println!("  ⚠️  Running in DRY RUN mode - no files will be modified");
        }
        println!();
    }

    // Parse intervals
    let intervals = Interval::parse_intervals(&intervals_arg)
        .map_err(|e| Error::InvalidInput(format!("Invalid intervals: {}", e)))?;
    if intervals.is_empty() {
        return Err(Error::InvalidInput("No valid intervals specified".to_string()));
    }

    // Parse target tickers
    let target_tickers = parse_tickers(tickers_arg)?;

    // Check data directory exists
    if !data_dir.exists() {
        return Err(Error::Io(format!(
            "{} data directory not found: {:?}",
            data_type, data_dir
        )));
    }

    // Create backup directory if needed
    let backup_base_dir = if backup && execute {
        let backup_dir = PathBuf::from("backups").join(data_type);
        fs::create_dir_all(&backup_dir)
            .map_err(|e| Error::Io(format!("Failed to create backup directory: {}", e)))?;
        Some(backup_dir)
    } else {
        None
    };

    // Process all ticker directories
    let mut stats = FixCsvStats::new();
    let entries = std::fs::read_dir(&data_dir)
        .map_err(|e| Error::Io(format!("Failed to read {} directory: {}", data_type, e)))?;

    for entry in entries {
        let entry = entry
            .map_err(|e| Error::Io(format!("Failed to read directory entry: {}", e)))?;
        let ticker_dir = entry.path();

        if !ticker_dir.is_dir() {
            continue;
        }

        let ticker = ticker_dir
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| Error::Io("Invalid ticker directory name".to_string()))?
            .to_string();

        // Check if this ticker should be processed
        if !should_process_ticker(&ticker, &target_tickers) {
            continue;
        }

        stats.tickers_processed += 1;

        // Process each interval for this ticker
        for interval in &intervals {
            if let Err(e) = fix_single_csv(
                &ticker,
                *interval,
                &data_dir,
                rows,
                verbose,
                execute,
                &backup_base_dir,
                &mut stats,
            ) {
                let error_msg = format!("{} {}: {}", ticker, interval.to_filename(), e);
                warn!("{}", error_msg);
                stats.errors.push(error_msg);
            }
        }
    }

    // Print summary
    stats.print_summary(!execute);

    let duration = start_time.elapsed();
    println!("  ⏱️  Total time: {:.2}s", duration.as_secs_f64());

    if stats.errors.is_empty() {
        info!("CSV fix completed successfully");
        Ok(())
    } else {
        warn!("CSV fix completed with {} errors", stats.errors.len());
        Ok(())
    }
}

/// Fix a single CSV file by removing last N rows
fn fix_single_csv(
    ticker: &str,
    interval: Interval,
    data_dir: &Path,
    rows_to_remove: usize,
    verbose: bool,
    execute: bool,
    backup_base_dir: &Option<PathBuf>,
    stats: &mut FixCsvStats,
) -> Result<(), Error> {
    let csv_path = data_dir.join(ticker).join(interval.to_filename());

    // Check if file exists
    if !csv_path.exists() {
        if verbose {
            println!("⏭️  {} {} - file not found, skipping", ticker, interval.to_filename());
        }
        stats.files_skipped += 1;
        return Ok(());
    }

    // Read CSV file
    let (headers, records) = read_csv_with_headers(&csv_path, ticker)?;

    if records.is_empty() {
        if verbose {
            println!("⏭️  {} {} - empty file, skipping", ticker, interval.to_filename());
        }
        stats.files_skipped += 1;
        return Ok(());
    }

    let total_rows = records.len();

    // Validate we can remove the requested number of rows
    if rows_to_remove > total_rows {
        let error_msg = format!(
            "Cannot remove {} rows from file with only {} rows",
            rows_to_remove, total_rows
        );
        if verbose {
            println!("⏭️  {} {} - {}", ticker, interval.to_filename(), error_msg);
        }
        stats.errors.push(format!("{} {}: {}", ticker, interval.to_filename(), error_msg));
        stats.files_skipped += 1;
        return Ok(());
    }

    let new_row_count = total_rows - rows_to_remove;
    let rows_to_remove_actual = rows_to_remove;

    // Create backup if requested
    if execute && backup_base_dir.is_some() {
        create_backup(&csv_path, ticker, interval, backup_base_dir.as_ref().unwrap())?;
        stats.files_backed_up += 1;
        if verbose {
            println!("  💾 {} {} - backup created", ticker, interval.to_filename());
        }
    }

    if execute {
        // Actually modify the file
        write_csv_with_headers(&csv_path, &headers, &records[..new_row_count])?;
        stats.files_modified += 1;
        stats.rows_removed += rows_to_remove_actual;
        if verbose {
            println!(
                "✅ {} {} - removed {} rows ({} → {} records)",
                ticker, interval.to_filename(), rows_to_remove_actual, total_rows, new_row_count
            );
        }
    } else {
        // Dry run - just report what would be done
        stats.files_modified += 1;
        stats.rows_removed += rows_to_remove_actual;
        if verbose {
            println!(
                "⏭️  {} {} - dry run mode, would remove {} rows ({} → {} records)",
                ticker, interval.to_filename(), rows_to_remove_actual, total_rows, new_row_count
            );
        }
    }

    Ok(())
}

/// Read CSV file with headers
fn read_csv_with_headers(csv_path: &Path, _ticker: &str) -> Result<(Vec<String>, Vec<Vec<String>>), Error> {
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true) // Allow varying number of fields per record
        .from_path(csv_path)
        .map_err(|e| Error::Io(format!("Failed to read {}: {}", csv_path.display(), e)))?;

    // Read headers
    let headers = reader
        .headers()
        .map_err(|e| Error::Io(format!("Failed to read headers from {}: {}", csv_path.display(), e)))?
        .iter()
        .map(|s| s.to_string())
        .collect();

    let mut records = Vec::new();

    for result in reader.records() {
        let record = result
            .map_err(|e| Error::Io(format!("CSV parse error in {}: {}", csv_path.display(), e)))?;

        // Convert record to Vec<String>
        let record_vec: Vec<String> = record.iter().map(|s| s.to_string()).collect();
        records.push(record_vec);
    }

    Ok((headers, records))
}

/// Write CSV file with headers
fn write_csv_with_headers(
    csv_path: &Path,
    headers: &[String],
    records: &[Vec<String>],
) -> Result<(), Error> {
    // Use file locking for safety (following rebuild_csv pattern)
    use fs2::FileExt;

    let file = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(csv_path)
        .map_err(|e| Error::Io(format!("Failed to open {} for writing: {}", csv_path.display(), e)))?;

    // Acquire exclusive lock
    file.lock_exclusive()
        .map_err(|e| Error::Io(format!("Failed to acquire lock on {}: {}", csv_path.display(), e)))?;

    let mut wtr = csv::Writer::from_writer(file);

    // Write headers
    wtr.write_record(headers)
        .map_err(|e| Error::Io(format!("Failed to write headers to {}: {}", csv_path.display(), e)))?;

    // Write records
    for record in records {
        wtr.write_record(record)
            .map_err(|e| Error::Io(format!("Failed to write record to {}: {}", csv_path.display(), e)))?;
    }

    wtr.flush()
        .map_err(|e| Error::Io(format!("Failed to flush {}: {}", csv_path.display(), e)))?;

    // Lock is released when file goes out of scope
    Ok(())
}

/// Create backup of a CSV file
fn create_backup(
    csv_path: &Path,
    ticker: &str,
    interval: Interval,
    backup_base_dir: &Path,
) -> Result<(), Error> {
    // Create backup directory for this ticker
    let ticker_backup_dir = backup_base_dir.join(ticker);
    fs::create_dir_all(&ticker_backup_dir)
        .map_err(|e| Error::Io(format!("Failed to create backup directory: {}", e)))?;

    // Generate timestamp for backup filename
    let timestamp = Utc::now().format("%Y%m%d_%H%M%S");
    let backup_filename = format!("{}.csv_{}", interval.to_filename(), timestamp);
    let backup_path = ticker_backup_dir.join(backup_filename);

    // Copy the file
    fs::copy(csv_path, &backup_path)
        .map_err(|e| Error::Io(format!("Failed to create backup {}: {}", backup_path.display(), e)))?;

    Ok(())
}

/// Parse tickers from command line argument
fn parse_tickers(tickers_arg: Option<String>) -> Result<Option<Vec<String>>, Error> {
    match tickers_arg {
        None => Ok(None), // Process all tickers
        Some(tickers_str) => {
            let tickers: Vec<String> = tickers_str
                .split(',')
                .map(|s| s.trim().to_uppercase())
                .filter(|s| !s.is_empty())
                .collect();

            if tickers.is_empty() {
                return Err(Error::InvalidInput("No valid tickers specified".to_string()));
            }

            Ok(Some(tickers))
        }
    }
}

/// Check if a ticker should be processed based on target tickers filter
fn should_process_ticker(ticker: &str, target_tickers: &Option<Vec<String>>) -> bool {
    match target_tickers {
        None => true, // Process all tickers
        Some(tickers) => tickers.contains(&ticker.to_uppercase()),
    }
}