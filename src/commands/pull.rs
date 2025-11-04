use crate::error::Error;
use crate::models::{Interval, SyncConfig};
use crate::services::DataSync;

pub fn run(intervals_arg: String, full: bool, resume_days: u32, start_date: String) {
    // Parse intervals
    let intervals = match Interval::parse_intervals(&intervals_arg) {
        Ok(intervals) => intervals,
        Err(e) => {
            eprintln!("❌ Error parsing intervals: {}", e);
            eprintln!("   Valid options: all, daily, hourly, minute, or comma-separated (e.g., daily,hourly)");
            std::process::exit(1);
        }
    };

    // Create sync config
    let config = SyncConfig::new(
        start_date,
        None, // Use default (today)
        10,   // Default batch size
        resume_days,
        intervals,
        full,
    );

    // Run sync
    match run_sync(config) {
        Ok(_) => {
            println!("\n✅ Data sync completed successfully!");
        }
        Err(e) => {
            eprintln!("\n❌ Data sync failed: {}", e);
            std::process::exit(1);
        }
    }
}

fn run_sync(config: SyncConfig) -> Result<(), Error> {
    // Create Tokio runtime
    let runtime = tokio::runtime::Runtime::new()
        .map_err(|e| Error::Network(format!("Failed to create runtime: {}", e)))?;

    // Run async sync
    runtime.block_on(async {
        let mut sync = DataSync::new(config)?;
        sync.sync_all_intervals().await
    })
}
