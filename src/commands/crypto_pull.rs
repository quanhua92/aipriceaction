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
        "hourly" | "1h" => {
            eprintln!("❌ Hourly interval not yet supported (coming in Phase 3)");
            std::process::exit(1);
        }
        "minute" | "1m" => {
            eprintln!("❌ Minute interval not yet supported (coming in Phase 3)");
            std::process::exit(1);
        }
        _ => {
            eprintln!("❌ Invalid interval: {}", interval_str);
            eprintln!("   Valid options: daily (1d)");
            std::process::exit(1);
        }
    };

    println!("🪙 Fetching {} {} data from CryptoCompare...", symbol, interval.to_filename());

    if full {
        println!("📅 Full history mode: Using allData=true");
    } else {
        println!("📅 Resume mode: Fetching only new data (not yet implemented, using full)");
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

    // Fetch data
    println!("📡 Calling CryptoCompare API...");

    let start_date = "2010-01-01"; // Bitcoin genesis: 2009-01-03, but trading started 2010

    let ohlcv_data = if interval == Interval::Daily && full {
        // Phase 2: Use allData=true for full history in one call
        println!("   Using allData=true for full daily history...");
        client.get_history(symbol, start_date, None, interval, None, true).await
            .map_err(|e| Error::Network(format!("API request failed: {}", e)))?
    } else {
        // Future: Resume mode or hourly/minute intervals
        return Err(Error::Other("Resume mode not yet implemented".to_string()));
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

    // Save enhanced CSV directly (20 columns, rewrite_all=true)
    let symbol_dir = crypto_data_dir.join(symbol);
    fs::create_dir_all(&symbol_dir)
        .map_err(|e| Error::Io(format!("Failed to create {}: {}", symbol_dir.display(), e)))?;

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
