use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::queries::ohlcv;
use crate::server::types::{is_vn_ticker, Mode};
use crate::server::AppState;
use crate::constants::api::SMA_MAX_PERIOD;

use super::{get_all_sources, get_ticker_sector, is_index_ticker, load_crypto_groups, load_yahoo_groups, parse_analysis_date, validate_limit, AnalysisResponse};

#[derive(Debug, Deserialize)]
pub struct TopPerformersQuery {
    pub date: Option<String>,
    #[serde(default = "default_sort_by")]
    pub sort_by: String,
    pub limit: Option<usize>,
    #[serde(default = "default_direction")]
    pub direction: String,
    pub min_volume: Option<u64>,
    #[serde(default)]
    pub mode: Mode,
    /// true = use EMA instead of SMA for MA indicators.
    #[serde(default)]
    pub ema: bool,
    /// true = use Redis snapshot cache (default).
    #[serde(default = "default_true")]
    pub snap: bool,
}

fn default_sort_by() -> String { "close_changed".to_string() }
fn default_direction() -> String { "desc".to_string() }
fn default_true() -> bool { true }

#[derive(Debug, Serialize)]
pub struct TopPerformersResponse {
    pub performers: Vec<PerformerInfo>,
    pub worst_performers: Vec<PerformerInfo>,
    pub hourly: Option<Vec<HourlyPerformers>>,
}

#[derive(Debug, Serialize)]
pub struct HourlyPerformers {
    pub hour: String,
    pub performers: Vec<PerformerInfo>,
    pub worst_performers: Vec<PerformerInfo>,
}

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
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
}

fn sort_optional_f64_desc(a: &Option<f64>, b: &Option<f64>) -> std::cmp::Ordering {
    match (a, b) {
        (Some(av), Some(bv)) => bv.partial_cmp(av).unwrap_or(std::cmp::Ordering::Equal),
        (Some(_), None) => std::cmp::Ordering::Less,
        (None, Some(_)) => std::cmp::Ordering::Greater,
        (None, None) => std::cmp::Ordering::Equal,
    }
}

fn sort_performers(
    performers: Vec<PerformerInfo>,
    sort_by: &str,
    direction: &str,
    limit: usize,
) -> (Vec<PerformerInfo>, Vec<PerformerInfo>) {
    let cmp = |a: &PerformerInfo, b: &PerformerInfo, ascending: bool| -> std::cmp::Ordering {
        let order = match sort_by {
            "close_changed" => sort_optional_f64_desc(&a.close_changed, &b.close_changed),
            "volume" => b.volume.cmp(&a.volume),
            "volume_changed" => sort_optional_f64_desc(&a.volume_changed, &b.volume_changed),
            "ma10_score" => sort_optional_f64_desc(&a.ma10_score, &b.ma10_score),
            "ma20_score" => sort_optional_f64_desc(&a.ma20_score, &b.ma20_score),
            "ma50_score" => sort_optional_f64_desc(&a.ma50_score, &b.ma50_score),
            "ma100_score" => sort_optional_f64_desc(&a.ma100_score, &b.ma100_score),
            "ma200_score" => sort_optional_f64_desc(&a.ma200_score, &b.ma200_score),
            "total_money_changed" => sort_optional_f64_desc(&a.total_money_changed, &b.total_money_changed),
            _ => sort_optional_f64_desc(&a.close_changed, &b.close_changed),
        };
        if ascending { order.reverse() } else { order }
    };

    let mut desc = performers.clone();
    let mut asc = performers.clone();
    desc.sort_by(|a, b| cmp(a, b, false));
    asc.sort_by(|a, b| cmp(a, b, true));

    if direction == "asc" {
        (asc.into_iter().take(limit).collect(), desc.into_iter().take(limit).collect())
    } else {
        (desc.into_iter().take(limit).collect(), asc.into_iter().take(limit).collect())
    }
}

pub async fn top_performers_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<TopPerformersQuery>,
) -> impl IntoResponse {
    let ticker_groups = match super::load_ticker_groups() {
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
    let analysis_date = parse_analysis_date(params.date.as_deref());

    // Build symbol lists per source for Redis batch reads
    let source_symbols: Vec<(&str, Vec<String>)> = if is_all {
        let sources = get_all_sources();
        sources.iter().map(|&src| {
            let symbols = match src {
                "vn" => ticker_groups.values().flat_map(|v| v.iter().cloned()).collect(),
                "crypto" => load_crypto_groups().map(|g| g.into_values().flatten().collect()).unwrap_or_default(),
                "yahoo" => load_yahoo_groups().map(|g| g.into_values().flatten().collect()).unwrap_or_default(),
                _ => Vec::new(),
            };
            (src, symbols)
        }).collect()
    } else {
        let source = params.mode.source_label();
        let symbols: Vec<String> = match source {
            "vn" => ticker_groups.values().flat_map(|v| v.iter().cloned()).collect(),
            "crypto" => load_crypto_groups().map(|g| g.into_values().flatten().collect()).unwrap_or_default(),
            "yahoo" => load_yahoo_groups().map(|g| g.into_values().flatten().collect()).unwrap_or_default(),
            _ => Vec::new(),
        };
        vec![(source, symbols)]
    };

    // Fetch latest daily data with snapshot optimization
    let rows: Vec<(crate::models::ohlcv::OhlcvJoined, &str)> = if is_all {
        let sources = get_all_sources();
        let redis_limit = 1 + SMA_MAX_PERIOD;
        let syms: Vec<Vec<String>> = sources.iter()
            .map(|src| source_symbols.iter().find(|(s,_)| *s == *src).map(|(_,v)| v.clone()).unwrap_or_default())
            .collect();
        let (r1, r2, r3, r4) = tokio::join!(
            super::fetch_source_enhanced(&state.redis_client, sources[0], &syms[0], "1D", redis_limit, "performers", params.ema, !params.snap),
            super::fetch_source_enhanced(&state.redis_client, sources[1], &syms[1], "1D", redis_limit, "performers", params.ema, !params.snap),
            super::fetch_source_enhanced(&state.redis_client, sources[2], &syms[2], "1D", redis_limit, "performers", params.ema, !params.snap),
            super::fetch_source_enhanced(&state.redis_client, sources[3], &syms[3], "1D", redis_limit, "performers", params.ema, !params.snap),
        );
        let mut merged: Vec<(crate::models::ohlcv::OhlcvJoined, &str)> = Vec::new();
        for (map, src) in [(r1, sources[0]), (r2, sources[1]), (r3, sources[2]), (r4, sources[3])] {
            for (_ticker, bars) in map {
                merged.extend(bars.into_iter().map(|row| (row, src)));
            }
        }
        merged
    } else {
        let source = params.mode.source_label();
        let symbols: Vec<String> = source_symbols.iter().find(|(s,_)| *s == source).map(|(_,v)| v.clone()).unwrap_or_default();
        let map = super::fetch_source_enhanced(&state.redis_client, source, &symbols, "1D", 1 + SMA_MAX_PERIOD, "performers/single", params.ema, !params.snap).await;
        let mut merged: Vec<(crate::models::ohlcv::OhlcvJoined, &str)> = Vec::new();
        for (_ticker, bars) in map {
            merged.extend(bars.into_iter().map(|row| (row, "")));
        }
        if !merged.is_empty() {
            merged
        } else {
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
        }
    };

    let min_volume = params.min_volume.unwrap_or(10000);

    let mut daily_performers = Vec::new();
    for (row, row_source) in &rows {
        if row.time > analysis_date {
            continue;
        }
        if is_index_ticker(&row.ticker) {
            continue;
        }
        // Only apply min_volume filter to VN tickers
        if is_vn_ticker(&row.ticker) && (row.volume as u64) < min_volume {
            continue;
        }

        let sector = get_ticker_sector(&row.ticker, &ticker_groups);

        daily_performers.push(PerformerInfo {
            symbol: row.ticker.clone(),
            close: row.close,
            volume: row.volume as u64,
            close_changed: row.close_changed,
            volume_changed: row.volume_changed,
            ma10: row.ma10,
            ma20: row.ma20,
            ma50: row.ma50,
            ma100: row.ma100,
            ma200: row.ma200,
            ma10_score: row.ma10_score,
            ma20_score: row.ma20_score,
            ma50_score: row.ma50_score,
            ma100_score: row.ma100_score,
            ma200_score: row.ma200_score,
            sector,
            total_money_changed: row.total_money_changed,
            source: if is_all { Some(row_source.to_string()) } else { None },
        });
    }

    let limit = validate_limit(params.limit);
    let (top_performers, worst_performers) = sort_performers(
        daily_performers,
        &params.sort_by,
        &params.direction,
        limit,
    );

    // Hourly breakdown not supported on PG backend (requires per-ticker hourly queries)
    let hourly_data = None;

    let total_analyzed = top_performers.len() + worst_performers.len();

    (
        StatusCode::OK,
        Json(AnalysisResponse {
            analysis_date: params.date.unwrap_or_else(|| "latest".to_string()),
            analysis_type: "top_performers".to_string(),
            total_analyzed,
            data: TopPerformersResponse {
                performers: top_performers,
                worst_performers,
                hourly: hourly_data,
            },
        }),
    ).into_response()
}
