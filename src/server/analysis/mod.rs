//! Analysis API endpoints for stock market data
//!
//! This module provides various analysis endpoints that leverage the existing
//! DataStore infrastructure to perform calculations and return insights.

pub mod performers;
pub mod ma_scores;

use serde::{Deserialize, Serialize};
use chrono::{DateTime, Utc};
use crate::models::StockData;
use std::collections::HashMap;

/// Re-export handlers for easier routing
pub use performers::top_performers_handler;
pub use ma_scores::ma_scores_by_sector_handler;

/// Common query parameters for analysis endpoints
#[derive(Debug, Deserialize)]
pub struct AnalysisQuery {
    /// Date to analyze (YYYY-MM-DD format, default: latest trading day)
    pub date: Option<String>,

    /// Number of results to return (default: 10, max: 100)
    pub limit: Option<usize>,
}

/// Common analysis response structure
#[derive(Debug, Serialize)]
pub struct AnalysisResponse<T> {
    pub analysis_date: String,
    pub analysis_type: String,
    pub total_analyzed: usize,
    pub data: T,
}

/// Get tickers for a specific sector
pub fn get_tickers_in_sector(sector: &str, ticker_groups: &HashMap<String, Vec<String>>) -> Vec<String> {
    ticker_groups
        .get(sector)
        .cloned()
        .unwrap_or_default()
}

/// Get sector for a specific ticker
pub fn get_ticker_sector(ticker: &str, ticker_groups: &HashMap<String, Vec<String>>) -> Option<String> {
    for (sector, tickers) in ticker_groups {
        if tickers.contains(&ticker.to_string()) {
            return Some(sector.clone());
        }
    }
    None
}

/// Calculate price change and percentage change
pub fn calculate_price_change(current: f64, previous: f64) -> (f64, f64) {
    let change = current - previous;
    let change_percent = if previous != 0.0 {
        (change / previous) * 100.0
    } else {
        0.0
    };
    (change, change_percent)
}

/// Find the latest record in stock data
pub fn find_latest_record(data: &[StockData]) -> Option<&StockData> {
    data.iter().max_by_key(|d| d.time)
}

/// Find the previous record before a specific date
pub fn find_previous_record(data: &[StockData], before_date: DateTime<Utc>) -> Option<&StockData> {
    data.iter()
        .filter(|d| d.time < before_date)
        .max_by_key(|d| d.time)
}

/// Validate and parse limit parameter
pub fn validate_limit(limit: Option<usize>) -> usize {
    limit.unwrap_or(10).min(100).max(1)
}

/// Parse date string or use latest available
pub fn parse_analysis_date(date_str: Option<String>, available_data: &[StockData]) -> DateTime<Utc> {
    if let Some(date_str) = date_str {
        // Parse the provided date (end of day for daily data)
        match chrono::NaiveDate::parse_from_str(&date_str, "%Y-%m-%d") {
            Ok(naive_date) => {
                let naive_dt = naive_date.and_hms_opt(23, 59, 59).unwrap();
                DateTime::<Utc>::from_naive_utc_and_offset(naive_dt, Utc)
            }
            Err(_) => {
                // Fallback to latest available date
                find_latest_record(available_data)
                    .map(|d| d.time)
                    .unwrap_or_else(|| Utc::now())
            }
        }
    } else {
        // Use latest available date
        find_latest_record(available_data)
            .map(|d| d.time)
            .unwrap_or_else(|| Utc::now())
    }
}

/// Load ticker groups from JSON file
pub fn load_ticker_groups() -> Result<HashMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    let content = std::fs::read_to_string("ticker_group.json")?;
    let groups: HashMap<String, Vec<String>> = serde_json::from_str(&content)?;
    Ok(groups)
}