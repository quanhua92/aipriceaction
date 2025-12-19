//! Cryptocurrency data retrieval command
//!
//! This command retrieves cryptocurrency data from Binance Vision ZIP files
//! with optional filtering parameters.
//!
//! Usage:
//! - Basic: `crypto-get BTCUSDT`
//! - With options: `crypto-get BTCUSDT --interval 1H --limit 50 --start-date 2025-11-20`

use crate::binance::{read_binance_ticker_data, read_binance_ticker_data_limited, list_available_tickers};
use crate::error::Result;
use chrono::{DateTime, Utc, NaiveDate};
use std::path::Path;

/// Run crypto-get command
///
/// # Arguments
/// * `ticker` - Cryptocurrency ticker symbol (required)
/// * `interval` - Data interval (default: "1D")
/// * `start_date` - Optional start date filter (YYYY-MM-DD)
/// * `end_date` - Optional end date filter (YYYY-MM-DD)
/// * `limit` - Maximum number of records to return (default: 200)
///
pub fn run(
    ticker: String,
    interval: String,
    start_date: Option<String>,
    end_date: Option<String>,
    limit: u32,
) {
    println!("🚀 Crypto Data Retrieval from Binance Vision");
    println!("=========================================");
    println!("📊 Ticker: {}", ticker);
    println!("⏰ Interval: {}", interval);
    println!("📅 Start Date: {:?}", start_date);
    println!("📅 End Date: {:?}", end_date);
    println!("🔢 Limit: {}", limit);
    println!();

    let spot_dir = Path::new("spot");

    if !spot_dir.exists() {
        println!("❌ Error: spot directory not found!");
        println!("   Make sure Binance Vision data is available in ./spot/ directory");
        return;
    }

    // Convert string dates to DateTime<Utc> if provided
    let start_dt = start_date.as_ref().and_then(|s| parse_date(s).ok());
    let end_dt = end_date.as_ref().and_then(|s| parse_date(s).ok());

    println!("🔍 Searching for {} {} data...", ticker, interval);

    // Read the data using our binance-utils module with limit optimization
    let result = match (start_dt, end_dt) {
        (Some(start), Some(end)) => {
            read_binance_ticker_data_date_range(&ticker, &interval, start, end, spot_dir)
        }
        _ => {
            read_binance_ticker_data_limited(&ticker, &interval, spot_dir, limit as usize)
        }
    };

    match result {
        Ok(data) => {
            println!("✅ Successfully retrieved {} data points!", data.len());
            println!();

            if data.is_empty() {
                println!("ℹ️  No data found. Check if ticker and interval are correct.");

                // Show available tickers
                if let Ok(available_tickers) = list_available_tickers(spot_dir) {
                    println!("\n📋 Available tickers (first 20):");
                    for (i, t) in available_tickers.iter().take(20).enumerate() {
                        println!("  {}. {}", i + 1, t);
                    }
                    if available_tickers.len() > 20 {
                        println!("  ... and {} more", available_tickers.len() - 20);
                    }
                }
                return;
            }

            let total_count = data.len();

            // Apply limit
            let display_data: Vec<_> = data.into_iter().take(limit as usize).collect();
            let actual_count = display_data.len();

            println!("📈 Sample Data (showing {} of {} available records):", actual_count, total_count);
            println!("{:<25} {:<12} {:<12} {:<12} {:<12} {:<12}",
                "Time", "Open", "High", "Low", "Close", "Volume");
            println!("{}", "-".repeat(85));

            for (i, ohlcv) in display_data.iter().enumerate() {
                let time_str = ohlcv.time.format("%Y-%m-%d %H:%M").to_string();
                println!("{:<25} {:<12.2} {:<12.2} {:<12.2} {:<12.2} {:<12}",
                    time_str,
                    ohlcv.open,
                    ohlcv.high,
                    ohlcv.low,
                    ohlcv.close,
                    ohlcv.volume
                );

                // Show ellipsis after first 5 and before last 5 if we have many records
                if i == 4 && actual_count > 10 {
                    println!("  ... ({} more records) ...", actual_count - 9);
                    // Skip to the last 5 records
                    if let Some(start_idx) = actual_count.checked_sub(5) {
                        for ohlcv in display_data[start_idx..].iter() {
                            let time_str = ohlcv.time.format("%Y-%m-%d %H:%M").to_string();
                            println!("{:<25} {:<12.2} {:<12.2} {:<12.2} {:<12.2} {:<12}",
                                time_str,
                                ohlcv.open,
                                ohlcv.high,
                                ohlcv.low,
                                ohlcv.close,
                                ohlcv.volume
                            );
                        }
                    }
                    break;
                }
            }

            // Show data summary
            if let Some(first) = display_data.first() {
                if let Some(last) = display_data.last() {
                    println!("\n📊 Data Summary:");
                    println!("  Time Range: {} to {}",
                        first.time.format("%Y-%m-%d %H:%M"),
                        last.time.format("%Y-%m-%d %H:%M"));
                    println!("  Price Range: {:.2} - {:.2}",
                        display_data.iter().map(|d| d.low).fold(f64::INFINITY, f64::min),
                        display_data.iter().map(|d| d.high).fold(f64::NEG_INFINITY, f64::max));
                    println!("  Total Volume: {}",
                        display_data.iter().map(|d| d.volume).sum::<u64>());
                }
            }
        }
        Err(e) => {
            println!("❌ Error retrieving data: {}", e);

            // Try to list available tickers for guidance
            if let Ok(available_tickers) = list_available_tickers(spot_dir) {
                println!("\n💡 Available tickers (first 10):");
                for ticker in available_tickers.iter().take(10) {
                    println!("  - {}", ticker);
                }
            }
        }
    }
}

/// Helper function to parse date string YYYY-MM-DD to DateTime<Utc>
fn parse_date(date_str: &str) -> Result<DateTime<Utc>> {
    let naive_date = NaiveDate::parse_from_str(date_str, "%Y-%m-%d")
        .map_err(|e| crate::error::AppError::Parse(format!("Invalid date format: {}", e)))?;

    let datetime = naive_date.and_hms_opt(0, 0, 0)
        .ok_or_else(|| crate::error::AppError::Parse("Failed to create datetime".to_string()))?;

    Ok(DateTime::from_naive_utc_and_offset(datetime, Utc))
}

/// Read ticker data with date range filtering
fn read_binance_ticker_data_date_range(
    ticker: &str,
    interval: &str,
    start_date: DateTime<Utc>,
    end_date: DateTime<Utc>,
    spot_dir: &Path,
) -> Result<Vec<crate::models::Ohlcv>> {
    let all_data = read_binance_ticker_data(ticker, interval, spot_dir)?;

    let filtered_data: Vec<crate::models::Ohlcv> = all_data
        .into_iter()
        .filter(|d| d.time >= start_date && d.time <= end_date)
        .collect();

    Ok(filtered_data)
}