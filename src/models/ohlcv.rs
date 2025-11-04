use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Basic OHLCV (Open, High, Low, Close, Volume) data point
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Ohlcv {
    /// Timestamp of the data point
    #[serde(with = "chrono::serde::ts_seconds")]
    pub time: DateTime<Utc>,

    /// Opening price
    pub open: f64,

    /// Highest price
    pub high: f64,

    /// Lowest price
    pub low: f64,

    /// Closing price
    pub close: f64,

    /// Trading volume
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
