use std::path::Path;

use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};

use crate::models::interval::Interval;
use crate::models::ohlcv::{IndicatorRow, OhlcvRow};

/// Result of parsing a single 20-column CSV file.
pub struct ParsedCsv {
    pub ticker: String,
    pub interval: Interval,
    pub rows: Vec<OhlcvRow>,
    pub indicators: Vec<IndicatorRow>,
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

/// Parse an optional f64: empty string → None.
fn parse_opt_f64(s: &str) -> Option<f64> {
    let trimmed = s.trim();
    if trimmed.is_empty() {
        None
    } else {
        trimmed.parse().ok()
    }
}

/// Parse a single CSV record (20 columns) into OHLCV + indicator rows.
fn parse_record(
    record: &csv::StringRecord,
    ticker_id: i32,
    interval: Interval,
    interval_str: &str,
) -> Result<(OhlcvRow, IndicatorRow), String> {
    if record.len() < 20 {
        return Err(format!("expected 20 columns, got {}", record.len()));
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

    let ohlcv = OhlcvRow {
        ticker_id,
        interval: interval_str.to_owned(),
        time,
        open,
        high,
        low,
        close,
        volume,
    };

    let indicators = IndicatorRow {
        ticker_id,
        interval: interval_str.to_owned(),
        time,
        ma10: parse_opt_f64(&record[7]),
        ma20: parse_opt_f64(&record[8]),
        ma50: parse_opt_f64(&record[9]),
        ma100: parse_opt_f64(&record[10]),
        ma200: parse_opt_f64(&record[11]),
        ma10_score: parse_opt_f64(&record[12]),
        ma20_score: parse_opt_f64(&record[13]),
        ma50_score: parse_opt_f64(&record[14]),
        ma100_score: parse_opt_f64(&record[15]),
        ma200_score: parse_opt_f64(&record[16]),
        close_changed: parse_opt_f64(&record[17]),
        volume_changed: parse_opt_f64(&record[18]),
        total_money_changed: parse_opt_f64(&record[19]),
    };

    Ok((ohlcv, indicators))
}

/// Parse a 20-column CSV file into structured rows.
/// `ticker_id` fields are left as 0 — the caller must fill them in before DB insertion.
pub fn parse_csv(path: &Path) -> Result<ParsedCsv, String> {
    let ticker = ticker_from_path(path)?;
    let interval = Interval::from_filename(path)?;
    let interval_str = interval.as_str().to_owned();

    let file = std::fs::File::open(path)
        .map_err(|e| format!("cannot open {}: {e}", path.display()))?;

    let mut reader = csv::ReaderBuilder::new().from_reader(file);

    let mut rows = Vec::new();
    let mut indicators = Vec::new();

    for (i, result) in reader.records().enumerate() {
        let record = result.map_err(|e| format!("CSV read error at row {i}: {e}"))?;
        // Skip the header row
        if i == 0 && record.len() >= 2 && record[1].trim() == "time" {
            continue;
        }
        let (ohlcv, indicator) = parse_record(&record, 0, interval, &interval_str)?;
        rows.push(ohlcv);
        indicators.push(indicator);
    }

    Ok(ParsedCsv {
        ticker,
        interval,
        rows,
        indicators,
    })
}
