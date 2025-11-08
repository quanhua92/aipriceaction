//! Moving Average scores by sector analysis endpoint
//!
//! Provides MA analysis grouped by stock sectors with various filtering options.

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
    AnalysisResponse, parse_analysis_date,
    load_ticker_groups, get_tickers_in_sector,
};

/// Query parameters for MA scores by sector analysis
#[derive(Debug, Deserialize)]
pub struct MaScoresBySectorQuery {
    /// Date to analyze (YYYY-MM-DD format, default: latest trading day)
    pub date: Option<String>,

    /// MA period to analyze: 10, 20, 50, 100, 200
    #[serde(default = "default_ma_period")]
    pub ma_period: u32,

    /// Score threshold (default: 0.0)
    #[serde(default = "default_min_score")]
    pub min_score: f64,

    /// Include only stocks above threshold
    #[serde(default)]
    pub above_threshold_only: bool,

    /// Maximum stocks per sector (default: 10)
    pub top_per_sector: Option<usize>,
}

fn default_ma_period() -> u32 {
    20
}

fn default_min_score() -> f64 {
    0.0
}

/// MA scores by sector response structure
#[derive(Debug, Serialize)]
pub struct MaScoresBySectorResponse {
    pub sectors: Vec<SectorMaAnalysis>,
    pub ma_period: u32,
    pub threshold: f64,
}

/// Sector analysis information
#[derive(Debug, Serialize)]
pub struct SectorMaAnalysis {
    pub sector_name: String,
    pub total_stocks: usize,
    pub stocks_above_threshold: usize,
    pub average_score: f64,
    pub top_stocks: Vec<StockMaInfo>,
}

/// Individual stock MA information
#[derive(Debug, Serialize)]
pub struct StockMaInfo {
    pub symbol: String,
    pub close: f64,
    pub volume: u64,
    pub ma_value: f64,
    pub ma_score: f64,
    pub close_changed: Option<f64>,
    pub volume_changed: Option<f64>,
}

/// Handler for MA scores by sector analysis endpoint
pub async fn ma_scores_by_sector_handler(
    State(data_state): State<SharedDataStore>,
    Query(params): Query<MaScoresBySectorQuery>,
) -> impl IntoResponse {
    // Validate MA period
    if ![10, 20, 50, 100, 200].contains(&params.ma_period) {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "Invalid MA period. Must be one of: 10, 20, 50, 100, 200"
            })),
        ).into_response();
    }

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

    let mut sector_analyses = Vec::new();
    let mut total_analyzed = 0;

    // Analyze each sector
    for sector_name in ticker_groups.keys() {
        let sector_tickers = get_tickers_in_sector(sector_name, &ticker_groups);

        if sector_tickers.is_empty() {
            continue;
        }

        // Fetch data for this sector
        let sector_data = data_state.get_data_with_cache(
            sector_tickers.clone(),
            Interval::Daily,
            None, // start_date
            None, // end_date
            true, // use_cache
        ).await;

        let mut sector_stocks = Vec::new();
        let mut scores_sum = 0.0;
        let mut scores_count = 0;
        let mut above_threshold_count = 0;

        for (ticker, stock_data) in sector_data {
            if stock_data.is_empty() {
                continue;
            }

            // Find analysis date and record on or before that date
            let analysis_date = parse_analysis_date(params.date.clone(), &stock_data);
            let current_record = stock_data.iter()
                .filter(|d| d.time <= analysis_date)
                .max_by_key(|d| d.time);

            if let Some(current) = current_record {
                // Get the MA value and score based on the requested period
                let (ma_value, ma_score) = match params.ma_period {
                    10 => (current.ma10, current.ma10_score),
                    20 => (current.ma20, current.ma20_score),
                    50 => (current.ma50, current.ma50_score),
                    100 => (current.ma100, current.ma100_score),
                    200 => (current.ma200, current.ma200_score),
                    _ => (None, None),
                };

                if let (Some(ma_val), Some(ma_scr)) = (ma_value, ma_score) {
                    // Check if above threshold
                    let above_threshold = ma_scr >= params.min_score;
                    if above_threshold {
                        above_threshold_count += 1;
                    }

                    // If filtering by threshold and this stock doesn't meet it, skip
                    if params.above_threshold_only && !above_threshold {
                        continue;
                    }

                    scores_sum += ma_scr;
                    scores_count += 1;

                    let stock_info = StockMaInfo {
                        symbol: ticker,
                        close: current.close,
                        volume: current.volume,
                        ma_value: ma_val,
                        ma_score: ma_scr,
                        close_changed: current.close_changed,
                        volume_changed: current.volume_changed,
                    };

                    sector_stocks.push(stock_info);
                }
            }
        }

        // Only include sectors that have stocks with valid MA data
        if !sector_stocks.is_empty() {
            let average_score = if scores_count > 0 {
                scores_sum / scores_count as f64
            } else {
                0.0
            };

            // Sort stocks within sector by MA score (descending)
            sector_stocks.sort_by(|a, b| {
                b.ma_score.partial_cmp(&a.ma_score).unwrap_or(std::cmp::Ordering::Equal)
            });

            // Limit top stocks per sector
            let top_per_sector = params.top_per_sector.unwrap_or(10).min(50);
            sector_stocks.truncate(top_per_sector);

            let sector_analysis = SectorMaAnalysis {
                sector_name: sector_name.clone(),
                total_stocks: scores_count,
                stocks_above_threshold: above_threshold_count,
                average_score,
                top_stocks: sector_stocks,
            };

            sector_analyses.push(sector_analysis);
            total_analyzed += scores_count;
        }
    }

    // Sort sectors by average score (descending)
    sector_analyses.sort_by(|a, b| {
        b.average_score.partial_cmp(&a.average_score).unwrap_or(std::cmp::Ordering::Equal)
    });

    (
        StatusCode::OK,
        Json(AnalysisResponse {
            analysis_date: params.date.unwrap_or_else(|| "latest".to_string()),
            analysis_type: "ma_scores_by_sector".to_string(),
            total_analyzed,
            data: MaScoresBySectorResponse {
                sectors: sector_analyses,
                ma_period: params.ma_period,
                threshold: params.min_score,
            },
        }),
    ).into_response()
}