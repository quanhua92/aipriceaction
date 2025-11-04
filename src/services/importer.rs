use crate::models::TickerGroups;
use crate::services::csv_parser;
use std::fs;
use std::path::{Path, PathBuf};

/// Import legacy data from reference project structure
///
/// Reads CSV files from the legacy structure (market_data, market_data_hour, market_data_minutes)
/// and converts them to the new ticker-first structure with full price format.
///
/// # Arguments
/// * `source_path` - Path to the reference data directory (e.g., "./references/aipriceaction-data")
///
/// # Example
/// ```no_run
/// import_legacy(Path::new("./references/aipriceaction-data"))?;
/// ```
pub fn import_legacy(source_path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    println!("🚀 Starting legacy data import from: {}", source_path.display());

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

    println!("📊 Found {} tickers to import", tickers.len());

    let mut success_count = 0;
    let mut error_count = 0;

    for (index, ticker) in tickers.iter().enumerate() {
        let progress = index + 1;
        println!("\n[{}/{}] Processing: {}", progress, tickers.len(), ticker);

        match import_ticker(source_path, ticker) {
            Ok(stats) => {
                println!("  ✅ Success: {} files imported", stats.files_imported);
                if stats.daily_records > 0 {
                    println!("     Daily: {} records", stats.daily_records);
                }
                if stats.hourly_records > 0 {
                    println!("     Hourly: {} records", stats.hourly_records);
                }
                if stats.minute_records > 0 {
                    println!("     Minute: {} records", stats.minute_records);
                }
                success_count += 1;
            }
            Err(e) => {
                println!("  ❌ Error: {}", e);
                error_count += 1;
            }
        }
    }

    println!("\n✨ Import complete!");
    println!("   ✅ Success: {}", success_count);
    if error_count > 0 {
        println!("   ❌ Errors: {}", error_count);
    }

    Ok(())
}

struct ImportStats {
    files_imported: usize,
    daily_records: usize,
    hourly_records: usize,
    minute_records: usize,
}

/// Import a single ticker from all timeframes
fn import_ticker(source_path: &Path, ticker: &str) -> Result<ImportStats, Box<dyn std::error::Error>> {
    let mut stats = ImportStats {
        files_imported: 0,
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
        match csv_parser::parse_daily_csv(&daily_source, &daily_dest) {
            Ok(count) => {
                stats.files_imported += 1;
                stats.daily_records = count;
            }
            Err(e) => {
                return Err(format!("Failed to import daily data: {}", e).into());
            }
        }
    }

    // Import hourly data
    let hourly_source = source_path.join("market_data_hour").join(format!("{}.csv", ticker));
    let hourly_dest = ticker_dir.join("1h.csv");
    if hourly_source.exists() {
        match csv_parser::parse_intraday_csv(&hourly_source, &hourly_dest) {
            Ok(count) => {
                stats.files_imported += 1;
                stats.hourly_records = count;
            }
            Err(e) => {
                eprintln!("  ⚠️  Warning: Failed to import hourly data: {}", e);
            }
        }
    }

    // Import minute data
    let minute_source = source_path.join("market_data_minutes").join(format!("{}.csv", ticker));
    let minute_dest = ticker_dir.join("1m.csv");
    if minute_source.exists() {
        match csv_parser::parse_intraday_csv(&minute_source, &minute_dest) {
            Ok(count) => {
                stats.files_imported += 1;
                stats.minute_records = count;
            }
            Err(e) => {
                eprintln!("  ⚠️  Warning: Failed to import minute data: {}", e);
            }
        }
    }

    // Ensure at least one file was imported
    if stats.files_imported == 0 {
        return Err(format!("No data files found for ticker: {}", ticker).into());
    }

    Ok(stats)
}
