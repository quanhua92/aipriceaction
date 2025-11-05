use crate::models::Interval;
use crate::services::{SharedDataStore, SharedHealthStats, estimate_memory_usage};
use axum::{
    extract::{State, Json},
    http::{HeaderMap, StatusCode, header::CACHE_CONTROL},
    response::IntoResponse,
};
use axum_extra::extract::Query;
use chrono::{NaiveDate, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use tracing::{debug, info, warn, instrument};

/// Query parameters for /tickers endpoint
#[derive(Debug, Deserialize)]
pub struct TickerQuery {
    /// Ticker symbols to query (can be repeated: symbol[]=VCB&symbol[]=FPT)
    pub symbol: Option<Vec<String>>,

    /// Interval: daily (default), 1h, 1m
    pub interval: Option<String>,

    /// Start date filter (YYYY-MM-DD)
    pub start_date: Option<String>,

    /// End date filter (YYYY-MM-DD)
    pub end_date: Option<String>,
}

/// Response structure for /tickers endpoint
#[derive(Debug, Serialize)]
pub struct TickersResponse {
    pub data: HashMap<String, Vec<crate::models::StockData>>,
    pub interval: String,
    pub ticker_count: usize,
    pub total_records: usize,
}

/// GET /tickers - Query stock data from in-memory store
///
/// Examples:
/// - /tickers?symbol[]=VCB&symbol[]=FPT (default to daily)
/// - /tickers?symbol[]=VCB&interval=1h
/// - /tickers?symbol[]=VCB&interval=daily&start_date=2024-01-01&end_date=2024-12-31
#[instrument(skip(data_state))]
pub async fn get_tickers_handler(
    State(data_state): State<SharedDataStore>,
    Query(params): Query<TickerQuery>,
) -> impl IntoResponse {
    debug!("Received request for tickers with params: {:?}", params);

    // Parse interval (default to daily)
    let interval = match params.interval.as_deref() {
        Some("daily") | None => Interval::Daily,
        Some("1h") => Interval::Hourly,
        Some("1m") => Interval::Minute,
        Some(other) => {
            warn!(interval = %other, "Invalid interval parameter");
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": "Invalid interval. Valid values: daily, 1h, 1m"
                }))
            ).into_response();
        }
    };

    // Get ticker symbols (required)
    let symbols = match params.symbol {
        Some(s) if !s.is_empty() => s,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": "Missing required parameter: symbol[]"
                }))
            ).into_response();
        }
    };

    // Parse date filters
    let start_date_filter = match &params.start_date {
        Some(date_str) => {
            match NaiveDate::parse_from_str(date_str, "%Y-%m-%d") {
                Ok(date) => Some(date.and_hms_opt(0, 0, 0).unwrap().and_utc()),
                Err(_) => {
                    warn!(start_date = %date_str, "Invalid start_date format");
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({
                            "error": "Invalid start_date format. Expected YYYY-MM-DD"
                        }))
                    ).into_response();
                }
            }
        }
        None => None,
    };

    let end_date_filter = match &params.end_date {
        Some(date_str) => {
            match NaiveDate::parse_from_str(date_str, "%Y-%m-%d") {
                Ok(date) => Some(date.and_hms_opt(23, 59, 59).unwrap().and_utc()),
                Err(_) => {
                    warn!(end_date = %date_str, "Invalid end_date format");
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({
                            "error": "Invalid end_date format. Expected YYYY-MM-DD"
                        }))
                    ).into_response();
                }
            }
        }
        None => None,
    };

    // Query data from in-memory store
    let data_guard = data_state.lock().await;
    let mut result_data: HashMap<String, Vec<crate::models::StockData>> = HashMap::new();

    for symbol in &symbols {
        if let Some(ticker_intervals) = data_guard.get(symbol) {
            if let Some(interval_data) = ticker_intervals.get(&interval) {
                // Apply date filtering
                let filtered: Vec<_> = interval_data.iter()
                    .filter(|d| {
                        let start_ok = start_date_filter.map_or(true, |start| d.time >= start);
                        let end_ok = end_date_filter.map_or(true, |end| d.time <= end);
                        start_ok && end_ok
                    })
                    .cloned()
                    .collect();

                if !filtered.is_empty() {
                    result_data.insert(symbol.clone(), filtered);
                }
            }
        }
    }

    drop(data_guard); // Release lock

    let ticker_count = result_data.len();
    let total_records: usize = result_data.values().map(|v| v.len()).sum();

    info!(
        ticker_count,
        total_records,
        interval = %interval.to_filename(),
        symbols = ?symbols,
        "Returning ticker data"
    );

    let response = TickersResponse {
        data: result_data,
        interval: interval.to_filename().to_string(),
        ticker_count,
        total_records,
    };

    let mut headers = HeaderMap::new();
    headers.insert(CACHE_CONTROL, "max-age=30".parse().unwrap());

    (StatusCode::OK, headers, Json(response)).into_response()
}

/// GET /health - Health statistics endpoint
#[instrument(skip(health_state, data_state))]
pub async fn health_handler(
    State(health_state): State<SharedHealthStats>,
    State(data_state): State<SharedDataStore>,
) -> impl IntoResponse {
    debug!("Received request for health stats");

    let mut health_stats = health_state.lock().await.clone();

    // Calculate current metrics dynamically
    {
        let data_guard = data_state.lock().await;
        let memory_bytes = estimate_memory_usage(&*data_guard);

        health_stats.memory_usage_bytes = memory_bytes;
        health_stats.memory_usage_mb = memory_bytes as f64 / (1024.0 * 1024.0);
        health_stats.memory_usage_percent =
            (memory_bytes as f64 / (health_stats.memory_limit_mb * 1024 * 1024) as f64) * 100.0;
        health_stats.active_tickers_count = data_guard.len();

        // Count records per interval
        let mut daily_count = 0;
        let mut hourly_count = 0;
        let mut minute_count = 0;

        for (_ticker, intervals) in data_guard.iter() {
            if let Some(data) = intervals.get(&Interval::Daily) {
                daily_count += data.len();
            }
            if let Some(data) = intervals.get(&Interval::Hourly) {
                hourly_count += data.len();
            }
            if let Some(data) = intervals.get(&Interval::Minute) {
                minute_count += data.len();
            }
        }

        health_stats.daily_records_count = daily_count;
        health_stats.hourly_records_count = hourly_count;
        health_stats.minute_records_count = minute_count;
    }

    health_stats.current_system_time = Utc::now().to_rfc3339();

    info!(
        memory_mb = health_stats.memory_usage_mb,
        active_tickers = health_stats.active_tickers_count,
        daily_records = health_stats.daily_records_count,
        "Returning health stats"
    );

    (StatusCode::OK, Json(health_stats)).into_response()
}

/// GET /tickers/group - Get ticker groups from ticker_group.json
#[instrument]
pub async fn get_ticker_groups_handler() -> impl IntoResponse {
    debug!("Received request for ticker groups");

    let ticker_groups_path = "ticker_group.json";

    match std::fs::read_to_string(ticker_groups_path) {
        Ok(content) => {
            match serde_json::from_str::<HashMap<String, Vec<String>>>(&content) {
                Ok(groups) => {
                    let group_count = groups.len();
                    let group_names: Vec<_> = groups.keys().cloned().collect();

                    info!(group_count, groups = ?group_names, "Returning ticker groups");
                    (StatusCode::OK, Json(groups)).into_response()
                }
                Err(e) => {
                    warn!(error = %e, "Failed to parse ticker_group.json");
                    (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(serde_json::json!({
                            "error": "Failed to parse ticker groups"
                        }))
                    ).into_response()
                }
            }
        }
        Err(e) => {
            warn!(error = %e, "Failed to read ticker_group.json");
            (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({
                    "error": "Ticker groups file not found"
                }))
            ).into_response()
        }
    }
}
