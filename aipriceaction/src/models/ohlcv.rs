use std::fmt;

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

impl fmt::Display for Ticker {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.name {
            Some(name) => write!(f, "{} (id={}, source={}, name={name}, status={})", self.ticker, self.id, self.source, self.status),
            None => write!(f, "{} (id={}, source={}, status={})", self.ticker, self.id, self.source, self.status),
        }
    }
}

#[derive(Clone, FromRow)]
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

impl fmt::Display for OhlcvRow {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} {} O={} H={} L={} C={} V={}",
            self.interval,
            self.time.format("%Y-%m-%dT%H:%M:%S"),
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume
        )
    }
}

impl fmt::Debug for OhlcvRow {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self}")
    }
}

#[derive(Clone, FromRow)]
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

impl fmt::Display for IndicatorRow {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} {} ma10={} ma20={} ma50={} close_changed={}",
            self.interval,
            self.time.format("%Y-%m-%dT%H:%M:%S"),
            opt_fmt(self.ma10),
            opt_fmt(self.ma20),
            opt_fmt(self.ma50),
            opt_fmt(self.close_changed),
        )
    }
}

impl fmt::Debug for IndicatorRow {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self}")
    }
}

/// Joined row matching the 20-column CSV format:
/// ticker,time,open,high,low,close,volume,
/// ma10,ma20,ma50,ma100,ma200,
/// ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,
/// close_changed,volume_changed,total_money_changed
#[derive(Clone, FromRow)]
pub struct OhlcvJoined {
    pub ticker: String,
    pub time: DateTime<Utc>,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
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

impl fmt::Display for OhlcvJoined {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
            self.ticker,
            self.time.format("%Y-%m-%dT%H:%M:%S"),
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            opt_fmt(self.ma10),
            opt_fmt(self.ma20),
            opt_fmt(self.ma50),
            opt_fmt(self.ma100),
            opt_fmt(self.ma200),
            opt_fmt(self.ma10_score),
            opt_fmt(self.ma20_score),
            opt_fmt(self.ma50_score),
            opt_fmt(self.ma100_score),
            opt_fmt(self.ma200_score),
            opt_fmt(self.close_changed),
            opt_fmt(self.volume_changed),
            opt_fmt(self.total_money_changed),
        )
    }
}

impl fmt::Debug for OhlcvJoined {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self}")
    }
}

/// Format an optional f64: empty string for None, value for Some.
fn opt_fmt(v: Option<f64>) -> String {
    match v {
        Some(n) => n.to_string(),
        None => String::new(),
    }
}
