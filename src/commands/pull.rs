use crate::error::Error;
use crate::models::{Interval, SyncConfig};
use crate::services::{DataSync, validate_and_repair_interval};
use crate::utils::get_market_data_dir;

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
        start_date.clone(),
        None, // Use default (today)
        batch_size,
        resume_days,
        intervals.clone(),
        full,
        3, // concurrent_batches: 3 concurrent requests
    );

    // Step 0: Validate and repair CSV files (recovery step)
    println!("\n🔍 Validating CSV files for corruption...");
    let market_data_dir = get_market_data_dir();
    let mut all_corrupted_tickers = Vec::new();

    for interval in &intervals {
        match validate_and_repair_interval(*interval, &market_data_dir) {
            Ok(reports) => {
                if !reports.is_empty() {
                    println!("\n⚠️  Found {} corrupted {} files:", reports.len(), interval.to_filename());
                    for report in &reports {
                        println!("   {} - Removed {} corrupted lines (last valid: {:?})",
                            report.ticker,
                            report.removed_lines,
                            report.last_valid_date
                        );
                        all_corrupted_tickers.push((report.ticker.clone(), *interval, report.last_valid_date));
                    }
                } else {
                    println!("✅ All {} files are valid", interval.to_filename());
                }
            }
            Err(e) => {
                eprintln!("⚠️  Validation failed for {}: {}", interval.to_filename(), e);
            }
        }
    }

    // Step 1: Recover corrupted tickers (fetch missing data)
    if !all_corrupted_tickers.is_empty() {
        println!("\n🔄 Recovering {} corrupted tickers...", all_corrupted_tickers.len());

        // Group by interval
        use std::collections::HashMap;
        let mut tickers_by_interval: HashMap<Interval, Vec<String>> = HashMap::new();

        for (ticker, interval, _last_valid) in &all_corrupted_tickers {
            tickers_by_interval.entry(*interval).or_insert_with(Vec::new).push(ticker.clone());
        }

        // Create recovery config for each interval with corrupted data
        for (interval, tickers) in tickers_by_interval {
            let recovery_config = SyncConfig::new(
                start_date.clone(),
                None,
                batch_size,
                None, // Full sync for corrupted tickers
                vec![interval],
                true, // Force full sync for recovery
                3,
            );

            println!("📥 Fetching full history for {} {} tickers...", tickers.len(), interval.to_filename());
            match run_sync_for_tickers(recovery_config, tickers, debug) {
                Ok(_) => {
                    println!("✅ Recovery completed for {} interval", interval.to_filename());
                }
                Err(e) => {
                    eprintln!("⚠️  Recovery failed for {} interval: {}", interval.to_filename(), e);
                }
            }
        }
    }

    // Step 2: Run normal sync (now includes enhancement in single phase)
    match run_sync(config, debug) {
        Ok(_) => {
            println!("\n✅ Data sync completed successfully!");
            println!("💡 Note: CSV files are already enhanced with technical indicators (single-phase write)");
        }
        Err(e) => {
            eprintln!("\n❌ Data sync failed: {}", e);
            std::process::exit(1);
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

fn run_sync_for_tickers(config: SyncConfig, _tickers: Vec<String>, debug: bool) -> Result<(), Error> {
    // For now, just run a full sync - the DataSync will handle fetching missing data
    // since we removed corrupted lines, the CSV is incomplete and needs recovery
    run_sync(config, debug)
}
