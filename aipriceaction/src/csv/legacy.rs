use std::path::Path;

use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};

use crate::models::interval::Interval;
use crate::models::ohlcv::OhlcvRow;

/// Result of parsing a single 20-column CSV file.
///
/// Only OHLCV data (columns 1-7) is extracted. Indicator columns (8-20)
/// are ignored — they are calculated on-the-fly at query time.
pub struct ParsedCsv {
    pub ticker: String,
    pub interval: Interval,
    pub rows: Vec<OhlcvRow>,
}

/// Extract the ticker symbol from the parent directory name of the CSV file.
fn ticker_from_path(path: &Path) -> Result<String, String> {
    path.parent()
        .and_then(|p| p.file_name())
        .and_then(|n| n.to_str())
        .map(|s| s.to_owned())
        .ok_or_else(|| format!("cannot extract ticker from path: {}", path.display()))
}

/// Parse a time string based on the interval type.
fn parse_time(s: &str, interval: Interval) -> Result<DateTime<Utc>, String> {
    let trimmed = s.trim();
    if trimmed.is_empty() {
        return Err("empty time string".into());
    }

    match interval {
        Interval::Daily => {
            // Format: YYYY-MM-DD
            let d = NaiveDate::parse_from_str(trimmed, "%Y-%m-%d")
                .map_err(|e| format!("invalid daily date '{trimmed}': {e}"))?;
            Ok(d
                .and_hms_opt(0, 0, 0)
                .unwrap()
                .and_utc())
        }
        Interval::Hourly | Interval::Minute => {
            // Format: YYYY-MM-DD HH:MM:SS or YYYY-MM-DDTHH:MM:SS
            if let Ok(ndt) = NaiveDateTime::parse_from_str(trimmed, "%Y-%m-%d %H:%M:%S") {
                return Ok(ndt.and_utc());
            }
            if let Ok(ndt) = NaiveDateTime::parse_from_str(trimmed, "%Y-%m-%dT%H:%M:%S") {
                return Ok(ndt.and_utc());
            }
            Err(format!("invalid intraday time '{trimmed}'"))
        }
    }
}

/// Parse a single CSV record (20 columns) into an OHLCV row.
/// Columns 8-20 (indicators) are parsed but discarded.
fn parse_record(
    record: &csv::StringRecord,
    ticker_id: i32,
    interval: Interval,
    interval_str: &str,
) -> Result<OhlcvRow, String> {
    if record.len() < 7 {
        return Err(format!("expected at least 7 columns, got {}", record.len()));
    }

    let time = parse_time(&record[1], interval)?;

    let open: f64 = record[2]
        .trim()
        .parse()
        .map_err(|e| format!("invalid open '{}': {e}", record[2].trim()))?;
    let high: f64 = record[3]
        .trim()
        .parse()
        .map_err(|e| format!("invalid high '{}': {e}", record[3].trim()))?;
    let low: f64 = record[4]
        .trim()
        .parse()
        .map_err(|e| format!("invalid low '{}': {e}", record[4].trim()))?;
    let close: f64 = record[5]
        .trim()
        .parse()
        .map_err(|e| format!("invalid close '{}': {e}", record[5].trim()))?;
    let volume: i64 = record[6]
        .trim()
        .parse()
        .map_err(|e| format!("invalid volume '{}': {e}", record[6].trim()))?;

    Ok(OhlcvRow {
        ticker_id,
        interval: interval_str.to_owned(),
        time,
        open,
        high,
        low,
        close,
        volume,
    })
}

/// Parse a CSV file into structured OHLCV rows.
/// `ticker_id` fields are left as 0 — the caller must fill them in before DB insertion.
pub fn parse_csv(path: &Path) -> Result<ParsedCsv, String> {
    let ticker = ticker_from_path(path)?;
    let interval = Interval::from_filename(path)?;
    let interval_str = interval.as_str().to_owned();

    let file = std::fs::File::open(path)
        .map_err(|e| format!("cannot open {}: {e}", path.display()))?;

    let mut reader = csv::ReaderBuilder::new().from_reader(file);

    let mut rows = Vec::new();

    for (i, result) in reader.records().enumerate() {
        let record = result.map_err(|e| format!("CSV read error at row {i}: {e}"))?;
        // Skip the header row
        if i == 0 && record.len() >= 2 && record[1].trim() == "time" {
            continue;
        }
        let ohlcv = parse_record(&record, 0, interval, &interval_str)?;
        rows.push(ohlcv);
    }

    Ok(ParsedCsv {
        ticker,
        interval,
        rows,
    })
}
