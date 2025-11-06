use crate::models::Interval;
use crate::utils::get_market_data_dir;
use chrono::{NaiveDate, NaiveDateTime};
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

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
}

pub fn run() {
    println!("🔍 Running health check on market_data...\n");

    let market_data_dir = get_market_data_dir();
    if !market_data_dir.exists() {
        eprintln!("❌ Error: market_data directory not found");
        std::process::exit(1);
    }

    let mut ticker_reports: Vec<TickerReport> = Vec::new();
    let mut total_tickers = 0;
    let mut total_issues = 0;

    // Get all ticker directories
    let entries = match std::fs::read_dir(market_data_dir) {
        Ok(entries) => entries,
        Err(e) => {
            eprintln!("❌ Failed to read market_data directory: {}", e);
            std::process::exit(1);
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

        total_tickers += 1;
        print!("   [{:>3}/{}] Checking {}... ", total_tickers, total_ticker_count, ticker);
        std::io::Write::flush(&mut std::io::stdout()).unwrap();

        // Check each interval
        let intervals = vec![Interval::Daily, Interval::Hourly, Interval::Minute];
        let mut ticker_issues = Vec::new();

        for interval in intervals {
            let csv_path = ticker_dir.join(interval.to_filename());
            let report = check_csv_file(&ticker, &csv_path, interval);

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
                        "  ⚠️  {} - Only {} records (need 50+ for MA50)",
                        interval.to_filename(),
                        report.record_count
                    ));
                } else if interval == Interval::Daily && report.record_count < 2000 {
                    ticker_issues.push(format!(
                        "  ⚠️  {} - Only {} records (recommended 2000+ for historical analysis)",
                        interval.to_filename(),
                        report.record_count
                    ));
                }

                if report.missing_indicators && report.record_count >= 50 {
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
            }
        }

        if !ticker_issues.is_empty() {
            total_issues += ticker_issues.len();
            println!("⚠️  {} issues", ticker_issues.len());
            ticker_reports.push(TickerReport {
                ticker: ticker.clone(),
                total_issues: ticker_issues.len(),
                issues: ticker_issues,
            });
        } else {
            println!("✅");
        }
    }

    println!();

    // Print summary
    println!("📊 Health Check Summary");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("Total tickers scanned: {}", total_tickers);
    println!("Tickers with issues:   {}", ticker_reports.len());
    println!("Total issues found:    {}", total_issues);
    println!();

    if ticker_reports.is_empty() {
        println!("✅ All tickers are healthy! No issues found.");
        return;
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

    // Exit with error code if issues found
    std::process::exit(1);
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

        // Valid CSV should have 7 (basic) or 16 (with indicators) fields
        if field_count != 7 && field_count != 16 {
            corrupted_lines.push(line_num + 1);
            continue;
        }

        record_count += 1;

        // Check if indicators are present (field 7+ should be non-empty)
        if field_count == 16 && record_count >= 50 {
            // Check if MA10 field (index 7) is non-empty
            if fields.len() > 7 && !fields[7].is_empty() {
                has_any_indicators = true;
            }
        }

        // Check time sequence (field 1 is the timestamp)
        if fields.len() >= 2 {
            let timestamp_str = fields[1];

            // Try to parse as full datetime first (for intraday data)
            if timestamp_str.contains(' ') {
                if let Ok(current_ts) = NaiveDateTime::parse_from_str(timestamp_str, "%Y-%m-%d %H:%M:%S") {
                    if let Some(prev_ts) = last_timestamp {
                        if current_ts <= prev_ts {
                            time_reversals.push(format!("Time not increasing: {} comes after {}", timestamp_str, prev_ts));
                        }
                    }
                    last_timestamp = Some(current_ts);
                }
            } else {
                // Daily data - just check dates
                if let Ok(date) = NaiveDate::parse_from_str(timestamp_str, "%Y-%m-%d") {
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
    if record_count >= 50 && !has_any_indicators {
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
        insufficient_data: record_count < 50,
        date_gaps,
        time_reversals,
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
