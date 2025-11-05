//! CSV Validation and Repair Service
//!
//! Detects and repairs corrupted CSV files before sync operations.

use crate::error::Error;
use crate::models::Interval;
use chrono::NaiveDate;
use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;

/// Report of corruption found in a CSV file
#[derive(Debug)]
pub struct CorruptionReport {
    pub ticker: String,
    pub corrupted_lines: Vec<usize>,
    pub last_valid_date: Option<NaiveDate>,
    pub total_lines: usize,
    pub removed_lines: usize,
}

/// Validate and repair all CSV files for an interval
pub fn validate_and_repair_interval(
    interval: Interval,
    market_data_dir: &Path,
) -> Result<Vec<CorruptionReport>, Error> {
    let mut reports = Vec::new();

    // Scan all ticker directories
    let entries = std::fs::read_dir(market_data_dir)
        .map_err(|e| Error::Io(format!("Failed to read market_data dir: {}", e)))?;

    for entry in entries {
        let entry = entry.map_err(|e| Error::Io(format!("Failed to read entry: {}", e)))?;
        let ticker_dir = entry.path();

        if !ticker_dir.is_dir() {
            continue;
        }

        let ticker = ticker_dir
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| Error::Io("Invalid ticker directory name".to_string()))?
            .to_string();

        let csv_path = ticker_dir.join(interval.to_filename());
        if !csv_path.exists() {
            continue;
        }

        // Validate and repair this CSV file
        if let Some(report) = validate_and_repair_csv(&ticker, &csv_path, interval)? {
            reports.push(report);
        }
    }

    Ok(reports)
}

/// Validate and repair a single CSV file
fn validate_and_repair_csv(
    ticker: &str,
    csv_path: &Path,
    interval: Interval,
) -> Result<Option<CorruptionReport>, Error> {
    let file = File::open(csv_path)
        .map_err(|e| Error::Io(format!("Failed to open {}: {}", csv_path.display(), e)))?;
    let reader = BufReader::new(file);

    let mut valid_lines = Vec::new();
    let mut corrupted_line_numbers = Vec::new();
    let mut last_valid_date: Option<NaiveDate> = None;
    let mut total_lines = 0;

    for (line_num, line_result) in reader.lines().enumerate() {
        total_lines += 1;
        let line = line_result
            .map_err(|e| Error::Io(format!("Failed to read line {}: {}", line_num + 1, e)))?;

        // Header line
        if line_num == 0 {
            valid_lines.push(line.clone());
            continue;
        }

        let fields: Vec<&str> = line.split(',').collect();
        let field_count = fields.len();

        // Check if this line has the correct number of fields
        let is_valid = field_count == 7 || field_count == 16;

        if !is_valid {
            corrupted_line_numbers.push(line_num + 1);
            tracing::warn!(
                ticker,
                line_num = line_num + 1,
                field_count,
                "Detected corrupted line, removing"
            );
            continue;
        }

        // Parse date from valid line
        if fields.len() >= 2 {
            if let Ok(date) = NaiveDate::parse_from_str(fields[1], "%Y-%m-%d") {
                last_valid_date = Some(date);
            }
        }

        valid_lines.push(line);
    }

    // If no corruption found, return None
    let removed_lines = corrupted_line_numbers.len();
    if removed_lines == 0 {
        return Ok(None);
    }

    // Write repaired CSV file
    let mut file = File::create(csv_path)
        .map_err(|e| Error::Io(format!("Failed to write {}: {}", csv_path.display(), e)))?;

    for line in &valid_lines {
        writeln!(file, "{}", line)
            .map_err(|e| Error::Io(format!("Failed to write line: {}", e)))?;
    }

    tracing::info!(
        ticker,
        interval = %interval.to_filename(),
        total_lines,
        removed = removed_lines,
        last_valid_date = ?last_valid_date,
        "Repaired corrupted CSV file"
    );

    Ok(Some(CorruptionReport {
        ticker: ticker.to_string(),
        corrupted_lines: corrupted_line_numbers,
        last_valid_date,
        total_lines,
        removed_lines,
    }))
}
