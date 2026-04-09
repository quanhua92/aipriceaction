use std::collections::HashMap;

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::models::indicators::calculate_wma;
use crate::models::ohlcv::OhlcvRow;
use crate::queries::ohlcv;
use crate::server::types::Mode;
use crate::server::AppState;

use super::{
    get_all_sources, get_ticker_sector, is_index_ticker, load_crypto_groups, load_ticker_groups,
    load_yahoo_groups, AnalysisResponse,
};

// ---------------------------------------------------------------------------
// Query params
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum RrgAlgorithm {
    #[default]
    Jdk,
}

#[derive(Debug, Deserialize)]
pub struct RrgQuery {
    pub benchmark: Option<String>,
    #[serde(default)]
    pub algorithm: RrgAlgorithm,
    #[serde(default = "default_period")]
    pub period: usize,
    #[serde(default)]
    pub trails: bool,
    #[serde(default = "default_trail_length")]
    pub trail_length: usize,
    #[serde(default)]
    pub mode: Mode,
}

fn default_period() -> usize {
    10
}
fn default_trail_length() -> usize {
    60
}

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct RrgResponse {
    pub benchmark: String,
    pub algorithm: String,
    pub period: usize,
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
// Handler
// ---------------------------------------------------------------------------

pub async fn rrg_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<RrgQuery>,
) -> impl IntoResponse {
    // Validate params
    let period = params.period.clamp(4, 50);
    let trail_length = params.trail_length.clamp(10, 120);
    let benchmark = params
        .benchmark
        .unwrap_or_else(|| "VNINDEX".to_string());
    let benchmark_upper = benchmark.to_uppercase();
    let is_all = params.mode == Mode::All;

    let compute_fn: RrgComputeFn = match params.algorithm {
        RrgAlgorithm::Jdk => compute_jdk,
    };
    let algorithm_name = "jdk";

    // Load sector info
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

        match ohlcv::get_ohlcv_batch_raw(
            &state.pool,
            source,
            &fetch_symbols,
            "1D",
            Some(250),
            None,
            None,
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
            let trails = if params.trails {
                let start = x_vals.len().saturating_sub(trail_length);
                let trail_points: Vec<RrgTrailPoint> = (start..x_vals.len())
                    .filter_map(|i| {
                        // AlignedData.dates has the same length as sec_closes/bench_closes.
                        // x_vals/y_vals are shorter due to smoothing offsets.
                        // We need to map x_vals[i] back to the corresponding date.
                        // The offset between aligned data and output: aligned.len() - x_vals.len()
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

            let sector = get_ticker_sector(sym, &ticker_groups);

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
            analysis_date: "latest".to_string(),
            analysis_type: "rrg".to_string(),
            total_analyzed,
            data: RrgResponse {
                benchmark: benchmark_upper,
                algorithm: algorithm_name.to_string(),
                period,
                tickers: snapshots,
            },
        }),
    )
        .into_response()
}
