//! Rebuild CSV Command
//!
//! Re-calculates technical indicators for existing CSV files.
//! This command reads existing OHLCV data and re-applies the CSV enhancement
//! process to ensure all files have the latest indicators, including value_changed.

use crate::error::Error;
use crate::models::Interval;
use crate::services::{csv_enhancer::{enhance_data, save_enhanced_csv}, OhlcvData};
use crate::utils::get_market_data_dir;
use chrono::DateTime;
use std::collections::HashMap;
use std::path::Path;
use tracing::{info, warn};

/// Statistics for rebuild operation
#[derive(Debug)]
pub struct RebuildStats {
    pub tickers_processed: usize,
    pub files_rebuilt: usize,
    pub records_enhanced: usize,
    pub errors: Vec<String>,
}

impl RebuildStats {
    pub fn new() -> Self {
        Self {
            tickers_processed: 0,
            files_rebuilt: 0,
            records_enhanced: 0,
            errors: Vec::new(),
        }
    }

    pub fn print_summary(&self) {
        println!("\n📊 Rebuild Summary:");
        println!("  ✅ Tickers processed: {}", self.tickers_processed);
        println!("  ✅ Files rebuilt: {}", self.files_rebuilt);
        println!("  ✅ Records enhanced: {}", self.records_enhanced);

        if !self.errors.is_empty() {
            println!("  ⚠️  Errors encountered: {}", self.errors.len());
            for error in &self.errors[0..std::cmp::min(5, self.errors.len())] {
                println!("    ❌ {}", error);
            }
            if self.errors.len() > 5 {
                println!("    ... and {} more errors", self.errors.len() - 5);
            }
        }
    }
}

/// Run the rebuild-csv command
pub fn run(
    intervals_arg: String,
    tickers_arg: Option<String>,
    verbose: bool,
) -> Result<(), Error> {
    let start_time = std::time::Instant::now();

    if verbose {
        println!("🔧 Starting CSV rebuild process...");
        println!("  Intervals: {}", intervals_arg);
        if let Some(ref tickers) = tickers_arg {
            println!("  Tickers: {}", tickers);
        } else {
            println!("  Tickers: ALL");
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

    // Get market data directory
    let market_data_dir = get_market_data_dir();
    if !market_data_dir.exists() {
        return Err(Error::Io(format!(
            "Market data directory not found: {:?}",
            market_data_dir
        )));
    }

    // Process all ticker directories
    let mut stats = RebuildStats::new();
    let entries = std::fs::read_dir(&market_data_dir)
        .map_err(|e| Error::Io(format!("Failed to read market_data directory: {}", e)))?;

    for entry in entries {
        let entry = entry.map_err(|e| Error::Io(format!("Failed to read directory entry: {}", e)))?;
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
            if let Err(e) = rebuild_single_csv(&ticker, *interval, &market_data_dir, verbose, &mut stats) {
                let error_msg = format!("{} {}: {}", ticker, interval.to_filename(), e);
                warn!("{}", error_msg);
                stats.errors.push(error_msg);
            }
        }
    }

    // Print summary
    stats.print_summary();

    let duration = start_time.elapsed();
    println!("  ⏱️  Total time: {:.2}s", duration.as_secs_f64());

    if stats.errors.is_empty() {
        info!("CSV rebuild completed successfully");
        Ok(())
    } else {
        warn!("CSV rebuild completed with {} errors", stats.errors.len());
        Ok(())
    }
}

/// Rebuild a single CSV file
fn rebuild_single_csv(
    ticker: &str,
    interval: Interval,
    market_data_dir: &Path,
    verbose: bool,
    stats: &mut RebuildStats,
) -> Result<(), Error> {
    let csv_path = market_data_dir.join(ticker).join(interval.to_filename());

    // Check if file exists
    if !csv_path.exists() {
        if verbose {
            println!("⏭️  {} {} - file not found, skipping", ticker, interval.to_filename());
        }
        return Ok(());
    }

    // Read existing CSV data
    let ticker_data = read_csv_for_rebuild(&csv_path, ticker)?;
    if ticker_data.is_empty() {
        if verbose {
            println!("⏭️  {} {} - no data found, skipping", ticker, interval.to_filename());
        }
        return Ok(());
    }

    // Enhance the data (this will calculate all indicators including value_changed)
    let mut data_map = HashMap::new();
    data_map.insert(ticker.to_string(), ticker_data);
    let enhanced_data = enhance_data(data_map);

    // Get enhanced data for this ticker
    let enhanced_ticker_data = enhanced_data.get(ticker).ok_or_else(|| {
        Error::Io(format!("Failed to enhance data for {}", ticker))
    })?;

    // Save enhanced data back to CSV
    // Use cutoff date to only write recent data (2 days ago)
    let cutoff_date = chrono::Utc::now() - chrono::Duration::days(2);
    save_enhanced_csv(ticker, enhanced_ticker_data, interval, cutoff_date, true)?;

    stats.files_rebuilt += 1;
    stats.records_enhanced += enhanced_ticker_data.len();

    if verbose {
        println!("✅ {} {} - {} records enhanced", ticker, interval.to_filename(), enhanced_ticker_data.len());
    }

    Ok(())
}

/// Read existing CSV data and convert to OhlcvData
fn read_csv_for_rebuild(csv_path: &Path, ticker: &str) -> Result<Vec<OhlcvData>, Error> {
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true) // Allow varying number of fields per record
        .from_path(csv_path)
        .map_err(|e| Error::Io(format!("Failed to read {}: {}", csv_path.display(), e)))?;

    let mut data = Vec::new();

    for result in reader.records() {
        let record = result
            .map_err(|e| Error::Io(format!("CSV parse error in {}: {}", csv_path.display(), e)))?;

        // Read basic OHLCV data (first 7 columns)
        if record.len() < 7 {
            continue;
        }

        let time_str = record
            .get(1)
            .ok_or_else(|| Error::Io("Missing time column".to_string()))?;
        let open: f64 = record
            .get(2)
            .ok_or_else(|| Error::Io("Missing open column".to_string()))?
            .parse()
            .map_err(|e| Error::Io(format!("Invalid open value: {}", e)))?;
        let high: f64 = record
            .get(3)
            .ok_or_else(|| Error::Io("Missing high column".to_string()))?
            .parse()
            .map_err(|e| Error::Io(format!("Invalid high value: {}", e)))?;
        let low: f64 = record
            .get(4)
            .ok_or_else(|| Error::Io("Missing low column".to_string()))?
            .parse()
            .map_err(|e| Error::Io(format!("Invalid low value: {}", e)))?;
        let close: f64 = record
            .get(5)
            .ok_or_else(|| Error::Io("Missing close column".to_string()))?
            .parse()
            .map_err(|e| Error::Io(format!("Invalid close value: {}", e)))?;
        let volume: u64 = record
            .get(6)
            .ok_or_else(|| Error::Io("Missing volume column".to_string()))?
            .parse()
            .map_err(|e| Error::Io(format!("Invalid volume value: {}", e)))?;

        // Parse datetime
        let time = parse_time(time_str)?;

        let ohlcv = OhlcvData {
            time,
            open,
            high,
            low,
            close,
            volume,
            symbol: Some(ticker.to_string()),
        };
        data.push(ohlcv);
    }

    // Sort by time (oldest first)
    data.sort_by_key(|d| d.time);

    Ok(data)
}

/// Parse time from string (supports multiple formats)
fn parse_time(time_str: &str) -> Result<DateTime<chrono::Utc>, Error> {
    // Try RFC3339 first
    if let Ok(dt) = DateTime::parse_from_rfc3339(time_str) {
        return Ok(dt.with_timezone(&chrono::Utc));
    }

    // Try datetime format "YYYY-MM-DD HH:MM:SS"
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(time_str, "%Y-%m-%d %H:%M:%S") {
        return Ok(dt.and_utc());
    }

    // Try date only format "YYYY-MM-DD"
    let date = chrono::NaiveDate::parse_from_str(time_str, "%Y-%m-%d")
        .map_err(|e| Error::Parse(format!("Invalid date format: {}", e)))?;

    Ok(date
        .and_hms_opt(0, 0, 0)
        .ok_or_else(|| Error::Parse("Failed to set time".to_string()))?
        .and_utc())
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