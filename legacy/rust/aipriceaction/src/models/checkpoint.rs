use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct Checkpoint {
    pub meta: CheckpointMeta,
    pub sources: Vec<SourceCheckpoint>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct CheckpointMeta {
    pub created_at: DateTime<Utc>,
    pub candles: u32,
    pub total_tickers: u32,
    pub total_rows: u64,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SourceCheckpoint {
    pub source: String,
    pub tickers: Vec<TickerCheckpoint>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct TickerCheckpoint {
    pub ticker: String,
    pub name: Option<String>,
    pub data: BTreeMap<String, Vec<OhlcvEntry>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OhlcvEntry {
    pub time: DateTime<Utc>,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
}
