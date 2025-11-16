//! Cryptocurrency data pull command
//!
//! This command fetches cryptocurrency data from CryptoCompare API and stores it
//! in crypto_data/ directory with the same CSV format as market_data/.
//!
//! **Phase 2 Implementation**: BTC daily data only (allData=true)
//! **Future Phases**: All intervals, all 100 cryptocurrencies

use crate::error::Error;
use crate::models::{Interval, StockData};
use crate::services::{CryptoCompareClient, save_enhanced_csv_to_dir};
use crate::services::vci::OhlcvData;
use crate::services::csv_enhancer::enhance_data;
use std::fs;
use std::path::PathBuf;
use std::collections::HashMap;
use std::io::{BufRead, BufReader};

/// Run crypto-pull command
///
/// **Current Phase 2**: Only supports BTC + daily interval
///
/// # Arguments
/// * `symbol` - Cryptocurrency symbol (default: BTC)
/// * `interval` - Data interval (default: daily, only daily supported in Phase 2)
/// * `full` - Force full history sync (default: false)
///
pub fn run(symbol: Option<String>, interval_str: String, full: bool) {
    // Phase 2: Only BTC and daily interval
    let symbol = symbol.unwrap_or_else(|| "BTC".to_string());
    let currency = "USD"; // Always USD for crypto

    // Parse interval
    let interval = match interval_str.to_lowercase().as_str() {
        "daily" | "1d" => Interval::Daily,
        "hourly" | "1h" => Interval::Hourly,
        "minute" | "1m" => Interval::Minute,
        _ => {
            eprintln!("❌ Invalid interval: {}", interval_str);
            eprintln!("   Valid options: daily (1d), hourly (1h), minute (1m)");
            std::process::exit(1);
        }
    };

    println!("🪙 Fetching {} {} data from CryptoCompare...", symbol, interval.to_filename());

    if full {
        println!("📅 Full history mode: Downloading complete history");
    } else {
        println!("📅 Resume mode: Fetching only new data since last sync");
    }

    // Create crypto_data directory if it doesn't exist
    let crypto_data_dir = PathBuf::from("crypto_data");
    if let Err(e) = fs::create_dir_all(&crypto_data_dir) {
        eprintln!("❌ Failed to create crypto_data directory: {}", e);
        std::process::exit(1);
    }

    // Create Tokio runtime
    let runtime = match tokio::runtime::Runtime::new() {
        Ok(r) => r,
        Err(e) => {
            eprintln!("❌ Failed to create async runtime: {}", e);
            std::process::exit(1);
        }
    };

    // Run async fetch
    match runtime.block_on(fetch_and_save(&symbol, currency, interval, full, &crypto_data_dir)) {
        Ok(count) => {
            println!("\n✅ Successfully fetched {} records for {}", count, symbol);
            println!("💾 Saved to crypto_data/{}/{}.csv", symbol, interval.to_filename());
        }
        Err(e) => {
            eprintln!("\n❌ Failed to fetch {}: {}", symbol, e);
            std::process::exit(1);
        }
    }
}

/// Read the last timestamp from an existing CSV file
///
/// Returns the timestamp of the last record, or None if file doesn't exist or is empty
fn get_last_timestamp_from_csv(csv_path: &PathBuf) -> Option<chrono::DateTime<chrono::Utc>> {
    if !csv_path.exists() {
        return None;
    }

    let file = fs::File::open(csv_path).ok()?;
    let reader = BufReader::new(file);

    // Read all lines and get the last non-empty one
    let lines: Vec<String> = reader.lines().filter_map(|l| l.ok()).collect();

    if lines.len() <= 1 {
        // Only header or empty file
        return None;
    }

    // Get last line (skip header)
    let last_line = lines.last()?;

    // Parse CSV: ticker,time,open,high,low,close,volume,...
    let parts: Vec<&str> = last_line.split(',').collect();
    if parts.len() < 2 {
        return None;
    }

    let time_str = parts[1];

    // Try parsing as date or datetime
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(time_str, "%Y-%m-%dT%H:%M:%S") {
        return Some(chrono::DateTime::<chrono::Utc>::from_naive_utc_and_offset(dt, chrono::Utc));
    }

    if let Ok(date) = chrono::NaiveDate::parse_from_str(time_str, "%Y-%m-%d") {
        let dt = date.and_hms_opt(0, 0, 0)?;
        return Some(chrono::DateTime::<chrono::Utc>::from_naive_utc_and_offset(dt, chrono::Utc));
    }

    None
}

/// Fetch paginated history for hourly and minute intervals
///
/// CryptoCompare limits to 2000 records per request, so we need to paginate
/// using the `toTs` parameter to get older data.
async fn fetch_paginated_history(
    client: &mut CryptoCompareClient,
    symbol: &str,
    start_date: &str,
    interval: Interval,
) -> Result<Vec<OhlcvData>, Error> {
    let mut all_data = Vec::new();
    let mut to_ts: Option<i64> = None;
    let limit = 2000; // CryptoCompare max

    loop {
        let batch = client
            .get_history(symbol, start_date, to_ts, interval, Some(limit), false)
            .await
            .map_err(|e| Error::Network(format!("Pagination request failed: {}", e)))?;

        if batch.is_empty() {
            println!("   📭 No more data available");
            break;
        }

        let batch_len = batch.len();
        let oldest = batch.first().unwrap().time;
        let newest = batch.last().unwrap().time;

        // Set toTs to the oldest timestamp for next batch
        to_ts = Some(oldest.timestamp());

        all_data.extend(batch);

        println!(
            "   📦 Fetched {} records (total: {}, range: {} to {})",
            batch_len,
            all_data.len(),
            oldest.format("%Y-%m-%d %H:%M"),
            newest.format("%Y-%m-%d %H:%M")
        );

        // If we got less than limit, we've reached the end
        if batch_len < limit {
            println!("   ✅ Reached oldest available data");
            break;
        }

        // Rate limit: wait 200ms between requests (5 calls/sec)
        tokio::time::sleep(tokio::time::Duration::from_millis(200)).await;
    }

    // Sort by time (oldest first)
    all_data.sort_by_key(|d| d.time);

    Ok(all_data)
}

/// Fetch cryptocurrency data and save to CSV
async fn fetch_and_save(
    symbol: &str,
    currency: &str,
    interval: Interval,
    full: bool,
    crypto_data_dir: &PathBuf,
) -> Result<usize, Error> {
    // Create API client
    let mut client = CryptoCompareClient::new(None)
        .map_err(|e| Error::Network(format!("Failed to create CryptoCompare client: {}", e)))?;

    // Fetch data based on interval
    println!("📡 Calling CryptoCompare API...");

    // Determine start date for fetching
    let csv_path = crypto_data_dir.join(symbol).join(interval.to_filename());
    let last_timestamp = get_last_timestamp_from_csv(&csv_path);

    let ohlcv_data = match interval {
        Interval::Daily => {
            if full || last_timestamp.is_none() {
                // Full history mode
                println!("   Using allData=true for full daily history...");
                client.get_history(symbol, "2010-01-01", None, interval, None, true).await
                    .map_err(|e| Error::Network(format!("API request failed: {}", e)))?
            } else {
                // Resume mode: fetch from last date to now
                let last_date = last_timestamp.unwrap();
                println!("   📅 Resume mode: Last date in CSV: {}", last_date.format("%Y-%m-%d"));
                println!("   📥 Fetching new daily data from {} to now...", last_date.format("%Y-%m-%d"));

                // Use pagination from last date (will get recent data)
                let start_date = last_date.format("%Y-%m-%d").to_string();
                fetch_paginated_history(&mut client, symbol, &start_date, interval).await?
            }
        }
        Interval::Hourly => {
            if full || last_timestamp.is_none() {
                // Full history mode
                println!("   Fetching full hourly history with pagination (limit=2000)...");
                fetch_paginated_history(&mut client, symbol, "2010-07-17", interval).await?
            } else {
                // Resume mode: fetch from last hour to now
                let last_hour = last_timestamp.unwrap();
                println!("   📅 Resume mode: Last hour in CSV: {}", last_hour.format("%Y-%m-%d %H:%M"));
                println!("   📥 Fetching new hourly data from {} to now...", last_hour.format("%Y-%m-%d %H:%M"));

                let start_date = last_hour.format("%Y-%m-%d").to_string();
                fetch_paginated_history(&mut client, symbol, &start_date, interval).await?
            }
        }
        Interval::Minute => {
            // CryptoCompare only keeps 7 days of minute data
            let seven_days_ago = chrono::Utc::now() - chrono::Duration::days(7);

            if full || last_timestamp.is_none() {
                // Full history mode (last 7 days)
                let start_date = seven_days_ago.format("%Y-%m-%d").to_string();
                println!("   Fetching last 7 days of minute data (CryptoCompare limitation)...");
                println!("   Start date: {}", start_date);
                fetch_paginated_history(&mut client, symbol, &start_date, interval).await?
            } else {
                // Resume mode: fetch from last minute (but respect 7-day limit)
                let last_minute = last_timestamp.unwrap();

                // If last data is older than 7 days, fetch full 7 days
                let start_date = if last_minute < seven_days_ago {
                    println!("   ⚠️  Last data is older than 7 days, fetching full 7-day window");
                    seven_days_ago.format("%Y-%m-%d").to_string()
                } else {
                    println!("   📅 Resume mode: Last minute in CSV: {}", last_minute.format("%Y-%m-%d %H:%M"));
                    println!("   📥 Fetching new minute data from {} to now...", last_minute.format("%Y-%m-%d %H:%M"));
                    last_minute.format("%Y-%m-%d").to_string()
                };

                fetch_paginated_history(&mut client, symbol, &start_date, interval).await?
            }
        }
    };

    println!("✅ Received {} records from API", ohlcv_data.len());

    if ohlcv_data.is_empty() {
        return Err(Error::InvalidInput(format!("No data returned for {}", symbol)));
    }

    // Convert to HashMap for enhance_data()
    println!("📊 Converting to StockData and calculating technical indicators...");
    let mut data_map: HashMap<String, Vec<OhlcvData>> = HashMap::new();
    data_map.insert(symbol.to_string(), ohlcv_data.clone());

    // Enhance data in-memory (calculates all MAs and scores)
    let enhanced_data = enhance_data(data_map);

    // Get the enhanced data for this symbol
    let stock_data = enhanced_data.get(symbol)
        .ok_or_else(|| Error::Other("Failed to enhance data".to_string()))?;

    println!("✅ Calculated indicators for {} records", stock_data.len());

    // Save enhanced CSV (rewrite if full mode, append if resume mode)
    let symbol_dir = crypto_data_dir.join(symbol);
    fs::create_dir_all(&symbol_dir)
        .map_err(|e| Error::Io(format!("Failed to create {}: {}", symbol_dir.display(), e)))?;

    let is_resume = last_timestamp.is_some() && !full;

    if is_resume {
        // Resume mode: use cutoff strategy (will append new data)
        let cutoff_date = last_timestamp.unwrap();
        println!("📝 Appending {} new records to existing CSV...", stock_data.len());

        save_enhanced_csv_to_dir(
            symbol,
            stock_data,
            interval,
            cutoff_date,
            false, // Don't rewrite all, use append logic
            crypto_data_dir
        )?;

        println!("✅ Appended to crypto_data/{}/{}", symbol, interval.to_filename());
    } else {
        // Full mode: rewrite entire file
        println!("📝 Writing full CSV with {} records...", stock_data.len());

        let cutoff_date = chrono::Utc::now() - chrono::Duration::days(365 * 20); // Far in past = rewrite all
        save_enhanced_csv_to_dir(
            symbol,
            stock_data,
            interval,
            cutoff_date,
            true, // rewrite_all
            crypto_data_dir
        )?;

        println!("✅ Saved enhanced CSV to crypto_data/{}/{}", symbol, interval.to_filename());
    }

    Ok(stock_data.len())
}

/// Save raw OHLCV data to CSV (no indicators)
///
/// Writes basic 7-column format that enhance_interval() will read and enhance
fn save_raw_csv(
    data: &[crate::services::vci::OhlcvData],
    symbol: &str,
    csv_path: &PathBuf,
) -> Result<(), Error> {
    use std::io::Write;

    let mut file = fs::File::create(csv_path)
        .map_err(|e| Error::Io(format!("Failed to create CSV: {}", e)))?;

    // Write header (basic 7 columns)
    writeln!(file, "ticker,time,open,high,low,close,volume")
        .map_err(|e| Error::Io(format!("Failed to write header: {}", e)))?;

    // Write data rows
    for ohlcv in data {
        let time_str = if matches!(Interval::Daily, Interval::Daily) {
            ohlcv.time.format("%Y-%m-%d").to_string()
        } else {
            ohlcv.time.format("%Y-%m-%d %H:%M:%S").to_string()
        };

        writeln!(
            file,
            "{},{},{},{},{},{},{}",
            symbol,
            time_str,
            ohlcv.open,
            ohlcv.high,
            ohlcv.low,
            ohlcv.close,
            ohlcv.volume as u64
        )
        .map_err(|e| Error::Io(format!("Failed to write row: {}", e)))?;
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_crypto_data_dir_creation() {
        let test_dir = PathBuf::from("test_crypto_data");
        let _ = fs::remove_dir_all(&test_dir); // Clean up first

        assert!(fs::create_dir_all(&test_dir).is_ok());
        assert!(test_dir.exists());
        assert!(test_dir.is_dir());

        let _ = fs::remove_dir_all(&test_dir); // Clean up
    }
}
