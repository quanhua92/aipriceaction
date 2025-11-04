use csv::Reader;
use std::path::Path;

/// Check if a ticker is an index (should not be multiplied by 1000)
fn is_index(ticker: &str) -> bool {
    matches!(ticker, "VNINDEX" | "VN30")
}

/// Apply price scaling: multiply by 1000 for stocks, keep as-is for indices
fn scale_price(price: f64, ticker: &str) -> f64 {
    if is_index(ticker) {
        price
    } else {
        price * 1000.0
    }
}

/// Parse and convert a daily CSV file (16 columns with indicators)
///
/// Reads from legacy format and converts prices according to ticker type.
/// Stock prices are multiplied by 1000, index prices are kept as-is.
pub fn parse_daily_csv(input_path: &Path, output_path: &Path) -> Result<usize, Box<dyn std::error::Error>> {
    let mut reader = Reader::from_path(input_path)?;
    let mut writer = csv::Writer::from_path(output_path)?;

    // Write header
    writer.write_record(&[
        "ticker", "time", "open", "high", "low", "close", "volume",
        "ma10", "ma20", "ma50", "ma10_score", "ma20_score", "ma50_score",
        "money_flow", "dollar_flow", "trend_score"
    ])?;

    let mut count = 0;
    for result in reader.records() {
        let record = result?;

        // Extract values
        let ticker = record.get(0).unwrap_or("");
        let time = record.get(1).unwrap_or("");
        let open: f64 = record.get(2).unwrap_or("").parse().unwrap_or(0.0);
        let high: f64 = record.get(3).unwrap_or("").parse().unwrap_or(0.0);
        let low: f64 = record.get(4).unwrap_or("").parse().unwrap_or(0.0);
        let close: f64 = record.get(5).unwrap_or("").parse().unwrap_or(0.0);
        let volume = record.get(6).unwrap_or("");

        // Moving averages (may be empty)
        let ma10_str = record.get(7).unwrap_or("");
        let ma20_str = record.get(8).unwrap_or("");
        let ma50_str = record.get(9).unwrap_or("");

        let ma10: Option<f64> = ma10_str.parse().ok();
        let ma20: Option<f64> = ma20_str.parse().ok();
        let ma50: Option<f64> = ma50_str.parse().ok();

        // Scores and flows (may be empty)
        let ma10_score = record.get(10).unwrap_or("");
        let ma20_score = record.get(11).unwrap_or("");
        let ma50_score = record.get(12).unwrap_or("");
        let money_flow = record.get(13).unwrap_or("");
        let dollar_flow = record.get(14).unwrap_or("");
        let trend_score = record.get(15).unwrap_or("");

        // Scale prices
        let scaled_open = scale_price(open, ticker);
        let scaled_high = scale_price(high, ticker);
        let scaled_low = scale_price(low, ticker);
        let scaled_close = scale_price(close, ticker);
        let scaled_ma10 = ma10.map(|v| scale_price(v, ticker));
        let scaled_ma20 = ma20.map(|v| scale_price(v, ticker));
        let scaled_ma50 = ma50.map(|v| scale_price(v, ticker));

        // Write record with scaled prices
        writer.write_record(&[
            ticker,
            time,
            &scaled_open.to_string(),
            &scaled_high.to_string(),
            &scaled_low.to_string(),
            &scaled_close.to_string(),
            volume,
            &scaled_ma10.map_or(String::new(), |v| v.to_string()),
            &scaled_ma20.map_or(String::new(), |v| v.to_string()),
            &scaled_ma50.map_or(String::new(), |v| v.to_string()),
            ma10_score,
            ma20_score,
            ma50_score,
            money_flow,
            dollar_flow,
            trend_score,
        ])?;

        count += 1;
    }

    writer.flush()?;
    Ok(count)
}

/// Parse and convert hourly/minute CSV file (7 columns, OHLCV only)
///
/// Reads from legacy format and converts prices according to ticker type.
/// Stock prices are multiplied by 1000, index prices are kept as-is.
pub fn parse_intraday_csv(input_path: &Path, output_path: &Path) -> Result<usize, Box<dyn std::error::Error>> {
    let mut reader = Reader::from_path(input_path)?;
    let mut writer = csv::Writer::from_path(output_path)?;

    // Write header
    writer.write_record(&["ticker", "time", "open", "high", "low", "close", "volume"])?;

    let mut count = 0;
    for result in reader.records() {
        let record = result?;

        // Extract values
        let ticker = record.get(0).unwrap_or("");
        let time = record.get(1).unwrap_or("");
        let open: f64 = record.get(2).unwrap_or("").parse().unwrap_or(0.0);
        let high: f64 = record.get(3).unwrap_or("").parse().unwrap_or(0.0);
        let low: f64 = record.get(4).unwrap_or("").parse().unwrap_or(0.0);
        let close: f64 = record.get(5).unwrap_or("").parse().unwrap_or(0.0);
        let volume = record.get(6).unwrap_or("");

        // Scale prices
        let scaled_open = scale_price(open, ticker);
        let scaled_high = scale_price(high, ticker);
        let scaled_low = scale_price(low, ticker);
        let scaled_close = scale_price(close, ticker);

        // Write record with scaled prices
        writer.write_record(&[
            ticker,
            time,
            &scaled_open.to_string(),
            &scaled_high.to_string(),
            &scaled_low.to_string(),
            &scaled_close.to_string(),
            volume,
        ])?;

        count += 1;
    }

    writer.flush()?;
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
    fn test_scale_price() {
        // Stock ticker should be multiplied by 1000
        assert_eq!(scale_price(23.2, "VCB"), 23200.0);
        assert_eq!(scale_price(60.5, "FPT"), 60500.0);

        // Index should remain unchanged
        assert_eq!(scale_price(1250.5, "VNINDEX"), 1250.5);
        assert_eq!(scale_price(600.25, "VN30"), 600.25);
    }
}
