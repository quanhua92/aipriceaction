use crate::error::Error;
use crate::models::{Interval, SyncConfig};
use crate::services::{DataSync, SharedHealthStats, csv_enhancer, validate_and_repair_interval, is_trading_hours};
use crate::utils::{get_market_data_dir, write_with_rotation, get_concurrent_batches};
use chrono::Utc;
use std::time::Duration;
use tokio::time::sleep;
use tracing::{info, warn, error, instrument};

// Hourly sync intervals
const HOURLY_TRADING_INTERVAL_SECS: u64 = 60; // 1 minute (trading hours)
const HOURLY_NON_TRADING_INTERVAL_SECS: u64 = 1800; // 30 minutes (off hours)

// Minute sync intervals
const MINUTE_TRADING_INTERVAL_SECS: u64 = 300; // 5 minutes (trading hours)
const MINUTE_NON_TRADING_INTERVAL_SECS: u64 = 1800; // 30 minutes (off hours)

#[instrument(skip(health_stats))]
pub async fn run(health_stats: SharedHealthStats) {
    info!(
        "Starting slow worker with 2 independent tasks:"
    );
    info!(
        "  - Hourly: {}s (trading) / {}s (off-hours)",
        HOURLY_TRADING_INTERVAL_SECS, HOURLY_NON_TRADING_INTERVAL_SECS
    );
    info!(
        "  - Minute: {}s (trading) / {}s (off-hours)",
        MINUTE_TRADING_INTERVAL_SECS, MINUTE_NON_TRADING_INTERVAL_SECS
    );

    // Spawn two independent async tasks
    let health_stats_hourly = health_stats.clone();
    let health_stats_minute = health_stats.clone();

    let hourly_task = tokio::spawn(async move {
        run_interval_worker(Interval::Hourly, health_stats_hourly).await;
    });

    let minute_task = tokio::spawn(async move {
        run_interval_worker(Interval::Minute, health_stats_minute).await;
    });

    // Wait for both tasks (they run forever)
    let _ = tokio::join!(hourly_task, minute_task);
}

/// Run a worker for a specific interval
async fn run_interval_worker(interval: Interval, health_stats: SharedHealthStats) {
    let mut iteration_count = 0u64;
    let market_data_dir = get_market_data_dir();
    let interval_name = match interval {
        Interval::Hourly => "Hourly",
        Interval::Minute => "Minute",
        Interval::Daily => "Daily", // shouldn't happen
    };

    info!("{} worker started", interval_name);

    loop {
        iteration_count += 1;
        let loop_start = std::time::Instant::now();
        let is_trading = is_trading_hours();

        info!(
            worker = interval_name,
            iteration = iteration_count,
            is_trading_hours = is_trading,
            "Starting sync"
        );

        // Step 0: Validate and repair CSV files (corruption recovery)
        match validate_and_repair_interval(interval, &market_data_dir) {
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
                // Continue to next iteration on failure
                let sync_interval = get_interval_duration(interval, is_trading);
                sleep(sync_interval).await;
                continue;
            }
        };

        // Write log entry for this interval
        write_log_entry(&sync_start, &sync_end, sync_duration, &stats, sync_success, interval);

        // Step 2: Enhance CSV files and sync to SQLite
        info!(worker = interval_name, iteration = iteration_count, "Enhancing CSV and syncing to SQLite");
        match csv_enhancer::enhance_interval_with_sqlite(interval, &market_data_dir, None).await {
            Ok(stats) => {
                info!(
                    worker = interval_name,
                    iteration = iteration_count,
                    tickers = stats.tickers,
                    records = stats.records,
                    duration_secs = stats.duration.as_secs_f64(),
                    "CSV enhancement and SQLite sync completed"
                );
            }
            Err(e) => {
                warn!(
                    worker = interval_name,
                    iteration = iteration_count,
                    error = %e,
                    "CSV enhancement/SQLite sync failed"
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
        }

        let loop_duration = loop_start.elapsed();

        // Get dynamic interval based on trading hours and interval type
        let sync_interval = get_interval_duration(interval, is_trading);

        info!(
            worker = interval_name,
            iteration = iteration_count,
            loop_duration_secs = loop_duration.as_secs_f64(),
            next_sync_secs = sync_interval.as_secs(),
            is_trading_hours = is_trading,
            "Iteration completed"
        );

        // Sleep for remaining time
        sleep(sync_interval).await;
    }
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
    // Calculate date range (last 7 days for resume mode)
    let end_date = Utc::now().format("%Y-%m-%d").to_string();
    let start_date = (Utc::now() - chrono::Duration::days(7))
        .format("%Y-%m-%d")
        .to_string();

    // Create sync config for single interval
    let concurrent_batches = get_concurrent_batches();
    let config = SyncConfig::new(
        start_date,
        Some(end_date),
        10, // batch_size (default)
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
