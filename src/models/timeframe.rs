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

    /// Get the directory name for this timeframe (legacy reference project structure)
    pub fn directory_name(&self) -> &'static str {
        match self {
            Timeframe::Minute1 | Timeframe::Minute5 | Timeframe::Minute15 | Timeframe::Minute30 => {
                "market_data_minutes"
            }
            Timeframe::Hour1 => "market_data_hour",
            Timeframe::Day1 | Timeframe::Week1 | Timeframe::Month1 => "market_data",
        }
    }

    /// Get the filename for this timeframe in the new ticker-first structure
    ///
    /// # Returns
    /// Filename like "1D.csv", "1H.csv", "5m.csv"
    pub fn to_filename(&self) -> &'static str {
        match self {
            Timeframe::Minute1 => "1m.csv",
            Timeframe::Minute5 => "5m.csv",
            Timeframe::Minute15 => "15m.csv",
            Timeframe::Minute30 => "30m.csv",
            Timeframe::Hour1 => "1H.csv",
            Timeframe::Day1 => "1D.csv",
            Timeframe::Week1 => "weekly.csv",
            Timeframe::Month1 => "monthly.csv",
        }
    }

    /// Get the full data path for a ticker in the new structure
    ///
    /// # Arguments
    /// * `ticker` - The ticker symbol (e.g., "VCB", "FPT", "VNINDEX")
    ///
    /// # Returns
    /// Path like "market_data/VCB/1D.csv"
    ///
    /// # Example
    /// ```
    /// use aipriceaction::models::Timeframe;
    ///
    /// let path = Timeframe::Day1.get_data_path("VCB");
    /// assert_eq!(path, "market_data/VCB/1D.csv");
    /// ```
    pub fn get_data_path(&self, ticker: &str) -> String {
        format!("market_data/{}/{}", ticker, self.to_filename())
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
