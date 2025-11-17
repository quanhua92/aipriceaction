use crate::constants::{CSV_BASIC_COLUMNS, CSV_ENHANCED_COLUMNS, MIN_RECORDS_FOR_MA50, MIN_RECORDS_FOR_ANALYSIS, INDEX_TICKERS};
use crate::models::{Interval, load_crypto_symbols, get_default_crypto_list_path};
use crate::utils::{get_market_data_dir, get_crypto_data_dir, parse_timestamp};
use chrono::{NaiveDate, NaiveDateTime};
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

#[derive(Debug)]
struct TickerReport {
    ticker: String,
    total_issues: usize,
    issues: Vec<String>,
}

#[derive(Debug)]
#[allow(dead_code)]
struct FileReport {
    interval: Interval,
    exists: bool,
    total_lines: usize,
    record_count: usize,
    header_valid: bool,
    corrupted_lines: Vec<usize>,
    missing_indicators: bool,
    insufficient_data: bool,
    date_gaps: Vec<String>,
    time_reversals: Vec<String>,
    first_date: Option<String>,
    last_date: Option<String>,
    duplicate_timestamps: HashMap<String, usize>, // timestamp -> count
}

/// Load all valid stock tickers from ticker_group.json and INDEX_TICKERS
fn load_stock_tickers() -> Option<HashSet<String>> {
    match std::fs::read_to_string("ticker_group.json") {
        Ok(content) => {
            match serde_json::from_str::<HashMap<String, Vec<String>>>(&content) {
                Ok(groups) => {
                    let mut all_tickers = HashSet::new();

                    // Add all stock tickers from ticker_group.json
                    for (_, tickers) in groups {
                        for ticker in tickers {
                            all_tickers.insert(ticker);
                        }
                    }

                    // Add index tickers (VNINDEX, VN30)
                    for index in INDEX_TICKERS {
                        all_tickers.insert(index.to_string());
                    }

                    Some(all_tickers)
                }
                Err(_) => None,
            }
        }
        Err(_) => None,
    }
}

pub fn run() {
    println!("🔍 Running health check on market data directories...\n");

    let mut has_any_issues = false;

    // Check both market_data and crypto_data
    let market_data_dir = get_market_data_dir();
    let crypto_data_dir = get_crypto_data_dir();

    // Load valid stock tickers from ticker_group.json
    let valid_stock_tickers = load_stock_tickers();

    // Load valid crypto symbols from crypto_top_100.json
    let valid_crypto_symbols = load_crypto_symbols(get_default_crypto_list_path())
        .ok()
        .map(|symbols| symbols.into_iter().collect::<HashSet<String>>());

    if market_data_dir.exists() {
        println!("📂 Checking market_data (VN stocks)...\n");
        let has_issues = check_directory(&market_data_dir, "VN Stocks", valid_stock_tickers.as_ref());
        has_any_issues = has_any_issues || has_issues;
        println!();
    } else {
        println!("⚠️  market_data directory not found\n");
    }

    if crypto_data_dir.exists() {
        println!("📂 Checking crypto_data (Cryptocurrencies)...\n");
        let has_issues = check_directory(&crypto_data_dir, "Crypto", valid_crypto_symbols.as_ref());
        has_any_issues = has_any_issues || has_issues;
    } else {
        println!("⚠️  crypto_data directory not found\n");
    }

    if has_any_issues {
        std::process::exit(1);
    }
}

fn check_directory(data_dir: &PathBuf, data_type: &str, valid_tickers: Option<&HashSet<String>>) -> bool {
    let mut ticker_reports: Vec<TickerReport> = Vec::new();
    let mut total_tickers = 0;
    let mut total_issues = 0;
    let mut unknown_tickers: Vec<String> = Vec::new();

    // Get all ticker directories
    let entries = match std::fs::read_dir(data_dir) {
        Ok(entries) => entries,
        Err(e) => {
            eprintln!("❌ Failed to read {} directory: {}", data_type, e);
            return true; // Has issues
        }
    };

    let mut ticker_dirs: Vec<_> = entries
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir())
        .collect();
    ticker_dirs.sort_by_key(|e| e.file_name());

    let total_ticker_count = ticker_dirs.len();
    println!("📋 Scanning {} tickers...\n", total_ticker_count);

    for entry in ticker_dirs {
        let ticker_dir = entry.path();
        let ticker = match ticker_dir.file_name().and_then(|n| n.to_str()) {
            Some(t) => t.to_string(),
            None => continue,
        };

        // Validate ticker against known list
        if let Some(valid_set) = valid_tickers {
            if !valid_set.contains(&ticker) {
                unknown_tickers.push(ticker.clone());
                println!("   ⚠️  Unknown ticker: {} (not in official {} ticker list)", ticker, data_type);
                continue; // Skip processing unknown tickers
            }
        }

        total_tickers += 1;
        print!("   [{:>3}/{}] Checking {}... ", total_tickers, total_ticker_count, ticker);
        std::io::Write::flush(&mut std::io::stdout()).unwrap();

        // Check each interval
        let intervals = vec![Interval::Daily, Interval::Hourly, Interval::Minute];
        let mut ticker_issues = Vec::new();
        let mut daily_date_range: Option<String> = None;

        for interval in intervals {
            let csv_path = ticker_dir.join(interval.to_filename());
            let report = check_csv_file(&ticker, &csv_path, interval);

            // Capture daily date range for display
            if interval == Interval::Daily && report.exists && report.first_date.is_some() && report.last_date.is_some() {
                daily_date_range = Some(format!("{} → {}",
                    report.first_date.as_ref().unwrap(),
                    report.last_date.as_ref().unwrap()
                ));
            }

            // Collect issues from this file
            if !report.exists {
                ticker_issues.push(format!("  ⚠️  {} - File missing", interval.to_filename()));
            } else {
                if !report.header_valid {
                    ticker_issues.push(format!("  ❌ {} - Invalid header", interval.to_filename()));
                }

                if !report.corrupted_lines.is_empty() {
                    ticker_issues.push(format!(
                        "  ❌ {} - {} corrupted lines at: {}",
                        interval.to_filename(),
                        report.corrupted_lines.len(),
                        format_line_numbers(&report.corrupted_lines)
                    ));
                }

                if report.record_count == 0 {
                    ticker_issues.push(format!("  ⚠️  {} - No records found", interval.to_filename()));
                } else if report.insufficient_data {
                    ticker_issues.push(format!(
                        "  ⚠️  {} - Only {} records (need {}+ for MA50)",
                        interval.to_filename(),
                        report.record_count,
                        MIN_RECORDS_FOR_MA50
                    ));
                } else if interval == Interval::Daily && report.record_count < MIN_RECORDS_FOR_ANALYSIS {
                    let date_info = if report.first_date.is_some() && report.last_date.is_some() {
                        format!(" [{} → {}]", report.first_date.as_ref().unwrap(), report.last_date.as_ref().unwrap())
                    } else {
                        String::new()
                    };
                    ticker_issues.push(format!(
                        "  ⚠️  {} - Only {} records{} (recommended {}+ for historical analysis)",
                        interval.to_filename(),
                        report.record_count,
                        date_info,
                        MIN_RECORDS_FOR_ANALYSIS
                    ));
                }

                if report.missing_indicators && report.record_count >= MIN_RECORDS_FOR_MA50 {
                    ticker_issues.push(format!(
                        "  ⚠️  {} - Missing technical indicators (should run enhancement)",
                        interval.to_filename()
                    ));
                }

                if !report.date_gaps.is_empty() && report.date_gaps.len() <= 5 {
                    for gap in &report.date_gaps {
                        ticker_issues.push(format!("  ⚠️  {} - {}", interval.to_filename(), gap));
                    }
                }

                if !report.time_reversals.is_empty() {
                    for reversal in &report.time_reversals {
                        ticker_issues.push(format!("  ❌ {} - {}", interval.to_filename(), reversal));
                    }
                }

                // Check for duplicate timestamps (like the 512x bug)
                let duplicates: Vec<(&String, usize)> = report.duplicate_timestamps
                    .iter()
                    .filter(|(_, count)| **count > 1)
                    .map(|(ts, count)| (ts, *count))
                    .collect();

                if !duplicates.is_empty() {
                    // Find the worst duplicate
                    let mut max_count = 0;
                    let mut max_timestamp = String::new();
                    for (ts, count) in &duplicates {
                        if *count > max_count {
                            max_count = *count;
                            max_timestamp = (*ts).clone();
                        }
                    }
                    let total_dup_timestamps = duplicates.len();
                    ticker_issues.push(format!(
                        "  ❌ {} - CRITICAL: {} timestamps duplicated! Max: {} appears {}x (file bloated {}x)",
                        interval.to_filename(),
                        total_dup_timestamps,
                        max_timestamp,
                        max_count,
                        max_count
                    ));
                }
            }
        }

        if !ticker_issues.is_empty() {
            total_issues += ticker_issues.len();
            if let Some(date_range) = daily_date_range {
                println!("⚠️  {} issues [{}]", ticker_issues.len(), date_range);
            } else {
                println!("⚠️  {} issues", ticker_issues.len());
            }
            ticker_reports.push(TickerReport {
                ticker: ticker.clone(),
                total_issues: ticker_issues.len(),
                issues: ticker_issues,
            });
        } else {
            if let Some(date_range) = daily_date_range {
                println!("✅ [{}]", date_range);
            } else {
                println!("✅");
            }
        }
    }

    println!();

    // Print summary
    println!("📊 Health Check Summary");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("Total tickers scanned: {}", total_tickers);
    println!("Unknown tickers found: {}", unknown_tickers.len());
    println!("Tickers with issues:   {}", ticker_reports.len());
    println!("Total issues found:    {}", total_issues);
    println!();

    // Print unknown tickers if found
    if !unknown_tickers.is_empty() {
        println!("❌ Unknown Tickers (not in official {} ticker list):\n", data_type);
        for ticker in &unknown_tickers {
            println!("  - {}", ticker);
        }
        println!();
    }

    if ticker_reports.is_empty() && unknown_tickers.is_empty() {
        println!("✅ All {} tickers are healthy! No issues found.", data_type);
        return false; // No issues
    }

    // Print detailed issues
    println!("📋 Detailed Issues:\n");
    for report in &ticker_reports {
        println!("🏷️  {} ({} issues):", report.ticker, report.total_issues);
        for issue in &report.issues {
            println!("{}", issue);
        }
        println!();
    }

    true // Has issues
}

fn check_csv_file(_ticker: &str, csv_path: &Path, interval: Interval) -> FileReport {
    if !csv_path.exists() {
        return FileReport {
            interval,
            exists: false,
            total_lines: 0,
            record_count: 0,
            header_valid: false,
            corrupted_lines: Vec::new(),
            missing_indicators: false,
            insufficient_data: false,
            date_gaps: Vec::new(),
            time_reversals: Vec::new(),
            first_date: None,
            last_date: None,
            duplicate_timestamps: HashMap::new(),
        };
    }

    let file = match File::open(csv_path) {
        Ok(f) => f,
        Err(_) => {
            return FileReport {
                interval,
                exists: true,
                total_lines: 0,
                record_count: 0,
                header_valid: false,
                corrupted_lines: Vec::new(),
                missing_indicators: false,
                insufficient_data: false,
                date_gaps: Vec::new(),
                time_reversals: Vec::new(),
                first_date: None,
                last_date: None,
                duplicate_timestamps: HashMap::new(),
            }
        }
    };

    let reader = BufReader::new(file);
    let mut total_lines = 0;
    let mut record_count = 0;
    let mut header_valid = false;
    let mut corrupted_lines = Vec::new();
    let mut missing_indicators = false;
    let mut has_any_indicators = false;
    let mut last_date: Option<NaiveDate> = None;
    let mut last_timestamp: Option<NaiveDateTime> = None;
    let date_gaps = Vec::new();
    let mut time_reversals = Vec::new();
    let mut first_date_str: Option<String> = None;
    let mut last_date_str: Option<String> = None;
    let mut duplicate_timestamps: HashMap<String, usize> = HashMap::new();

    for (line_num, line_result) in reader.lines().enumerate() {
        total_lines += 1;
        let line = match line_result {
            Ok(l) => l,
            Err(_) => {
                corrupted_lines.push(line_num + 1);
                continue;
            }
        };

        // Check header
        if line_num == 0 {
            header_valid = line.starts_with("ticker,") || line.starts_with("symbol,");
            if header_valid && line.contains("ma10") {
                has_any_indicators = true;
            }
            continue;
        }

        let fields: Vec<&str> = line.split(',').collect();
        let field_count = fields.len();

        // Valid CSV should have CSV_BASIC_COLUMNS (basic) or CSV_ENHANCED_COLUMNS (with indicators) fields
        if field_count != CSV_BASIC_COLUMNS && field_count != CSV_ENHANCED_COLUMNS {
            corrupted_lines.push(line_num + 1);
            continue;
        }

        record_count += 1;

        // Check if indicators are present (field 7+ should be non-empty)
        if field_count == CSV_ENHANCED_COLUMNS && record_count >= MIN_RECORDS_FOR_MA50 {
            // Check if MA10 field (index 7) is non-empty
            if fields.len() > 7 && !fields[7].is_empty() {
                has_any_indicators = true;
            }
        }

        // Check time sequence (field 1 is the timestamp)
        if fields.len() >= 2 {
            let timestamp_str = fields[1];

            // Track duplicate timestamps
            *duplicate_timestamps.entry(timestamp_str.to_string()).or_insert(0) += 1;

            // Capture first date
            if first_date_str.is_none() {
                first_date_str = Some(timestamp_str.to_string());
            }
            // Always update last date
            last_date_str = Some(timestamp_str.to_string());

            // Try to parse timestamp using centralized utility
            if let Ok(parsed_dt) = parse_timestamp(timestamp_str) {
                let parsed_naive = parsed_dt.naive_utc();

                // Check if it's a datetime or date-only by checking if it has a time component
                if timestamp_str.contains(' ') || timestamp_str.contains('T') {
                    // Intraday data with time
                    if let Some(prev_ts) = last_timestamp {
                        if parsed_naive <= prev_ts {
                            time_reversals.push(format!("Time not increasing: {} comes after {}", timestamp_str, prev_ts));
                        }
                    }
                    last_timestamp = Some(parsed_naive);
                } else {
                    // Daily data - just check dates
                    let date = parsed_naive.date();
                    if let Some(prev_date) = last_date {
                        if date <= prev_date {
                            time_reversals.push(format!("Time not increasing: {} comes after {}", timestamp_str, prev_date));
                        }
                    }
                    last_date = Some(date);
                }
            }
        }
    }

    // Determine if indicators are missing
    if record_count >= MIN_RECORDS_FOR_MA50 && !has_any_indicators {
        missing_indicators = true;
    }

    FileReport {
        interval,
        exists: true,
        total_lines,
        record_count,
        header_valid,
        corrupted_lines,
        missing_indicators,
        insufficient_data: record_count < MIN_RECORDS_FOR_MA50,
        date_gaps,
        time_reversals,
        first_date: first_date_str,
        last_date: last_date_str,
        duplicate_timestamps,
    }
}

fn format_line_numbers(lines: &[usize]) -> String {
    if lines.len() <= 5 {
        lines.iter()
            .map(|n| n.to_string())
            .collect::<Vec<_>>()
            .join(", ")
    } else {
        format!(
            "{}, ... ({} more)",
            lines.iter()
                .take(3)
                .map(|n| n.to_string())
                .collect::<Vec<_>>()
                .join(", "),
            lines.len() - 3
        )
    }
}
