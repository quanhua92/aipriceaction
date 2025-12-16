use crate::error::Error;
use crate::models::{Interval, SyncConfig};
use crate::models::sync_config::SyncStats;
use crate::services::{DataSync, SharedHealthStats, csv_enhancer, validate_and_repair_interval, is_trading_hours};
use crate::services::mpsc::TickerUpdate;
use crate::utils::{get_market_data_dir, write_with_rotation, get_concurrent_batches};
use chrono::Utc;
use std::path::Path;
use std::sync::mpsc::SyncSender;
use std::time::Duration;
use tokio::time::sleep;
use tracing::{info, warn, error, instrument};

// Hourly sync intervals
const HOURLY_TRADING_INTERVAL_SECS: u64 = 60; // 1 minute (trading hours)
const HOURLY_NON_TRADING_INTERVAL_SECS: u64 = 1800; // 30 minutes (off hours)

// Minute sync intervals
const MINUTE_TRADING_INTERVAL_SECS: u64 = 300; // 5 minutes (trading hours)
const MINUTE_NON_TRADING_INTERVAL_SECS: u64 = 1800; // 30 minutes (off hours)

// NOTE: Legacy run() and run_interval_worker() functions removed since serve.rs uses run_with_channel()
// and hour/minute workers now run in separate runtimes via run_hourly_worker_separate() and run_minute_worker_separate()

/// Run hourly worker in separate runtime to avoid API overload
#[instrument(skip(health_stats, channel_sender))]
pub async fn run_hourly_worker_separate(
    health_stats: SharedHealthStats,
    channel_sender: Option<SyncSender<TickerUpdate>>,
) {
    println!("[SYNC::HOURLY] === STARTING HOURLY WORKER IN SEPARATE RUNTIME ===");
    info!("Starting hourly worker in separate runtime to avoid API overload");
    println!("[SYNC::HOURLY] Channel sender exists: {}", channel_sender.is_some());

    let mut iteration = 0;
    loop {
        iteration += 1;
        println!("[SYNC::HOURLY] === HOURLY ITERATION {} STARTING ===", iteration);

        let is_trading = is_trading_hours();
        let sleep_secs = if is_trading {
            HOURLY_TRADING_INTERVAL_SECS
        } else {
            HOURLY_NON_TRADING_INTERVAL_SECS
        };

        println!("[SYNC::HOURLY] Trading hours: {}, sleep will be {}s", is_trading, sleep_secs);
        info!(
            interval = "Hourly",
            trading_hours = if is_trading { "ACTIVE" } else { "CLOSED" },
            "Hourly worker sync started"
        );

        // Perform hourly sync
        let start_time = Utc::now();
        let sync_result = run_sync_with_channel(
            Interval::Hourly,
            &health_stats,
            channel_sender.as_ref(),
        ).await;

        let end_time = Utc::now();
        let duration = end_time.signed_duration_since(start_time);
        let stats = sync_result.unwrap_or_else(|e| {
            println!("[SYNC::HOURLY] Hourly sync failed with error: {}", e);
            error!(error = %e, "Hourly sync failed");
            SyncStats::new()
        });

        write_log_entry(&start_time, &end_time, duration.num_seconds(), &stats, true, Interval::Hourly);

        println!("[SYNC::HOURLY] === HOURLY ITERATION {} COMPLETED ===", iteration);
        println!("[SYNC::HOURLY] Sleeping for {} seconds...", sleep_secs);
        // Sleep until next iteration
        sleep(Duration::from_secs(sleep_secs)).await;
        println!("[SYNC::HOURLY] Woke up from sleep");
    }
}

/// Perform sync for a single iteration
async fn perform_sync(
    interval: Interval,
    interval_name: &str,
    iteration_count: u64,
    market_data_dir: &Path,
    health_stats: &SharedHealthStats,
) -> Result<(), crate::error::Error> {
    let loop_start = std::time::Instant::now();
    let is_trading = is_trading_hours();

    // Step 0: Validate and repair CSV files (corruption recovery)
    match validate_and_repair_interval(interval, market_data_dir) {
        Ok(reports) => {
            if !reports.is_empty() {
                warn!(
                    worker = interval_name,
                    iteration = iteration_count,
                    corrupted_count = reports.len(),
                    "Found and repaired corrupted CSV files"
                );
                for report in &reports {
                    warn!(
                        worker = interval_name,
                        iteration = iteration_count,
                        ticker = %report.ticker,
                        removed_lines = report.removed_lines,
                        "Repaired corrupted file"
                    );
                }
            }
        }
        Err(e) => {
            warn!(
                worker = interval_name,
                iteration = iteration_count,
                error = %e,
                "Validation failed"
            );
        }
    }

    // Step 1: Sync this interval
    let sync_start = Utc::now();

    // Add comprehensive logging before VCI calls
    tracing::info!(
        worker = interval_name,
        iteration = iteration_count,
        interval = ?interval,
        start_date = ?sync_start,
        is_trading_hours = is_trading,
        "SLOW_WORKER:: Starting interval sync"
    );

    tracing::info!(
        worker = interval_name,
        iteration = iteration_count,
        interval_type = interval.to_vci_format(),
        mode = "resume",
        resume_days = 2,
        "SLOW_WORKER:: VCI sync parameters"
    );

    let sync_result = sync_interval_data(interval).await;
    let sync_end = Utc::now();
    let sync_duration = (sync_end - sync_start).num_seconds();

    let (sync_success, stats) = match sync_result {
        Ok(s) => {
            info!(worker = interval_name, iteration = iteration_count, "Sync completed");
            (true, s)
        }
        Err(e) => {
            error!(worker = interval_name, iteration = iteration_count, error = %e, "Sync failed");
            return Err(e);
        }
    };

    // Write log entry for this interval
    write_log_entry(&sync_start, &sync_end, sync_duration, &stats, sync_success, interval);

    // Step 2: Enhance CSV files
    info!(worker = interval_name, iteration = iteration_count, "Enhancing CSV");
    match csv_enhancer::enhance_interval(interval, market_data_dir) {
        Ok(stats) => {
            info!(
                worker = interval_name,
                iteration = iteration_count,
                tickers = stats.tickers,
                records = stats.records,
                duration_secs = stats.duration.as_secs_f64(),
                "Enhancement completed"
            );
        }
        Err(e) => {
            warn!(
                worker = interval_name,
                iteration = iteration_count,
                error = %e,
                "Enhancement failed"
            );
        }
    }

    // Step 3: Update health stats
    {
        let mut health = health_stats.write().await;
        match interval {
            Interval::Hourly => {
                health.hourly_last_sync = Some(Utc::now().to_rfc3339());
            }
            Interval::Minute => {
                health.minute_last_sync = Some(Utc::now().to_rfc3339());
            }
            Interval::Daily => {} // shouldn't happen
        }
        health.slow_iteration_count = iteration_count;
        health.is_trading_hours = is_trading;
    }

    let loop_duration = loop_start.elapsed();

    info!(
        worker = interval_name,
        iteration = iteration_count,
        loop_duration_secs = loop_duration.as_secs_f64(),
        is_trading_hours = is_trading,
        "Sync iteration completed"
    );

    Ok(())
}

/// Get sync interval duration based on interval type and trading hours
fn get_interval_duration(interval: Interval, is_trading: bool) -> Duration {
    match interval {
        Interval::Hourly => {
            if is_trading {
                Duration::from_secs(HOURLY_TRADING_INTERVAL_SECS)
            } else {
                Duration::from_secs(HOURLY_NON_TRADING_INTERVAL_SECS)
            }
        }
        Interval::Minute => {
            if is_trading {
                Duration::from_secs(MINUTE_TRADING_INTERVAL_SECS)
            } else {
                Duration::from_secs(MINUTE_NON_TRADING_INTERVAL_SECS)
            }
        }
        Interval::Daily => Duration::from_secs(300), // shouldn't happen
    }
}

/// Sync a single interval using existing DataSync infrastructure
async fn sync_interval_data(interval: Interval) -> Result<crate::models::SyncStats, Error> {
    // Calculate date range (last 2 days for resume mode - matches pull behavior)
    let end_date = Utc::now().format("%Y-%m-%d").to_string();
    let start_date = (Utc::now() - chrono::Duration::days(2))
        .format("%Y-%m-%d")
        .to_string();

    // Create sync config for single interval
    let concurrent_batches = get_concurrent_batches();
    let config = SyncConfig::new(
        start_date,
        Some(end_date),
        3, // batch_size (reduced from 10 to avoid VCI overload)
        Some(2), // resume_days: 2 days adaptive mode
        vec![interval],
        false, // not full sync
        concurrent_batches, // Auto-detected based on CPU cores
    );

    // Run sync directly (already in async context)
    let mut sync = DataSync::new(config)?;
    sync.sync_all_intervals(false).await?;
    Ok(sync.get_stats().clone())
}

/// Write compact log entry to slow_worker.log
fn write_log_entry(
    start_time: &chrono::DateTime<Utc>,
    end_time: &chrono::DateTime<Utc>,
    duration_secs: i64,
    stats: &crate::models::SyncStats,
    success: bool,
    interval: Interval,
) {
    let log_path = get_market_data_dir().join("slow_worker.log");

    let status = if success { "OK" } else { "FAIL" };
    let interval_str = match interval {
        Interval::Hourly => "1H",
        Interval::Minute => "1m",
        Interval::Daily => "1D", // shouldn't happen but handle it
    };
    let log_line = format!(
        "{} | {} | {}s | {} | {} | ok:{} fail:{} skip:{} upd:{} files:{} recs:{}\n",
        start_time.format("%Y-%m-%d %H:%M:%S"),
        end_time.format("%Y-%m-%d %H:%M:%S"),
        duration_secs,
        interval_str,
        status,
        stats.successful,
        stats.failed,
        stats.skipped,
        stats.updated,
        stats.files_written,
        stats.total_records
    );

    // Use log rotation utility
    let _ = write_with_rotation(&log_path, &log_line);
}

/// Run slow worker with MPSC channel support for real-time updates
/// NOTE: Hourly worker moved to separate runtime via run_hourly_worker_separate()
#[instrument(skip(health_stats, channel_sender))]
pub async fn run_with_channel(
    health_stats: SharedHealthStats,
    channel_sender: Option<SyncSender<TickerUpdate>>,
) {
    println!("[SYNC::SLOW] === STARTING SLOW WORKER WITH MPSC CHANNEL ===");
    info!("Slow worker now uses separate runtimes for hourly and minute workers");
    println!("[SYNC::SLOW] Channel sender exists: {}", channel_sender.is_some());
    println!("[SYNC::SLOW] Hourly and minute workers are now in separate runtimes");

    // This function is kept for compatibility but hourly/minute workers run separately
    println!("[SYNC::SLOW] === SLOW WORKER COMPLETED (WORKERS MOVED TO SEPARATE RUNTIMES) ===");
}

/// Run minute worker in separate runtime to avoid API overload
#[instrument(skip(health_stats, channel_sender))]
pub async fn run_minute_worker_separate(
    health_stats: SharedHealthStats,
    channel_sender: Option<SyncSender<TickerUpdate>>,
) {
    println!("[SYNC::MINUTE] === STARTING MINUTE WORKER IN SEPARATE RUNTIME ===");
    info!("Starting minute worker in separate runtime to avoid API overload");
    println!("[SYNC::MINUTE] Channel sender exists: {}", channel_sender.is_some());

    let mut iteration = 0;
    loop {
        iteration += 1;
        println!("[SYNC::MINUTE] === MINUTE ITERATION {} STARTING ===", iteration);

        let is_trading = is_trading_hours();
        let sleep_secs = if is_trading {
            MINUTE_TRADING_INTERVAL_SECS
        } else {
            MINUTE_NON_TRADING_INTERVAL_SECS
        };

        println!("[SYNC::MINUTE] Trading hours: {}, sleep will be {}s", is_trading, sleep_secs);
        info!(
            interval = "Minute",
            trading_hours = if is_trading { "ACTIVE" } else { "CLOSED" },
            "Minute worker sync started"
        );

        // Perform minute sync
        let start_time = Utc::now();
        let sync_result = run_sync_with_channel(
            Interval::Minute,
            &health_stats,
            channel_sender.as_ref(),
        ).await;

        let end_time = Utc::now();
        let duration = end_time.signed_duration_since(start_time);
        let stats = sync_result.unwrap_or_else(|e| {
            println!("[SYNC::MINUTE] Minute sync failed with error: {}", e);
            error!(error = %e, "Minute sync failed");
            SyncStats::new()
        });

        write_log_entry(&start_time, &end_time, duration.num_seconds(), &stats, true, Interval::Minute);

        println!("[SYNC::MINUTE] === MINUTE ITERATION {} COMPLETED ===", iteration);
        println!("[SYNC::MINUTE] Sleeping for {} seconds...", sleep_secs);
        // Sleep until next iteration
        sleep(Duration::from_secs(sleep_secs)).await;
        println!("[SYNC::MINUTE] Woke up from sleep");
    }
}

/// Helper function to run sync with channel support
async fn run_sync_with_channel(
    interval: Interval,
    health_stats: &SharedHealthStats,
    channel_sender: Option<&SyncSender<TickerUpdate>>,
) -> Result<SyncStats, Error> {
    let interval_str = match interval {
        Interval::Hourly => "Hourly",
        Interval::Minute => "Minute",
        _ => "Unknown",
    };

    println!("[SYNC::SLOW] === run_sync_with_channel STARTING for {} ===", interval_str);
    println!("[SYNC::SLOW] Channel sender: {:?}", channel_sender);

    // Update health stats quickly - lock only for the brief moment needed
    println!("[SYNC::SLOW] About to acquire health stats lock...");
    {
        let mut health = health_stats.write().await;
        println!("[SYNC::SLOW] Health stats lock acquired");
        match interval {
            Interval::Hourly => {
                health.hourly_last_sync = Some(Utc::now().to_rfc3339());
                health.slow_iteration_count += 1;
                println!("[SYNC::SLOW] Updated hourly health stats");
            }
            Interval::Minute => {
                health.minute_last_sync = Some(Utc::now().to_rfc3339());
                health.slow_iteration_count += 1;
                println!("[SYNC::SLOW] Updated minute health stats");
            }
            _ => {}
        }
    } // Lock released here immediately
    println!("[SYNC::SLOW] Health stats lock released");

    println!("[SYNC::SLOW] About to create SyncConfig...");
    let concurrent_batches = get_concurrent_batches();
    let start_date = (Utc::now() - chrono::Days::new(7)).format("%Y-%m-%d").to_string();
    let end_date = Utc::now().format("%Y-%m-%d").to_string();

    println!("[SYNC::SLOW] Sync config: start={}, end={}, batches={}", start_date, end_date, concurrent_batches);

    // Create sync config (same as pull command)
    let concurrent_batches = get_concurrent_batches();
    let config = SyncConfig::new(
        start_date,
        Some(end_date),
        20, // batch_size (optimized for 1H/1m intervals)
        Some(2), // resume_days: 2 days adaptive mode
        vec![interval],
        false, // not full sync
        concurrent_batches, // Auto-detected based on CPU cores
    );

    println!("[SYNC::SLOW] About to create DataSync with channel...");
    // Create DataSync with channel support (fresh client each time like pull)
    let mut sync = DataSync::new_with_channel(
        config,
        channel_sender.cloned()
    )?;
    println!("[SYNC::SLOW] ✅ DataSync client created successfully - new HTTP connections!");

    println!("[SYNC::SLOW] About to call sync_all_intervals...");
    sync.sync_all_intervals(false).await?; // Full sync (not debug mode)
    println!("[SYNC::SLOW] ✅ sync_all_intervals completed successfully!");

    let stats = sync.get_stats().clone();
    println!("[SYNC::SLOW] === run_sync_with_channel COMPLETED for {} ===", interval_str);
    Ok(stats)
}
