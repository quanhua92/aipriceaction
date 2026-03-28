use chrono::{DateTime, Utc};
use sqlx::FromRow;

#[derive(Debug, Clone, FromRow)]
pub struct Ticker {
    pub id: i32,
    pub source: String,
    pub ticker: String,
    pub name: Option<String>,
    pub status: String,
}

#[derive(Debug, Clone)]
pub struct OhlcvRow {
    pub ticker_id: i32,
    pub interval: String,
    pub time: DateTime<Utc>,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
}

#[derive(Debug, Clone)]
pub struct IndicatorRow {
    pub ticker_id: i32,
    pub interval: String,
    pub time: DateTime<Utc>,
    pub ma10: Option<f64>,
    pub ma20: Option<f64>,
    pub ma50: Option<f64>,
    pub ma100: Option<f64>,
    pub ma200: Option<f64>,
    pub ma10_score: Option<f64>,
    pub ma20_score: Option<f64>,
    pub ma50_score: Option<f64>,
    pub ma100_score: Option<f64>,
    pub ma200_score: Option<f64>,
    pub close_changed: Option<f64>,
    pub volume_changed: Option<f64>,
    pub total_money_changed: Option<f64>,
}
