use crate::models::Interval;
use crate::services::csv_enhancer::{enhance_data, save_enhanced_csv};
use crate::services::vci::OhlcvData;
use chrono::{DateTime, NaiveDate, Utc};
use csv::Reader;
use std::collections::HashMap;
use std::path::Path;

/// Check if a ticker is an index (should not be multiplied by 1000)
fn is_index(ticker: &str) -> bool {
    matches!(ticker, "VNINDEX" | "VN30")
}

/// Apply price scaling for legacy import: multiply by 1000 for stocks, keep as-is for indices
/// Legacy CSV has human-readable prices (e.g., 49.8)
/// New system stores full numbers (e.g., 49800) to match API format
fn scale_price_legacy(price: f64, ticker: &str) -> f64 {
    if is_index(ticker) {
        price
    } else {
        price * 1000.0
    }
}

/// Parse time from string (supports multiple formats)
fn parse_time(time_str: &str) -> Result<DateTime<Utc>, Box<dyn std::error::Error>> {
    // Try RFC3339 first
    if let Ok(dt) = DateTime::parse_from_rfc3339(time_str) {
        return Ok(dt.with_timezone(&Utc));
    }

    // Try datetime format "YYYY-MM-DD HH:MM:SS"
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(time_str, "%Y-%m-%d %H:%M:%S") {
        return Ok(dt.and_utc());
    }

    // Try date only format "YYYY-MM-DD"
    let date = NaiveDate::parse_from_str(time_str, "%Y-%m-%d")?;
    Ok(date
        .and_hms_opt(0, 0, 0)
        .ok_or_else(|| "Failed to set time")?
        .and_utc())
}

/// Parse and convert a daily CSV file - NEW SINGLE-PHASE APPROACH
///
/// Reads from legacy format, converts to OhlcvData, enhances with indicators,
/// and writes enhanced 11-column CSV in one pass to the standard market_data directory.
///
/// Stock prices are multiplied by 1000, index prices are kept as-is.
///
/// Note: Output path is determined automatically as market_data/{ticker}/1D.csv
pub fn parse_daily_csv(input_path: &Path) -> Result<usize, Box<dyn std::error::Error>> {
    let mut reader = Reader::from_path(input_path)?;
    let mut ohlcv_data = Vec::new();
    let mut ticker_name = String::new();

    // Read all data rows and convert to OhlcvData
    for result in reader.records() {
        let record = result?;

        // Extract values
        let ticker = record.get(0).unwrap_or("");
        if ticker_name.is_empty() {
            ticker_name = ticker.to_string();
        }

        let time_str = record.get(1).unwrap_or("");
        let open: f64 = record.get(2).unwrap_or("").parse().unwrap_or(0.0);
        let high: f64 = record.get(3).unwrap_or("").parse().unwrap_or(0.0);
        let low: f64 = record.get(4).unwrap_or("").parse().unwrap_or(0.0);
        let close: f64 = record.get(5).unwrap_or("").parse().unwrap_or(0.0);
        let volume: u64 = record.get(6).unwrap_or("").parse().unwrap_or(0);

        // Scale prices for legacy import (multiply stocks by 1000, keep indices as-is)
        let scaled_open = scale_price_legacy(open, ticker);
        let scaled_high = scale_price_legacy(high, ticker);
        let scaled_low = scale_price_legacy(low, ticker);
        let scaled_close = scale_price_legacy(close, ticker);

        // Parse time
        let time = parse_time(time_str)?;

        let ohlcv = OhlcvData {
            time,
            open: scaled_open,
            high: scaled_high,
            low: scaled_low,
            close: scaled_close,
            volume,
            symbol: Some(ticker.to_string()),
        };
        ohlcv_data.push(ohlcv);
    }

    let count = ohlcv_data.len();

    if !ohlcv_data.is_empty() {
        // Create HashMap for enhance_data
        let mut data_map = HashMap::new();
        data_map.insert(ticker_name.clone(), ohlcv_data);

        // Enhance data in-memory (calculates MA, MA scores, close_changed, volume_changed)
        let enhanced = enhance_data(data_map);

        // Save enhanced data to CSV (11 columns) with cutoff date far in the past (to include all data)
        let cutoff_date = Utc::now() - chrono::Duration::days(36500); // ~100 years ago

        if let Some(stock_data) = enhanced.get(&ticker_name) {
            save_enhanced_csv(&ticker_name, stock_data, Interval::Daily, cutoff_date, false)?;
        }
    }

    Ok(count)
}

/// Parse and convert hourly/minute CSV file - NEW SINGLE-PHASE APPROACH
///
/// Reads from legacy format, converts to OhlcvData, enhances with indicators,
/// and writes enhanced 11-column CSV in one pass to the standard market_data directory.
///
/// Stock prices are multiplied by 1000, index prices are kept as-is.
///
/// Note: Output path is determined automatically as market_data/{ticker}/{interval}.csv
pub fn parse_intraday_csv(
    input_path: &Path,
    interval: Interval,
) -> Result<usize, Box<dyn std::error::Error>> {
    let mut reader = Reader::from_path(input_path)?;
    let mut ohlcv_data = Vec::new();
    let mut ticker_name = String::new();

    // Read all data rows and convert to OhlcvData
    for result in reader.records() {
        let record = result?;

        // Extract values
        let ticker = record.get(0).unwrap_or("");
        if ticker_name.is_empty() {
            ticker_name = ticker.to_string();
        }

        let time_str = record.get(1).unwrap_or("");
        let open: f64 = record.get(2).unwrap_or("").parse().unwrap_or(0.0);
        let high: f64 = record.get(3).unwrap_or("").parse().unwrap_or(0.0);
        let low: f64 = record.get(4).unwrap_or("").parse().unwrap_or(0.0);
        let close: f64 = record.get(5).unwrap_or("").parse().unwrap_or(0.0);
        let volume: u64 = record.get(6).unwrap_or("").parse().unwrap_or(0);

        // Scale prices for legacy import (multiply stocks by 1000, keep indices as-is)
        let scaled_open = scale_price_legacy(open, ticker);
        let scaled_high = scale_price_legacy(high, ticker);
        let scaled_low = scale_price_legacy(low, ticker);
        let scaled_close = scale_price_legacy(close, ticker);

        // Parse time
        let time = parse_time(time_str)?;

        let ohlcv = OhlcvData {
            time,
            open: scaled_open,
            high: scaled_high,
            low: scaled_low,
            close: scaled_close,
            volume,
            symbol: Some(ticker.to_string()),
        };
        ohlcv_data.push(ohlcv);
    }

    let count = ohlcv_data.len();

    if !ohlcv_data.is_empty() {
        // Create HashMap for enhance_data
        let mut data_map = HashMap::new();
        data_map.insert(ticker_name.clone(), ohlcv_data);

        // Enhance data in-memory (calculates MA, MA scores, close_changed, volume_changed)
        let enhanced = enhance_data(data_map);

        // Save enhanced data to CSV (11 columns) with cutoff date far in the past (to include all data)
        let cutoff_date = Utc::now() - chrono::Duration::days(36500); // ~100 years ago

        if let Some(stock_data) = enhanced.get(&ticker_name) {
            save_enhanced_csv(&ticker_name, stock_data, interval, cutoff_date, false)?;
        }
    }

    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_index() {
        assert!(is_index("VNINDEX"));
        assert!(is_index("VN30"));
        assert!(!is_index("VCB"));
        assert!(!is_index("FPT"));
    }

    #[test]
    fn test_scale_price_legacy() {
        // Stock ticker should be multiplied by 1000
        assert_eq!(scale_price_legacy(23.2, "VCB"), 23200.0);
        assert_eq!(scale_price_legacy(60.5, "FPT"), 60500.0);

        // Index should remain unchanged
        assert_eq!(scale_price_legacy(1250.5, "VNINDEX"), 1250.5);
        assert_eq!(scale_price_legacy(600.25, "VN30"), 600.25);
    }
}
