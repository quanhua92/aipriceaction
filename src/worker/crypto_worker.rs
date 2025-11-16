use crate::constants::IGNORED_CRYPTOS;
use crate::error::Error;
use crate::models::{Interval, SyncConfig, load_crypto_symbols, get_default_crypto_list_path};
use crate::services::{CryptoSync, SharedHealthStats, csv_enhancer};
use crate::utils::{get_crypto_data_dir, write_with_rotation};
use chrono::Utc;
use std::time::Duration;
use tokio::time::sleep;
use tracing::{info, warn, error, instrument};

// Priority cryptos sync every 15 minutes (all intervals)
const PRIORITY_CRYPTOS: &[&str] = &["BTC", "ETH", "XRP"];

// Regular cryptos use staggered intervals to manage rate limits
const REGULAR_DAILY_SYNC_INTERVAL_SECS: u64 = 3600;   // 1 hour
const REGULAR_HOURLY_SYNC_INTERVAL_SECS: u64 = 10800; // 3 hours
const REGULAR_MINUTE_SYNC_INTERVAL_SECS: u64 = 21600; // 6 hours

// Main loop check interval
const LOOP_CHECK_INTERVAL_SECS: u64 = 900; // 15 minutes (matches priority sync)

#[instrument(skip(health_stats))]
pub async fn run(health_stats: SharedHealthStats) {
    info!(
        "Starting crypto worker with two-tier sync strategy"
    );
    info!(
        "  - Priority cryptos: {} (every 15min, all intervals)",
        PRIORITY_CRYPTOS.join(", ")
    );
    info!(
        "  - Regular cryptos: Daily=1h, Hourly=3h, Minute=6h"
    );
    info!(
        "  - Main loop interval: {}s (15 minutes)",
        LOOP_CHECK_INTERVAL_SECS
    );

    let mut iteration_count = 0u64;
    let crypto_data_dir = get_crypto_data_dir();

    // Track last sync times for regular cryptos per interval
    let mut last_regular_daily_sync = std::time::Instant::now();
    let mut last_regular_hourly_sync = std::time::Instant::now();
    let mut last_regular_minute_sync = std::time::Instant::now();

    info!("Crypto worker started");

    loop {
        iteration_count += 1;
        let loop_start = std::time::Instant::now();

        info!(
            worker = "Crypto",
            iteration = iteration_count,
            "Starting crypto sync cycle"
        );

        // Load crypto symbols
        let (priority_symbols, regular_symbols) = match load_crypto_symbols(get_default_crypto_list_path()) {
            Ok(mut symbols) => {
                // Filter out ignored cryptos
                let original_count = symbols.len();
                symbols.retain(|s| !IGNORED_CRYPTOS.contains(&s.as_str()));
                let ignored_count = original_count - symbols.len();

                if ignored_count > 0 {
                    info!(
                        worker = "Crypto",
                        iteration = iteration_count,
                        ignored_count = ignored_count,
                        ignored_symbols = ?IGNORED_CRYPTOS,
                        "Filtered ignored cryptos"
                    );
                }

                // Split into priority and regular
                let mut priority = Vec::new();
                let mut regular = Vec::new();

                for symbol in symbols {
                    if PRIORITY_CRYPTOS.contains(&symbol.as_str()) {
                        priority.push(symbol);
                    } else {
                        regular.push(symbol);
                    }
                }

                info!(
                    worker = "Crypto",
                    iteration = iteration_count,
                    priority_count = priority.len(),
                    regular_count = regular.len(),
                    "Loaded and categorized crypto symbols"
                );

                (priority, regular)
            }
            Err(e) => {
                error!(
                    worker = "Crypto",
                    iteration = iteration_count,
                    error = %e,
                    "Failed to load crypto symbols, skipping iteration"
                );
                sleep(Duration::from_secs(LOOP_CHECK_INTERVAL_SECS)).await;
                continue;
            }
        };

        let mut all_intervals_successful = true;

        // TIER 1: Always sync priority cryptos (every 15 minutes, all intervals)
        if !priority_symbols.is_empty() {
            info!(
                worker = "Crypto",
                iteration = iteration_count,
                "Syncing priority cryptos: {}",
                PRIORITY_CRYPTOS.join(", ")
            );

            for interval in &[Interval::Daily, Interval::Hourly, Interval::Minute] {
                let success = sync_and_enhance(
                    *interval,
                    &priority_symbols,
                    iteration_count,
                    "Priority",
                    &crypto_data_dir,
                ).await;

                if !success {
                    all_intervals_successful = false;
                }
            }
        }

        // TIER 2: Sync regular cryptos based on per-interval timing
        if !regular_symbols.is_empty() {
            // Check Daily (every 1 hour)
            if last_regular_daily_sync.elapsed().as_secs() >= REGULAR_DAILY_SYNC_INTERVAL_SECS {
                info!(
                    worker = "Crypto",
                    iteration = iteration_count,
                    "Syncing regular cryptos: Daily (1 hour elapsed)"
                );

                let success = sync_and_enhance(
                    Interval::Daily,
                    &regular_symbols,
                    iteration_count,
                    "Regular",
                    &crypto_data_dir,
                ).await;

                if !success {
                    all_intervals_successful = false;
                }
                last_regular_daily_sync = std::time::Instant::now();
            }

            // Check Hourly (every 3 hours)
            if last_regular_hourly_sync.elapsed().as_secs() >= REGULAR_HOURLY_SYNC_INTERVAL_SECS {
                info!(
                    worker = "Crypto",
                    iteration = iteration_count,
                    "Syncing regular cryptos: Hourly (3 hours elapsed)"
                );

                let success = sync_and_enhance(
                    Interval::Hourly,
                    &regular_symbols,
                    iteration_count,
                    "Regular",
                    &crypto_data_dir,
                ).await;

                if !success {
                    all_intervals_successful = false;
                }
                last_regular_hourly_sync = std::time::Instant::now();
            }

            // Check Minute (every 6 hours)
            if last_regular_minute_sync.elapsed().as_secs() >= REGULAR_MINUTE_SYNC_INTERVAL_SECS {
                info!(
                    worker = "Crypto",
                    iteration = iteration_count,
                    "Syncing regular cryptos: Minute (6 hours elapsed)"
                );

                let success = sync_and_enhance(
                    Interval::Minute,
                    &regular_symbols,
                    iteration_count,
                    "Regular",
                    &crypto_data_dir,
                ).await;

                if !success {
                    all_intervals_successful = false;
                }
                last_regular_minute_sync = std::time::Instant::now();
            }
        }

        // Step 3: Update health stats
        {
            let mut health = health_stats.write().await;
            health.crypto_last_sync = Some(Utc::now().to_rfc3339());
            health.crypto_iteration_count = iteration_count;
        }

        let loop_duration = loop_start.elapsed();

        info!(
            worker = "Crypto",
            iteration = iteration_count,
            loop_duration_secs = loop_duration.as_secs_f64(),
            next_check_secs = LOOP_CHECK_INTERVAL_SECS,
            all_successful = all_intervals_successful,
            "Iteration completed"
        );

        // Sleep for 15 minutes before next check
        sleep(Duration::from_secs(LOOP_CHECK_INTERVAL_SECS)).await;
    }
}

/// Sync and enhance a single interval for given symbols
async fn sync_and_enhance(
    interval: Interval,
    symbols: &[String],
    iteration: u64,
    tier: &str,
    crypto_data_dir: &std::path::PathBuf,
) -> bool {
    let interval_name = match interval {
        Interval::Daily => "Daily",
        Interval::Hourly => "Hourly",
        Interval::Minute => "Minute",
    };

    info!(
        worker = "Crypto",
        iteration = iteration,
        tier = tier,
        interval = interval_name,
        crypto_count = symbols.len(),
        "Starting interval sync"
    );

    // Step 1: Sync
    let sync_start = Utc::now();
    let sync_result = sync_crypto_interval(interval, symbols).await;
    let sync_end = Utc::now();
    let sync_duration = (sync_end - sync_start).num_seconds();

    let sync_success = match sync_result {
        Ok(_) => {
            info!(
                worker = "Crypto",
                iteration = iteration,
                tier = tier,
                interval = interval_name,
                duration_secs = sync_duration,
                "Sync completed"
            );
            true
        }
        Err(e) => {
            error!(
                worker = "Crypto",
                iteration = iteration,
                tier = tier,
                interval = interval_name,
                error = %e,
                "Sync failed"
            );
            false
        }
    };

    // Write log entry
    write_log_entry(&sync_start, &sync_end, sync_duration, sync_success, interval, symbols, tier);

    // Step 2: Enhance CSV if sync succeeded
    if sync_success {
        info!(
            worker = "Crypto",
            iteration = iteration,
            tier = tier,
            interval = interval_name,
            "Enhancing CSV"
        );

        match csv_enhancer::enhance_interval(interval, crypto_data_dir) {
            Ok(stats) => {
                info!(
                    worker = "Crypto",
                    iteration = iteration,
                    tier = tier,
                    interval = interval_name,
                    tickers = stats.tickers,
                    records = stats.records,
                    duration_secs = stats.duration.as_secs_f64(),
                    "Enhancement completed"
                );
            }
            Err(e) => {
                warn!(
                    worker = "Crypto",
                    iteration = iteration,
                    tier = tier,
                    interval = interval_name,
                    error = %e,
                    "Enhancement failed"
                );
            }
        }
    }

    sync_success
}

/// Sync a single interval for all cryptocurrencies using CryptoSync
async fn sync_crypto_interval(interval: Interval, symbols: &[String]) -> Result<(), Error> {
    // Calculate date range
    let end_date = Utc::now().format("%Y-%m-%d").to_string();

    // Use a 2-day window - CryptoSync's categorization will handle the rest:
    // - If CSV exists: categorize as "resume", fetch from last CSV date
    // - If CSV missing: categorize as "full history", fetch from BTC inception (2010-07-17)
    let start_date = (Utc::now() - chrono::Duration::days(2))
        .format("%Y-%m-%d")
        .to_string();

    // Create sync config for single interval (resume mode, not full)
    let config = SyncConfig {
        intervals: vec![interval],
        start_date,
        end_date,
        force_full: false, // Resume mode - let CryptoSync categorize
        ..Default::default()
    };

    // Run crypto sync
    let mut sync = CryptoSync::new(config, None)?;
    sync.sync_all_intervals(symbols).await?;

    Ok(())
}

/// Write compact log entry to crypto_worker.log
fn write_log_entry(
    start_time: &chrono::DateTime<Utc>,
    end_time: &chrono::DateTime<Utc>,
    duration_secs: i64,
    success: bool,
    interval: Interval,
    symbols: &[String],
    tier: &str,
) {
    let log_path = get_crypto_data_dir().join("crypto_worker.log");

    let status = if success { "OK" } else { "FAIL" };
    let interval_str = match interval {
        Interval::Hourly => "1H",
        Interval::Minute => "1m",
        Interval::Daily => "1D",
    };
    let log_line = format!(
        "{} | {} | {}s | {} | {} | {} | cryptos:{}\n",
        start_time.format("%Y-%m-%d %H:%M:%S"),
        end_time.format("%Y-%m-%d %H:%M:%S"),
        duration_secs,
        interval_str,
        tier,
        status,
        symbols.len()
    );

    // Use log rotation utility
    let _ = write_with_rotation(&log_path, &log_line);
}
