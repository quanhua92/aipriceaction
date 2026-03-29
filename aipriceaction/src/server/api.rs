use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use axum_extra::extract::Query;
use chrono::{Datelike, NaiveDate, Timelike};
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use crate::server::types::{
    GroupQuery, Mode, NormalizedInterval, StockDataResponse,
    TickersQuery,
};
use crate::services::ohlcv;

use super::AppState;

// ── /health ──

pub async fn health(State(state): State<Arc<AppState>>) -> Response {
    use super::HealthRow;

    // Run stats + sync times in parallel
    let stats_fut = sqlx::query_as::<_, HealthRow>(
        r#"SELECT
               t.source,
               array_length(t.ids, 1)::bigint AS ticker_count,
               (SELECT COUNT(DISTINCT ticker_id)::bigint FROM ohlcv_daily
                WHERE time > NOW() - INTERVAL '7 days') AS active_tickers,
               (SELECT GREATEST(reltuples, 0)::bigint FROM pg_class
                WHERE relname = 'ohlcv_daily') AS daily_records,
               (SELECT COALESCE(SUM(GREATEST(reltuples, 0)), 0)::bigint
                FROM pg_class WHERE relname LIKE 'ohlcv_hourly_%' AND relkind = 'r')
               AS hourly_records,
               (SELECT COALESCE(SUM(GREATEST(reltuples, 0)), 0)::bigint
                FROM pg_class WHERE relname LIKE 'ohlcv_minute_%' AND relkind = 'r')
               AS minute_records,
               0::bigint AS ohlcv_total,
               0::bigint AS indicator_records
           FROM (SELECT source, array_agg(id) AS ids FROM tickers GROUP BY source) t"#,
    )
    .fetch_all(&state.pool);

    let daily_sync_fut = sqlx::query_scalar::<_, Option<chrono::DateTime<chrono::Utc>>>(
        "SELECT MAX(updated_at) FROM ohlcv_daily",
    ).fetch_one(&state.pool);

    let hourly_sync_fut = sqlx::query_scalar::<_, Option<chrono::DateTime<chrono::Utc>>>(
        "SELECT MAX(updated_at) FROM ohlcv_hourly_2026",
    ).fetch_one(&state.pool);

    let minute_sync_fut = sqlx::query_scalar::<_, Option<chrono::DateTime<chrono::Utc>>>(
        "SELECT MAX(updated_at) FROM ohlcv_minute_2026",
    ).fetch_one(&state.pool);

    let (stats_res, daily_sync_res, hourly_sync_res, minute_sync_res) =
        tokio::join!(stats_fut, daily_sync_fut, hourly_sync_fut, minute_sync_fut);

    let stats = match stats_res {
        Ok(rows) => rows,
        Err(_) => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({
                    "status": "error",
                    "detail": "Database connection failed"
                })),
            )
                .into_response();
        }
    };

    let daily_sync = daily_sync_res.ok().flatten();
    let hourly_sync = hourly_sync_res.ok().flatten();
    let minute_sync = minute_sync_res.ok().flatten();

    let mut total_tickers = 0i64;
    let mut active_tickers = 0i64;
    let mut daily = 0i64;
    let mut hourly = 0i64;
    let mut minute = 0i64;

    for row in &stats {
        total_tickers += row.ticker_count;
        active_tickers += row.active_tickers;
        daily += row.daily_records;
        hourly += row.hourly_records;
        minute += row.minute_records;
    }

    let uptime_secs = state.started_at.elapsed().as_secs();

    // Trading hours: 9:00-15:00 ICT (UTC+7) = 02:00-08:00 UTC, Mon-Fri
    let now = chrono::Utc::now();
    let weekday = now.weekday();
    let hour_utc = now.hour();
    let is_trading_hours = weekday.num_days_from_monday() < 5
        && hour_utc >= 2
        && hour_utc < 8;

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "total_tickers_count": total_tickers,
            "active_tickers_count": active_tickers,
            "daily_records_count": daily,
            "hourly_records_count": hourly,
            "minute_records_count": minute,
            "daily_last_sync": daily_sync,
            "hourly_last_sync": hourly_sync,
            "minute_last_sync": minute_sync,
            "is_trading_hours": is_trading_hours,
            "trading_hours_timezone": "Asia/Ho_Chi_Minh",
            "uptime_secs": uptime_secs,
            "current_system_time": chrono::Utc::now().to_rfc3339(),
        })),
    )
        .into_response()
}

// ── /tickers ──

pub async fn tickers(
    State(state): State<Arc<AppState>>,
    Query(params): Query<TickersQuery>,
) -> Response {
    // No symbols → query all tickers for the mode
    let symbols = match params.symbol {
        Some(ref syms) if !syms.is_empty() => syms.clone(),
        None => {
            // Load all tickers for the given mode from the DB
            let source = params.mode.source_label();
            match ohlcv::list_tickers(&state.pool, source).await {
                Ok(tickers) => tickers.into_iter().map(|t| t.ticker).collect(),
                Err(e) => {
                    tracing::warn!("Failed to list tickers for {source}: {e}");
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(serde_json::json!({ "error": "Failed to list tickers" })),
                    )
                        .into_response();
                }
            }
        }
        _ => return (StatusCode::OK, Json(BTreeMap::<String, Vec<StockDataResponse>>::new())).into_response(),
    };

    // Validate interval
    let interval = match NormalizedInterval::parse(
        params.interval.as_deref().unwrap_or("1D"),
    ) {
        Some(iv) => iv,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": format!(
                        "Invalid interval '{}'. Must be one of: {}",
                        params.interval.as_deref().unwrap_or(""),
                        NormalizedInterval::all_valid()
                    )
                })),
            )
                .into_response()
        }
    };

    let is_csv = params.format.eq_ignore_ascii_case("csv");

    match interval {
        NormalizedInterval::Native(db_interval) => {
            native_tickers(&state, symbols, db_interval, &params, is_csv).await
        }
        NormalizedInterval::Aggregated(agg) => {
            aggregated_tickers(&state, symbols, agg, &params, is_csv).await
        }
    }
}

/// Native interval: query DB directly.
async fn native_tickers(
    state: &Arc<AppState>,
    symbols: Vec<String>,
    interval: &str,
    params: &TickersQuery,
    is_csv: bool,
) -> Response {
    let start_time = params.start_date.as_deref().and_then(parse_date);
    let end_time = params.end_date.as_deref().and_then(parse_date_end);
    let source = params.mode.source_label();
    let is_daily = interval == "1D";

    // Use batch query — single SQL query for all tickers instead of N sequential queries.
    // When symbols is empty (no ?symbol= param), fetches all tickers for the source.
    // When symbols is non-empty, fetches only the specified tickers.
    let batch_map = match ohlcv::get_ohlcv_joined_batch(
        &state.pool,
        source,
        &symbols,
        interval,
        params.limit,
        start_time,
        end_time,
    )
    .await
    {
        Ok(m) => m,
        Err(e) => {
            tracing::warn!("Failed to batch-fetch tickers ({interval}): {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "Failed to fetch data" })),
            )
                .into_response();
        }
    };

    let mut result: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();

    for (ticker, rows) in batch_map {
        let mut mapped: Vec<StockDataResponse> = rows
            .into_iter()
            .map(|r| map_ohlcv_to_response(r, is_daily, params.legacy, params.mode))
            .collect();

        // DB returns newest first (DESC index scan), but API contract is oldest first
        mapped.reverse();

        result.insert(ticker, mapped);
    }

    // Remove tickers with no data (matches production behavior)
    result.retain(|_, v| !v.is_empty());

    if is_csv {
        csv_response(&result)
    } else {
        (StatusCode::OK, Json(result)).into_response()
    }
}

/// Aggregated interval: fetch source data, aggregate, enhance, trim.
async fn aggregated_tickers(
    state: &Arc<AppState>,
    symbols: Vec<String>,
    agg: crate::models::aggregated_interval::AggregatedInterval,
    params: &TickersQuery,
    is_csv: bool,
) -> Response {
    use crate::services::aggregator::{AggregatedOhlcv, Aggregator};

    let base_interval = agg.base_interval().as_str();
    let source = params.mode.source_label();

    // Fetch source data with lookback buffer for MA200
    let limit = params.limit.unwrap_or(100);
    let lookback = limit + 5000;
    let start_time = params.start_date.as_deref().and_then(parse_date);
    let end_time = params.end_date.as_deref().and_then(parse_date_end);

    let is_daily = base_interval == "1D";

    let mut per_ticker: HashMap<String, Vec<AggregatedOhlcv>> = HashMap::new();

    for symbol in &symbols {
        let rows = match ohlcv::get_ohlcv_joined_range(
            &state.pool,
            source,
            symbol,
            base_interval,
            Some(lookback),
            start_time,
            end_time,
        )
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!("Failed to fetch {symbol} ({base_interval}) for aggregation: {e}");
                continue;
            }
        };

        // Aggregate
        let aggregated = if is_daily {
            Aggregator::aggregate_daily_data(rows, agg)
        } else {
            Aggregator::aggregate_minute_data(rows, agg)
        };

        per_ticker.insert(symbol.clone(), aggregated);
    }

    // Enhance with indicators
    let enhanced = Aggregator::enhance_aggregated_data(per_ticker);

    // Trim to requested limit and map to response
    let mut result: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();

    for symbol in &symbols {
        if let Some(data) = enhanced.get(symbol) {
            let len = data.len();
            let start = if len > limit as usize { len - limit as usize } else { 0 };
            let trimmed: Vec<StockDataResponse> = data[start..]
                .iter()
                .map(|d| map_aggregated_to_response(d, is_daily, params.legacy, params.mode))
                .collect();

            result.insert(symbol.clone(), trimmed);
        }
    }

    // Remove tickers with no data (matches production behavior)
    result.retain(|_, v| !v.is_empty());

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

// ── /explorer ──

pub async fn explorer_handler() -> Response {
    let index_path = std::path::Path::new("public").join("index.html");

    match tokio::fs::read_to_string(&index_path).await {
        Ok(html) => (
            StatusCode::OK,
            [("content-type", "text/html; charset=utf-8")],
            html,
        )
            .into_response(),
        Err(_) => (
            StatusCode::NOT_FOUND,
            "Explorer not found",
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
        row.time.format("%Y-%m-%dT%H:%M:%S").to_string()
    };

    let legacy_divisor =
        if legacy && mode == Mode::Vn && !crate::server::types::is_index_ticker(&row.ticker) {
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

fn map_aggregated_to_response(
    row: &crate::services::aggregator::AggregatedOhlcv,
    is_daily: bool,
    legacy: bool,
    mode: Mode,
) -> StockDataResponse {
    let time_str = if is_daily {
        row.time.format("%Y-%m-%d").to_string()
    } else {
        row.time.format("%Y-%m-%dT%H:%M:%S").to_string()
    };

    let legacy_divisor =
        if legacy && mode == Mode::Vn && !crate::server::types::is_index_ticker(&row.ticker) {
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
        symbol: row.ticker.clone(),
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

fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(n) => n.to_string(),
        None => String::new(),
    }
}

fn csv_response(data: &BTreeMap<String, Vec<StockDataResponse>>) -> Response {
    let mut buf = String::from(
        "symbol,time,open,high,low,close,volume,ma10,ma20,ma50,ma100,ma200,ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,close_changed,volume_changed,total_money_changed\n",
    );

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
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma10));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma20));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma50));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma100));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma200));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma10_score));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma20_score));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma50_score));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma100_score));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma200_score));
            buf.push(',');
            buf.push_str(&fmt_opt(r.close_changed));
            buf.push(',');
            buf.push_str(&fmt_opt(r.volume_changed));
            buf.push(',');
            buf.push_str(&fmt_opt(r.total_money_changed));
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
    let path = resolve_data_file("binance_tickers.json")?;
    let content = std::fs::read_to_string(&path)?;

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
