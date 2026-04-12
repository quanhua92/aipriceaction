use std::collections::{BTreeMap, HashMap};

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::models::indicators::calculate_wma;
use crate::models::ohlcv::{OhlcvJoined, OhlcvRow};
use crate::queries::ohlcv;
use crate::server::types::Mode;
use crate::server::AppState;
use crate::constants::api::SMA_MAX_PERIOD;

use super::{
    get_all_sources, get_ticker_sector, is_index_ticker, load_crypto_groups, load_ticker_groups,
    load_yahoo_groups, try_redis_batch, AnalysisResponse,
};

// ---------------------------------------------------------------------------
// Query params
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum RrgAlgorithm {
    #[default]
    Jdk,
    Mascore,
}

#[derive(Debug, Deserialize)]
pub struct RrgQuery {
    pub benchmark: Option<String>,
    #[serde(default)]
    pub algorithm: RrgAlgorithm,
    #[serde(default = "default_period")]
    pub period: usize,
    #[serde(default = "default_trails")]
    pub trails: usize,
    #[serde(default = "default_min_volume")]
    pub min_volume: i64,
    pub date: Option<String>,
    #[serde(default)]
    pub mode: Mode,
}

fn default_period() -> usize {
    10
}
fn default_trails() -> usize {
    10
}
fn default_min_volume() -> i64 {
    100_000
}

fn parse_rrg_date(date_str: &str) -> Option<DateTime<Utc>> {
    chrono::NaiveDate::parse_from_str(date_str, "%Y-%m-%d")
        .ok()
        .and_then(|d| d.and_hms_opt(23, 59, 59))
        .map(|dt| dt.and_utc())
}

/// Build a source → sector-groups lookup so each source gets its own sector mapping.
fn build_source_sector_groups(
    vn_groups: &HashMap<String, Vec<String>>,
) -> HashMap<&'static str, BTreeMap<String, Vec<String>>> {
    let mut map = HashMap::new();
    // VN: convert HashMap → BTreeMap
    map.insert("vn", vn_groups.iter().map(|(k, v)| (k.clone(), v.clone())).collect());
    // Crypto
    if let Ok(groups) = load_crypto_groups() {
        map.insert("crypto", groups);
    }
    // Yahoo/global
    if let Ok(groups) = load_yahoo_groups() {
        map.insert("yahoo", groups);
    }
    map
}

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct RrgResponse {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub benchmark: Option<String>,
    pub algorithm: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub period: Option<usize>,
    pub tickers: Vec<RrgTickerSnapshot>,
}

#[derive(Debug, Serialize)]
pub struct RrgTickerSnapshot {
    pub symbol: String,
    pub rs_ratio: f64,
    pub rs_momentum: f64,
    pub raw_rs: f64,
    pub close: f64,
    pub volume: i64,
    pub sector: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trails: Option<Vec<RrgTrailPoint>>,
}

#[derive(Debug, Serialize)]
pub struct RrgTrailPoint {
    pub date: String,
    pub rs_ratio: f64,
    pub rs_momentum: f64,
}

// ---------------------------------------------------------------------------
// Math helpers
// ---------------------------------------------------------------------------

/// Signature shared by all RRG algorithms.
type RrgComputeFn = fn(security: &[f64], benchmark: &[f64], period: usize) -> Option<(Vec<f64>, Vec<f64>)>;

/// Double-smoothed WMA (WMA applied twice).
fn double_smoothed_wma(data: &[f64], period: usize) -> Vec<f64> {
    let first = calculate_wma(data, period);
    calculate_wma(&first, period)
}

/// Rolling Z-score normalization with 100-base scaling.
fn normalize_rolling_zscore(values: &[f64], window: usize) -> Vec<f64> {
    let mut result = vec![100.0; values.len()];
    if window == 0 || values.len() < window {
        return result;
    }
    for i in (window - 1)..values.len() {
        let slice = &values[i + 1 - window..=i];
        let mean: f64 = slice.iter().sum::<f64>() / window as f64;
        let variance: f64 = slice.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / window as f64;
        let std_dev = variance.sqrt();
        if std_dev == 0.0 {
            result[i] = 100.0;
        } else {
            result[i] = 100.0 + 10.0 * (values[i] - mean) / std_dev;
        }
    }
    result
}

/// JdK RS-Ratio algorithm.
fn compute_jdk(
    security: &[f64],
    benchmark: &[f64],
    period: usize,
) -> Option<(Vec<f64>, Vec<f64>)> {
    if security.len() != benchmark.len() || security.len() < 3 * period + 1 {
        return None;
    }

    // Step 3 — Raw RS
    let raw_rs: Vec<f64> = security
        .iter()
        .zip(benchmark.iter())
        .map(|(&s, &b)| if b != 0.0 { s / b } else { 0.0 })
        .collect();

    // Step 4 — RS-Ratio (double-smoothed WMA of raw RS)
    let rs_ratio_raw = double_smoothed_wma(&raw_rs, period);

    // Step 5 — RS-Momentum (1-period ROC of RS-Ratio)
    let rs_mom_raw: Vec<f64> = (1..rs_ratio_raw.len())
        .map(|i| {
            if rs_ratio_raw[i - 1] != 0.0 {
                (rs_ratio_raw[i] - rs_ratio_raw[i - 1]) / rs_ratio_raw[i - 1]
            } else {
                0.0
            }
        })
        .collect();

    // Step 6 — Normalize both
    let rs_ratio_norm = normalize_rolling_zscore(&rs_ratio_raw, period);
    let rs_mom_norm = normalize_rolling_zscore(&rs_mom_raw, period);

    // Step 7 — Align
    let ratio_offset = rs_ratio_raw.len() - rs_mom_raw.len(); // = 1

    let mut x_vals = Vec::new();
    let mut y_vals = Vec::new();

    for i in (period - 1)..rs_mom_norm.len() {
        x_vals.push(rs_ratio_norm[i + ratio_offset]);
        y_vals.push(rs_mom_norm[i]);
    }

    Some((x_vals, y_vals))
}

// ---------------------------------------------------------------------------
// Data alignment
// ---------------------------------------------------------------------------

struct AlignedData {
    dates: Vec<DateTime<Utc>>,
    sec_closes: Vec<f64>,
    bench_closes: Vec<f64>,
}

fn align_closes_by_date(
    security_rows: &[OhlcvRow],
    benchmark_rows: &[OhlcvRow],
) -> Option<AlignedData> {
    // Reverse to chronological order (DB returns DESC)
    let sec_chrono: Vec<&OhlcvRow> = security_rows.iter().rev().collect();
    let bench_chrono: Vec<&OhlcvRow> = benchmark_rows.iter().rev().collect();

    let bench_map: HashMap<DateTime<Utc>, f64> = bench_chrono
        .iter()
        .filter_map(|r| {
            if r.close != 0.0 {
                Some((r.time, r.close))
            } else {
                None
            }
        })
        .collect();

    let mut dates = Vec::new();
    let mut sec_closes = Vec::new();
    let mut bench_closes = Vec::new();

    for row in &sec_chrono {
        if let Some(&bclose) = bench_map.get(&row.time) {
            dates.push(row.time);
            sec_closes.push(row.close);
            bench_closes.push(bclose);
        }
    }

    if sec_closes.len() < 2 {
        return None;
    }

    Some(AlignedData {
        dates,
        sec_closes,
        bench_closes,
    })
}

// ---------------------------------------------------------------------------
// Handler (dispatch)
// ---------------------------------------------------------------------------

pub async fn rrg_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<RrgQuery>,
) -> impl IntoResponse {
    // Load sector info (shared by both algorithms)
    let ticker_groups = match load_ticker_groups() {
        Ok(g) => g,
        Err(e) => {
            tracing::error!("Failed to load ticker groups: {}", e);
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "Failed to load sector information" })),
            )
                .into_response();
        }
    };

    let is_all = params.mode == Mode::All;

    // Validate date format early if provided
    let end_time: Option<DateTime<Utc>> = match &params.date {
        Some(date_str) => match parse_rrg_date(date_str) {
            Some(dt) => Some(dt),
            None => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({ "error": format!("Invalid date format '{}'. Use YYYY-MM-DD", date_str) })),
                )
                    .into_response();
            }
        },
        None => None,
    };

    let analysis_date = match &end_time {
        Some(dt) => dt.format("%Y-%m-%d").to_string(),
        None => "latest".to_string(),
    };

    match params.algorithm {
        RrgAlgorithm::Mascore => {
            handle_mascore(state, &params, &ticker_groups, is_all, end_time, &analysis_date).await
        }
        RrgAlgorithm::Jdk => {
            handle_jdk(state, params, &ticker_groups, is_all, end_time, &analysis_date).await
        }
    }
}

// ---------------------------------------------------------------------------
// Mascore handler
// ---------------------------------------------------------------------------

async fn handle_mascore(
    state: Arc<AppState>,
    params: &RrgQuery,
    ticker_groups: &std::collections::HashMap<String, Vec<String>>,
    is_all: bool,
    end_time: Option<DateTime<Utc>>,
    analysis_date: &str,
) -> axum::response::Response {
    // Build per-source sector groups for correct sector assignment
    let source_groups = build_source_sector_groups(ticker_groups);

    // Collect ticker symbols per source (reuse handle_jdk pattern)
    let mut source_symbols: Vec<(&str, Vec<String>)> = Vec::new();
    match params.mode {
        Mode::Vn => {
            let symbols = ticker_groups
                .values()
                .flat_map(|v| v.iter().cloned())
                .collect::<Vec<_>>();
            source_symbols.push(("vn", symbols));
        }
        Mode::Crypto => {
            if let Ok(groups) = load_crypto_groups() {
                let symbols = groups
                    .values()
                    .flat_map(|v| v.iter().cloned())
                    .collect::<Vec<_>>();
                source_symbols.push(("crypto", symbols));
            }
        }
        Mode::Yahoo => {
            if let Ok(groups) = load_yahoo_groups() {
                let symbols = groups
                    .values()
                    .flat_map(|v| v.iter().cloned())
                    .collect::<Vec<_>>();
                source_symbols.push(("yahoo", symbols));
            }
        }
        Mode::All => {
            let sources = get_all_sources();
            for &src in &sources {
                let symbols = match src {
                    "vn" => ticker_groups
                        .values()
                        .flat_map(|v| v.iter().cloned())
                        .collect(),
                    "crypto" => load_crypto_groups()
                        .map(|g| g.into_values().flatten().collect())
                        .unwrap_or_default(),
                    "yahoo" => load_yahoo_groups()
                        .map(|g| g.into_values().flatten().collect())
                        .unwrap_or_default(),
                    _ => Vec::new(),
                };
                source_symbols.push((src, symbols));
            }
        }
    }

    // When trails=0 and no date filter, use the efficient get_latest_daily_per_ticker (DISTINCT ON)
    // When trails>0 or date is specified, use get_ohlcv_joined_batch to get historical rows
    if params.trails == 0 && end_time.is_none() {
        // Efficient path: only latest row per ticker
        let rows: Vec<(OhlcvJoined, &str)> = if is_all {
            let sources = get_all_sources();
            // Pre-extract symbol vectors to avoid temporary lifetime issues in tokio::join!
            let syms: Vec<Vec<String>> = sources.iter()
                .map(|src| source_symbols.iter().find(|(s,_)| *s == *src).map(|(_,v)| v.clone()).unwrap_or_default())
                .collect();
            // Try Redis for each source concurrently
            let (r1, r2, r3, r4) = tokio::join!(
                try_redis_batch(&state.redis_client, sources[0], &syms[0], "1D", 1 + SMA_MAX_PERIOD, "rrg"),
                try_redis_batch(&state.redis_client, sources[1], &syms[1], "1D", 1 + SMA_MAX_PERIOD, "rrg"),
                try_redis_batch(&state.redis_client, sources[2], &syms[2], "1D", 1 + SMA_MAX_PERIOD, "rrg"),
                try_redis_batch(&state.redis_client, sources[3], &syms[3], "1D", 1 + SMA_MAX_PERIOD, "rrg"),
            );
            let mut merged: Vec<(OhlcvJoined, &str)> = Vec::new();
            for (redis_result, src) in [(r1, sources[0]), (r2, sources[1]), (r3, sources[2]), (r4, sources[3])] {
                if let Some(map) = redis_result {
                    for (ticker, orows) in map {
                        let enhanced = ohlcv::enhance_rows(&ticker, orows, Some(1), None, true);
                        merged.extend(enhanced.into_iter().map(|row| (row, src)));
                    }
                } else {
                    // Redis failed for this source — fall back to PG
                    match ohlcv::get_latest_daily_per_ticker(&state.pool, src).await {
                        Ok(v) => merged.extend(v.into_iter().map(|row| (row, src))),
                        Err(e) => tracing::warn!("Failed to fetch daily data for source '{}': {}", src, e),
                    }
                }
            }
            merged
        } else {
            let source = params.mode.source_label();
            let symbols: Vec<String> = source_symbols.iter().find(|(s,_)| *s == source).map(|(_,v)| v.clone()).unwrap_or_default();
            if let Some(map) = try_redis_batch(&state.redis_client, source, &symbols, "1D", 1 + SMA_MAX_PERIOD, "rrg/single").await {
                let mut merged: Vec<(OhlcvJoined, &str)> = Vec::new();
                for (ticker, orows) in map {
                    let enhanced = ohlcv::enhance_rows(&ticker, orows, Some(1), None, true);
                    merged.extend(enhanced.into_iter().map(|row| (row, source)));
                }
                if merged.is_empty() {
                    // Redis returned empty — fall back to PG
                    match ohlcv::get_latest_daily_per_ticker(&state.pool, source).await {
                        Ok(r) => r.into_iter().map(|row| (row, source)).collect(),
                        Err(e) => {
                            tracing::error!("Failed to fetch daily data: {}", e);
                            return (
                                StatusCode::INTERNAL_SERVER_ERROR,
                                Json(serde_json::json!({ "error": "Failed to fetch market data" })),
                            )
                                .into_response();
                        }
                    }
                } else {
                    merged
                }
            } else {
                // Redis unavailable — fall back to PG
                match ohlcv::get_latest_daily_per_ticker(&state.pool, source).await {
                    Ok(r) => r.into_iter().map(|row| (row, source)).collect(),
                    Err(e) => {
                        tracing::error!("Failed to fetch daily data: {}", e);
                        return (
                            StatusCode::INTERNAL_SERVER_ERROR,
                            Json(serde_json::json!({ "error": "Failed to fetch market data" })),
                        )
                            .into_response();
                    }
                }
            }
        };

        let mut snapshots = Vec::new();
        for (row, row_source) in &rows {
            if is_index_ticker(&row.ticker) {
                continue;
            }
            if row.volume < params.min_volume {
                continue;
            }
            let x = match row.ma20_score {
                Some(v) => v,
                None => continue,
            };
            let y = match row.ma100_score {
                Some(v) => v,
                None => continue,
            };
            let sector = source_groups
                .get(*row_source)
                .and_then(|g| get_ticker_sector(&row.ticker, g));
            snapshots.push(RrgTickerSnapshot {
                symbol: row.ticker.clone(),
                rs_ratio: x,
                rs_momentum: y,
                raw_rs: 0.0,
                close: row.close,
                volume: row.volume,
                sector,
                source: if is_all { Some((*row_source).to_string()) } else { None },
                trails: None,
            });
        }
        let total_analyzed = snapshots.len();
        return (
            StatusCode::OK,
            Json(AnalysisResponse {
                analysis_date: analysis_date.to_string(),
                analysis_type: "rrg".to_string(),
                total_analyzed,
                data: RrgResponse {
                    benchmark: None,
                    algorithm: "mascore".to_string(),
                    period: None,
                    tickers: snapshots,
                },
            }),
        )
            .into_response();
    }

    // When date is specified with trails=0, we still need to fetch data via batch path.
    // Use limit=1 to get just the latest row before the cutoff date.
    let effective_limit = if params.trails == 0 {
        Some(1)
    } else {
        Some(params.trails.clamp(1, 120) as i64)
    };

    let effective_trails = if params.trails == 0 { 0 } else { params.trails.clamp(1, 120) };

    // Trails path: fetch historical rows per source
    let mut all_joined: Vec<(HashMap<String, Vec<OhlcvJoined>>, &str)> = Vec::new();
    let redis_limit = effective_limit.unwrap_or(1) + SMA_MAX_PERIOD;

    if is_all {
        let sources = get_all_sources();
        let et = end_time;
        let el = effective_limit;
        // Pre-extract symbol vectors for tokio::join!
        let syms: Vec<Vec<String>> = sources.iter()
            .map(|src| source_symbols.iter().find(|(s,_)| *s == *src).map(|(_,v)| v.clone()).unwrap_or_default())
            .collect();
        let (r1, r2, r3, r4) = tokio::join!(
            try_redis_batch(&state.redis_client, sources[0], &syms[0], "1D", redis_limit, "rrg"),
            try_redis_batch(&state.redis_client, sources[1], &syms[1], "1D", redis_limit, "rrg"),
            try_redis_batch(&state.redis_client, sources[2], &syms[2], "1D", redis_limit, "rrg"),
            try_redis_batch(&state.redis_client, sources[3], &syms[3], "1D", redis_limit, "rrg"),
        );
        for (redis_result, src) in [(r1, sources[0]), (r2, sources[1]), (r3, sources[2]), (r4, sources[3])] {
            if let Some(map) = redis_result {
                let joined: HashMap<String, Vec<OhlcvJoined>> = map.into_iter()
                    .map(|(ticker, orows)| {
                        let filtered = match et {
                            Some(end) => orows.into_iter().filter(|r| r.time <= end).collect(),
                            None => orows,
                        };
                        let enhanced = ohlcv::enhance_rows(&ticker, filtered, effective_limit, None, true);
                        (ticker, enhanced)
                    })
                    .filter(|(_, v)| !v.is_empty())
                    .collect();
                if !joined.is_empty() {
                    all_joined.push((joined, src));
                    continue;
                }
            }
            // Redis failed or returned empty for this source — fall back to PG
            match ohlcv::get_ohlcv_joined_batch(&state.pool, src, &[], "1D", el, None, et, true).await {
                Ok(map) => all_joined.push((map, src)),
                Err(e) => tracing::warn!("Failed to fetch daily data for source '{}': {}", src, e),
            }
        }
    } else {
        let source = params.mode.source_label();
        let symbols: Vec<String> = source_symbols.iter().find(|(s,_)| *s == source).map(|(_,v)| v.clone()).unwrap_or_default();
        if let Some(map) = try_redis_batch(&state.redis_client, source, &symbols, "1D", redis_limit, "rrg/single").await {
            let joined: HashMap<String, Vec<OhlcvJoined>> = map.into_iter()
                .map(|(ticker, orows)| {
                    let filtered = match end_time {
                        Some(end) => orows.into_iter().filter(|r| r.time <= end).collect(),
                        None => orows,
                    };
                    let enhanced = ohlcv::enhance_rows(&ticker, filtered, effective_limit, None, true);
                    (ticker, enhanced)
                })
                .filter(|(_, v)| !v.is_empty())
                .collect();
            if !joined.is_empty() {
                all_joined.push((joined, source));
            } else {
                // Redis returned empty — fall back to PG
                match ohlcv::get_ohlcv_joined_batch(&state.pool, source, &[], "1D", effective_limit, None, end_time, true).await {
                    Ok(map) => all_joined.push((map, source)),
                    Err(e) => {
                        tracing::error!("Failed to fetch daily data: {}", e);
                        return (
                            StatusCode::INTERNAL_SERVER_ERROR,
                            Json(serde_json::json!({ "error": "Failed to fetch market data" })),
                        )
                            .into_response();
                    }
                }
            }
        } else {
            // Redis unavailable — fall back to PG
            match ohlcv::get_ohlcv_joined_batch(&state.pool, source, &[], "1D", effective_limit, None, end_time, true).await {
                Ok(map) => all_joined.push((map, source)),
                Err(e) => {
                    tracing::error!("Failed to fetch daily data: {}", e);
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(serde_json::json!({ "error": "Failed to fetch market data" })),
                    )
                        .into_response();
                }
            }
        }
    }

    let mut snapshots = Vec::new();

    for (joined_map, src) in &all_joined {
        for (ticker, rows) in joined_map {
            if is_index_ticker(ticker) {
                continue;
            }

            // DB returns DESC order; reverse to chronological
            let mut chrono_rows: Vec<&OhlcvJoined> = rows.iter().rev().collect();

            // Filter rows that have both ma20_score and ma100_score
            chrono_rows.retain(|r| r.ma20_score.is_some() && r.ma100_score.is_some());

            if chrono_rows.is_empty() {
                continue;
            }

            // Latest row for volume check
            let latest = chrono_rows.last().unwrap();
            if latest.volume < params.min_volume {
                continue;
            }

            // Take the last effective_trails rows
            let start = chrono_rows.len().saturating_sub(effective_trails as usize);
            chrono_rows = chrono_rows[start..].to_vec();

            // Build trail points (only when effective_trails > 0)
            let trails = if effective_trails > 0 {
                let trail_points: Vec<RrgTrailPoint> = chrono_rows
                    .iter()
                    .map(|r| RrgTrailPoint {
                        date: r.time.format("%Y-%m-%d").to_string(),
                        rs_ratio: r.ma20_score.unwrap(),
                        rs_momentum: r.ma100_score.unwrap(),
                    })
                    .collect();
                if trail_points.is_empty() { None } else { Some(trail_points) }
            } else {
                None
            };

            // Re-get latest after trimming to trail_length
            let latest = chrono_rows.last().unwrap();
            let sector = source_groups
                .get(src)
                .and_then(|g| get_ticker_sector(ticker, g));

            snapshots.push(RrgTickerSnapshot {
                symbol: ticker.clone(),
                rs_ratio: latest.ma20_score.unwrap(),
                rs_momentum: latest.ma100_score.unwrap(),
                raw_rs: 0.0,
                close: latest.close,
                volume: latest.volume,
                sector,
                source: if is_all { Some((*src).to_string()) } else { None },
                trails,
            });
        }
    }

    let total_analyzed = snapshots.len();

    (
        StatusCode::OK,
        Json(AnalysisResponse {
            analysis_date: analysis_date.to_string(),
            analysis_type: "rrg".to_string(),
            total_analyzed,
            data: RrgResponse {
                benchmark: None,
                algorithm: "mascore".to_string(),
                period: None,
                tickers: snapshots,
            },
        }),
    )
        .into_response()
}

// ---------------------------------------------------------------------------
// JdK handler (original logic)
// ---------------------------------------------------------------------------

async fn handle_jdk(
    state: Arc<AppState>,
    params: RrgQuery,
    ticker_groups: &std::collections::HashMap<String, Vec<String>>,
    is_all: bool,
    end_time: Option<DateTime<Utc>>,
    analysis_date: &str,
) -> axum::response::Response {
    let source_groups = build_source_sector_groups(ticker_groups);

    let period = params.period.clamp(4, 50);
    let trail_length = params.trails.clamp(1, 120);
    let benchmark = params
        .benchmark
        .unwrap_or_else(|| "VNINDEX".to_string());
    let benchmark_upper = benchmark.to_uppercase();

    let compute_fn: RrgComputeFn = compute_jdk;

    // Collect all ticker symbols per source
    let mut source_symbols: Vec<(&str, Vec<String>)> = Vec::new();

    match params.mode {
        Mode::Vn => {
            let symbols = ticker_groups
                .values()
                .flat_map(|v| v.iter().cloned())
                .collect::<Vec<_>>();
            source_symbols.push(("vn", symbols));
        }
        Mode::Crypto => {
            if let Ok(groups) = load_crypto_groups() {
                let symbols = groups
                    .values()
                    .flat_map(|v| v.iter().cloned())
                    .collect::<Vec<_>>();
                source_symbols.push(("crypto", symbols));
            }
        }
        Mode::Yahoo => {
            if let Ok(groups) = load_yahoo_groups() {
                let symbols = groups
                    .values()
                    .flat_map(|v| v.iter().cloned())
                    .collect::<Vec<_>>();
                source_symbols.push(("yahoo", symbols));
            }
        }
        Mode::All => {
            let sources = get_all_sources();
            for &src in &sources {
                let symbols = match src {
                    "vn" => ticker_groups
                        .values()
                        .flat_map(|v| v.iter().cloned())
                        .collect(),
                    "crypto" => load_crypto_groups()
                        .map(|g| g.into_values().flatten().collect())
                        .unwrap_or_default(),
                    "yahoo" => load_yahoo_groups()
                        .map(|g| g.into_values().flatten().collect())
                        .unwrap_or_default(),
                    _ => Vec::new(),
                };
                source_symbols.push((src, symbols));
            }
        }
    }

    // For each source, fetch batch raw OHLCV for all its symbols + benchmark
    let min_bars = 3 * period + 1;
    let mut results: HashMap<String, Vec<OhlcvRow>> = HashMap::new();

    for (source, symbols) in &source_symbols {
        if symbols.is_empty() {
            continue;
        }
        let mut fetch_symbols: Vec<String> = symbols.clone();
        fetch_symbols.push(benchmark_upper.clone());
        fetch_symbols.sort();
        fetch_symbols.dedup();

        // Try Redis first
        let jdk_limit = 250 + SMA_MAX_PERIOD;
        if let Some(map) = try_redis_batch(
            &state.redis_client, source, &fetch_symbols, "1D", jdk_limit, "rrg/jdk",
        ).await {
            for (sym, rows) in map {
                let filtered = match end_time {
                    Some(end) => rows.into_iter().filter(|r| r.time <= end).collect(),
                    None => rows,
                };
                results.insert(format!("{source}:{sym}"), filtered);
            }
        } else {
            // Fall back to PG
            match ohlcv::get_ohlcv_batch_raw(
                &state.pool,
                source,
                &fetch_symbols,
                "1D",
                Some(250),
                None,
                end_time,
            )
            .await
            {
                Ok(map) => {
                    for (sym, rows) in map {
                        results.insert(format!("{source}:{sym}"), rows);
                    }
                }
                Err(e) => {
                    tracing::warn!("Failed to fetch OHLCV for source '{}': {}", source, e);
                }
            }
        }
    }

    // Get benchmark rows from the first source that has them
    let benchmark_rows = results
        .iter()
        .find(|(k, _)| k.ends_with(&format!(":{}", benchmark_upper)))
        .map(|(_, v)| v.as_slice());

    let benchmark_rows = match benchmark_rows {
        Some(r) => r,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({ "error": format!("Benchmark '{}' not found", benchmark_upper) })),
            )
                .into_response();
        }
    };

    // Compute RRG for each ticker
    let mut snapshots = Vec::new();

    for (source, symbols) in &source_symbols {
        for sym in symbols {
            if sym.eq_ignore_ascii_case(&benchmark_upper) || is_index_ticker(sym) {
                continue;
            }

            let key = format!("{source}:{sym}");
            let sec_rows = match results.get(&key) {
                Some(r) => r.as_slice(),
                None => continue,
            };

            let aligned = match align_closes_by_date(sec_rows, benchmark_rows) {
                Some(a) => a,
                None => continue,
            };

            if aligned.sec_closes.len() < min_bars {
                continue;
            }

            let (x_vals, y_vals) = match compute_fn(&aligned.sec_closes, &aligned.bench_closes, period) {
                Some(v) => v,
                None => continue,
            };

            if x_vals.is_empty() {
                continue;
            }

            // Latest point
            let latest_ratio = *x_vals.last().unwrap();
            let latest_momentum = *y_vals.last().unwrap();
            let latest_close = *aligned.sec_closes.last().unwrap();

            // Latest volume from the most recent security row (newest in time)
            let latest_volume = sec_rows
                .first()
                .map(|r| r.volume)
                .unwrap_or(0);

            // Skip low-volume tickers early to save computation
            if latest_volume < params.min_volume {
                continue;
            }

            // Raw RS for latest point
            let raw_rs = if latest_close != 0.0 && !aligned.bench_closes.is_empty() {
                let bench_close = *aligned.bench_closes.last().unwrap();
                if bench_close != 0.0 {
                    latest_close / bench_close
                } else {
                    0.0
                }
            } else {
                0.0
            };

            // Trails
            let trails = if params.trails > 0 {
                let start = x_vals.len().saturating_sub(trail_length);
                let trail_points: Vec<RrgTrailPoint> = (start..x_vals.len())
                    .filter_map(|i| {
                        let date_idx = aligned.dates.len() - x_vals.len() + i;
                        aligned.dates.get(date_idx).map(|dt| RrgTrailPoint {
                            date: dt.format("%Y-%m-%d").to_string(),
                            rs_ratio: x_vals[i],
                            rs_momentum: y_vals[i],
                        })
                    })
                    .collect();
                if trail_points.is_empty() {
                    None
                } else {
                    Some(trail_points)
                }
            } else {
                None
            };

            let sector = source_groups
                .get(source)
                .and_then(|g| get_ticker_sector(sym, g));

            snapshots.push(RrgTickerSnapshot {
                symbol: sym.clone(),
                rs_ratio: latest_ratio,
                rs_momentum: latest_momentum,
                raw_rs,
                close: latest_close,
                volume: latest_volume,
                sector,
                source: if is_all { Some(source.to_string()) } else { None },
                trails,
            });
        }
    }

    let total_analyzed = snapshots.len();

    (
        StatusCode::OK,
        Json(AnalysisResponse {
            analysis_date: analysis_date.to_string(),
            analysis_type: "rrg".to_string(),
            total_analyzed,
            data: RrgResponse {
                benchmark: Some(benchmark_upper),
                algorithm: "jdk".to_string(),
                period: Some(period),
                tickers: snapshots,
            },
        }),
    )
        .into_response()
}
