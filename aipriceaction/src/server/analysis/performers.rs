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

use super::{validate_limit, parse_analysis_date, load_ticker_groups, get_ticker_sector, is_index_ticker, AnalysisResponse};

#[derive(Debug, Deserialize)]
pub struct TopPerformersQuery {
    pub date: Option<String>,
    #[serde(default = "default_sort_by")]
    pub sort_by: String,
    pub limit: Option<usize>,
    #[serde(default = "default_direction")]
    pub direction: String,
    pub sector: Option<String>,
    pub min_volume: Option<u64>,
    pub with_hour: Option<bool>,
    #[serde(default)]
    pub mode: Mode,
}

fn default_sort_by() -> String { "close_changed".to_string() }
fn default_direction() -> String { "desc".to_string() }

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
    let analysis_date = parse_analysis_date(params.date.as_deref());

    // Fetch latest daily data for all tickers
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

    let min_volume = params.min_volume.unwrap_or(10000);

    let mut daily_performers = Vec::new();
    for row in &rows {
        if row.time > analysis_date {
            continue;
        }
        if is_index_ticker(&row.ticker) {
            continue;
        }
        if params.mode == Mode::Vn && (row.volume as u64) < min_volume {
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
