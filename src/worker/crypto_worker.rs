use crate::constants::IGNORED_CRYPTOS;
use crate::error::Error;
use crate::models::{Interval, SyncConfig, load_crypto_symbols, get_default_crypto_list_path};
use crate::services::{CryptoSync, SharedHealthStats, csv_enhancer};
use crate::services::mpsc::TickerUpdate;
use crate::utils::{get_crypto_data_dir, write_with_rotation, get_concurrent_batches};
use crate::worker::crypto_sync_info::{CryptoSyncInfo, get_crypto_sync_info_path};
use chrono::Utc;
use std::sync::mpsc::SyncSender;
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

#[instrument(skip(health_stats))]
pub async fn run(health_stats: SharedHealthStats) {
    // Check crypto data source configuration
    let target_url = std::env::var("CRYPTO_WORKER_TARGET_URL").ok();
    let target_host = std::env::var("CRYPTO_WORKER_TARGET_HOST").ok();

    info!(
        "Starting crypto worker with two-tier sync strategy"
    );

    // Log data source configuration
    if let Some(ref url) = target_url {
        info!(
            "  - Data Source: Alternative API ({})",
            url
        );
        if let Some(ref host) = target_host {
            info!(
                "  - Host Header: {}",
                host
            );
        }
        info!(
            "  - Fallback: None (fail-fast, skip crypto on error)"
        );
    } else {
        info!(
            "  - Data Source: CryptoCompare API (default)"
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
        "  - Priority cryptos: {} (every {}s)",
        PRIORITY_CRYPTOS.join(", "), loop_interval
    );
    if is_proxy_mode {
        info!("  - Regular cryptos: all intervals every {}s (proxy mode)", PROXY_SYNC_INTERVAL_SECS);
    } else {
        info!("  - Regular cryptos: Daily=1h, Hourly=3h, Minute=6h (CryptoCompare mode)");
    }
    info!(
        "  - Main loop interval: {}s",
        loop_interval
    );

    let crypto_data_dir = get_crypto_data_dir();
    let info_path = get_crypto_sync_info_path(&crypto_data_dir);

    // Load sync info from disk (persists across restarts)
    // For proxy mode, start fresh to sync immediately
    let mut sync_info = if is_proxy_mode {
        info!("Proxy mode: starting fresh sync info for immediate sync");
        CryptoSyncInfo::default()
    } else {
        CryptoSyncInfo::load(&info_path)
    };

    info!(
        "Loaded sync info: iteration={}, last_syncs: priority_daily={:?}, regular_daily={:?}",
        sync_info.iteration_count,
        sync_info.priority_daily_last_sync,
        sync_info.regular_daily_last_sync
    );

    info!("Crypto worker started");

    loop {
        sync_info.increment_iteration();
        let loop_start = std::time::Instant::now();

        info!(
            worker = "Crypto",
            iteration = sync_info.iteration_count,
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
                        iteration = sync_info.iteration_count,
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
                    iteration = sync_info.iteration_count,
                    priority_count = priority.len(),
                    regular_count = regular.len(),
                    "Loaded and categorized crypto symbols"
                );

                (priority, regular)
            }
            Err(e) => {
                error!(
                    worker = "Crypto",
                    iteration = sync_info.iteration_count,
                    error = %e,
                    "Failed to load crypto symbols, skipping iteration"
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
                "Checking priority cryptos: {}",
                PRIORITY_CRYPTOS.join(", ")
            );

            // Daily
            if sync_info.should_sync(sync_info.priority_daily_last_sync, loop_interval) {
                match sync_and_enhance(
                    Interval::Daily,
                    &priority_symbols,
                    sync_info.iteration_count,
                    "Priority",
                    &crypto_data_dir,
                ).await {
                    Ok(true) => {
                        sync_info.update_priority_daily();
                    }
                    Ok(false) => {
                        all_intervals_successful = false;
                    }
                    Err(crate::error::Error::RateLimit) => {
                        error!("Rate limit hit - aborting current sync cycle and waiting for next interval");
                        // Skip all remaining intervals and wait for next main loop
                        continue;
                    }
                    Err(e) => {
                        error!("Unexpected error in daily sync: {}", e);
                        all_intervals_successful = false;
                    }
                }
            }

            // Hourly
            if sync_info.should_sync(sync_info.priority_hourly_last_sync, loop_interval) {
                match sync_and_enhance(
                    Interval::Hourly,
                    &priority_symbols,
                    sync_info.iteration_count,
                    "Priority",
                    &crypto_data_dir,
                ).await {
                    Ok(true) => {
                        sync_info.update_priority_hourly();
                    }
                    Ok(false) => {
                        all_intervals_successful = false;
                    }
                    Err(crate::error::Error::RateLimit) => {
                        error!("Rate limit hit - aborting current sync cycle and waiting for next interval");
                        // Skip all remaining intervals and wait for next main loop
                        continue;
                    }
                    Err(e) => {
                        error!("Unexpected error in hourly sync: {}", e);
                        all_intervals_successful = false;
                    }
                }
            }

            // Minute
            if sync_info.should_sync(sync_info.priority_minute_last_sync, loop_interval) {
                match sync_and_enhance(
                    Interval::Minute,
                    &priority_symbols,
                    sync_info.iteration_count,
                    "Priority",
                    &crypto_data_dir,
                ).await {
                    Ok(true) => {
                        sync_info.update_priority_minute();
                    }
                    Ok(false) => {
                        all_intervals_successful = false;
                    }
                    Err(crate::error::Error::RateLimit) => {
                        error!("Rate limit hit - aborting current sync cycle and waiting for next interval");
                        // Skip all remaining intervals and wait for next main loop
                        continue;
                    }
                    Err(e) => {
                        error!("Unexpected error in minute sync: {}", e);
                        all_intervals_successful = false;
                    }
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
                    "Syncing regular cryptos: Daily"
                );

                match sync_and_enhance(
                    Interval::Daily,
                    &regular_symbols,
                    sync_info.iteration_count,
                    "Regular",
                    &crypto_data_dir,
                ).await {
                    Ok(true) => {
                        sync_info.update_regular_daily();
                    }
                    Ok(false) => {
                        all_intervals_successful = false;
                    }
                    Err(crate::error::Error::RateLimit) => {
                        error!("Rate limit hit - aborting current sync cycle and waiting for next interval");
                        // Skip all remaining intervals and wait for next main loop
                        continue;
                    }
                    Err(e) => {
                        error!("Unexpected error in regular daily sync: {}", e);
                        all_intervals_successful = false;
                    }
                }
            }

            // Hourly
            if sync_info.should_sync(sync_info.regular_hourly_last_sync, hourly_interval) {
                info!(
                    worker = "Crypto",
                    iteration = sync_info.iteration_count,
                    "Syncing regular cryptos: Hourly"
                );

                match sync_and_enhance(
                    Interval::Hourly,
                    &regular_symbols,
                    sync_info.iteration_count,
                    "Regular",
                    &crypto_data_dir,
                ).await {
                    Ok(true) => {
                        sync_info.update_regular_hourly();
                    }
                    Ok(false) => {
                        all_intervals_successful = false;
                    }
                    Err(crate::error::Error::RateLimit) => {
                        error!("Rate limit hit - aborting current sync cycle and waiting for next interval");
                        // Skip all remaining intervals and wait for next main loop
                        continue;
                    }
                    Err(e) => {
                        error!("Unexpected error in regular hourly sync: {}", e);
                        all_intervals_successful = false;
                    }
                }
            }

            // Minute
            if sync_info.should_sync(sync_info.regular_minute_last_sync, minute_interval) {
                info!(
                    worker = "Crypto",
                    iteration = sync_info.iteration_count,
                    "Syncing regular cryptos: Minute"
                );

                match sync_and_enhance(
                    Interval::Minute,
                    &regular_symbols,
                    sync_info.iteration_count,
                    "Regular",
                    &crypto_data_dir,
                ).await {
                    Ok(true) => {
                        sync_info.update_regular_minute();
                    }
                    Ok(false) => {
                        all_intervals_successful = false;
                    }
                    Err(crate::error::Error::RateLimit) => {
                        error!("Rate limit hit - aborting current sync cycle and waiting for next interval");
                        // Skip all remaining intervals and wait for next main loop
                        continue;
                    }
                    Err(e) => {
                        error!("Unexpected error in regular minute sync: {}", e);
                        all_intervals_successful = false;
                    }
                }
            }
        }

        // Step 3: Save sync info to disk (persists across restarts)
        if let Err(e) = sync_info.save(&info_path) {
            warn!(
                worker = "Crypto",
                iteration = sync_info.iteration_count,
                error = %e,
                "Failed to save sync info to disk"
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
            "Iteration completed, sync info saved to disk"
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
) -> Result<bool, Error> {
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
                        "Pre-check: all cryptos unchanged, skipping sync and enhancement"
                    );
                    return Ok(true); // Skip sync, return success
                }
                Ok(false) => {
                    info!(
                        worker = "Crypto",
                        iteration = iteration,
                        tier = tier,
                        interval = interval_name,
                        "Pre-check: data changed or not applicable, proceeding with sync"
                    );
                }
                Err(e) => {
                    warn!(
                        worker = "Crypto",
                        iteration = iteration,
                        tier = tier,
                        interval = interval_name,
                        error = %e,
                        "Pre-check failed, proceeding with sync anyway"
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
                "Failed to create fetcher for pre-check, proceeding with sync anyway"
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

            // Re-return rate limit errors so the main loop can handle them
            if matches!(e, crate::error::Error::RateLimit) {
                return Err(e);
            }

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
            "Enhancing CSV (filtered to {} symbols)",
            symbols.len()
        );

        match csv_enhancer::enhance_interval_filtered(interval, crypto_data_dir, Some(symbols)) {
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

      Ok(sync_success)
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

/// Run crypto worker with MPSC channel support for real-time updates
#[instrument(skip(health_stats, channel_sender))]
pub async fn run_with_channel(
    health_stats: SharedHealthStats,
    channel_sender: Option<SyncSender<TickerUpdate>>,
) {
    // Check crypto data source configuration
    let target_url = std::env::var("CRYPTO_WORKER_TARGET_URL").ok();
    let target_host = std::env::var("CRYPTO_WORKER_TARGET_HOST").ok();
    let use_proxy = target_url.is_some();

    let (sync_interval_secs, loop_check_interval_secs) = if use_proxy {
        (PROXY_SYNC_INTERVAL_SECS, PROXY_LOOP_CHECK_INTERVAL_SECS)
    } else {
        (LOOP_CHECK_INTERVAL_SECS, LOOP_CHECK_INTERVAL_SECS) // CryptoCompare mode: single interval
    };

    info!(
        "Starting crypto worker with MPSC channel and two-tier sync strategy"
    );
    if use_proxy {
        info!("  Data Source: ApiProxy mode (URL: {})", target_url.as_ref().unwrap_or(&String::new()));
        info!("  Sync interval: {}s (all intervals)", sync_interval_secs);
        info!("  Priority cryptos: {:?} (every {}s)", PRIORITY_CRYPTOS, sync_interval_secs);
    } else {
        info!("  Data Source: CryptoCompare API (default)");
        info!("  Main loop interval: {}s", loop_check_interval_secs);
        info!("  Priority cryptos: {:?} (every {}s)", PRIORITY_CRYPTOS, loop_check_interval_secs);
        info!("  Regular cryptos: Daily=1h, Hourly=3h, Minute=6h (CryptoCompare mode)");
    }

    // Setup sync info file path
    let crypto_data_dir = get_crypto_data_dir();
    let info_path = get_crypto_sync_info_path(&crypto_data_dir);

    // Load crypto symbols
    let symbols = match load_crypto_symbols(&get_default_crypto_list_path()) {
        Ok(symbols) => {
            let original_count = symbols.len();
            let filtered: Vec<String> = symbols
                .into_iter()
                .filter(|s| !IGNORED_CRYPTOS.contains(&s.as_str()))
                .collect();
            info!("Loaded {} crypto symbols ({} filtered out)", filtered.len(), original_count - filtered.len());
            filtered
        }
        Err(e) => {
            error!("Failed to load crypto symbols: {}", e);
            // Use default list as fallback
            PRIORITY_CRYPTOS.iter().map(|s| s.to_string()).collect()
        }
    };

    // Create initial crypto sync info
    let _initial_sync_info = CryptoSyncInfo::default();
    if let Err(e) = _initial_sync_info.save(&info_path) {
        error!("Failed to write initial crypto sync info: {}", e);
    }

    // Initial delay before first sync (4 minutes)
    info!("[SYNC::CRYPTO] Initial delay: waiting 240 seconds before first sync...");
    tokio::time::sleep(tokio::time::Duration::from_secs(240)).await;

    loop {
        let loop_start = std::time::Instant::now();
        let mut rate_limit_hit = false;

        // Priority cryptos - sync all intervals every sync_interval_secs
        let priority_result = 'priority_loop: {
            for symbol in PRIORITY_CRYPTOS {
                if !symbols.contains(&symbol.to_string()) {
                    continue;
                }

                for interval in &[Interval::Daily, Interval::Hourly, Interval::Minute] {
                    // Sleep longer between intervals to allow VN workers to get CPU time
                    tokio::time::sleep(tokio::time::Duration::from_millis(200)).await;

                    let interval_start = Utc::now();
                    let symbol_str = symbol.to_string(); // Move inside loop

                    info!(
                        symbol = symbol_str,
                        interval = ?interval,
                        tier = "Priority",
                        "Starting sync"
                    );

                    match run_sync_with_channel(
                        &symbol_str,
                        *interval,
                        &health_stats,
                        channel_sender.as_ref(),
                        use_proxy,
                        &target_url,
                        &target_host,
                    ).await {
                        Ok(_) => {
                            let interval_end = Utc::now();
                let duration = interval_end.signed_duration_since(interval_start);
                            tracing::info!(
                                symbol = symbol_str,
                                interval = ?interval,
                                tier = "Priority",
                                duration_ms = duration.num_milliseconds(),
                                "Priority sync completed successfully"
                            );

                            // Write log entry
                            write_log_entry(
                                &interval_start,
                                &interval_end,
                                duration.num_seconds(),
                                true,
                                *interval,
                                &[symbol_str],
                                "Priority"
                            );
                        }
                        Err(crate::error::Error::RateLimit) => {
                            error!("Rate limit hit in priority sync - breaking priority loop");
                            rate_limit_hit = true;
                            break 'priority_loop Err(crate::error::Error::RateLimit);
                        }
                        Err(e) => {
                            let interval_end = Utc::now();
                let duration = interval_end.signed_duration_since(interval_start);
                            tracing::error!(
                                symbol = symbol_str,
                                interval = ?interval,
                                tier = "Priority",
                                error = %e,
                                duration_ms = duration.num_milliseconds(),
                                "Priority sync failed"
                            );
                        }
                    }
                }
            }
            Ok(())
        };

        // If rate limit was hit during priority sync, skip to sleep
        if rate_limit_hit {
            error!("Rate limit hit during priority sync - skipping regular sync and waiting for next interval");
        } else {

        // Regular cryptos - staggered sync based on CryptoCompare rate limits
        if !use_proxy {
            // CryptoCompare mode: stagger regular cryptos to manage rate limits
            let intervals = &[Interval::Daily, Interval::Hourly, Interval::Minute];
            'regular_intervals: for (_i, &interval) in intervals.iter().enumerate() {
                let interval_symbols = symbols.iter()
                    .filter(|s| !PRIORITY_CRYPTOS.contains(&s.as_str()))
                    .collect::<Vec<_>>();

                if interval_symbols.is_empty() {
                    continue;
                }

                let chunk_size = CryptoSync::new(SyncConfig::default(), None).unwrap()
                    .get_chunk_size_for_interval(interval);

                'chunks: for chunk in interval_symbols.chunks(chunk_size) {
                    // Sleep longer to allow VN workers to get CPU time
                    tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;

                    let chunk_start = std::time::Instant::now();

                    for symbol in chunk {
                        // Longer delay between symbols to allow other tasks to run
                        tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;

                        match run_sync_with_channel(
                            symbol,
                            interval,
                            &health_stats,
                            channel_sender.as_ref(),
                            use_proxy,
                            &target_url,
                            &target_host,
                        ).await {
                            Ok(_) => {
                                tracing::info!(
                                    symbol = symbol,
                                    interval = ?interval,
                                    tier = "Regular",
                                    "Regular sync completed"
                                );
                            }
                            Err(crate::error::Error::RateLimit) => {
                                error!("Rate limit hit in regular sync - breaking all loops and waiting for next interval");
                                rate_limit_hit = true;
                                break 'regular_intervals;
                            }
                            Err(e) => {
                                tracing::error!(
                                    symbol = symbol,
                                    interval = ?interval,
                                    tier = "Regular",
                                    error = %e,
                                    "Regular sync failed"
                                );
                            }
                        }
                    }

                    let chunk_duration = chunk_start.elapsed();
                    tracing::info!(
                        interval = ?interval,
                        tier = "Regular",
                        chunk_size = chunk.len(),
                        duration_ms = chunk_duration.as_millis(),
                        "Completed regular sync chunk"
                    );
                }
            }
        } else {
            // ApiProxy mode: sync all regular cryptos at once
            let regular_symbols: Vec<String> = symbols
                .iter()
                .filter(|s| !PRIORITY_CRYPTOS.contains(&s.as_str()))
                .cloned()
                .collect();

            if !regular_symbols.is_empty() {
                let regular_start = std::time::Instant::now();

                match run_sync_batch_with_channel(
                    &regular_symbols,
                    &health_stats,
                    channel_sender.as_ref(),
                    target_url.as_deref().unwrap_or(""),
                    &target_host,
                ).await {
                    Ok(_) => {
                        let duration = regular_start.elapsed();
                        info!(
                            regular_count = regular_symbols.len(),
                            duration_ms = duration.as_millis(),
                            "Regular batch sync completed"
                        );
                    }
                    Err(e) => {
                        error!(
                            regular_count = regular_symbols.len(),
                            error = %e,
                            "Regular batch sync failed"
                        );
                    }
                }
            }
        }
        } // Close the else block for rate_limit_hit check

        // Update health stats
        {
            let mut health = health_stats.write().await;
            health.crypto_last_sync = Some(Utc::now().to_rfc3339());
            health.crypto_iteration_count += 1;
        }

        // Update sync info
        let mut sync_info = CryptoSyncInfo::load(&info_path);
        sync_info.increment_iteration();
        sync_info.update_priority_daily();
        sync_info.update_priority_hourly();
        sync_info.update_priority_minute();
        if let Err(e) = sync_info.save(&info_path) {
            error!("Failed to update crypto sync info: {}", e);
        }

        // Log overall loop completion
        let loop_duration = loop_start.elapsed();
        info!(
            loop_duration_ms = loop_duration.as_millis(),
            "Crypto sync loop completed, sleeping for {}s",
            sync_interval_secs
        );

        // Sleep until next iteration
        sleep(Duration::from_secs(sync_interval_secs)).await;
    }
}

/// Helper function to run sync for a single crypto symbol with channel support
async fn run_sync_with_channel(
    symbol: &str,
    interval: Interval,
    _health_stats: &SharedHealthStats,
    channel_sender: Option<&SyncSender<TickerUpdate>>,
    use_proxy: bool,
    _target_url: &Option<String>,
    _target_host: &Option<String>,
) -> Result<(), Error> {
    let concurrent_batches = if use_proxy { 1 } else { get_concurrent_batches() };

    let config = SyncConfig {
        start_date: (Utc::now() - chrono::Days::new(30)).format("%Y-%m-%d").to_string(), // 30 days for crypto
        end_date: Utc::now().format("%Y-%m-%d").to_string(),
        batch_size: if use_proxy { 100 } else { 20 }, // Larger batches for proxy mode
        resume_days: Some(7), // 7 days for crypto
        intervals: vec![interval],
        force_full: false,
        concurrent_batches,
    };

    // Create CryptoSync with channel support
    let mut sync = if use_proxy {
        // ApiProxy mode: Use the provided URL to fetch data
        CryptoSync::new_with_channel(config, None, channel_sender.cloned())
    } else {
        // CryptoCompare mode: Use API key from environment
        let api_key = std::env::var("CRYPTOCOMPARE_API_KEY").ok();
        CryptoSync::new_with_channel(config, api_key, channel_sender.cloned())
    }?;

    // For now, use standard sync regardless of proxy mode
    // TODO: Implement proxy-specific sync logic if needed
    sync.sync_all_intervals(&[symbol.to_string()]).await
}

/// Helper function to sync a batch of cryptos via proxy with channel support
async fn run_sync_batch_with_channel(
    symbols: &[String],
    _health_stats: &SharedHealthStats,
    channel_sender: Option<&SyncSender<TickerUpdate>>,
    _target_url: &str,
    _target_host: &Option<String>,
) -> Result<(), Error> {
    let config = SyncConfig {
        start_date: (Utc::now() - chrono::Days::new(30)).format("%Y-%m-%d").to_string(),
        end_date: Utc::now().format("%Y-%m-%d").to_string(),
        batch_size: 100,
        resume_days: Some(7),
        intervals: vec![Interval::Daily, Interval::Hourly, Interval::Minute],
        force_full: false,
        concurrent_batches: 3,
    };

    let mut sync = CryptoSync::new_with_channel(config, None, channel_sender.cloned())?;
    // For now, use standard sync regardless of proxy mode
    // TODO: Implement proxy-specific sync logic if needed
    sync.sync_all_intervals(symbols).await
}
