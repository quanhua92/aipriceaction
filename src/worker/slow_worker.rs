use crate::error::Error;
use crate::models::{Interval, SyncConfig};
use crate::models::sync_config::SyncStats;
use crate::services::{DataSync, SharedHealthStats, is_trading_hours};
use crate::services::mpsc::TickerUpdate;
use crate::utils::{get_market_data_dir, write_with_rotation, get_concurrent_batches};
use chrono::Utc;
use std::sync::mpsc::SyncSender;
use std::time::Duration;
use tokio::time::sleep;
use tracing::{info, error, instrument};

// Hourly sync intervals
const HOURLY_TRADING_INTERVAL_SECS: u64 = 300; // 5 minutes (trading hours)
const HOURLY_NON_TRADING_INTERVAL_SECS: u64 = 3600; // 1 hour (off hours)

// Minute sync intervals
const MINUTE_TRADING_INTERVAL_SECS: u64 = 600; // 10 minutes (trading hours)
const MINUTE_NON_TRADING_INTERVAL_SECS: u64 = 3600; // 1 hour (off hours)

// NOTE: Legacy run() and run_interval_worker() functions removed since serve.rs uses run_with_channel()
// and hour/minute workers now run in separate runtimes via run_hourly_worker_separate() and run_minute_worker_separate()

/// Run hourly worker in separate runtime to avoid API overload
#[instrument(skip(health_stats, channel_sender))]
pub async fn run_hourly_worker_separate(
    health_stats: SharedHealthStats,
    channel_sender: Option<SyncSender<TickerUpdate>>,
) {
    info!("[SYNC::HOURLY] === STARTING HOURLY WORKER IN SEPARATE RUNTIME ===");
    info!("Starting hourly worker in separate runtime to avoid API overload");
    info!("[SYNC::HOURLY] Channel sender exists: {}", channel_sender.is_some());

    // Initial delay before first sync (2 minutes)
    info!("[SYNC::HOURLY] Initial delay: waiting 120 seconds before first sync...");
    tokio::time::sleep(tokio::time::Duration::from_secs(120)).await;

    let mut iteration = 0;
    loop {
        iteration += 1;
        info!("[SYNC::HOURLY] === HOURLY ITERATION {} STARTING ===", iteration);

        let is_trading = is_trading_hours();
        let sleep_secs = if is_trading {
            HOURLY_TRADING_INTERVAL_SECS
        } else {
            HOURLY_NON_TRADING_INTERVAL_SECS
        };

        info!("[SYNC::HOURLY] Trading hours: {}, sleep will be {}s", is_trading, sleep_secs);
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
            error!("[SYNC::HOURLY] Hourly sync failed with error: {}", e);
            error!(error = %e, "Hourly sync failed");
            SyncStats::new()
        });

        write_log_entry(&start_time, &end_time, duration.num_seconds(), &stats, true, Interval::Hourly);

        info!("[SYNC::HOURLY] === HOURLY ITERATION {} COMPLETED ===", iteration);
        info!("[SYNC::HOURLY] Sleeping for {} seconds...", sleep_secs);
        // Sleep until next iteration
        sleep(Duration::from_secs(sleep_secs)).await;
        info!("[SYNC::HOURLY] Woke up from sleep");
    }
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
#[instrument(skip(_health_stats, channel_sender))]
pub async fn run_with_channel(
    _health_stats: SharedHealthStats,
    channel_sender: Option<SyncSender<TickerUpdate>>,
) {
    info!("[SYNC::SLOW] === STARTING SLOW WORKER WITH MPSC CHANNEL ===");
    info!("Slow worker now uses separate runtimes for hourly and minute workers");
    info!("[SYNC::SLOW] Channel sender exists: {}", channel_sender.is_some());
    info!("[SYNC::SLOW] Hourly and minute workers are now in separate runtimes");

    // This function is kept for compatibility but hourly/minute workers run separately
    info!("[SYNC::SLOW] === SLOW WORKER COMPLETED (WORKERS MOVED TO SEPARATE RUNTIMES) ===");
}

/// Run minute worker in separate runtime to avoid API overload
#[instrument(skip(health_stats, channel_sender))]
pub async fn run_minute_worker_separate(
    health_stats: SharedHealthStats,
    channel_sender: Option<SyncSender<TickerUpdate>>,
) {
    info!("[SYNC::MINUTE] === STARTING MINUTE WORKER IN SEPARATE RUNTIME ===");
    info!("Starting minute worker in separate runtime to avoid API overload");
    info!("[SYNC::MINUTE] Channel sender exists: {}", channel_sender.is_some());

    // Initial delay before first sync (3 minutes)
    info!("[SYNC::MINUTE] Initial delay: waiting 180 seconds before first sync...");
    tokio::time::sleep(tokio::time::Duration::from_secs(180)).await;

    let mut iteration = 0;
    loop {
        iteration += 1;
        info!("[SYNC::MINUTE] === MINUTE ITERATION {} STARTING ===", iteration);

        let is_trading = is_trading_hours();
        let sleep_secs = if is_trading {
            MINUTE_TRADING_INTERVAL_SECS
        } else {
            MINUTE_NON_TRADING_INTERVAL_SECS
        };

        info!("[SYNC::MINUTE] Trading hours: {}, sleep will be {}s", is_trading, sleep_secs);
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
            error!("[SYNC::MINUTE] Minute sync failed with error: {}", e);
            error!(error = %e, "Minute sync failed");
            SyncStats::new()
        });

        write_log_entry(&start_time, &end_time, duration.num_seconds(), &stats, true, Interval::Minute);

        info!("[SYNC::MINUTE] === MINUTE ITERATION {} COMPLETED ===", iteration);
        info!("[SYNC::MINUTE] Sleeping for {} seconds...", sleep_secs);
        // Sleep until next iteration
        sleep(Duration::from_secs(sleep_secs)).await;
        info!("[SYNC::MINUTE] Woke up from sleep");
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

    info!("[SYNC::SLOW] === run_sync_with_channel STARTING for {} ===", interval_str);
    info!("[SYNC::SLOW] Channel sender: {:?}", channel_sender);

    // Update health stats quickly - lock only for the brief moment needed
    info!("[SYNC::SLOW] About to acquire health stats lock...");
    {
        let mut health = health_stats.write().await;
        info!("[SYNC::SLOW] Health stats lock acquired");
        match interval {
            Interval::Hourly => {
                health.hourly_last_sync = Some(Utc::now().to_rfc3339());
                health.slow_iteration_count += 1;
                info!("[SYNC::SLOW] Updated hourly health stats");
            }
            Interval::Minute => {
                health.minute_last_sync = Some(Utc::now().to_rfc3339());
                health.slow_iteration_count += 1;
                info!("[SYNC::SLOW] Updated minute health stats");
            }
            _ => {}
        }
    } // Lock released here immediately
    info!("[SYNC::SLOW] Health stats lock released");

    info!("[SYNC::SLOW] About to create SyncConfig...");
    let concurrent_batches = get_concurrent_batches();
    let start_date = (Utc::now() - chrono::Days::new(7)).format("%Y-%m-%d").to_string();
    let end_date = Utc::now().format("%Y-%m-%d").to_string();

    info!("[SYNC::SLOW] Sync config: start={}, end={}, batches={}", start_date, end_date, concurrent_batches);

    // Create sync config (same as pull command)
    let concurrent_batches = get_concurrent_batches();
    let config = SyncConfig::new(
        start_date,
        Some(end_date),
        match interval {
            crate::models::Interval::Hourly => crate::constants::VCI_BATCH_SIZE_HOURLY,
            crate::models::Interval::Minute => crate::constants::VCI_BATCH_SIZE_MINUTE,
            _ => crate::constants::VCI_BATCH_SIZE_DAILY,
        }, // batch_size (uses constants to avoid API blocking)
        Some(2), // resume_days: 2 days adaptive mode
        vec![interval],
        false, // not full sync
        concurrent_batches, // Auto-detected based on CPU cores
    );

    info!("[SYNC::SLOW] About to create DataSync with channel...");
    // Create DataSync with channel support (fresh client each time like pull)
    let mut sync = DataSync::new_with_channel_async(
        config,
        channel_sender.cloned()
    ).await?;
    info!("[SYNC::SLOW] ✅ DataSync client created successfully - new HTTP connections!");

    info!("[SYNC::SLOW] About to call sync_all_intervals...");
    sync.sync_all_intervals(false).await?; // Full sync (not debug mode)
    info!("[SYNC::SLOW] ✅ sync_all_intervals completed successfully!");

    let stats = sync.get_stats().clone();
    info!("[SYNC::SLOW] === run_sync_with_channel COMPLETED for {} ===", interval_str);
    Ok(stats)
}
