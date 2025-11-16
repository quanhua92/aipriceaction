//! Cryptocurrency data pull command
//!
//! This command fetches cryptocurrency data from CryptoCompare API and stores it
//! in crypto_data/ directory with the same CSV format as market_data/.
//!
//! **Phase 5 Implementation**: Supports all 100 cryptocurrencies, all intervals
//!
//! Usage:
//! - Single crypto: `crypto-pull --symbol BTC --interval daily`
//! - All cryptos: `crypto-pull --symbol all --interval daily`
//! - Default (all cryptos, all intervals): `crypto-pull`

use crate::models::{Interval, SyncConfig, load_crypto_symbols, get_default_crypto_list_path};
use crate::services::CryptoSync;
use crate::constants::IGNORED_CRYPTOS;

/// Run crypto-pull command
///
/// # Arguments
/// * `symbol` - Cryptocurrency symbol ("BTC", "ETH", "all", or None for all)
/// * `interval_str` - Data interval ("daily", "hourly", "minute", "all")
/// * `full` - Force full history sync (default: false)
///
pub fn run(symbol: Option<String>, interval_str: String, full: bool) {
    // Load crypto symbols
    let mut crypto_symbols = match symbol {
        Some(ref s) if s.to_lowercase() == "all" => {
            // Load all 100 cryptos from crypto_top_100.json
            match load_crypto_symbols(get_default_crypto_list_path()) {
                Ok(symbols) => symbols,
                Err(e) => {
                    eprintln!("❌ Failed to load crypto_top_100.json: {}", e);
                    std::process::exit(1);
                }
            }
        }
        Some(s) => {
            // Single crypto
            vec![s]
        }
        None => {
            // Default: all cryptos
            match load_crypto_symbols(get_default_crypto_list_path()) {
                Ok(symbols) => symbols,
                Err(e) => {
                    eprintln!("❌ Failed to load crypto_top_100.json: {}", e);
                    std::process::exit(1);
                }
            }
        }
    };

    // Filter out ignored cryptos (no data available from CryptoCompare API)
    let original_count = crypto_symbols.len();
    crypto_symbols.retain(|s| !IGNORED_CRYPTOS.contains(&s.as_str()));
    let ignored_count = original_count - crypto_symbols.len();

    if ignored_count > 0 {
        println!("ℹ️  Skipping {} ignored crypto(s): {:?}", ignored_count, IGNORED_CRYPTOS);
    }

    // Parse intervals
    let intervals = match interval_str.to_lowercase().as_str() {
        "daily" | "1d" => vec![Interval::Daily],
        "hourly" | "1h" => vec![Interval::Hourly],
        "minute" | "1m" => vec![Interval::Minute],
        "all" => vec![Interval::Daily, Interval::Hourly, Interval::Minute],
        _ => {
            eprintln!("❌ Invalid interval: {}", interval_str);
            eprintln!("   Valid options: daily (1d), hourly (1h), minute (1m), all");
            std::process::exit(1);
        }
    };

    // Create sync config
    let config = SyncConfig {
        intervals: intervals.clone(),
        // Use 2-day window for resume mode, unless --full flag is set
        // CryptoSync will categorize each crypto:
        // - If CSV exists: resume from last date
        // - If CSV missing or --full: fetch full history from BTC inception
        start_date: (chrono::Utc::now() - chrono::Duration::days(2))
            .format("%Y-%m-%d")
            .to_string(),
        end_date: chrono::Utc::now().format("%Y-%m-%d").to_string(),
        force_full: full,
        ..Default::default()
    };

    // Create Tokio runtime
    let runtime = match tokio::runtime::Runtime::new() {
        Ok(r) => r,
        Err(e) => {
            eprintln!("❌ Failed to create async runtime: {}", e);
            std::process::exit(1);
        }
    };

    // Run async sync via CryptoSync orchestration
    match runtime.block_on(async {
        let mut sync = CryptoSync::new(config, None)?;
        sync.sync_all_intervals(&crypto_symbols).await
    }) {
        Ok(_) => {
            println!("\n✅ Crypto sync completed successfully!");
        }
        Err(e) => {
            eprintln!("\n❌ Crypto sync failed: {}", e);
            std::process::exit(1);
        }
    }
}
