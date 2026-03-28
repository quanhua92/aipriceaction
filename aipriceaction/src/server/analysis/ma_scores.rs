use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::queries::ohlcv;
use crate::server::types::Mode;
use crate::server::AppState;

use super::{load_ticker_groups, get_tickers_in_sector, is_index_ticker, AnalysisResponse};

#[derive(Debug, Deserialize)]
pub struct MaScoresBySectorQuery {
    pub date: Option<String>,
    #[serde(default = "default_ma_period")]
    pub ma_period: u32,
    #[serde(default = "default_min_score")]
    pub min_score: f64,
    #[serde(default)]
    pub above_threshold_only: bool,
    pub top_per_sector: Option<usize>,
    #[serde(default)]
    pub mode: Mode,
}

fn default_ma_period() -> u32 { 20 }
fn default_min_score() -> f64 { 0.0 }

#[derive(Debug, Serialize)]
pub struct MaScoresBySectorResponse {
    pub sectors: Vec<SectorMaAnalysis>,
    pub ma_period: u32,
    pub threshold: f64,
}

#[derive(Debug, Serialize)]
pub struct SectorMaAnalysis {
    pub sector_name: String,
    pub total_stocks: usize,
    pub stocks_above_threshold: usize,
    pub average_score: f64,
    pub top_stocks: Vec<StockMaInfo>,
}

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

pub async fn ma_scores_by_sector_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<MaScoresBySectorQuery>,
) -> impl IntoResponse {
    if ![10, 20, 50, 100, 200].contains(&params.ma_period) {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "Invalid MA period. Must be one of: 10, 20, 50, 100, 200"
            })),
        ).into_response();
    }

    let ticker_groups = match load_ticker_groups() {
        Ok(groups) => groups,
        Err(e) => {
            tracing::error!("Failed to load ticker groups: {}", e);
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "Failed to load sector information" })),
            ).into_response();
        }
    };

    let source = params.mode.source_label();

    // Fetch all latest daily data in one query
    let rows = match ohlcv::get_latest_daily_per_ticker(&state.pool, source).await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("Failed to fetch daily data: {}", e);
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "Failed to fetch market data" })),
            ).into_response();
        }
    };

    // Build a lookup: ticker -> row
    let mut data_map: std::collections::HashMap<String, _> = std::collections::HashMap::new();
    for row in rows {
        data_map.insert(row.ticker.clone(), row);
    }

    let mut sector_analyses = Vec::new();
    let mut total_analyzed = 0;

    for sector_name in ticker_groups.keys() {
        let sector_tickers: Vec<String> = get_tickers_in_sector(sector_name, &ticker_groups)
            .into_iter()
            .filter(|t| !is_index_ticker(t))
            .collect();

        if sector_tickers.is_empty() {
            continue;
        }

        let mut sector_stocks = Vec::new();
        let mut scores_sum = 0.0;
        let mut scores_count = 0;
        let mut above_threshold_count = 0;

        for ticker in &sector_tickers {
            let current = match data_map.get(ticker) {
                Some(r) => r,
                None => continue,
            };

            let (ma_value, ma_score) = match params.ma_period {
                10 => (current.ma10, current.ma10_score),
                20 => (current.ma20, current.ma20_score),
                50 => (current.ma50, current.ma50_score),
                100 => (current.ma100, current.ma100_score),
                200 => (current.ma200, current.ma200_score),
                _ => (None, None),
            };

            if let (Some(ma_val), Some(ma_scr)) = (ma_value, ma_score) {
                let above_threshold = ma_scr >= params.min_score;
                if above_threshold {
                    above_threshold_count += 1;
                }

                if params.above_threshold_only && !above_threshold {
                    continue;
                }

                scores_sum += ma_scr;
                scores_count += 1;

                sector_stocks.push(StockMaInfo {
                    symbol: ticker.clone(),
                    close: current.close,
                    volume: current.volume as u64,
                    ma_value: ma_val,
                    ma_score: ma_scr,
                    close_changed: current.close_changed,
                    volume_changed: current.volume_changed,
                });
            }
        }

        if !sector_stocks.is_empty() {
            let average_score = if scores_count > 0 {
                scores_sum / scores_count as f64
            } else {
                0.0
            };

            sector_stocks.sort_by(|a, b| {
                b.ma_score.partial_cmp(&a.ma_score).unwrap_or(std::cmp::Ordering::Equal)
            });

            let top_per_sector = params.top_per_sector.unwrap_or(10).min(50);
            let top_per_sector_len = top_per_sector.min(sector_stocks.len());
            sector_stocks.truncate(top_per_sector_len);

            sector_analyses.push(SectorMaAnalysis {
                sector_name: sector_name.clone(),
                total_stocks: scores_count,
                stocks_above_threshold: above_threshold_count,
                average_score,
                top_stocks: sector_stocks,
            });
            total_analyzed += scores_count;
        }
    }

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
