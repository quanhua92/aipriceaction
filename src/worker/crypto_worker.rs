use crate::constants::IGNORED_CRYPTOS;
use crate::error::Error;
use crate::models::{Interval, SyncConfig, load_crypto_symbols, get_default_crypto_list_path};
use crate::services::{CryptoSync, SharedHealthStats, csv_enhancer};
use crate::utils::{get_crypto_data_dir, write_with_rotation};
use chrono::Utc;
use std::time::Duration;
use tokio::time::sleep;
use tracing::{info, warn, error, instrument};

// Crypto markets are 24/7, no trading hours concept
// Sync all intervals every 15 minutes
const CRYPTO_SYNC_INTERVAL_SECS: u64 = 900; // 15 minutes

#[instrument(skip(health_stats))]
pub async fn run(health_stats: SharedHealthStats) {
    info!(
        "Starting crypto worker with sequential interval sync"
    );
    info!(
        "  - Sync interval: {}s (15 minutes)",
        CRYPTO_SYNC_INTERVAL_SECS
    );
    info!(
        "  - Intervals: Daily → Hourly → Minute (sequential)"
    );

    let mut iteration_count = 0u64;
    let crypto_data_dir = get_crypto_data_dir();

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
        let crypto_symbols = match load_crypto_symbols(get_default_crypto_list_path()) {
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

                info!(
                    worker = "Crypto",
                    iteration = iteration_count,
                    crypto_count = symbols.len(),
                    "Loaded crypto symbols"
                );

                symbols
            }
            Err(e) => {
                error!(
                    worker = "Crypto",
                    iteration = iteration_count,
                    error = %e,
                    "Failed to load crypto symbols, skipping iteration"
                );
                sleep(Duration::from_secs(CRYPTO_SYNC_INTERVAL_SECS)).await;
                continue;
            }
        };

        // Sync all three intervals sequentially: Daily → Hourly → Minute
        let intervals = vec![Interval::Daily, Interval::Hourly, Interval::Minute];
        let mut all_intervals_successful = true;

        for interval in intervals {
            let interval_name = match interval {
                Interval::Daily => "Daily",
                Interval::Hourly => "Hourly",
                Interval::Minute => "Minute",
            };

            info!(
                worker = "Crypto",
                iteration = iteration_count,
                interval = interval_name,
                "Starting interval sync"
            );

            // Step 1: Sync this interval for all cryptos
            let sync_start = Utc::now();
            let sync_result = sync_crypto_interval(interval, &crypto_symbols).await;
            let sync_end = Utc::now();
            let sync_duration = (sync_end - sync_start).num_seconds();

            let sync_success = match sync_result {
                Ok(_) => {
                    info!(
                        worker = "Crypto",
                        iteration = iteration_count,
                        interval = interval_name,
                        duration_secs = sync_duration,
                        "Sync completed"
                    );
                    true
                }
                Err(e) => {
                    error!(
                        worker = "Crypto",
                        iteration = iteration_count,
                        interval = interval_name,
                        error = %e,
                        "Sync failed"
                    );
                    all_intervals_successful = false;
                    false
                }
            };

            // Write log entry for this interval
            write_log_entry(&sync_start, &sync_end, sync_duration, sync_success, interval, &crypto_symbols);

            // Step 2: Enhance CSV files for this interval
            if sync_success {
                info!(
                    worker = "Crypto",
                    iteration = iteration_count,
                    interval = interval_name,
                    "Enhancing CSV"
                );

                match csv_enhancer::enhance_interval(interval, &crypto_data_dir) {
                    Ok(stats) => {
                        info!(
                            worker = "Crypto",
                            iteration = iteration_count,
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
                            iteration = iteration_count,
                            interval = interval_name,
                            error = %e,
                            "Enhancement failed"
                        );
                    }
                }
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
            next_sync_secs = CRYPTO_SYNC_INTERVAL_SECS,
            all_successful = all_intervals_successful,
            "Iteration completed"
        );

        // Sleep for 15 minutes before next cycle
        sleep(Duration::from_secs(CRYPTO_SYNC_INTERVAL_SECS)).await;
    }
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
) {
    let log_path = get_crypto_data_dir().join("crypto_worker.log");

    let status = if success { "OK" } else { "FAIL" };
    let interval_str = match interval {
        Interval::Hourly => "1H",
        Interval::Minute => "1m",
        Interval::Daily => "1D",
    };
    let log_line = format!(
        "{} | {} | {}s | {} | {} | cryptos:{}\n",
        start_time.format("%Y-%m-%d %H:%M:%S"),
        end_time.format("%Y-%m-%d %H:%M:%S"),
        duration_secs,
        interval_str,
        status,
        symbols.len()
    );

    // Use log rotation utility
    let _ = write_with_rotation(&log_path, &log_line);
}
