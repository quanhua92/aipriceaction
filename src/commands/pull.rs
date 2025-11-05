use crate::error::Error;
use crate::models::{Interval, SyncConfig};
use crate::services::{DataSync, csv_enhancer};
use std::path::Path;

pub fn run(intervals_arg: String, full: bool, resume_days: Option<u32>, start_date: String, debug: bool, batch_size: usize) {
    // Parse intervals
    let intervals = match Interval::parse_intervals(&intervals_arg) {
        Ok(intervals) => intervals,
        Err(e) => {
            eprintln!("❌ Error parsing intervals: {}", e);
            eprintln!("   Valid options: all, daily, hourly, minute, or comma-separated (e.g., daily,hourly)");
            std::process::exit(1);
        }
    };

    if debug {
        println!("🐛 DEBUG MODE: Using hardcoded test tickers (VNINDEX, VIC, VCB)");
    }

    // Show resume mode info
    if !full {
        if let Some(days) = resume_days {
            println!("📅 Resume mode: Using fixed {} days (overrides adaptive mode)", days);
        } else {
            println!("📅 Resume mode: ADAPTIVE (reads last date from CSV files)");
            println!("   Fallback: 2 days if CSV read fails");
        }
    }

    // Create sync config
    let config = SyncConfig::new(
        start_date,
        None, // Use default (today)
        batch_size,
        resume_days,
        intervals,
        full,
        3, // concurrent_batches: 3 concurrent requests
    );

    // Run sync
    let synced_intervals = config.intervals.clone();
    match run_sync(config, debug) {
        Ok(_) => {
            println!("\n✅ Data sync completed successfully!");
        }
        Err(e) => {
            eprintln!("\n❌ Data sync failed: {}", e);
            std::process::exit(1);
        }
    }

    // Enhance CSV files with indicators
    println!("\n📊 Enhancing CSV files with indicators...");
    let market_data_dir = Path::new("market_data");

    for interval in &synced_intervals {
        match csv_enhancer::enhance_interval(*interval, market_data_dir) {
            Ok(stats) => {
                if stats.records > 0 {
                    println!("✅ {} enhanced: {} tickers, {} records in {:.2}s",
                        interval.to_filename(),
                        stats.tickers,
                        stats.records,
                        stats.duration.as_secs_f64()
                    );

                    // Detailed breakdown
                    println!("   ⏱️  Read CSVs:     {:.2}s ({:.1}%)",
                        stats.read_time.as_secs_f64(),
                        (stats.read_time.as_secs_f64() / stats.duration.as_secs_f64()) * 100.0
                    );
                    println!("   ⏱️  Calculate MAs: {:.2}s ({:.1}%)",
                        stats.ma_time.as_secs_f64(),
                        (stats.ma_time.as_secs_f64() / stats.duration.as_secs_f64()) * 100.0
                    );
                    println!("   ⏱️  Money flows:   {:.2}s ({:.1}%)",
                        stats.money_flow_time.as_secs_f64(),
                        (stats.money_flow_time.as_secs_f64() / stats.duration.as_secs_f64()) * 100.0
                    );
                    println!("   ⏱️  Trend scores:  {:.2}s ({:.1}%)",
                        stats.trend_score_time.as_secs_f64(),
                        (stats.trend_score_time.as_secs_f64() / stats.duration.as_secs_f64()) * 100.0
                    );
                    println!("   ⏱️  Write CSVs:    {:.2}s ({:.1}%)",
                        stats.write_time.as_secs_f64(),
                        (stats.write_time.as_secs_f64() / stats.duration.as_secs_f64()) * 100.0
                    );

                    // Throughput metrics
                    let records_per_sec = stats.records as f64 / stats.duration.as_secs_f64();
                    let mb_written = stats.total_bytes_written as f64 / (1024.0 * 1024.0);
                    let write_throughput = mb_written / stats.write_time.as_secs_f64();

                    println!("   📊 Throughput:    {:.0} records/sec",
                        records_per_sec
                    );
                    println!("   💾 Data written:  {:.2} MB ({:.2} MB/s)",
                        mb_written,
                        write_throughput
                    );
                }
            }
            Err(e) => {
                eprintln!("⚠️  {} enhancement failed: {}", interval.to_filename(), e);
            }
        }
    }
}

fn run_sync(config: SyncConfig, debug: bool) -> Result<(), Error> {
    // Create Tokio runtime
    let runtime = tokio::runtime::Runtime::new()
        .map_err(|e| Error::Network(format!("Failed to create runtime: {}", e)))?;

    // Run async sync
    runtime.block_on(async {
        let mut sync = DataSync::new(config)?;
        sync.sync_all_intervals(debug).await
    })
}
