use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use axum_extra::extract::Query;
use chrono::NaiveDate;
use std::collections::BTreeMap;
use std::sync::Arc;

use crate::server::types::{GroupQuery, Mode, StockDataResponse, TickersQuery};
use crate::services::ohlcv;

use super::AppState;

// ── /health ──

pub async fn health(State(state): State<Arc<AppState>>) -> Response {
    match sqlx::query_scalar!("SELECT 1 as ok")
        .fetch_one(&state.pool)
        .await
    {
        Ok(_) => (
            StatusCode::OK,
            Json(serde_json::json!({
                "status": "ok",
                "storage": "postgresql"
            })),
        )
            .into_response(),
        Err(e) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "status": "error",
                "detail": e.to_string()
            })),
        )
            .into_response(),
    }
}

// ── /tickers ──

pub async fn tickers(
    State(state): State<Arc<AppState>>,
    Query(params): Query<TickersQuery>,
) -> Response {
    // No symbols → empty object (matching parent project behaviour)
    let symbols = match params.symbol {
        Some(ref syms) if !syms.is_empty() => syms.clone(),
        _ => return (StatusCode::OK, Json(BTreeMap::<String, Vec<StockDataResponse>>::new())).into_response(),
    };

    // Validate interval
    let interval = match crate::server::types::normalise_interval(
        params.interval.as_deref().unwrap_or("1D"),
    ) {
        Some(iv) => iv,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": format!(
                        "Invalid interval '{}'. Must be one of: 1D, 1H, 1m",
                        params.interval.as_deref().unwrap_or("")
                    )
                })),
            )
                .into_response()
        }
    };

    // Parse optional date range
    let start_time = params.start_date.as_deref().and_then(|s| parse_date(s));
    let end_time = params.end_date.as_deref().and_then(|s| parse_date_end(s));

    let source = params.mode.source_label();
    let mut result: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();
    let is_daily = interval == "1D";
    let is_csv = params.format.eq_ignore_ascii_case("csv");

    for symbol in &symbols {
        let rows = match ohlcv::get_ohlcv_joined_range(
            &state.pool,
            source,
            symbol,
            interval,
            params.limit,
            start_time,
            end_time,
        )
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!("Failed to fetch {symbol} ({interval}): {e}");
                continue;
            }
        };

        let mapped: Vec<StockDataResponse> = rows
            .into_iter()
            .map(|r| map_ohlcv_to_response(r, is_daily, params.legacy, params.mode))
            .collect();

        result.insert(symbol.clone(), mapped);
    }

    if is_csv {
        csv_response(&result)
    } else {
        (StatusCode::OK, Json(result)).into_response()
    }
}

// ── /tickers/group ──

pub async fn tickers_group(Query(params): Query<GroupQuery>) -> Response {
    let result: Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error>> = match params.mode {
        Mode::Vn => load_vn_groups(),
        Mode::Crypto => load_crypto_groups(),
    };

    match result {
        Ok(groups) => (StatusCode::OK, Json(groups)).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": e.to_string() })),
        )
            .into_response(),
    }
}

// ── Mapping helpers ──

fn map_ohlcv_to_response(
    row: crate::models::ohlcv::OhlcvJoined,
    is_daily: bool,
    legacy: bool,
    mode: Mode,
) -> StockDataResponse {
    let time_str = if is_daily {
        row.time.format("%Y-%m-%d").to_string()
    } else {
        row.time.format("%Y-%m-%d %H:%M:%S").to_string()
    };

    let legacy_divisor = if legacy && mode == Mode::Vn && !crate::server::types::is_index_ticker(&row.ticker) {
        1000.0
    } else {
        1.0
    };

    StockDataResponse {
        time: time_str,
        open: row.open / legacy_divisor,
        high: row.high / legacy_divisor,
        low: row.low / legacy_divisor,
        close: row.close / legacy_divisor,
        volume: row.volume as u64,
        symbol: row.ticker,
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
        close_changed: row.close_changed,
        volume_changed: row.volume_changed,
        total_money_changed: row.total_money_changed,
    }
}

// ── CSV response builder ──

fn csv_response(data: &BTreeMap<String, Vec<StockDataResponse>>) -> Response {
    let mut buf = String::from("ticker,time,open,high,low,close,volume\n");

    for (symbol, rows) in data {
        for r in rows {
            buf.push_str(symbol);
            buf.push(',');
            buf.push_str(&r.time);
            buf.push(',');
            buf.push_str(&r.open.to_string());
            buf.push(',');
            buf.push_str(&r.high.to_string());
            buf.push(',');
            buf.push_str(&r.low.to_string());
            buf.push(',');
            buf.push_str(&r.close.to_string());
            buf.push(',');
            buf.push_str(&r.volume.to_string());
            buf.push('\n');
        }
    }

    (StatusCode::OK, [("content-type", "text/csv")], buf).into_response()
}

// ── Date parsing ──

fn parse_date(s: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    NaiveDate::parse_from_str(s, "%Y-%m-%d")
        .ok()
        .and_then(|d| d.and_hms_opt(0, 0, 0))
        .map(|dt| dt.and_utc())
}

fn parse_date_end(s: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    NaiveDate::parse_from_str(s, "%Y-%m-%d")
        .ok()
        .and_then(|d| d.and_hms_opt(23, 59, 59))
        .map(|dt| dt.and_utc())
}

// ── Group file loaders ──

/// Resolve a data file by searching CWD then parent directory.
fn resolve_data_file(name: &str) -> Result<std::path::PathBuf, Box<dyn std::error::Error>> {
    let cwd = std::path::Path::new(name);
    if cwd.exists() {
        return Ok(cwd.to_path_buf());
    }
    let parent = std::path::Path::new("..").join(name);
    if parent.exists() {
        return Ok(parent);
    }
    Err(format!("Data file not found: {name} (searched . and ../)").into())
}

fn load_vn_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error>> {
    let path = resolve_data_file("ticker_group.json")?;
    let content = std::fs::read_to_string(&path)?;
    let groups: BTreeMap<String, Vec<String>> = serde_json::from_str(&content)?;
    Ok(groups)
}

fn load_crypto_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error>> {
    let path = resolve_data_file("crypto_top_100.json")?;
    let content = std::fs::read_to_string(&path)?;

    // crypto_top_100.json has structure: { "data": [...], ... }
    let raw: serde_json::Value = serde_json::from_str(&content)?;

    let symbols: Vec<String> = raw["data"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|item| item["symbol"].as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();

    let mut map = BTreeMap::new();
    map.insert("CRYPTO_TOP_100".to_string(), symbols);
    Ok(map)
}
