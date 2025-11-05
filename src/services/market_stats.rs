use crate::models::Interval;
use crate::utils::get_market_data_dir;
use std::fs;
use std::path::Path;

/// Market data statistics
#[derive(Debug, Clone)]
pub struct MarketStats {
    pub total_tickers: usize,
    pub has_data: bool,
}

/// Ticker information with all timeframes
#[derive(Debug, Clone)]
pub struct TickerInfo {
    pub ticker: String,
    pub daily: Option<TimeframeInfo>,
    pub hourly: Option<TimeframeInfo>,
    pub minute: Option<TimeframeInfo>,
}

/// Information about a specific timeframe
#[derive(Debug, Clone)]
pub struct TimeframeInfo {
    pub record_count: usize,
    pub first_date: String,
    pub last_date: String,
    pub last_close: f64,
}

/// Get overall market data statistics
pub fn get_market_stats() -> Result<MarketStats, Box<dyn std::error::Error>> {
    let market_data_path = get_market_data_dir();

    if !market_data_path.exists() {
        return Ok(MarketStats {
            total_tickers: 0,
            has_data: false,
        });
    }

    let ticker_count = fs::read_dir(market_data_path)?
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir())
        .count();

    Ok(MarketStats {
        total_tickers: ticker_count,
        has_data: ticker_count > 0,
    })
}

/// Get detailed information for a specific ticker
pub fn get_ticker_info(ticker: &str) -> Result<TickerInfo, Box<dyn std::error::Error>> {
    let ticker_path = get_market_data_dir().join(ticker);

    if !ticker_path.exists() {
        return Err(format!("Ticker '{}' not found", ticker).into());
    }

    Ok(TickerInfo {
        ticker: ticker.to_string(),
        daily: read_timeframe_info(&ticker_path.join(Interval::Daily.to_filename()))?,
        hourly: read_timeframe_info(&ticker_path.join(Interval::Hourly.to_filename()))?,
        minute: read_timeframe_info(&ticker_path.join(Interval::Minute.to_filename()))?,
    })
}

/// Read information about a specific timeframe file
fn read_timeframe_info(path: &Path) -> Result<Option<TimeframeInfo>, Box<dyn std::error::Error>> {
    if !path.exists() {
        return Ok(None);
    }

    let content = fs::read_to_string(path)?;
    let lines: Vec<&str> = content.lines().collect();

    if lines.len() <= 1 {
        return Ok(None);
    }

    let record_count = lines.len() - 1;
    let first_line = lines.get(1).unwrap_or(&"");
    let last_line = lines.last().unwrap_or(&"");

    Ok(Some(TimeframeInfo {
        record_count,
        first_date: extract_date(first_line),
        last_date: extract_date(last_line),
        last_close: extract_close(last_line),
    }))
}

/// Extract date from a CSV line
fn extract_date(line: &str) -> String {
    let parts: Vec<&str> = line.split(',').collect();
    if parts.len() > 1 {
        // Get just the date part (remove time if present)
        parts[1].split_whitespace().next().unwrap_or("N/A").to_string()
    } else {
        "N/A".to_string()
    }
}

/// Extract closing price from a CSV line
fn extract_close(line: &str) -> f64 {
    let parts: Vec<&str> = line.split(',').collect();
    if parts.len() > 5 {
        parts[5].parse().unwrap_or(0.0)
    } else {
        0.0
    }
}

/// Check if a ticker is an index
pub fn is_index(ticker: &str) -> bool {
    matches!(ticker, "VNINDEX" | "VN30")
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
}
