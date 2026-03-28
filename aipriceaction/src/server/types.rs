use serde::{Deserialize, Serialize};

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

/// Normalise interval to the canonical form used in the DB.
/// Accepts lowercase aliases.
pub fn normalise_interval(raw: &str) -> Option<&'static str> {
    match raw.to_ascii_uppercase().as_str() {
        "1D" => Some("1D"),
        "1H" => Some("1h"),
        "1M" => Some("1m"),
        _ => None,
    }
}

/// Index tickers whose prices should NOT be divided by 1000 in legacy mode.
const INDEX_TICKERS: &[&str] = &["VNINDEX", "VN30"];

/// Whether a ticker is an index (no legacy price scaling).
pub fn is_index_ticker(ticker: &str) -> bool {
    INDEX_TICKERS.contains(&ticker.to_uppercase().as_str())
}
