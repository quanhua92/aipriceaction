use crate::models::{Interval, TickerGroups};
use crate::services::csv_parser;
use crate::utils::get_market_data_dir;
use std::fs;
use std::path::Path;
use std::time::Instant;

/// Import legacy data from reference project structure
///
/// Smart incremental import that validates existing data before reimporting.
/// Checks last 10 rows against source to detect data changes (e.g., dividend adjustments).
///
/// # Arguments
/// * `source_path` - Path to the reference data directory (e.g., "./references/aipriceaction-data")
/// * `intervals` - Comma-separated list of intervals to import: "all", "daily", "hourly", "minute"
/// * `force` - If true, delete existing files and reimport from scratch
///
/// # Example
/// ```no_run
/// use aipriceaction::services::import_legacy;
/// use std::path::Path;
///
/// # fn main() -> Result<(), Box<dyn std::error::Error>> {
/// import_legacy(Path::new("./references/aipriceaction-data"), "all".to_string(), false)?;
/// # Ok(())
/// # }
/// ```
pub fn import_legacy(source_path: &Path, intervals: String, force: bool) -> Result<(), Box<dyn std::error::Error>> {
    let start_time = Instant::now();

    println!("🚀 Starting smart import from: {}", source_path.display());
    println!("⏱️  Start time: {}\n", chrono::Local::now().format("%H:%M:%S"));

    // Parse intervals
    let import_daily = intervals == "all" || intervals.contains("daily");
    let import_hourly = intervals == "all" || intervals.contains("hourly") || intervals.contains("1h");
    let import_minute = intervals == "all" || intervals.contains("minute") || intervals.contains("1m");

    println!("📋 Import plan:");
    println!("   Daily:  {}", if import_daily { "✅" } else { "⏭️  Skip" });
    println!("   Hourly: {}", if import_hourly { "✅" } else { "⏭️  Skip" });
    println!("   Minute: {}", if import_minute { "✅" } else { "⏭️  Skip" });
    println!();

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

        match import_ticker(source_path, ticker, import_daily, import_hourly, import_minute, force) {
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

    let total_elapsed = start_time.elapsed();

    println!("\n✨ Import complete!");
    println!("   ✅ Success: {} tickers", success_count);
    println!("   ⏭️  Skipped: {} files (already up to date)", skipped_count);
    println!("   🔄 Reimported: {} files (data changed)", reimport_count);
    if error_count > 0 {
        println!("   ❌ Errors: {}", error_count);
    }
    println!("\n⏱️  Total time: {:.2}s ({} tickers)", total_elapsed.as_secs_f64(), tickers.len());
    println!("   Average: {:.3}s per ticker", total_elapsed.as_secs_f64() / tickers.len() as f64);
    println!("\n💡 Note: CSVs are already enhanced with technical indicators (single-phase import)");

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

    // Check if source has significantly more data than destination
    // This catches cases where pull command added recent data but historical data is missing
    if source_lines.len() > dest_lines.len() + 100 {
        return false;  // Source has 100+ more rows - needs reimport
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

/// Import a single ticker from specified timeframes
fn import_ticker(
    source_path: &Path,
    ticker: &str,
    import_daily: bool,
    import_hourly: bool,
    import_minute: bool,
    force: bool,
) -> Result<ImportStats, Box<dyn std::error::Error>> {
    let mut stats = ImportStats {
        files_imported: 0,
        skipped: 0,
        reimported: 0,
        daily_records: 0,
        hourly_records: 0,
        minute_records: 0,
    };

    // Create ticker directory in market_data
    let ticker_dir = get_market_data_dir().join(ticker);
    fs::create_dir_all(&ticker_dir)?;

    // Import daily data
    if import_daily {
        let daily_source = source_path.join("market_data").join(format!("{}.csv", ticker));
        let daily_dest = ticker_dir.join(Interval::Daily.to_filename());

        // If force mode, delete existing file
        if force && daily_dest.exists() {
            fs::remove_file(&daily_dest)?;
        }

        if daily_source.exists() {
        if !force && is_data_up_to_date(&daily_source, &daily_dest, ticker, 10) {
            stats.skipped += 1;
        } else {
            let existed_before = daily_dest.exists();
            match csv_parser::parse_daily_csv(&daily_source) {
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
    }

    // Import hourly data
    if import_hourly {
    let hourly_source = source_path.join("market_data_hour").join(format!("{}.csv", ticker));
    let hourly_dest = ticker_dir.join(Interval::Hourly.to_filename());

    // If force mode, delete existing file
    if force && hourly_dest.exists() {
        fs::remove_file(&hourly_dest)?;
    }

    if hourly_source.exists() {
        if !force && is_data_up_to_date(&hourly_source, &hourly_dest, ticker, 10) {
            stats.skipped += 1;
        } else {
            let existed_before = hourly_dest.exists();
            match csv_parser::parse_intraday_csv(&hourly_source, Interval::Hourly) {
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
    }

    // Import minute data
    if import_minute {
    let minute_source = source_path.join("market_data_minutes").join(format!("{}.csv", ticker));
    let minute_dest = ticker_dir.join(Interval::Minute.to_filename());

    // If force mode, delete existing file
    if force && minute_dest.exists() {
        fs::remove_file(&minute_dest)?;
    }

    if minute_source.exists() {
        if !force && is_data_up_to_date(&minute_source, &minute_dest, ticker, 10) {
            stats.skipped += 1;
        } else {
            let existed_before = minute_dest.exists();
            match csv_parser::parse_intraday_csv(&minute_source, Interval::Minute) {
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
    }

    // Ensure at least one file was imported or skipped
    if stats.files_imported == 0 && stats.skipped == 0 {
        return Err(format!("No data files found for ticker: {}", ticker).into());
    }

    Ok(stats)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::TempDir;

    /// Helper to create a temporary CSV file with test data
    fn create_test_csv(path: &Path, _ticker: &str, rows: Vec<&str>) -> std::io::Result<()> {
        let mut file = std::fs::File::create(path)?;
        // Write header
        writeln!(file, "ticker,time,open,high,low,close,volume")?;
        // Write data rows
        for row in rows {
            writeln!(file, "{}", row)?;
        }
        Ok(())
    }

    #[test]
    fn test_is_data_up_to_date_files_match() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create source with stock ticker data (prices will be scaled 1000x in dest)
        let source_rows = vec![
            "VCB,2024-01-01,23.1,23.5,23.0,23.2,1000000",
            "VCB,2024-01-02,23.2,23.6,23.1,23.3,1100000",
            "VCB,2024-01-03,23.3,23.7,23.2,23.4,1200000",
        ];
        create_test_csv(&source, "VCB", source_rows.clone()).unwrap();

        // Create dest with scaled prices (multiply by 1000)
        let dest_rows = vec![
            "VCB,2024-01-01,23100.0,23500.0,23000.0,23200.0,1000000",
            "VCB,2024-01-02,23200.0,23600.0,23100.0,23300.0,1100000",
            "VCB,2024-01-03,23300.0,23700.0,23200.0,23400.0,1200000",
        ];
        create_test_csv(&dest, "VCB", dest_rows).unwrap();

        // Should return true - data matches (accounting for scaling)
        assert!(is_data_up_to_date(&source, &dest, "VCB", 3));
    }

    #[test]
    fn test_is_data_up_to_date_files_differ() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create source
        let source_rows = vec![
            "VCB,2024-01-01,23.1,23.5,23.0,23.2,1000000",
            "VCB,2024-01-02,23.2,23.6,23.1,23.3,1100000",
            "VCB,2024-01-03,23.3,23.7,23.2,23.4,1200000",
        ];
        create_test_csv(&source, "VCB", source_rows).unwrap();

        // Create dest with DIFFERENT last row (price change - e.g., dividend adjustment)
        let dest_rows = vec![
            "VCB,2024-01-01,23100.0,23500.0,23000.0,23200.0,1000000",
            "VCB,2024-01-02,23200.0,23600.0,23100.0,23300.0,1100000",
            "VCB,2024-01-03,23300.0,23700.0,23200.0,22400.0,1200000", // Different close: 22400 vs 23400
        ];
        create_test_csv(&dest, "VCB", dest_rows).unwrap();

        // Should return false - data differs
        assert!(!is_data_up_to_date(&source, &dest, "VCB", 3));
    }

    #[test]
    fn test_is_data_up_to_date_dest_missing() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv"); // Doesn't exist

        let source_rows = vec![
            "VCB,2024-01-01,23.1,23.5,23.0,23.2,1000000",
        ];
        create_test_csv(&source, "VCB", source_rows).unwrap();

        // Should return false - dest doesn't exist
        assert!(!is_data_up_to_date(&source, &dest, "VCB", 1));
    }

    #[test]
    fn test_is_data_up_to_date_index_no_scaling() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create source with index data (no scaling)
        let source_rows = vec![
            "VNINDEX,2024-01-01,1250.5,1260.3,1245.2,1255.8,0",
            "VNINDEX,2024-01-02,1255.8,1265.4,1250.1,1260.2,0",
        ];
        create_test_csv(&source, "VNINDEX", source_rows.clone()).unwrap();

        // Create dest with SAME prices (no scaling for indices)
        let dest_rows = vec![
            "VNINDEX,2024-01-01,1250.5,1260.3,1245.2,1255.8,0",
            "VNINDEX,2024-01-02,1255.8,1265.4,1250.1,1260.2,0",
        ];
        create_test_csv(&dest, "VNINDEX", dest_rows).unwrap();

        // Should return true - data matches (no scaling for indices)
        assert!(is_data_up_to_date(&source, &dest, "VNINDEX", 2));
    }

    #[test]
    fn test_is_data_up_to_date_vn30_no_scaling() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create source with VN30 index data
        let source_rows = vec![
            "VN30,2024-01-01,850.5,860.3,845.2,855.8,0",
        ];
        create_test_csv(&source, "VN30", source_rows.clone()).unwrap();

        // Create dest with same prices
        let dest_rows = vec![
            "VN30,2024-01-01,850.5,860.3,845.2,855.8,0",
        ];
        create_test_csv(&dest, "VN30", dest_rows).unwrap();

        // Should return true
        assert!(is_data_up_to_date(&source, &dest, "VN30", 1));
    }

    #[test]
    fn test_is_data_up_to_date_insufficient_rows() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create files with only 2 data rows
        let rows = vec![
            "VCB,2024-01-01,23.1,23.5,23.0,23.2,1000000",
            "VCB,2024-01-02,23.2,23.6,23.1,23.3,1100000",
        ];
        create_test_csv(&source, "VCB", rows.clone()).unwrap();

        let dest_rows = vec![
            "VCB,2024-01-01,23100.0,23500.0,23000.0,23200.0,1000000",
            "VCB,2024-01-02,23200.0,23600.0,23100.0,23300.0,1100000",
        ];
        create_test_csv(&dest, "VCB", dest_rows).unwrap();

        // Asking for 10 rows but only 2 exist - should return false
        assert!(!is_data_up_to_date(&source, &dest, "VCB", 10));
    }

    #[test]
    fn test_is_data_up_to_date_volume_differs() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create source
        let source_rows = vec![
            "VCB,2024-01-01,23.1,23.5,23.0,23.2,1000000",
        ];
        create_test_csv(&source, "VCB", source_rows).unwrap();

        // Create dest with different volume
        let dest_rows = vec![
            "VCB,2024-01-01,23100.0,23500.0,23000.0,23200.0,999999", // Different volume
        ];
        create_test_csv(&dest, "VCB", dest_rows).unwrap();

        // Should return false - volume differs
        assert!(!is_data_up_to_date(&source, &dest, "VCB", 1));
    }

    #[test]
    fn test_is_data_up_to_date_checks_only_last_n_rows() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create source with 5 rows
        let source_rows = vec![
            "VCB,2024-01-01,23.1,23.5,23.0,23.2,1000000",
            "VCB,2024-01-02,23.2,23.6,23.1,23.3,1100000",
            "VCB,2024-01-03,23.3,23.7,23.2,23.4,1200000",
            "VCB,2024-01-04,23.4,23.8,23.3,23.5,1300000",
            "VCB,2024-01-05,23.5,23.9,23.4,23.6,1400000",
        ];
        create_test_csv(&source, "VCB", source_rows).unwrap();

        // Create dest where first row differs but last 3 rows match
        let dest_rows = vec![
            "VCB,2024-01-01,99999.0,99999.0,99999.0,99999.0,999999", // DIFFERENT
            "VCB,2024-01-02,23200.0,23600.0,23100.0,23300.0,1100000",
            "VCB,2024-01-03,23300.0,23700.0,23200.0,23400.0,1200000",
            "VCB,2024-01-04,23400.0,23800.0,23300.0,23500.0,1300000",
            "VCB,2024-01-05,23500.0,23900.0,23400.0,23600.0,1400000",
        ];
        create_test_csv(&dest, "VCB", dest_rows).unwrap();

        // Check only last 3 rows - should return true (they match)
        assert!(is_data_up_to_date(&source, &dest, "VCB", 3));

        // Check all 5 rows - should return false (first row differs)
        assert!(!is_data_up_to_date(&source, &dest, "VCB", 5));
    }

    #[test]
    fn test_is_data_up_to_date_floating_point_tolerance() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create source
        let source_rows = vec![
            "VCB,2024-01-01,23.1,23.5,23.0,23.2,1000000",
        ];
        create_test_csv(&source, "VCB", source_rows).unwrap();

        // Create dest with tiny floating point difference (within 0.01 tolerance)
        let dest_rows = vec![
            "VCB,2024-01-01,23100.005,23500.003,23000.002,23200.001,1000000",
        ];
        create_test_csv(&dest, "VCB", dest_rows).unwrap();

        // Should return true - differences are within tolerance
        assert!(is_data_up_to_date(&source, &dest, "VCB", 1));
    }

    #[test]
    fn test_is_data_up_to_date_empty_files() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create empty files (just header)
        std::fs::File::create(&source).unwrap();
        std::fs::File::create(&dest).unwrap();

        // Should return false - not enough data
        assert!(!is_data_up_to_date(&source, &dest, "VCB", 1));
    }

    #[test]
    fn test_is_data_up_to_date_source_has_more_rows() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create source with 200 rows (simulating complete historical data)
        let mut source_rows = Vec::new();
        for i in 1..=200 {
            source_rows.push(format!("VCB,2024-01-{:02},23.1,23.5,23.0,23.2,1000000", i % 28 + 1));
        }
        let source_rows_str: Vec<&str> = source_rows.iter().map(|s| s.as_str()).collect();
        create_test_csv(&source, "VCB", source_rows_str).unwrap();

        // Create dest with only 50 rows (simulating partial import with recent data)
        // This simulates the CTR bug: dest has recent data but missing historical middle data
        let mut dest_rows = Vec::new();
        for i in 151..=200 {
            dest_rows.push(format!("VCB,2024-01-{:02},23100.0,23500.0,23000.0,23200.0,1000000", i % 28 + 1));
        }
        let dest_rows_str: Vec<&str> = dest_rows.iter().map(|s| s.as_str()).collect();
        create_test_csv(&dest, "VCB", dest_rows_str).unwrap();

        // Should return false because source has 150 more rows than dest (> 100 threshold)
        // Even though the last 10 rows might match, we need to reimport the missing data
        assert!(!is_data_up_to_date(&source, &dest, "VCB", 10));
    }

    #[test]
    fn test_is_data_up_to_date_small_row_difference_ok() {
        let temp_dir = TempDir::new().unwrap();
        let source = temp_dir.path().join("source.csv");
        let dest = temp_dir.path().join("dest.csv");

        // Create source with 50 rows
        let mut source_rows = Vec::new();
        for i in 1..=50 {
            source_rows.push(format!("VCB,2024-01-{:02},23.{},23.5,23.0,23.2,1000000", i % 28 + 1, i));
        }
        let source_rows_str: Vec<&str> = source_rows.iter().map(|s| s.as_str()).collect();
        create_test_csv(&source, "VCB", source_rows_str).unwrap();

        // Create dest with 40 rows (only 10 row difference, below 100 threshold)
        let mut dest_rows = Vec::new();
        for i in 1..=40 {
            dest_rows.push(format!("VCB,2024-01-{:02},23{}00.0,23500.0,23000.0,23200.0,1000000", i % 28 + 1, i));
        }
        let dest_rows_str: Vec<&str> = dest_rows.iter().map(|s| s.as_str()).collect();
        create_test_csv(&dest, "VCB", dest_rows_str).unwrap();

        // With only 10 row difference (< 100 threshold), should check last N rows
        // In this test they won't match due to different values, so should return false
        assert!(!is_data_up_to_date(&source, &dest, "VCB", 10));
    }

    #[test]
    fn test_import_stats_initialization() {
        let stats = ImportStats {
            files_imported: 0,
            skipped: 0,
            reimported: 0,
            daily_records: 0,
            hourly_records: 0,
            minute_records: 0,
        };

        assert_eq!(stats.files_imported, 0);
        assert_eq!(stats.skipped, 0);
        assert_eq!(stats.reimported, 0);
        assert_eq!(stats.daily_records, 0);
        assert_eq!(stats.hourly_records, 0);
        assert_eq!(stats.minute_records, 0);
    }
}
