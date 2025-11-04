use serde::{Deserialize, Serialize};
use std::fmt;

/// Timeframe for stock data
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Timeframe {
    /// 1-minute candles
    Minute1,
    /// 5-minute candles
    Minute5,
    /// 15-minute candles
    Minute15,
    /// 30-minute candles
    Minute30,
    /// 1-hour candles
    Hour1,
    /// Daily candles
    Day1,
    /// Weekly candles
    Week1,
    /// Monthly candles
    Month1,
}

impl Timeframe {
    /// Convert to interval string representation
    pub fn to_interval_string(&self) -> &'static str {
        match self {
            Timeframe::Minute1 => "1m",
            Timeframe::Minute5 => "5m",
            Timeframe::Minute15 => "15m",
            Timeframe::Minute30 => "30m",
            Timeframe::Hour1 => "1H",
            Timeframe::Day1 => "1D",
            Timeframe::Week1 => "1W",
            Timeframe::Month1 => "1M",
        }
    }

    /// Get the directory name for this timeframe
    pub fn directory_name(&self) -> &'static str {
        match self {
            Timeframe::Minute1 | Timeframe::Minute5 | Timeframe::Minute15 | Timeframe::Minute30 => {
                "market_data_minutes"
            }
            Timeframe::Hour1 => "market_data_hour",
            Timeframe::Day1 | Timeframe::Week1 | Timeframe::Month1 => "market_data",
        }
    }

    /// Get all available timeframes
    pub fn all() -> Vec<Timeframe> {
        vec![
            Timeframe::Minute1,
            Timeframe::Minute5,
            Timeframe::Minute15,
            Timeframe::Minute30,
            Timeframe::Hour1,
            Timeframe::Day1,
            Timeframe::Week1,
            Timeframe::Month1,
        ]
    }
}

impl fmt::Display for Timeframe {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.to_interval_string())
    }
}

impl Default for Timeframe {
    fn default() -> Self {
        Timeframe::Day1
    }
}
