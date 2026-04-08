use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;

use crate::queries::ohlcv;
use crate::server::types::Mode;
use crate::server::AppState;

use super::{get_all_sources, get_tickers_in_sector, is_index_ticker, load_crypto_groups, load_ticker_groups, load_yahoo_groups, AnalysisResponse};

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
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
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

    let is_all = params.mode == Mode::All;

    // Fetch latest daily data; for mode=all, also collect source per row
    let rows: Vec<(crate::models::ohlcv::OhlcvJoined, &str)> = if is_all {
        let sources = get_all_sources();
        let (r1, r2, r3, r4) = tokio::join!(
            ohlcv::get_latest_daily_per_ticker(&state.pool, sources[0]),
            ohlcv::get_latest_daily_per_ticker(&state.pool, sources[1]),
            ohlcv::get_latest_daily_per_ticker(&state.pool, sources[2]),
            ohlcv::get_latest_daily_per_ticker(&state.pool, sources[3]),
        );
        let mut merged = Vec::new();
        for (r, src) in [(r1, sources[0]), (r2, sources[1]), (r3, sources[2]), (r4, sources[3])] {
            match r {
                Ok(v) => merged.extend(v.into_iter().map(|row| (row, src))),
                Err(e) => tracing::warn!("Failed to fetch daily data for source '{}': {}", src, e),
            }
        }
        merged
    } else {
        let source = params.mode.source_label();
        match ohlcv::get_latest_daily_per_ticker(&state.pool, source).await {
            Ok(r) => r.into_iter().map(|row| (row, "")).collect(),
            Err(e) => {
                tracing::error!("Failed to fetch daily data: {}", e);
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(serde_json::json!({ "error": "Failed to fetch market data" })),
                ).into_response();
            }
        }
    };

    // Build a lookup: keyed by "source:symbol" for mode=all, plain symbol for single-mode
    let mut data_map: HashMap<String, (&str, _)> = HashMap::new();
    for (row, row_source) in rows {
        if is_all {
            let key = format!("{row_source}:{}", row.ticker);
            data_map.insert(key, (row_source, row));
        } else {
            data_map.insert(row.ticker.clone(), ("", row));
        }
    }

    // Collect all sector definitions: VN groups + (for mode=all) yahoo/global + crypto groups
    // Each entry: (sector_name, tickers, is_vn, preferred_source)
    // preferred_source is used when mode=all to disambiguate symbols that exist in multiple sources
    let mut all_sector_tickers: Vec<(String, Vec<String>, bool, Option<&str>)> = Vec::new();

    // VN sectors — preferred source is "vn"
    for sector_name in ticker_groups.keys() {
        let tickers: Vec<String> = get_tickers_in_sector(sector_name, &ticker_groups)
            .into_iter()
            .filter(|t| !is_index_ticker(t))
            .collect();
        all_sector_tickers.push((sector_name.clone(), tickers, true, Some("vn")));
    }

    // For mode=all, add yahoo/global and crypto sector groups
    if is_all {
        if let Ok(yahoo_groups) = load_yahoo_groups() {
            for (sector_name, tickers) in yahoo_groups {
                all_sector_tickers.push((sector_name, tickers, false, Some("yahoo")));
            }
        }
        if let Ok(crypto_groups) = load_crypto_groups() {
            for (sector_name, tickers) in crypto_groups {
                all_sector_tickers.push((sector_name, tickers, false, Some("crypto")));
            }
        }
    }

    let mut sector_analyses = Vec::new();
    let mut total_analyzed = 0;

    for (sector_name, sector_tickers, is_vn_sector, preferred_source) in &all_sector_tickers {
        if sector_tickers.is_empty() {
            continue;
        }

        let mut sector_stocks = Vec::new();
        let mut scores_sum = 0.0;
        let mut scores_count = 0;
        let mut above_threshold_count = 0;

        for ticker in sector_tickers {
            // Only apply index ticker filter to VN tickers
            if *is_vn_sector && is_index_ticker(ticker) {
                continue;
            }

            let current = if is_all {
                // Look up the ticker using the preferred source for this sector group
                let ticker_source = preferred_source.unwrap_or("unknown");
                match data_map.get(&format!("{ticker_source}:{ticker}")) {
                    Some((_, r)) => r,
                    None => continue,
                }
            } else {
                match data_map.get(ticker) {
                    Some((_, r)) => r,
                    None => continue,
                }
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

                let ticker_source = if is_all {
                    preferred_source.map(|s| s.to_string())
                } else {
                    None
                };

                sector_stocks.push(StockMaInfo {
                    symbol: ticker.clone(),
                    close: current.close,
                    volume: current.volume as u64,
                    ma_value: ma_val,
                    ma_score: ma_scr,
                    close_changed: current.close_changed,
                    volume_changed: current.volume_changed,
                    source: ticker_source,
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
