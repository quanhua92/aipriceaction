use crate::error::Error;
use crate::models::{Interval, SyncConfig};
use crate::services::{DataSync, DataStore, SharedHealthStats, csv_enhancer};
use chrono::Utc;
use std::path::Path;
use std::time::Duration;
use tokio::time::sleep;
use tracing::{info, warn, error, instrument};

const SYNC_INTERVAL_SECS: u64 = 300; // 5 minutes for slow updates

#[instrument(skip(data_store, health_stats))]
pub async fn run(data_store: DataStore, health_stats: SharedHealthStats) {
    info!("Starting slow worker (every {} seconds)", SYNC_INTERVAL_SECS);

    let mut iteration_count = 0u64;
    let market_data_dir = Path::new("market_data");
    let intervals = vec![Interval::Hourly, Interval::Minute];

    loop {
        iteration_count += 1;
        let loop_start = std::time::Instant::now();

        info!(iteration = iteration_count, "Slow worker: Starting sync");

        // Step 1: Sync hourly and minute data using existing DataSync
        match sync_slow_data().await {
            Ok(_) => {
                info!(iteration = iteration_count, "Slow worker: Sync completed");
            }
            Err(e) => {
                error!(iteration = iteration_count, error = %e, "Slow worker: Sync failed");
                // Continue to next iteration even if sync fails
                sleep(Duration::from_secs(SYNC_INTERVAL_SECS)).await;
                continue;
            }
        }

        // Step 2: Enhance CSV files for each interval
        for interval in &intervals {
            info!(iteration = iteration_count, interval = %interval.to_filename(), "Slow worker: Enhancing CSV");
            match csv_enhancer::enhance_interval(*interval, market_data_dir) {
                Ok(stats) => {
                    info!(
                        iteration = iteration_count,
                        interval = %interval.to_filename(),
                        tickers = stats.tickers,
                        records = stats.records,
                        duration_secs = stats.duration.as_secs_f64(),
                        "Slow worker: Enhancement completed"
                    );
                }
                Err(e) => {
                    warn!(
                        iteration = iteration_count,
                        interval = %interval.to_filename(),
                        error = %e,
                        "Slow worker: Enhancement failed"
                    );
                }
            }
        }

        // Step 3: Reload data into shared memory
        for interval in &intervals {
            info!(iteration = iteration_count, interval = %interval.to_filename(), "Slow worker: Reloading into memory");
            match data_store.reload_interval(*interval).await {
                Ok(_) => {
                    info!(
                        iteration = iteration_count,
                        interval = %interval.to_filename(),
                        "Slow worker: Reload completed"
                    );
                }
                Err(e) => {
                    warn!(
                        iteration = iteration_count,
                        interval = %interval.to_filename(),
                        error = %e,
                        "Slow worker: Reload failed"
                    );
                }
            }
        }

        // Step 4: Update health stats
        {
            let mut health = health_stats.lock().await;
            health.hourly_last_sync = Some(Utc::now().to_rfc3339());
            health.minute_last_sync = Some(Utc::now().to_rfc3339());
            health.slow_iteration_count = iteration_count;
        }

        let loop_duration = loop_start.elapsed();
        info!(
            iteration = iteration_count,
            loop_duration_secs = loop_duration.as_secs_f64(),
            "Slow worker: Iteration completed"
        );

        // Sleep for remaining time
        sleep(Duration::from_secs(SYNC_INTERVAL_SECS)).await;
    }
}

/// Sync hourly and minute data using existing DataSync infrastructure
async fn sync_slow_data() -> Result<(), Error> {
    // Calculate date range (last 7 days for resume mode)
    let end_date = Utc::now().format("%Y-%m-%d").to_string();
    let start_date = (Utc::now() - chrono::Duration::days(7))
        .format("%Y-%m-%d")
        .to_string();

    // Create sync config for hourly and minute intervals
    let config = SyncConfig::new(
        start_date,
        Some(end_date),
        50, // batch_size
        Some(2), // resume_days: 2 days adaptive mode
        vec![Interval::Hourly, Interval::Minute],
        false, // not full sync
        3, // concurrent_batches
    );

    // Run sync directly (already in async context)
    let mut sync = DataSync::new(config)?;
    sync.sync_all_intervals(false).await
}
