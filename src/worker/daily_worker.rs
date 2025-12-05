use crate::error::Error;
use crate::models::{Interval, SyncConfig};
use crate::services::{DataSync, SharedHealthStats, csv_enhancer, validate_and_repair_interval, is_trading_hours, get_sync_interval};
use crate::utils::{get_market_data_dir, write_with_rotation, get_concurrent_batches};
use chrono::Utc;
use std::time::Duration;
use tokio::time::sleep;
use tracing::{info, warn, error, instrument};

// Trading hours: 15 seconds (active market)
// Non-trading hours: 5 minutes (market closed, relaxed sync)
const TRADING_INTERVAL_SECS: u64 = 15;
const NON_TRADING_INTERVAL_SECS: u64 = 300; // 5 minutes

#[instrument(skip(health_stats))]
pub async fn run(health_stats: SharedHealthStats) {
    info!(
        "Starting daily worker - Trading hours: {}s, Non-trading hours: {}s",
        TRADING_INTERVAL_SECS, NON_TRADING_INTERVAL_SECS
    );

    let mut iteration_count = 0u64;
    let market_data_dir = get_market_data_dir();

    loop {
        iteration_count += 1;
        let loop_start = std::time::Instant::now();
        let is_trading = is_trading_hours();

        info!(
            iteration = iteration_count,
            is_trading_hours = is_trading,
            "Daily worker: Starting sync"
        );

        // Step 0: Validate and repair CSV files (corruption recovery)
        match validate_and_repair_interval(Interval::Daily, &market_data_dir) {
            Ok(reports) => {
                if !reports.is_empty() {
                    warn!(
                        iteration = iteration_count,
                        corrupted_count = reports.len(),
                        "Daily worker: Found and repaired corrupted CSV files"
                    );
                    for report in &reports {
                        warn!(
                            iteration = iteration_count,
                            ticker = %report.ticker,
                            removed_lines = report.removed_lines,
                            "Daily worker: Repaired corrupted file"
                        );
                    }
                }
            }
            Err(e) => {
                warn!(iteration = iteration_count, error = %e, "Daily worker: Validation failed");
            }
        }

        // Step 1: Sync daily data using existing DataSync
        let sync_start = Utc::now();
        let sync_result = sync_daily_data().await;
        let sync_end = Utc::now();
        let sync_duration = (sync_end - sync_start).num_seconds();

        let (sync_success, stats) = match sync_result {
            Ok(s) => {
                info!(iteration = iteration_count, "Daily worker: Sync completed");
                (true, s)
            }
            Err(e) => {
                error!(iteration = iteration_count, error = %e, "Daily worker: Sync failed");
                // Continue to next iteration even if sync fails
                let sync_interval = get_sync_interval(
                    Duration::from_secs(TRADING_INTERVAL_SECS),
                    Duration::from_secs(NON_TRADING_INTERVAL_SECS)
                );
                sleep(sync_interval).await;
                continue;
            }
        };

        // Step 2: Enhance CSV files with technical indicators
        info!(iteration = iteration_count, "Daily worker: Enhancing CSV");
        match csv_enhancer::enhance_interval(Interval::Daily, &market_data_dir) {
            Ok(stats) => {
                info!(
                    iteration = iteration_count,
                    tickers = stats.tickers,
                    records = stats.records,
                    duration_secs = stats.duration.as_secs_f64(),
                    "Daily worker: Enhancement completed"
                );
            }
            Err(e) => {
                warn!(iteration = iteration_count, error = %e, "Daily worker: Enhancement failed");
            }
        }

        // Step 3: Update health stats (memory cache updated lazily by API on cache miss)
        {
            let mut health = health_stats.write().await;
            health.daily_last_sync = Some(Utc::now().to_rfc3339());
            health.daily_iteration_count = iteration_count;
            health.is_trading_hours = is_trading;
        }

        let loop_duration = loop_start.elapsed();

        // Get dynamic interval based on trading hours
        let sync_interval = get_sync_interval(
            Duration::from_secs(TRADING_INTERVAL_SECS),
            Duration::from_secs(NON_TRADING_INTERVAL_SECS)
        );

        info!(
            iteration = iteration_count,
            loop_duration_secs = loop_duration.as_secs_f64(),
            next_sync_secs = sync_interval.as_secs(),
            is_trading_hours = is_trading,
            "Daily worker: Iteration completed"
        );

        // Write compact log entry
        write_log_entry(&sync_start, &sync_end, sync_duration, &stats, sync_success);

        // Sleep for remaining time
        sleep(sync_interval).await;
    }
}

/// Sync daily data using existing DataSync infrastructure
async fn sync_daily_data() -> Result<crate::models::SyncStats, Error> {
    // Calculate date range (last 7 days for resume mode)
    let end_date = Utc::now().format("%Y-%m-%d").to_string();
    let start_date = (Utc::now() - chrono::Duration::days(7))
        .format("%Y-%m-%d")
        .to_string();

    // Create sync config for daily interval only
    let concurrent_batches = get_concurrent_batches();
    let config = SyncConfig::new(
        start_date,
        Some(end_date),
        10, // batch_size (default)
        Some(5), // resume_days: 5 days for dividend detection
        vec![Interval::Daily],
        false, // not full sync
        concurrent_batches, // Auto-detected based on CPU cores
    );

    // Run sync directly (already in async context)
    let mut sync = DataSync::new(config)?;
    sync.sync_all_intervals(false).await?;
    Ok(sync.get_stats().clone())
}

/// Write compact log entry to fast_worker.log
fn write_log_entry(
    start_time: &chrono::DateTime<Utc>,
    end_time: &chrono::DateTime<Utc>,
    duration_secs: i64,
    stats: &crate::models::SyncStats,
    success: bool,
) {
    let log_path = get_market_data_dir().join("fast_worker.log");

    let status = if success { "OK" } else { "FAIL" };
    let log_line = format!(
        "{} | {} | {}s | 1D | {} | ok:{} fail:{} skip:{} upd:{} files:{} recs:{}\n",
        start_time.format("%Y-%m-%d %H:%M:%S"),
        end_time.format("%Y-%m-%d %H:%M:%S"),
        duration_secs,
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
