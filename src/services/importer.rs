use crate::models::TickerGroups;
use crate::services::csv_parser;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

/// Import legacy data from reference project structure
///
/// Smart incremental import that validates existing data before reimporting.
/// Checks last 10 rows against source to detect data changes (e.g., dividend adjustments).
///
/// # Arguments
/// * `source_path` - Path to the reference data directory (e.g., "./references/aipriceaction-data")
///
/// # Example
/// ```no_run
/// import_legacy(Path::new("./references/aipriceaction-data"))?;
/// ```
pub fn import_legacy(source_path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let start_time = Instant::now();

    println!("🚀 Starting smart import from: {}", source_path.display());
    println!("⏱️  Start time: {}\n", chrono::Local::now().format("%H:%M:%S"));

    // Load ticker groups
    let ticker_groups = TickerGroups::load_default()?;
    let mut tickers = ticker_groups.all_tickers();

    // Add indices explicitly if not in ticker groups
    if !tickers.contains(&"VNINDEX".to_string()) {
        tickers.push("VNINDEX".to_string());
    }
    if !tickers.contains(&"VN30".to_string()) {
        tickers.push("VN30".to_string());
    }

    println!("📊 Found {} tickers to process\n", tickers.len());

    let mut success_count = 0;
    let mut error_count = 0;
    let mut skipped_count = 0;
    let mut reimport_count = 0;

    for (index, ticker) in tickers.iter().enumerate() {
        let progress = index + 1;
        print!("[{}/{}] {}: ", progress, tickers.len(), ticker);

        match import_ticker(source_path, ticker) {
            Ok(stats) => {
                // Display what happened for this ticker
                if stats.reimported > 0 && stats.skipped > 0 {
                    println!("🔄 Reimported {}, ⏭️  Skipped {} files", stats.reimported, stats.skipped);
                } else if stats.reimported > 0 {
                    println!("🔄 Reimported {} files (data changed)", stats.reimported);
                } else if stats.skipped > 0 {
                    println!("⏭️  Skipped {} files (data up to date)", stats.skipped);
                } else if stats.files_imported > 0 {
                    println!("✅ Imported {} files", stats.files_imported);
                }

                // Update counters
                skipped_count += stats.skipped;
                reimport_count += stats.reimported;
                success_count += 1;
            }
            Err(e) => {
                println!("❌ Error: {}", e);
                error_count += 1;
            }
        }
    }

    let elapsed = start_time.elapsed();

    println!("\n✨ Import complete!");
    println!("   ✅ Success: {} tickers", success_count);
    println!("   ⏭️  Skipped: {} files (already up to date)", skipped_count);
    println!("   🔄 Reimported: {} files (data changed)", reimport_count);
    if error_count > 0 {
        println!("   ❌ Errors: {}", error_count);
    }
    println!("\n⏱️  Total time: {:.2}s ({} tickers)", elapsed.as_secs_f64(), tickers.len());
    println!("   Average: {:.3}s per ticker", elapsed.as_secs_f64() / tickers.len() as f64);

    Ok(())
}

struct ImportStats {
    files_imported: usize,
    skipped: usize,
    reimported: usize,
    daily_records: usize,
    hourly_records: usize,
    minute_records: usize,
}

/// Check if a file needs reimporting by comparing last N rows
/// Returns true if file is up to date (can skip), false if needs reimport
fn is_data_up_to_date(source: &Path, dest: &Path, ticker: &str, rows_to_check: usize) -> bool {
    // If destination doesn't exist, needs import
    if !dest.exists() {
        return false;
    }

    // Try to read both files
    let source_content = match fs::read_to_string(source) {
        Ok(content) => content,
        Err(_) => return false,
    };

    let dest_content = match fs::read_to_string(dest) {
        Ok(content) => content,
        Err(_) => return false,
    };

    let source_lines: Vec<&str> = source_content.lines().collect();
    let dest_lines: Vec<&str> = dest_content.lines().collect();

    // Need at least header + rows_to_check lines
    if source_lines.len() < rows_to_check + 1 || dest_lines.len() < rows_to_check + 1 {
        return false;
    }

    // Compare last N rows
    let source_last = &source_lines[source_lines.len().saturating_sub(rows_to_check)..];
    let dest_last = &dest_lines[dest_lines.len().saturating_sub(rows_to_check)..];

    if source_last.len() != dest_last.len() {
        return false;
    }

    // Helper to check if ticker is an index
    let is_index = matches!(ticker, "VNINDEX" | "VN30");
    let scale_factor = if is_index { 1.0 } else { 1000.0 };

    // Compare each row
    for (source_line, dest_line) in source_last.iter().zip(dest_last.iter()) {
        let source_parts: Vec<&str> = source_line.split(',').collect();
        let dest_parts: Vec<&str> = dest_line.split(',').collect();

        if source_parts.len() < 6 || dest_parts.len() < 6 {
            continue;
        }

        // Compare prices (columns 2-5: open, high, low, close)
        for i in 2..6 {
            let source_price: f64 = source_parts[i].parse().unwrap_or(0.0);
            let dest_price: f64 = dest_parts[i].parse().unwrap_or(0.0);
            let scaled_source = source_price * scale_factor;

            // Allow small floating point differences (0.01)
            if (scaled_source - dest_price).abs() > 0.01 {
                return false;
            }
        }

        // Compare volume (column 6)
        if source_parts[6] != dest_parts[6] {
            return false;
        }
    }

    true
}

/// Import a single ticker from all timeframes
fn import_ticker(source_path: &Path, ticker: &str) -> Result<ImportStats, Box<dyn std::error::Error>> {
    let mut stats = ImportStats {
        files_imported: 0,
        skipped: 0,
        reimported: 0,
        daily_records: 0,
        hourly_records: 0,
        minute_records: 0,
    };

    // Create ticker directory in market_data
    let ticker_dir = PathBuf::from("market_data").join(ticker);
    fs::create_dir_all(&ticker_dir)?;

    // Import daily data
    let daily_source = source_path.join("market_data").join(format!("{}.csv", ticker));
    let daily_dest = ticker_dir.join("daily.csv");
    if daily_source.exists() {
        if is_data_up_to_date(&daily_source, &daily_dest, ticker, 10) {
            stats.skipped += 1;
        } else {
            let existed_before = daily_dest.exists();
            match csv_parser::parse_daily_csv(&daily_source, &daily_dest) {
                Ok(count) => {
                    stats.files_imported += 1;
                    if existed_before {
                        stats.reimported += 1;
                    }
                    stats.daily_records = count;
                }
                Err(e) => {
                    return Err(format!("Failed to import daily data: {}", e).into());
                }
            }
        }
    }

    // Import hourly data
    let hourly_source = source_path.join("market_data_hour").join(format!("{}.csv", ticker));
    let hourly_dest = ticker_dir.join("1h.csv");
    if hourly_source.exists() {
        if is_data_up_to_date(&hourly_source, &hourly_dest, ticker, 10) {
            stats.skipped += 1;
        } else {
            let existed_before = hourly_dest.exists();
            match csv_parser::parse_intraday_csv(&hourly_source, &hourly_dest) {
                Ok(count) => {
                    stats.files_imported += 1;
                    if existed_before {
                        stats.reimported += 1;
                    }
                    stats.hourly_records = count;
                }
                Err(e) => {
                    eprintln!("  ⚠️  Warning: Failed to import hourly data: {}", e);
                }
            }
        }
    }

    // Import minute data
    let minute_source = source_path.join("market_data_minutes").join(format!("{}.csv", ticker));
    let minute_dest = ticker_dir.join("1m.csv");
    if minute_source.exists() {
        if is_data_up_to_date(&minute_source, &minute_dest, ticker, 10) {
            stats.skipped += 1;
        } else {
            let existed_before = minute_dest.exists();
            match csv_parser::parse_intraday_csv(&minute_source, &minute_dest) {
                Ok(count) => {
                    stats.files_imported += 1;
                    if existed_before {
                        stats.reimported += 1;
                    }
                    stats.minute_records = count;
                }
                Err(e) => {
                    eprintln!("  ⚠️  Warning: Failed to import minute data: {}", e);
                }
            }
        }
    }

    // Ensure at least one file was imported or skipped
    if stats.files_imported == 0 && stats.skipped == 0 {
        return Err(format!("No data files found for ticker: {}", ticker).into());
    }

    Ok(stats)
}
