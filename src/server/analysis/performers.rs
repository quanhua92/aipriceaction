//! Top performers analysis endpoint
//!
//! Provides analysis of top/bottom performing stocks based on various metrics.

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use crate::{
    models::{Interval, Mode},
    constants::INDEX_TICKERS,
    server::AppState,
    services::SharedDataStore,
};
use super::{
    AnalysisResponse, validate_limit, parse_analysis_date,
    load_ticker_groups, get_ticker_sector,
};

/// Query parameters for top performers analysis
#[derive(Debug, Deserialize)]
pub struct TopPerformersQuery {
    /// Date to analyze (YYYY-MM-DD format, default: latest trading day)
    pub date: Option<String>,

    /// Metric to sort by: close_changed, volume, volume_changed, total_money_changed, ma10_score, ma20_score, ma50_score, ma100_score, ma200_score
    #[serde(default = "default_sort_by")]
    pub sort_by: String,

    /// Number of results to return (default: 10, max: 100)
    pub limit: Option<usize>,

    /// Direction: asc or desc (default: desc for top performers)
    #[serde(default = "default_direction")]
    pub direction: String,

    /// Sector filter (optional)
    pub sector: Option<String>,

    /// Minimum volume filter (default: 10000)
    pub min_volume: Option<u64>,

    /// Include hourly breakdown (default: false)
    pub with_hour: Option<bool>,

    /// Market mode: vn (default) or crypto
    #[serde(default)]
    pub mode: Mode,
}

fn default_sort_by() -> String {
    "close_changed".to_string()
}

fn default_direction() -> String {
    "desc".to_string()
}

/// Top performers response structure
#[derive(Debug, Serialize)]
pub struct TopPerformersResponse {
    pub performers: Vec<PerformerInfo>,
    pub worst_performers: Vec<PerformerInfo>,
    pub hourly: Option<Vec<HourlyPerformers>>,
}

/// Hourly performers breakdown
#[derive(Debug, Serialize)]
pub struct HourlyPerformers {
    pub hour: String,                             // Exact CSV timestamp: "2023-09-11 03:00:00"
    pub performers: Vec<PerformerInfo>,          // Top performers for this hour
    pub worst_performers: Vec<PerformerInfo>,    // Worst performers for this hour
}

/// Individual performer information
#[derive(Debug, Clone, Serialize)]
pub struct PerformerInfo {
    pub symbol: String,
    pub close: f64,
    pub volume: u64,
    pub close_changed: Option<f64>,
    pub volume_changed: Option<f64>,
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
    pub sector: Option<String>,
    pub total_money_changed: Option<f64>,
}

/// Helper function to sort performers and return top/worst performers
fn sort_performers(performers: Vec<PerformerInfo>, sort_by: &str, direction: &str, limit: usize) -> (Vec<PerformerInfo>, Vec<PerformerInfo>) {
    // Clone performers for sorting in opposite direction
    let mut performers_asc = performers.clone();
    let mut performers_desc = performers.clone();

    // Sort descending for top performers
    match sort_by.to_lowercase().as_str() {
        "close_changed" => {
            performers_desc.sort_by(|a, b| {
                match (a.close_changed, b.close_changed) {
                    (Some(a_val), Some(b_val)) => b_val.partial_cmp(&a_val).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Less,
                    (None, Some(_)) => std::cmp::Ordering::Greater,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
            performers_asc.sort_by(|a, b| {
                match (a.close_changed, b.close_changed) {
                    (Some(a_val), Some(b_val)) => a_val.partial_cmp(&b_val).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Greater,
                    (None, Some(_)) => std::cmp::Ordering::Less,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
        },
        "volume" => {
            performers_desc.sort_by(|a, b| b.volume.cmp(&a.volume));
            performers_asc.sort_by(|a, b| a.volume.cmp(&b.volume));
        },
        "volume_changed" => {
            performers_desc.sort_by(|a, b| {
                match (a.volume_changed, b.volume_changed) {
                    (Some(a_val), Some(b_val)) => b_val.partial_cmp(&a_val).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Less,
                    (None, Some(_)) => std::cmp::Ordering::Greater,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
            performers_asc.sort_by(|a, b| {
                match (a.volume_changed, b.volume_changed) {
                    (Some(a_val), Some(b_val)) => a_val.partial_cmp(&b_val).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Greater,
                    (None, Some(_)) => std::cmp::Ordering::Less,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
        },
        "ma10_score" => {
            performers_desc.sort_by(|a, b| {
                match (a.ma10_score, b.ma10_score) {
                    (Some(a_score), Some(b_score)) => b_score.partial_cmp(&a_score).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Less,
                    (None, Some(_)) => std::cmp::Ordering::Greater,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
            performers_asc.sort_by(|a, b| {
                match (a.ma10_score, b.ma10_score) {
                    (Some(a_score), Some(b_score)) => a_score.partial_cmp(&b_score).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Greater,
                    (None, Some(_)) => std::cmp::Ordering::Less,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
        },
        "ma20_score" => {
            performers_desc.sort_by(|a, b| {
                match (a.ma20_score, b.ma20_score) {
                    (Some(a_score), Some(b_score)) => b_score.partial_cmp(&a_score).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Less,
                    (None, Some(_)) => std::cmp::Ordering::Greater,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
            performers_asc.sort_by(|a, b| {
                match (a.ma20_score, b.ma20_score) {
                    (Some(a_score), Some(b_score)) => a_score.partial_cmp(&b_score).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Greater,
                    (None, Some(_)) => std::cmp::Ordering::Less,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
        },
        "ma50_score" => {
            performers_desc.sort_by(|a, b| {
                match (a.ma50_score, b.ma50_score) {
                    (Some(a_score), Some(b_score)) => b_score.partial_cmp(&a_score).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Less,
                    (None, Some(_)) => std::cmp::Ordering::Greater,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
            performers_asc.sort_by(|a, b| {
                match (a.ma50_score, b.ma50_score) {
                    (Some(a_score), Some(b_score)) => a_score.partial_cmp(&b_score).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Greater,
                    (None, Some(_)) => std::cmp::Ordering::Less,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
        },
        "ma100_score" => {
            performers_desc.sort_by(|a, b| {
                match (a.ma100_score, b.ma100_score) {
                    (Some(a_score), Some(b_score)) => b_score.partial_cmp(&a_score).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Less,
                    (None, Some(_)) => std::cmp::Ordering::Greater,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
            performers_asc.sort_by(|a, b| {
                match (a.ma100_score, b.ma100_score) {
                    (Some(a_score), Some(b_score)) => a_score.partial_cmp(&b_score).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Greater,
                    (None, Some(_)) => std::cmp::Ordering::Less,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
        },
        "ma200_score" => {
            performers_desc.sort_by(|a, b| {
                match (a.ma200_score, b.ma200_score) {
                    (Some(a_score), Some(b_score)) => b_score.partial_cmp(&a_score).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Less,
                    (None, Some(_)) => std::cmp::Ordering::Greater,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
            performers_asc.sort_by(|a, b| {
                match (a.ma200_score, b.ma200_score) {
                    (Some(a_score), Some(b_score)) => a_score.partial_cmp(&b_score).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Greater,
                    (None, Some(_)) => std::cmp::Ordering::Less,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
        },
        "total_money_changed" => {
            performers_desc.sort_by(|a, b| {
                match (a.total_money_changed, b.total_money_changed) {
                    (Some(a_val), Some(b_val)) => b_val.partial_cmp(&a_val).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Less,
                    (None, Some(_)) => std::cmp::Ordering::Greater,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
            performers_asc.sort_by(|a, b| {
                match (a.total_money_changed, b.total_money_changed) {
                    (Some(a_val), Some(b_val)) => a_val.partial_cmp(&b_val).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Greater,
                    (None, Some(_)) => std::cmp::Ordering::Less,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
        },
        _ => {
            // Default sorting by close_changed
            performers_desc.sort_by(|a, b| {
                match (a.close_changed, b.close_changed) {
                    (Some(a_val), Some(b_val)) => b_val.partial_cmp(&a_val).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Less,
                    (None, Some(_)) => std::cmp::Ordering::Greater,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
            performers_asc.sort_by(|a, b| {
                match (a.close_changed, b.close_changed) {
                    (Some(a_val), Some(b_val)) => a_val.partial_cmp(&b_val).unwrap_or(std::cmp::Ordering::Equal),
                    (Some(_), None) => std::cmp::Ordering::Greater,
                    (None, Some(_)) => std::cmp::Ordering::Less,
                    (None, None) => std::cmp::Ordering::Equal,
                }
            });
        }
    }

    // Return results based on direction
    if direction == "asc" {
        let top_performers = performers_asc.into_iter().take(limit).collect::<Vec<_>>();
        let worst_performers = performers_desc.into_iter().take(limit).collect::<Vec<_>>();
        (top_performers, worst_performers)
    } else {
        // Default: desc (top performers have highest values)
        let top_performers = performers_desc.into_iter().take(limit).collect::<Vec<_>>();
        let worst_performers = performers_asc.into_iter().take(limit).collect::<Vec<_>>();
        (top_performers, worst_performers)
    }
}

/// Handler for top performers analysis endpoint
pub async fn top_performers_handler(
    State(app_state): State<AppState>,
    Query(params): Query<TopPerformersQuery>,
) -> impl IntoResponse {
    // Get DataStore based on mode
    let data_state = app_state.get_data_store(params.mode);

    // Load ticker groups for sector mapping
    let ticker_groups = match load_ticker_groups() {
        Ok(groups) => groups,
        Err(e) => {
            tracing::error!("Failed to load ticker groups: {}", e);
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({
                    "error": "Failed to load sector information"
                })),
            ).into_response();
        }
    };

    // Determine tickers to analyze
    let tickers: Vec<String> = if let Some(sector_filter) = &params.sector {
        // Get sector tickers, excluding market indices
        super::get_tickers_in_sector(sector_filter, &ticker_groups)
            .into_iter()
            .filter(|ticker| !INDEX_TICKERS.contains(&ticker.as_str()))
            .collect()
    } else {
        // Get all available tickers from data store, excluding market indices
        data_state.get_all_ticker_names().await
            .into_iter()
            .filter(|ticker| !INDEX_TICKERS.contains(&ticker.as_str()))
            .collect()
    };

    if tickers.is_empty() {
        return (
            StatusCode::OK,
            Json(AnalysisResponse {
                analysis_date: params.date.clone().unwrap_or_else(|| "N/A".to_string()),
                analysis_type: "top_performers".to_string(),
                total_analyzed: 0,
                data: TopPerformersResponse {
                    performers: vec![],
                    worst_performers: vec![],
                    hourly: None,
                },
            }),
        ).into_response();
    }

    // Fetch data from DataStore using existing pattern
    let data = data_state.get_data_with_cache(
        tickers.clone(),
        Interval::Daily,
        None, // start_date
        None, // end_date
        None, // limit
        true, // use_cache
    ).await;

    // Process daily data
    let mut daily_performers = Vec::new();

    for (ticker, stock_data) in data {
        if stock_data.is_empty() {
            continue;
        }

        // Find analysis date and record
        let analysis_date = parse_analysis_date(params.date.clone(), &stock_data);

        // Find the record on or before the analysis date
        let current_record = stock_data.iter()
            .filter(|d| d.time <= analysis_date)
            .max_by_key(|d| d.time);

        if let Some(current) = current_record {
            // Apply minimum volume filter
            let min_volume = params.min_volume.unwrap_or(10000);
            if current.volume < min_volume {
                continue;
            }

            // Get sector information
            let sector = get_ticker_sector(&ticker, &ticker_groups);

            let performer = PerformerInfo {
                symbol: ticker,
                close: current.close,
                volume: current.volume,
                close_changed: current.close_changed,
                volume_changed: current.volume_changed,
                ma10: current.ma10,
                ma20: current.ma20,
                ma50: current.ma50,
                ma100: current.ma100,
                ma200: current.ma200,
                ma10_score: current.ma10_score,
                ma20_score: current.ma20_score,
                ma50_score: current.ma50_score,
                ma100_score: current.ma100_score,
                ma200_score: current.ma200_score,
                sector,
                total_money_changed: current.total_money_changed,
            };

            daily_performers.push(performer);
        }
    }

    let limit = validate_limit(params.limit);
    let (top_performers, worst_performers) = sort_performers(
        daily_performers,
        &params.sort_by,
        &params.direction,
        limit
    );

    // Process hourly data if requested
    let hourly_data = if params.with_hour.unwrap_or(false) {
        match process_hourly_data(
            &data_state,
            &tickers,
            &ticker_groups,
            &params,
            limit
        ).await {
            Ok(hourly) => Some(hourly),
            Err(e) => {
                tracing::error!("Failed to process hourly data: {}", e);
                None
            }
        }
    } else {
        None
    };

    let total_analyzed = top_performers.len() + worst_performers.len();

    (
        StatusCode::OK,
        Json(AnalysisResponse {
            analysis_date: params.date.unwrap_or_else(|| "latest".to_string()),
            analysis_type: "top_performers".to_string(),
            total_analyzed,
            data: TopPerformersResponse {
                performers: top_performers.to_vec(),
                worst_performers: worst_performers.to_vec(),
                hourly: hourly_data,
            },
        }),
    ).into_response()
}

/// Process hourly data for top performers analysis
async fn process_hourly_data(
    data_state: &SharedDataStore,
    tickers: &[String],
    ticker_groups: &std::collections::HashMap<String, Vec<String>>,
    params: &TopPerformersQuery,
    limit: usize,
) -> Result<Vec<HourlyPerformers>, Box<dyn std::error::Error + Send + Sync>> {
    // Fetch hourly data from DataStore
    let hourly_data = data_state.get_data_with_cache(
        tickers.to_vec(),
        Interval::Hourly,
        None, // start_date
        None, // end_date
        None, // limit
        true, // use_cache
    ).await;

    // Group hourly data by exact CSV timestamps
    let mut hourly_groups: HashMap<String, Vec<PerformerInfo>> = HashMap::new();

    for (ticker, stock_data) in hourly_data {
        if stock_data.is_empty() {
            continue;
        }

        // Find analysis date and record
        let analysis_date = parse_analysis_date(params.date.clone(), &stock_data);

        // Find records on the analysis date
        for record in stock_data.iter() {
            // Check if record is on the analysis date
            if record.time.date_naive() == analysis_date.date_naive() {
                // Apply minimum volume filter
                let min_volume = params.min_volume.unwrap_or(10000);
                if record.volume < min_volume {
                    continue;
                }

                // Get sector information
                let sector = get_ticker_sector(&ticker, &ticker_groups);

                let performer = PerformerInfo {
                    symbol: ticker.clone(),
                    close: record.close,
                    volume: record.volume,
                    close_changed: record.close_changed,
                    volume_changed: record.volume_changed,
                    ma10: record.ma10,
                    ma20: record.ma20,
                    ma50: record.ma50,
                    ma100: record.ma100,
                    ma200: record.ma200,
                    ma10_score: record.ma10_score,
                    ma20_score: record.ma20_score,
                    ma50_score: record.ma50_score,
                    ma100_score: record.ma100_score,
                    ma200_score: record.ma200_score,
                    sector,
                    total_money_changed: record.total_money_changed,
                };

                // Use ISO 8601 timestamp format "YYYY-MM-DDTHH:MM:SS"
                let hour_key = record.time.format("%Y-%m-%dT%H:%M:%S").to_string();
                hourly_groups.entry(hour_key).or_insert_with(Vec::new).push(performer);
            }
        }
    }

    // Sort hourly timestamps chronologically
    let mut sorted_hours: Vec<String> = hourly_groups.keys().cloned().collect();
    sorted_hours.sort();

    // Process each hour
    let mut hourly_performers = Vec::new();
    for hour in sorted_hours {
        if let Some(performers) = hourly_groups.get(&hour) {
            let (top_hourly, worst_hourly) = sort_performers(
                performers.clone(),
                &params.sort_by,
                &params.direction,
                limit
            );

            hourly_performers.push(HourlyPerformers {
                hour,
                performers: top_hourly,
                worst_performers: worst_hourly,
            });
        }
    }

    Ok(hourly_performers)
}