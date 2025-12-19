//! Binance Vision Data Utilities
//!
//! This module provides read-only utilities for accessing Binance Vision data
//! stored in ZIP files. All operations are performed in-memory without extracting
//! files to disk.
//!
//! Data structure:
//! ```
//! spot/daily/klines/{TICKER}/{1d|1h|1m}/{TICKER}-{interval}-{date}.zip
//! ```

use chrono::{DateTime, Utc};
use std::path::Path;
use std::fs;
use std::collections::HashSet;
use zip::read::ZipArchive;
use std::io::Read;

use crate::error::{AppError, Result};
use crate::models::Ohlcv;

/// Read all Binance ticker data for a specific interval
///
/// # Arguments
/// * `ticker` - Cryptocurrency ticker (e.g., "BTCUSDT")
/// * `interval` - Time interval ("1D", "1H", or "1m")
/// * `spot_dir` - Path to the spot directory
///
/// # Returns
/// Vector of OHLCV data points
pub fn read_binance_ticker_data(
    ticker: &str,
    interval: &str,
    spot_dir: &Path,
) -> Result<Vec<Ohlcv>> {
    let binance_interval = map_project_interval_to_binance(interval)?;
    validate_ticker(ticker)?;

    let ticker_dir = spot_dir
        .join("daily/klines")
        .join(ticker)
        .join(binance_interval);

    if !ticker_dir.exists() {
        return Err(AppError::NotFound(format!(
            "Ticker directory not found: {}",
            ticker_dir.display()
        )));
    }

    // Scan for ZIP files
    let mut all_data = Vec::new();
    let mut seen_timestamps = HashSet::new();
    let entries = fs::read_dir(&ticker_dir)?;

    for entry in entries {
        let entry = entry?;
        let path = entry.path();

        if path.extension().and_then(|s| s.to_str()) == Some("zip") {
            match extract_and_parse_zip(&path, ticker) {
                Ok(mut data) => {
                    // Add only records with unique timestamps
                    for record in data.drain(..) {
                        if !seen_timestamps.contains(&record.time) {
                            seen_timestamps.insert(record.time);
                            all_data.push(record);
                        }
                    }
                }
                Err(e) => {
                    eprintln!("Warning: Failed to process {}: {}", path.display(), e);
                    // Continue with other files
                }
            }
        }
    }

    // Sort by timestamp
    all_data.sort_by_key(|d| d.time);

    Ok(all_data)
}

/// Read Binance ticker data with limit optimization
///
/// Only processes enough ZIP files to meet the specified limit,
/// reading most recent files first for efficiency.
///
/// # Arguments
/// * `ticker` - Cryptocurrency ticker (e.g., "BTCUSDT")
/// * `interval` - Time interval ("1D", "1H", or "1m")
/// * `spot_dir` - Path to the spot directory
/// * `limit` - Maximum number of data points to read
///
/// # Returns
/// Vector of OHLCV data points
pub fn read_binance_ticker_data_limited(
    ticker: &str,
    interval: &str,
    spot_dir: &Path,
    limit: usize,
) -> Result<Vec<Ohlcv>> {
    let binance_interval = map_project_interval_to_binance(interval)?;
    validate_ticker(ticker)?;

    let ticker_dir = spot_dir
        .join("daily/klines")
        .join(ticker)
        .join(binance_interval);

    if !ticker_dir.exists() {
        return Err(AppError::NotFound(format!(
            "Ticker directory not found: {}",
            ticker_dir.display()
        )));
    }

    // Collect ZIP files and sort by filename (newest first by reversing)
    let mut zip_files: Vec<_> = fs::read_dir(&ticker_dir)?
        .filter_map(|entry| {
            let entry = entry.ok()?;
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) == Some("zip") {
                Some(path)
            } else {
                None
            }
        })
        .collect();

    // Sort by filename and reverse to get newest files first
    zip_files.sort();
    zip_files.reverse();

    let mut all_data = Vec::new();
    let mut seen_timestamps = HashSet::new();

    // Process ZIP files until we have enough data
    for zip_path in zip_files {
        // Check if we already have enough data before processing more files
        if all_data.len() >= limit {
            break;
        }

        match extract_and_parse_zip(&zip_path, ticker) {
            Ok(mut data) => {
                // Add only records with unique timestamps
                for record in data.drain(..) {
                    if !seen_timestamps.contains(&record.time) {
                        seen_timestamps.insert(record.time);
                        all_data.push(record);

                        // Check if we've reached the limit after adding this record
                        if all_data.len() >= limit {
                            break;
                        }
                    }
                }
            }
            Err(e) => {
                eprintln!("Warning: Failed to process {}: {}", zip_path.display(), e);
            }
        }
    }

    // Sort by timestamp
    all_data.sort_by_key(|d| d.time);

    // Apply limit
    all_data.truncate(limit);

    Ok(all_data)
}

/// Read Binance ticker data for a specific date range
pub fn read_binance_ticker_data_date_range(
    ticker: &str,
    interval: &str,
    start_date: DateTime<Utc>,
    end_date: DateTime<Utc>,
    spot_dir: &Path,
) -> Result<Vec<Ohlcv>> {
    let all_data = read_binance_ticker_data(ticker, interval, spot_dir)?;

    // Filter by date range
    let filtered_data: Vec<Ohlcv> = all_data
        .into_iter()
        .filter(|d| d.time >= start_date && d.time <= end_date)
        .collect();

    Ok(filtered_data)
}

/// List all available tickers in the spot directory
pub fn list_available_tickers(spot_dir: &Path) -> Result<Vec<String>> {
    let klines_dir = spot_dir.join("daily/klines");

    if !klines_dir.exists() {
        return Err(AppError::NotFound(format!(
            "Klines directory not found: {}",
            klines_dir.display()
        )));
    }

    let mut tickers = Vec::new();
    let entries = fs::read_dir(&klines_dir)?;

    for entry in entries {
        let entry = entry?;
        let path = entry.path();

        if path.is_dir() {
            if let Some(ticker) = path.file_name().and_then(|n| n.to_str()) {
                tickers.push(ticker.to_string());
            }
        }
    }

    tickers.sort();
    Ok(tickers)
}

/// List all available dates for a ticker and interval
pub fn list_available_dates(
    ticker: &str,
    interval: &str,
    spot_dir: &Path,
) -> Result<Vec<String>> {
    let binance_interval = map_project_interval_to_binance(interval)?;
    validate_ticker(ticker)?;

    let interval_dir = spot_dir
        .join("daily/klines")
        .join(ticker)
        .join(binance_interval);

    if !interval_dir.exists() {
        return Err(AppError::NotFound(format!(
            "Interval directory not found: {}",
            interval_dir.display()
        )));
    }

    let mut dates = Vec::new();
    let entries = fs::read_dir(&interval_dir)?;

    for entry in entries {
        let entry = entry?;
        let path = entry.path();

        if let Some(zip_name) = path.file_name().and_then(|n| n.to_str()) {
            if zip_name.ends_with(".zip") {
                // Extract date from filename: TICKER-interval-date.zip
                let parts: Vec<&str> = zip_name.split('-').collect();
                if parts.len() >= 3 {
                    dates.push(parts[2].replace(".zip", ""));
                }
            }
        }
    }

    dates.sort();
    Ok(dates)
}

/// Extract CSV content from a ZIP file (in-memory)
fn extract_csv_from_zip(zip_path: &Path) -> Result<String> {
    let file = fs::File::open(zip_path)?;
    let mut archive = ZipArchive::new(file)?;

    if archive.len() == 0 {
        return Err(AppError::Zip("Empty ZIP file".to_string()));
    }

    // Extract the first (and only) CSV file
    let mut csv_file = archive.by_index(0)?;
    let mut csv_content = String::new();
    csv_file.read_to_string(&mut csv_content)?;

        Ok(csv_content)
}

/// Extract ZIP and parse its CSV content
fn extract_and_parse_zip(zip_path: &Path, ticker: &str) -> Result<Vec<Ohlcv>> {
    let csv_content = extract_csv_from_zip(zip_path)?;
    parse_binance_csv(&csv_content, ticker)
}

/// Parse Binance CSV format (12 columns) to OHLCV
///
/// Binance format:
/// open_time,open,high,low,close,volume,close_time,quote_asset_volume,
/// trade_count,taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore
fn parse_binance_csv(csv_content: &str, ticker: &str) -> Result<Vec<Ohlcv>> {
    let mut reader = csv::ReaderBuilder::new()
        .has_headers(false)
        .from_reader(csv_content.as_bytes());
    let mut ohlcv_data = Vec::new();

    for (i, result) in reader.records().enumerate() {
        match result {
            Ok(record) => {
                if record.len() != 12 {
                    return Err(AppError::Binance(format!(
                        "Invalid CSV record: expected 12 columns, got {} at line {}",
                        record.len(),
                        i + 2 // +2 because of header
                    )));
                }

                // Parse Binance CSV fields
                let open_time_ms: i64 = record[0].parse()
                    .map_err(|e| AppError::Binance(format!(
                        "Invalid open_time at line {}: {}", i + 2, e
                    )))?;

                let open: f64 = record[1].parse()
                    .map_err(|e| AppError::Binance(format!(
                        "Invalid open price at line {}: {}", i + 2, e
                    )))?;

                let high: f64 = record[2].parse()
                    .map_err(|e| AppError::Binance(format!(
                        "Invalid high price at line {}: {}", i + 2, e
                    )))?;

                let low: f64 = record[3].parse()
                    .map_err(|e| AppError::Binance(format!(
                        "Invalid low price at line {}: {}", i + 2, e
                    )))?;

                let close: f64 = record[4].parse()
                    .map_err(|e| AppError::Binance(format!(
                        "Invalid close price at line {}: {}", i + 2, e
                    )))?;

                let volume: f64 = record[5].parse()
                    .map_err(|e| AppError::Binance(format!(
                        "Invalid volume at line {}: {}", i + 2, e
                    )))?;

                // Convert timestamp from milliseconds to DateTime<Utc>
                let timestamp = convert_binance_timestamp(open_time_ms)?;

                // Create OHLCV data point
                let ohlcv = Ohlcv::with_symbol(
                    timestamp,
                    open,
                    high,
                    low,
                    close,
                    volume as u64,
                    ticker.to_string(),
                );

                ohlcv_data.push(ohlcv);
            }
            Err(e) => {
                return Err(AppError::Binance(format!(
                    "CSV parsing error at line {}: {}", i + 2, e
                )));
            }
        }
    }

    Ok(ohlcv_data)
}

/// Convert project interval notation to Binance folder notation
fn map_project_interval_to_binance(interval: &str) -> Result<&str> {
    match interval.to_uppercase().as_str() {
        "1D" => Ok("1d"),
        "1H" => Ok("1h"),
        "1M" => Ok("1m"),
        _ => Err(AppError::InvalidTicker(format!(
            "Invalid interval: {}. Supported: 1D, 1H, 1m",
            interval
        ))),
    }
}

/// Validate ticker format
fn validate_ticker(ticker: &str) -> Result<()> {
    if ticker.is_empty() {
        return Err(AppError::InvalidTicker("Ticker cannot be empty".to_string()));
    }

    if ticker.len() > 20 {
        return Err(AppError::InvalidTicker("Ticker too long".to_string()));
    }

    // Basic validation: alphanumeric characters only
    if !ticker.chars().all(|c| c.is_alphanumeric()) {
        return Err(AppError::InvalidTicker(format!(
            "Invalid ticker format: {}. Only alphanumeric characters allowed",
            ticker
        )));
    }

    Ok(())
}

/// Convert Binance timestamp to DateTime<Utc>
/// Handles both milliseconds (13 digits) and microseconds (16 digits) formats
fn convert_binance_timestamp(timestamp: i64) -> Result<DateTime<Utc>> {
    if timestamp < 0 {
        return Err(AppError::Binance(
            "Negative timestamp not supported".to_string()
        ));
    }

    // Determine if timestamp is in microseconds (16+ digits) or milliseconds (13 digits)
    let timestamp_secs = if timestamp >= 1_000_000_000_000_000 {
        // Microseconds format (16+ digits)
        timestamp / 1_000_000
    } else if timestamp >= 1_000_000_000_000 {
        // Milliseconds format (13 digits)
        timestamp / 1000
    } else {
        // Already in seconds format (unlikely but handle anyway)
        timestamp
    };

    DateTime::from_timestamp(timestamp_secs, 0)
        .ok_or_else(|| AppError::Binance(
            format!("Invalid timestamp: {}", timestamp)
        ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_interval_mapping() {
        assert_eq!(map_project_interval_to_binance("1D").unwrap(), "1d");
        assert_eq!(map_project_interval_to_binance("1H").unwrap(), "1h");
        assert_eq!(map_project_interval_to_binance("1m").unwrap(), "1m");
        assert_eq!(map_project_interval_to_binance("1M").unwrap(), "1m");

        assert!(map_project_interval_to_binance("4H").is_err());
        assert!(map_project_interval_to_binance("invalid").is_err());
    }

    #[test]
    fn test_ticker_validation() {
        assert!(validate_ticker("BTCUSDT").is_ok());
        assert!(validate_ticker("ETH").is_ok());
        assert!(validate_ticker("BTC123").is_ok());

        assert!(validate_ticker("").is_err());
        assert!(validate_ticker("BTC-USDT").is_err());
        assert!(validate_ticker("BTC@USDT").is_err());
    }

    #[test]
    fn test_timestamp_conversion() {
        let ts = 1700000000000i64; // Some timestamp in milliseconds
        let result = convert_binance_timestamp(ts);
        assert!(result.is_ok());

        let negative_ts = -1i64;
        assert!(convert_binance_timestamp(negative_ts).is_err());
    }

    #[test]
    fn test_csv_parsing() {
        let csv_content = r#"open_time,open,high,low,close,volume,close_time,quote_asset_volume,trade_count,taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore
1700000000000,45000.0,45500.0,44800.0,45200.0,1000.5,1700000059000,45000000.0,5000,600.3,27000000.0,0
1700000060000,45200.0,45600.0,45100.0,45400.0,800.2,1700000119000,36320000.0,4000,400.1,18100000.0,0"#;

        let result = parse_binance_csv(csv_content, "BTCUSDT");
        assert!(result.is_ok());

        let data = result.unwrap();
        assert_eq!(data.len(), 2);
        assert_eq!(data[0].symbol, Some("BTCUSDT".to_string()));
        assert_eq!(data[0].open, 45000.0);
        assert_eq!(data[0].close, 45200.0);
    }
}