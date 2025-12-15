use crate::constants::IGNORED_CRYPTOS;
use crate::error::Error;
use crate::models::{Interval, SyncConfig, load_crypto_symbols, get_default_crypto_list_path};
use crate::services::{CryptoSync, SharedHealthStats, csv_enhancer};
use crate::services::data_store::{DataMode, DataUpdateMessage};
use crate::utils::{get_crypto_data_dir, write_with_rotation, get_concurrent_batches};
use crate::worker::crypto_sync_info::{CryptoSyncInfo, get_crypto_sync_info_path};
use chrono::Utc;
use std::time::Duration;
use tokio::time::sleep;
use tracing::{info, warn, error, instrument};

// Priority cryptos sync every 15 minutes (all intervals)
const PRIORITY_CRYPTOS: &[&str] = &["BTC", "ETH", "XRP"];

// CryptoCompare mode: staggered intervals to manage rate limits
const REGULAR_DAILY_SYNC_INTERVAL_SECS: u64 = 3600;   // 1 hour
const REGULAR_HOURLY_SYNC_INTERVAL_SECS: u64 = 10800; // 3 hours
const REGULAR_MINUTE_SYNC_INTERVAL_SECS: u64 = 21600; // 6 hours
const LOOP_CHECK_INTERVAL_SECS: u64 = 900; // 15 minutes

// ApiProxy mode: faster intervals (no rate limit from CryptoCompare)
const PROXY_SYNC_INTERVAL_SECS: u64 = 300; // 5 minutes for all intervals
const PROXY_LOOP_CHECK_INTERVAL_SECS: u64 = 300; // 5 minutes

#[instrument(skip(health_stats, channel_sender))]
pub async fn run(
    health_stats: SharedHealthStats,
    channel_sender: Option<std::sync::mpsc::Sender<DataUpdateMessage>>,
) {
    // Check crypto data source configuration
    let target_url = std::env::var("CRYPTO_WORKER_TARGET_URL").ok();
    let target_host = std::env::var("CRYPTO_WORKER_TARGET_HOST").ok();

    info!(
        "[CRYPTO] Starting crypto worker with two-tier sync strategy"
    );

    // Log data source configuration
    if let Some(ref url) = target_url {
        info!(
            "[CRYPTO]   - Data Source: Alternative API ({})",
            url
        );
        if let Some(ref host) = target_host {
            info!(
                "[CRYPTO]   - Host Header: {}",
                host
            );
        }
        info!(
            "[CRYPTO]   - Fallback: None (fail-fast, skip crypto on error)"
        );
    } else {
        info!(
            "[CRYPTO]   - Data Source: CryptoCompare API (default)"
        );
    }

    // Determine intervals based on mode
    let is_proxy_mode = target_url.is_some();
    let (daily_interval, hourly_interval, minute_interval, loop_interval) = if is_proxy_mode {
        (PROXY_SYNC_INTERVAL_SECS, PROXY_SYNC_INTERVAL_SECS, PROXY_SYNC_INTERVAL_SECS, PROXY_LOOP_CHECK_INTERVAL_SECS)
    } else {
        (REGULAR_DAILY_SYNC_INTERVAL_SECS, REGULAR_HOURLY_SYNC_INTERVAL_SECS, REGULAR_MINUTE_SYNC_INTERVAL_SECS, LOOP_CHECK_INTERVAL_SECS)
    };

    info!(
        "[CRYPTO]   - Priority cryptos: {} (every {}s)",
        PRIORITY_CRYPTOS.join(", "), loop_interval
    );
    if is_proxy_mode {
        info!("[CRYPTO]   - Regular cryptos: all intervals every {}s (proxy mode)", PROXY_SYNC_INTERVAL_SECS);
    } else {
        info!("[CRYPTO]   - Regular cryptos: Daily=1h, Hourly=3h, Minute=6h (CryptoCompare mode)");
    }
    info!(
        "[CRYPTO]   - Main loop interval: {}s",
        loop_interval
    );

    let crypto_data_dir = get_crypto_data_dir();
    let info_path = get_crypto_sync_info_path(&crypto_data_dir);

    // Load sync info from disk (persists across restarts)
    // For proxy mode, start fresh to sync immediately
    let mut sync_info = if is_proxy_mode {
        info!("[CRYPTO] Proxy mode: starting fresh sync info for immediate sync");
        CryptoSyncInfo::default()
    } else {
        CryptoSyncInfo::load(&info_path)
    };

    info!(
        "[CRYPTO] Loaded sync info: iteration={}, last_syncs: priority_daily={:?}, regular_daily={:?}",
        sync_info.iteration_count,
        sync_info.priority_daily_last_sync,
        sync_info.regular_daily_last_sync
    );

    info!("[CRYPTO] Crypto worker started");

    loop {
        sync_info.increment_iteration();
        let loop_start = std::time::Instant::now();

        info!(
            worker = "Crypto",
            iteration = sync_info.iteration_count,
            "[CRYPTO] Starting crypto sync cycle"
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
                        iteration = sync_info.iteration_count,
                        ignored_count = ignored_count,
                        ignored_symbols = ?IGNORED_CRYPTOS,
                        "[CRYPTO] Filtered ignored cryptos"
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
                    iteration = sync_info.iteration_count,
                    priority_count = priority.len(),
                    regular_count = regular.len(),
                    "[CRYPTO] Loaded and categorized crypto symbols"
                );

                (priority, regular)
            }
            Err(e) => {
                error!(
                    worker = "Crypto",
                    iteration = sync_info.iteration_count,
                    error = %e,
                    "[CRYPTO] Failed to load crypto symbols, skipping iteration"
                );
                sleep(Duration::from_secs(LOOP_CHECK_INTERVAL_SECS)).await;
                continue;
            }
        };

        let mut all_intervals_successful = true;

        // TIER 1: Sync priority cryptos (every 15 minutes, all intervals)
        if !priority_symbols.is_empty() {
            info!(
                worker = "Crypto",
                iteration = sync_info.iteration_count,
                "[CRYPTO] Checking priority cryptos: {}",
                PRIORITY_CRYPTOS.join(", ")
            );

            // Daily
            if sync_info.should_sync(sync_info.priority_daily_last_sync, loop_interval) {
                let success = sync_and_enhance(
                    Interval::Daily,
                    &priority_symbols,
                    sync_info.iteration_count,
                    "Priority",
                    &crypto_data_dir,
                    channel_sender.clone(),
                ).await;
                if success {
                    sync_info.update_priority_daily();
                } else {
                    all_intervals_successful = false;
                }
            }

            // Hourly
            if sync_info.should_sync(sync_info.priority_hourly_last_sync, loop_interval) {
                let success = sync_and_enhance(
                    Interval::Hourly,
                    &priority_symbols,
                    sync_info.iteration_count,
                    "Priority",
                    &crypto_data_dir,
                    channel_sender.clone(),
                ).await;
                if success {
                    sync_info.update_priority_hourly();
                } else {
                    all_intervals_successful = false;
                }
            }

            // Minute
            if sync_info.should_sync(sync_info.priority_minute_last_sync, loop_interval) {
                let success = sync_and_enhance(
                    Interval::Minute,
                    &priority_symbols,
                    sync_info.iteration_count,
                    "Priority",
                    &crypto_data_dir,
                    channel_sender.clone(),
                ).await;
                if success {
                    sync_info.update_priority_minute();
                } else {
                    all_intervals_successful = false;
                }
            }
        }

        // TIER 2: Sync regular cryptos based on per-interval timing
        if !regular_symbols.is_empty() {
            // Daily
            if sync_info.should_sync(sync_info.regular_daily_last_sync, daily_interval) {
                info!(
                    worker = "Crypto",
                    iteration = sync_info.iteration_count,
                    "[CRYPTO] Syncing regular cryptos: Daily"
                );

                let success = sync_and_enhance(
                    Interval::Daily,
                    &regular_symbols,
                    sync_info.iteration_count,
                    "Regular",
                    &crypto_data_dir,
                    channel_sender.clone(),
                ).await;

                if success {
                    sync_info.update_regular_daily();
                } else {
                    all_intervals_successful = false;
                }
            }

            // Hourly
            if sync_info.should_sync(sync_info.regular_hourly_last_sync, hourly_interval) {
                info!(
                    worker = "Crypto",
                    iteration = sync_info.iteration_count,
                    "[CRYPTO] Syncing regular cryptos: Hourly"
                );

                let success = sync_and_enhance(
                    Interval::Hourly,
                    &regular_symbols,
                    sync_info.iteration_count,
                    "Regular",
                    &crypto_data_dir,
                    channel_sender.clone(),
                ).await;

                if success {
                    sync_info.update_regular_hourly();
                } else {
                    all_intervals_successful = false;
                }
            }

            // Minute
            if sync_info.should_sync(sync_info.regular_minute_last_sync, minute_interval) {
                info!(
                    worker = "Crypto",
                    iteration = sync_info.iteration_count,
                    "[CRYPTO] Syncing regular cryptos: Minute"
                );

                let success = sync_and_enhance(
                    Interval::Minute,
                    &regular_symbols,
                    sync_info.iteration_count,
                    "Regular",
                    &crypto_data_dir,
                    channel_sender.clone(),
                ).await;

                if success {
                    sync_info.update_regular_minute();
                } else {
                    all_intervals_successful = false;
                }
            }
        }

        // Step 3: Save sync info to disk (persists across restarts)
        if let Err(e) = sync_info.save(&info_path) {
            warn!(
                worker = "Crypto",
                iteration = sync_info.iteration_count,
                error = %e,
                "[CRYPTO] Failed to save sync info to disk"
            );
        }

        // Step 4: Update health stats
        {
            let mut health = health_stats.write().await;
            health.crypto_last_sync = Some(Utc::now().to_rfc3339());
            health.crypto_iteration_count = sync_info.iteration_count;
        }

        let loop_duration = loop_start.elapsed();

        info!(
            worker = "Crypto",
            iteration = sync_info.iteration_count,
            loop_duration_secs = loop_duration.as_secs_f64(),
            next_check_secs = loop_interval,
            all_successful = all_intervals_successful,
            "[CRYPTO] Iteration completed, sync info saved to disk"
        );

        sleep(Duration::from_secs(loop_interval)).await;
    }
}

/// Sync and enhance a single interval for given symbols
async fn sync_and_enhance(
    interval: Interval,
    symbols: &[String],
    iteration: u64,
    tier: &str,
    crypto_data_dir: &std::path::PathBuf,
    channel_sender: Option<std::sync::mpsc::Sender<DataUpdateMessage>>,
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
        "[CRYPTO] Starting interval sync"
    );

    // Step 0: Pre-check (only in ApiProxy mode)
    // Check if data has changed before doing expensive sync operation
    use crate::services::CryptoFetcher;
    match CryptoFetcher::new(None) {
        Ok(mut fetcher) => {
            match fetcher.pre_check_interval_unchanged(symbols, interval).await {
                Ok(true) => {
                    info!(
                        worker = "Crypto",
                        iteration = iteration,
                        tier = tier,
                        interval = interval_name,
                        crypto_count = symbols.len(),
                        "[CRYPTO] Pre-check: all cryptos unchanged, skipping sync and enhancement"
                    );
                    return true; // Skip sync, return success
                }
                Ok(false) => {
                    info!(
                        worker = "Crypto",
                        iteration = iteration,
                        tier = tier,
                        interval = interval_name,
                        "[CRYPTO] Pre-check: data changed or not applicable, proceeding with sync"
                    );
                }
                Err(e) => {
                    warn!(
                        worker = "Crypto",
                        iteration = iteration,
                        tier = tier,
                        interval = interval_name,
                        error = %e,
                        "[CRYPTO] Pre-check failed, proceeding with sync anyway"
                    );
                }
            }
        }
        Err(e) => {
            warn!(
                worker = "Crypto",
                iteration = iteration,
                tier = tier,
                interval = interval_name,
                error = %e,
                "[CRYPTO] Failed to create fetcher for pre-check, proceeding with sync anyway"
            );
        }
    }

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
                "[CRYPTO] Sync completed"
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
                "[CRYPTO] Sync failed"
            );
            false
        }
    };

    // Write log entry
    write_log_entry(&sync_start, &sync_end, sync_duration, sync_success, interval, symbols, tier);

    // Step 2: Enhance CSV if sync succeeded (ONLY enhance the symbols that were synced)
    if sync_success {
        info!(
            worker = "Crypto",
            iteration = iteration,
            tier = tier,
            interval = interval_name,
            "[CRYPTO] Enhancing CSV (filtered to {} symbols)",
            symbols.len()
        );

        match csv_enhancer::enhance_interval_filtered(interval, crypto_data_dir, Some(symbols), channel_sender.as_ref(), DataMode::Crypto) {
            Ok(stats) => {
                info!(
                    worker = "Crypto",
                    iteration = iteration,
                    tier = tier,
                    interval = interval_name,
                    tickers = stats.tickers,
                    records = stats.records,
                    duration_secs = stats.duration.as_secs_f64(),
                    "[CRYPTO] Enhancement completed"
                );
            }
            Err(e) => {
                warn!(
                    worker = "Crypto",
                    iteration = iteration,
                    tier = tier,
                    interval = interval_name,
                    error = %e,
                    "[CRYPTO] Enhancement failed"
                );
            }
        }
    }

    sync_success
}

/// Get adaptive resume date for crypto sync (like stock sync)
fn get_adaptive_crypto_resume_date(
    symbols: &[String],
    interval: Interval
) -> Result<String, Error> {
    use crate::services::CryptoFetcher;

    // Create fetcher to read CSV dates
    let fetcher = CryptoFetcher::new(None)?;

    // Categorize cryptos to read last CSV dates
    let category = fetcher.categorize_cryptos(symbols, interval, false)?;

    // Find minimum last date across resume and partial cryptos
    let mut last_dates = Vec::new();

    // Collect last dates from resume cryptos
    for (_, last_date) in &category.resume_cryptos {
        last_dates.push(last_date.clone());
    }

    // Collect last dates from partial history cryptos
    for (_, last_date) in &category.partial_history_cryptos {
        last_dates.push(last_date.clone());
    }

    // Return minimum date, or fallback to 2 days ago
    if let Some(min_date) = last_dates.iter().min() {
        Ok(min_date.clone())
    } else {
        // No existing CSV data - use 2 days ago as fallback
        Ok((Utc::now() - chrono::Duration::days(2))
            .format("%Y-%m-%d")
            .to_string())
    }
}

/// Sync a single interval for all cryptocurrencies using CryptoSync
async fn sync_crypto_interval(interval: Interval, symbols: &[String]) -> Result<(), Error> {
    // Calculate date range
    let end_date = Utc::now().format("%Y-%m-%d").to_string();

    // Use adaptive resume date like stock sync - CryptoSync's categorization will handle the rest:
    // - If CSV exists: categorize as "resume", fetch from actual last CSV date
    // - If CSV missing: categorize as "full history", fetch from BTC inception (2010-07-17)
    let start_date = get_adaptive_crypto_resume_date(symbols, interval)
        .unwrap_or_else(|_| {
            // Fallback to 2 days ago if categorization fails
            (Utc::now() - chrono::Duration::days(2))
                .format("%Y-%m-%d")
                .to_string()
        });

    // Create sync config for single interval (resume mode, not full)
    let concurrent_batches = get_concurrent_batches();
    let config = SyncConfig {
        intervals: vec![interval],
        start_date,
        end_date,
        force_full: false, // Resume mode - let CryptoSync categorize
        concurrent_batches, // Auto-detected based on CPU cores
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
