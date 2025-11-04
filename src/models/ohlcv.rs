use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Basic OHLCV (Open, High, Low, Close, Volume) data point
///
/// # Price Format
/// **IMPORTANT**: All prices (open, high, low, close) are stored in **full format**.
///
/// ## Stock Tickers (VCB, FPT, HPG, etc.)
/// - CSV stores: 23.2 (price/1000)
/// - **Store as**: 23200.0 (multiply by 1000)
///
/// ## Market Indices (VNINDEX, VN30)
/// - CSV stores: 1250.5 (actual value)
/// - **Store as**: 1250.5 (no conversion)
///
/// **Rule**: Multiply by 1000 ONLY for stock tickers, NOT for indices.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Ohlcv {
    /// Timestamp of the data point
    #[serde(with = "chrono::serde::ts_seconds")]
    pub time: DateTime<Utc>,

    /// Opening price in full VND (e.g., 23200, not 23.2)
    pub open: f64,

    /// Highest price in full VND (e.g., 23700, not 23.7)
    pub high: f64,

    /// Lowest price in full VND (e.g., 22600, not 22.6)
    pub low: f64,

    /// Closing price in full VND (e.g., 23700, not 23.7)
    pub close: f64,

    /// Trading volume (number of shares)
    pub volume: u64,

    /// Optional ticker symbol
    #[serde(skip_serializing_if = "Option::is_none")]
    pub symbol: Option<String>,
}

impl Ohlcv {
    /// Create a new OHLCV data point
    pub fn new(
        time: DateTime<Utc>,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        volume: u64,
    ) -> Self {
        Self {
            time,
            open,
            high,
            low,
            close,
            volume,
            symbol: None,
        }
    }

    /// Create a new OHLCV data point with symbol
    pub fn with_symbol(
        time: DateTime<Utc>,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        volume: u64,
        symbol: String,
    ) -> Self {
        Self {
            time,
            open,
            high,
            low,
            close,
            volume,
            symbol: Some(symbol),
        }
    }
}
