use serde::{Deserialize, Serialize};

use crate::models::aggregated_interval::AggregatedInterval;

/// Data source mode matching the parent project.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    #[default]
    #[serde(alias = "stock", alias = "stocks")]
    Vn,
    #[serde(alias = "cryptos")]
    Crypto,
}

impl Mode {
    pub fn source_label(&self) -> &'static str {
        match self {
            Mode::Vn => "vn",
            Mode::Crypto => "crypto",
        }
    }
}

/// Query parameters for GET /tickers
#[derive(Debug, Deserialize, Clone)]
pub struct TickersQuery {
    pub symbol: Option<Vec<String>>,
    pub interval: Option<String>,
    pub start_date: Option<String>,
    pub end_date: Option<String>,
    pub limit: Option<i64>,
    #[serde(default)]
    pub legacy: bool,
    #[serde(default = "default_format")]
    pub format: String,
    #[serde(default)]
    pub cache: bool,
    #[serde(default)]
    pub mode: Mode,
}

fn default_format() -> String {
    "json".to_string()
}

/// Query parameters for GET /tickers/group
#[derive(Debug, Deserialize)]
pub struct GroupQuery {
    #[serde(default)]
    pub mode: Mode,
}

/// Normalised interval — either a native DB interval or an aggregated one.
#[derive(Debug, Clone, Copy)]
pub enum NormalizedInterval {
    /// Native interval stored in the DB: "1D", "1h", or "1m"
    Native(&'static str),
    /// Aggregated interval computed from base data
    Aggregated(AggregatedInterval),
}

impl NormalizedInterval {
    /// Parse from a raw user-supplied string.
    ///
    /// Must distinguish `"1m"` (native minute) from `"1M"` (aggregated monthly)
    /// **before** case conversion.
    pub fn parse(raw: &str) -> Option<Self> {
        // Check aggregated intervals first (case-sensitive)
        if let Some(agg) = AggregatedInterval::from_str(raw) {
            return Some(NormalizedInterval::Aggregated(agg));
        }

        // Native intervals
        match raw.to_ascii_uppercase().as_str() {
            "1D" | "DAILY" => Some(NormalizedInterval::Native("1D")),
            "1H" | "HOURLY" => Some(NormalizedInterval::Native("1h")),
            "1M" | "MINUTE" => Some(NormalizedInterval::Native("1m")),
            _ => None,
        }
    }

    /// Display string for error messages.
    pub fn all_valid() -> &'static str {
        "1D, 1H, 1m, 5m, 15m, 30m, 1W, 2W, 1M (or daily, hourly, minute)"
    }
}

/// Stock data response matching the parent project format.
#[derive(Debug, Serialize)]
pub struct StockDataResponse {
    pub time: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: u64,
    pub symbol: String,

    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma10: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma20: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma50: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma100: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma200: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma10_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma20_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma50_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma100_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma200_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub close_changed: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub volume_changed: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_money_changed: Option<f64>,
}

/// Index tickers whose prices should NOT be divided by 1000 in legacy mode.
const INDEX_TICKERS: &[&str] = &["VNINDEX", "VN30"];

/// Whether a ticker is an index (no legacy price scaling).
pub fn is_index_ticker(ticker: &str) -> bool {
    INDEX_TICKERS.contains(&ticker.to_uppercase().as_str())
}

/// Health response structure matching parent project format.
#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub status: String,
    pub storage: String,
    pub vn: HealthSourceStats,
    pub crypto: HealthSourceStats,
    pub current_system_time: String,
}

#[derive(Debug, Serialize)]
pub struct HealthSourceStats {
    pub tickers: i64,
    pub ohlcv_records: i64,
    pub indicator_records: i64,
    pub daily_records: i64,
    pub hourly_records: i64,
    pub minute_records: i64,
}
