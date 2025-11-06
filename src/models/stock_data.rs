use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Stock data with OHLCV and technical indicators
///
/// # Price Format
/// **IMPORTANT**: All prices and MAs are stored in **full format**.
///
/// ## Stock Tickers (VCB, FPT, HPG, etc.)
/// - CSV stores: 23.2 (price/1000)
/// - **Store as**: 23200.0 (multiply by 1000)
/// - Applies to: OHLC prices and moving averages
///
/// ## Market Indices (VNINDEX, VN30)
/// - CSV stores: 1250.5 (actual value)
/// - **Store as**: 1250.5 (no conversion)
///
/// ## MA Scores
/// - Always percentages (format-independent)
/// - No conversion needed
///
/// **Rule**: Multiply by 1000 ONLY for stock tickers, NOT for indices.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StockData {
    /// Timestamp of the data point
    #[serde(with = "chrono::serde::ts_seconds")]
    pub time: DateTime<Utc>,

    /// Ticker symbol
    pub ticker: String,

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

    // Moving Averages
    /// 10-period moving average in full VND (e.g., 22500, not 22.5)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma10: Option<f64>,

    /// 20-period moving average in full VND (e.g., 21800, not 21.8)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma20: Option<f64>,

    /// 50-period moving average in full VND (e.g., 20300, not 20.3)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma50: Option<f64>,

    /// 100-period moving average in full VND (e.g., 19800, not 19.8)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma100: Option<f64>,

    /// 200-period moving average in full VND (e.g., 18500, not 18.5)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma200: Option<f64>,

    // Moving Average Scores (percentage difference from MA)
    /// MA10 score: ((close - ma10) / ma10) * 100
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma10_score: Option<f64>,

    /// MA20 score: ((close - ma20) / ma20) * 100
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma20_score: Option<f64>,

    /// MA50 score: ((close - ma50) / ma50) * 100
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma50_score: Option<f64>,

    /// MA100 score: ((close - ma100) / ma100) * 100
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma100_score: Option<f64>,

    /// MA200 score: ((close - ma200) / ma200) * 100
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma200_score: Option<f64>,

    // Change Indicators (percentage change from previous row)
    /// Close price percentage change from previous row: ((curr_close - prev_close) / prev_close) * 100
    #[serde(skip_serializing_if = "Option::is_none")]
    pub close_changed: Option<f64>,

    /// Volume percentage change from previous row: ((curr_volume - prev_volume) / prev_volume) * 100
    #[serde(skip_serializing_if = "Option::is_none")]
    pub volume_changed: Option<f64>,
}

impl StockData {
    /// Create a new stock data point with only OHLCV
    pub fn new(
        time: DateTime<Utc>,
        ticker: String,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        volume: u64,
    ) -> Self {
        Self {
            time,
            ticker,
            open,
            high,
            low,
            close,
            volume,
            ma10: None,
            ma20: None,
            ma50: None,
            ma100: None,
            ma200: None,
            ma10_score: None,
            ma20_score: None,
            ma50_score: None,
            ma100_score: None,
            ma200_score: None,
            close_changed: None,
            volume_changed: None,
        }
    }

    /// Calculate and set MA scores from MA values
    pub fn calculate_ma_scores(&mut self) {
        if let Some(ma10) = self.ma10 {
            self.ma10_score = Some(Self::calculate_score(self.close, ma10));
        }
        if let Some(ma20) = self.ma20 {
            self.ma20_score = Some(Self::calculate_score(self.close, ma20));
        }
        if let Some(ma50) = self.ma50 {
            self.ma50_score = Some(Self::calculate_score(self.close, ma50));
        }
        if let Some(ma100) = self.ma100 {
            self.ma100_score = Some(Self::calculate_score(self.close, ma100));
        }
        if let Some(ma200) = self.ma200 {
            self.ma200_score = Some(Self::calculate_score(self.close, ma200));
        }
    }

    /// Calculate MA score: ((close - ma) / ma) * 100
    pub fn calculate_score(close: f64, ma: f64) -> f64 {
        if ma == 0.0 {
            0.0
        } else {
            ((close - ma) / ma) * 100.0
        }
    }
}
