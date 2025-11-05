use crate::error::Error;
use crate::models::{Interval, SyncConfig};
use crate::services::{DataSync, DataStore, SharedHealthStats, csv_enhancer, validate_and_repair_interval};
use chrono::Utc;
use std::path::Path;
use std::time::Duration;
use tokio::time::sleep;
use tracing::{info, warn, error, instrument};

const SYNC_INTERVAL_SECS: u64 = 15; // 15 seconds for fast daily updates

#[instrument(skip(data_store, health_stats))]
pub async fn run(data_store: DataStore, health_stats: SharedHealthStats) {
    info!("Starting daily worker (every {} seconds)", SYNC_INTERVAL_SECS);

    let mut iteration_count = 0u64;
    let market_data_dir = Path::new("market_data");

    loop {
        iteration_count += 1;
        let loop_start = std::time::Instant::now();

        info!(iteration = iteration_count, "Daily worker: Starting sync");

        // Step 0: Validate and repair CSV files (corruption recovery)
        match validate_and_repair_interval(Interval::Daily, market_data_dir) {
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
        match sync_daily_data().await {
            Ok(_) => {
                info!(iteration = iteration_count, "Daily worker: Sync completed");
            }
            Err(e) => {
                error!(iteration = iteration_count, error = %e, "Daily worker: Sync failed");
                // Continue to next iteration even if sync fails
                sleep(Duration::from_secs(SYNC_INTERVAL_SECS)).await;
                continue;
            }
        }

        // Step 2: Enhance CSV files with technical indicators
        info!(iteration = iteration_count, "Daily worker: Enhancing CSV");
        match csv_enhancer::enhance_interval(Interval::Daily, market_data_dir) {
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

        // Step 3: Reload daily data into shared memory
        info!(iteration = iteration_count, "Daily worker: Reloading into memory");
        match data_store.reload_interval(Interval::Daily).await {
            Ok(_) => {
                info!(iteration = iteration_count, "Daily worker: Reload completed");
            }
            Err(e) => {
                warn!(iteration = iteration_count, error = %e, "Daily worker: Reload failed");
            }
        }

        // Step 4: Update health stats
        {
            let mut health = health_stats.lock().await;
            health.daily_last_sync = Some(Utc::now().to_rfc3339());
            health.daily_iteration_count = iteration_count;
        }

        let loop_duration = loop_start.elapsed();
        info!(
            iteration = iteration_count,
            loop_duration_secs = loop_duration.as_secs_f64(),
            "Daily worker: Iteration completed"
        );

        // Sleep for remaining time
        sleep(Duration::from_secs(SYNC_INTERVAL_SECS)).await;
    }
}

/// Sync daily data using existing DataSync infrastructure
async fn sync_daily_data() -> Result<(), Error> {
    // Calculate date range (last 7 days for resume mode)
    let end_date = Utc::now().format("%Y-%m-%d").to_string();
    let start_date = (Utc::now() - chrono::Duration::days(7))
        .format("%Y-%m-%d")
        .to_string();

    // Create sync config for daily interval only
    let config = SyncConfig::new(
        start_date,
        Some(end_date),
        10, // batch_size (default)
        Some(2), // resume_days: 2 days adaptive mode
        vec![Interval::Daily],
        false, // not full sync
        3, // concurrent_batches
    );

    // Run sync directly (already in async context)
    let mut sync = DataSync::new(config)?;
    sync.sync_all_intervals(false).await
}
