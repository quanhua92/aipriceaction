//! Top performers analysis endpoint
//!
//! Provides analysis of top/bottom performing stocks based on various metrics.

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde::{Deserialize, Serialize};
use crate::{
    services::data_store::SharedDataStore,
    models::Interval,
};
use super::{
    AnalysisResponse, validate_limit, parse_analysis_date,
    find_latest_record, find_previous_record, calculate_price_change,
    load_ticker_groups, get_ticker_sector,
};

/// Query parameters for top performers analysis
#[derive(Debug, Deserialize)]
pub struct TopPerformersQuery {
    /// Date to analyze (YYYY-MM-DD format, default: latest trading day)
    pub date: Option<String>,

    /// Metric to sort by: close_change, close_change_percent, volume, volume_change, ma20_score, ma50_score
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
}

fn default_sort_by() -> String {
    "close_change_percent".to_string()
}

fn default_direction() -> String {
    "desc".to_string()
}

/// Top performers response structure
#[derive(Debug, Serialize)]
pub struct TopPerformersResponse {
    pub performers: Vec<PerformerInfo>,
}

/// Individual performer information
#[derive(Debug, Serialize)]
pub struct PerformerInfo {
    pub symbol: String,
    pub close_price: f64,
    pub close_change: f64,
    pub close_change_percent: f64,
    pub volume: u64,
    pub volume_change: Option<f64>,
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
}

/// Handler for top performers analysis endpoint
pub async fn top_performers_handler(
    State(data_state): State<SharedDataStore>,
    Query(params): Query<TopPerformersQuery>,
) -> impl IntoResponse {
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
    let tickers = if let Some(sector_filter) = &params.sector {
        super::get_tickers_in_sector(sector_filter, &ticker_groups)
    } else {
        // Get all available tickers from data store
        data_state.get_all_ticker_names().await
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
        true, // use_cache
    ).await;

    let mut performers = Vec::new();

    for (ticker, stock_data) in data {
        if stock_data.is_empty() {
            continue;
        }

        // Find analysis date and previous record
        let analysis_date = parse_analysis_date(params.date.clone(), &stock_data);
        let current_record = find_latest_record(&stock_data);
        let previous_record = find_previous_record(&stock_data, analysis_date);

        if let (Some(current), Some(previous)) = (current_record, previous_record) {
            // Apply minimum volume filter
            let min_volume = params.min_volume.unwrap_or(10000);
            if current.volume < min_volume {
                continue;
            }

            // Calculate price changes
            let (close_change, close_change_percent) =
                calculate_price_change(current.close, previous.close);

            let volume_change = if previous.volume > 0 {
                Some((current.volume as f64 - previous.volume as f64) / previous.volume as f64 * 100.0)
            } else {
                None
            };

            // Get sector information
            let sector = get_ticker_sector(&ticker, &ticker_groups);

            let performer = PerformerInfo {
                symbol: ticker,
                close_price: current.close,
                close_change,
                close_change_percent,
                volume: current.volume,
                volume_change,
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
            };

            performers.push(performer);
        }
    }

    // Sort performers based on the requested metric and direction
    let direction_desc = params.direction.to_lowercase() != "asc";

    match params.sort_by.to_lowercase().as_str() {
        "close_change" => performers.sort_by(|a, b| {
            if direction_desc {
                b.close_change.partial_cmp(&a.close_change).unwrap_or(std::cmp::Ordering::Equal)
            } else {
                a.close_change.partial_cmp(&b.close_change).unwrap_or(std::cmp::Ordering::Equal)
            }
        }),
        "close_change_percent" => performers.sort_by(|a, b| {
            if direction_desc {
                b.close_change_percent.partial_cmp(&a.close_change_percent).unwrap_or(std::cmp::Ordering::Equal)
            } else {
                a.close_change_percent.partial_cmp(&b.close_change_percent).unwrap_or(std::cmp::Ordering::Equal)
            }
        }),
        "volume" => performers.sort_by(|a, b| {
            if direction_desc {
                b.volume.cmp(&a.volume)
            } else {
                a.volume.cmp(&b.volume)
            }
        }),
        "volume_change" => performers.sort_by(|a, b| {
            match (a.volume_change, b.volume_change) {
                (Some(a_change), Some(b_change)) => {
                    if direction_desc {
                        b_change.partial_cmp(&a_change).unwrap_or(std::cmp::Ordering::Equal)
                    } else {
                        a_change.partial_cmp(&b_change).unwrap_or(std::cmp::Ordering::Equal)
                    }
                }
                (Some(_), None) => if direction_desc { std::cmp::Ordering::Less } else { std::cmp::Ordering::Greater },
                (None, Some(_)) => if direction_desc { std::cmp::Ordering::Greater } else { std::cmp::Ordering::Less },
                (None, None) => std::cmp::Ordering::Equal,
            }
        }),
        "ma10_score" => performers.sort_by(|a, b| {
            match (a.ma10_score, b.ma10_score) {
                (Some(a_score), Some(b_score)) => {
                    if direction_desc {
                        b_score.partial_cmp(&a_score).unwrap_or(std::cmp::Ordering::Equal)
                    } else {
                        a_score.partial_cmp(&b_score).unwrap_or(std::cmp::Ordering::Equal)
                    }
                }
                (Some(_), None) => if direction_desc { std::cmp::Ordering::Less } else { std::cmp::Ordering::Greater },
                (None, Some(_)) => if direction_desc { std::cmp::Ordering::Greater } else { std::cmp::Ordering::Less },
                (None, None) => std::cmp::Ordering::Equal,
            }
        }),
        "ma20_score" => performers.sort_by(|a, b| {
            match (a.ma20_score, b.ma20_score) {
                (Some(a_score), Some(b_score)) => {
                    if direction_desc {
                        b_score.partial_cmp(&a_score).unwrap_or(std::cmp::Ordering::Equal)
                    } else {
                        a_score.partial_cmp(&b_score).unwrap_or(std::cmp::Ordering::Equal)
                    }
                }
                (Some(_), None) => if direction_desc { std::cmp::Ordering::Less } else { std::cmp::Ordering::Greater },
                (None, Some(_)) => if direction_desc { std::cmp::Ordering::Greater } else { std::cmp::Ordering::Less },
                (None, None) => std::cmp::Ordering::Equal,
            }
        }),
        "ma50_score" => performers.sort_by(|a, b| {
            match (a.ma50_score, b.ma50_score) {
                (Some(a_score), Some(b_score)) => {
                    if direction_desc {
                        b_score.partial_cmp(&a_score).unwrap_or(std::cmp::Ordering::Equal)
                    } else {
                        a_score.partial_cmp(&b_score).unwrap_or(std::cmp::Ordering::Equal)
                    }
                }
                (Some(_), None) => if direction_desc { std::cmp::Ordering::Less } else { std::cmp::Ordering::Greater },
                (None, Some(_)) => if direction_desc { std::cmp::Ordering::Greater } else { std::cmp::Ordering::Less },
                (None, None) => std::cmp::Ordering::Equal,
            }
        }),
        _ => {
            // Default sorting by close_change_percent
            performers.sort_by(|a, b| {
                if direction_desc {
                    b.close_change_percent.partial_cmp(&a.close_change_percent).unwrap_or(std::cmp::Ordering::Equal)
                } else {
                    a.close_change_percent.partial_cmp(&b.close_change_percent).unwrap_or(std::cmp::Ordering::Equal)
                }
            });
        }
    }

    // Apply limit
    let limit = validate_limit(params.limit);
    performers.truncate(limit);

    let total_analyzed = performers.len();

    (
        StatusCode::OK,
        Json(AnalysisResponse {
            analysis_date: params.date.unwrap_or_else(|| "latest".to_string()),
            analysis_type: "top_performers".to_string(),
            total_analyzed,
            data: TopPerformersResponse {
                performers,
            },
        }),
    ).into_response()
}