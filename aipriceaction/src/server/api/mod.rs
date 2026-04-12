pub(super) mod data_loader;
mod fetch;
mod response;

use axum::extract::State;
use axum::http::{HeaderName, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Json, Response};
use axum_extra::extract::Query;
use chrono::{Datelike, Timelike};
use std::collections::BTreeMap;
use std::sync::Arc;

use crate::server::types::{
    GroupQuery, Mode, NormalizedInterval, StockDataResponse,
    TickersQuery,
};

use super::AppState;

// ── /health ──

pub async fn health(State(state): State<Arc<AppState>>) -> Response {
    let snap = state.health_snapshot.read().await;

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
            "total_tickers_count": snap.total_tickers,
            "active_tickers_count": snap.active_tickers,
            "daily_records_count": snap.daily_records,
            "hourly_records_count": snap.hourly_records,
            "minute_records_count": snap.minute_records,
            "daily_last_sync": snap.daily_last_sync,
            "hourly_last_sync": snap.hourly_last_sync,
            "minute_last_sync": snap.minute_last_sync,
            "is_trading_hours": is_trading_hours,
            "trading_hours_timezone": "Asia/Ho_Chi_Minh",
            "uptime_secs": uptime_secs,
            "current_system_time": chrono::Utc::now().to_rfc3339(),
            "crypto_last_sync": 0,
            "daily_iteration_count": 0,
            "slow_iteration_count": 0,
            "crypto_iteration_count": 0,
            "memory_usage_bytes": 0,
            "memory_usage_mb": 0.0,
            "memory_limit_mb": 0,
            "memory_usage_percent": 0.0,
            "disk_cache_entries": 0,
            "disk_cache_size_bytes": 0,
            "disk_cache_size_mb": 0.0,
            "disk_cache_limit_mb": 0,
            "disk_cache_usage_percent": 0.0,
        })),
    )
        .into_response()
}

// ── /tickers ──

pub async fn tickers(
    State(state): State<Arc<AppState>>,
    Query(params): Query<TickersQuery>,
) -> Response {
    // Validate interval first (needed for all modes)
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

    // mode=all: query across all sources
    if params.mode == Mode::All {
        return handle_mode_all(&state, params, interval).await;
    }

    let extra_sources = if params.mode == Mode::Yahoo {
        crate::constants::MERGE_WITH_YAHOO
    } else {
        &[][..]
    };

    // Early return for empty or blank explicit symbol list
    if let Some(ref syms) = params.symbol {
        if syms.is_empty() || syms.iter().all(|s| s.is_empty()) {
            return (StatusCode::OK, Json(BTreeMap::<String, Vec<StockDataResponse>>::new())).into_response();
        }
    }

    // Compute effective limit before any DB call
    let is_single_ticker = params.symbol.as_ref().map(|s| s.len()) == Some(1);
    let effective_limit = params.limit.unwrap_or_else(|| {
        if is_single_ticker {
            crate::constants::api::DEFAULT_LIMIT
        } else {
            1
        }
    });
    let effective_limit = if is_single_ticker { effective_limit } else { effective_limit.min(crate::constants::api::max_limit()) };

    let is_csv = params.format.eq_ignore_ascii_case("csv");

    // Build cache key with symbols available so far
    let cache_key_symbols = params.symbol.as_deref().unwrap_or(&[]);
    let cache_key = fetch::build_cache_key(&params, &interval, cache_key_symbols, Some(effective_limit));

    // Try cache BEFORE any DB call
    if params.cache {
        let mut guard = state.tickers_cache.write().await;
        if let Some(cached) = guard.get(&cache_key) {
            let mut resp = response::build_response(cached, params.legacy, params.mode, is_csv);
            resp.headers_mut().insert(
                HeaderName::from_static("x-data-source"),
                HeaderValue::from_static("in-memory"),
            );
            return resp;
        }
        drop(guard);
    }

    // Cache miss — now load symbols from DB if needed, then fetch data
    let symbols = match params.symbol {
        Some(ref syms) => syms.clone(),
        None => {
            let source = params.mode.source_label();
            // Try Redis first (fast, no PG dependency)
            if let Some(redis_tickers) = super::redis_reader::read_ticker_list_from_redis(&state.redis_client).await {
                let mut syms: Vec<String> = redis_tickers
                    .into_iter()
                    .filter(|t| {
                        t.source == source || extra_sources.contains(&t.source.as_str())
                    })
                    .map(|t| t.ticker)
                    .collect();
                syms.sort();
                syms.dedup();
                if !syms.is_empty() {
                    tracing::debug!("Using Redis cached ticker list: {} tickers for {source}", syms.len());
                    syms
                } else {
                    fetch::pg_list_tickers(&state.pool, source, extra_sources).await
                }
            } else {
                fetch::pg_list_tickers(&state.pool, source, extra_sources).await
            }
        }
    };

    let source = params.mode.source_label();
    let start_time = params.start_date.as_deref().and_then(fetch::parse_date);
    let end_time = params.end_date.as_deref().and_then(fetch::parse_date_end);

    let (result, source_tag, redis_meta) = match interval {
        NormalizedInterval::Native(db_interval) => {
            fetch::fetch_native_tickers(
                &state.pool, &state.redis_client, source, symbols,
                db_interval, start_time, end_time,
                Some(effective_limit), extra_sources, params.redis, params.ma, params.ema,
            ).await
        }
        NormalizedInterval::Aggregated(agg) => {
            fetch::fetch_aggregated_tickers(
                &state.pool, &state.redis_client, source, symbols,
                agg, start_time, end_time,
                effective_limit, extra_sources, params.redis, params.ma, params.ema,
            ).await
        }
    };

    // Store in cache
    if params.cache {
        let mut guard = state.tickers_cache.write().await;
        guard.put(cache_key, &result);
    }

    let mut response = response::build_response(result, params.legacy, params.mode, is_csv);
    response.headers_mut().insert(
        HeaderName::from_static("x-data-source"),
        HeaderValue::from_static(source_tag),
    );
    if let Some(meta) = redis_meta {
        if let Ok(v) = HeaderValue::from_str(&meta.base_interval) {
            response.headers_mut().insert(HeaderName::from_static("x-redis-base"), v);
        }
        if let Ok(v) = HeaderValue::from_str(&meta.raw_close_count.to_string()) {
            response.headers_mut().insert(HeaderName::from_static("x-redis-raw-count"), v);
        }
        if let Ok(v) = HeaderValue::from_str(&meta.aligned_count.to_string()) {
            response.headers_mut().insert(HeaderName::from_static("x-redis-aligned"), v);
        }
        if let Ok(v) = HeaderValue::from_str(&meta.requested_limit.to_string()) {
            response.headers_mut().insert(HeaderName::from_static("x-redis-limit"), v);
        }
    }
    response
}

/// Handle mode=all: query tickers across all sources in parallel, then merge.
async fn handle_mode_all(
    state: &Arc<AppState>,
    params: TickersQuery,
    interval: NormalizedInterval,
) -> Response {
    // Early return for empty or blank explicit symbol list
    if let Some(ref syms) = params.symbol {
        if syms.is_empty() || syms.iter().all(|s| s.is_empty()) {
            return (StatusCode::OK, Json(BTreeMap::<String, Vec<StockDataResponse>>::new())).into_response();
        }
    }

    // Compute effective_limit and is_csv before any DB call
    let has_explicit_symbols = params.symbol.is_some();
    let is_single_ticker = has_explicit_symbols && params.symbol.as_ref().map(|s| s.len()) == Some(1);
    let effective_limit = params.limit.unwrap_or_else(|| {
        if is_single_ticker {
            crate::constants::api::DEFAULT_LIMIT
        } else {
            1
        }
    });
    let effective_limit = if is_single_ticker { effective_limit } else { effective_limit.min(crate::constants::api::max_limit()) };

    let is_csv = params.format.eq_ignore_ascii_case("csv");

    // Build cache key before any DB call
    let cache_key_symbols = params.symbol.as_deref().unwrap_or(&[]);
    let cache_key = fetch::build_cache_key(&params, &interval, cache_key_symbols, Some(effective_limit));

    // Check cache BEFORE any DB call
    if params.cache {
        let mut guard = state.tickers_cache.write().await;
        if let Some(cached) = guard.get(&cache_key) {
            return response::build_response(cached, params.legacy, params.mode, is_csv);
        }
        drop(guard);
    }

    // Resolve symbols → sources via shared fetch function
    let source_map = fetch::resolve_source_map(
        &state.redis_client,
        &state.pool,
        params.symbol.as_deref(),
    ).await;

    // Fetch per source in parallel using shared fetch functions
    let mut handles = Vec::new();
    let with_ma = params.ma;
    let use_ema = params.ema;
    for (source, syms) in &source_map {
        let pool = state.pool.clone();
        let redis_client = state.redis_client.clone();
        let syms = syms.clone();
        let source = source.clone();
        let limit = effective_limit;
        let start_time = params.start_date.as_deref().and_then(fetch::parse_date);
        let end_time = params.end_date.as_deref().and_then(fetch::parse_date_end);

        match &interval {
            NormalizedInterval::Native(db_interval) => {
                let db_interval = db_interval.to_string();
                handles.push(tokio::spawn(async move {
                    let (data, tag, _meta) = fetch::fetch_native_tickers(
                        &pool, &redis_client, &source, syms,
                        &db_interval, start_time, end_time,
                        Some(limit), &[], true, with_ma, use_ema,
                    ).await;
                    (source, data, tag)
                }));
            }
            NormalizedInterval::Aggregated(agg) => {
                let agg = *agg;
                handles.push(tokio::spawn(async move {
                    let (data, tag, _meta) = fetch::fetch_aggregated_tickers(
                        &pool, &redis_client, &source, syms,
                        agg, start_time, end_time,
                        limit, &[], true, with_ma, use_ema,
                    ).await;
                    (source, data, tag)
                }));
            }
        }
    }

    // Merge results from all sources, collecting source tags
    let mut merged: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();
    let mut source_tags: std::collections::HashSet<&'static str> = std::collections::HashSet::new();
    for handle in handles {
        match handle.await {
            Ok((_source, mut source_data, tag)) => {
                if !source_data.is_empty() {
                    source_tags.insert(tag);
                    merged.append(&mut source_data);
                }
            }
            Err(e) => {
                tracing::warn!("Task failed in mode=all: {e}");
            }
        }
    }

    // Store in cache
    if params.cache {
        let mut guard = state.tickers_cache.write().await;
        guard.put(cache_key, &merged);
    }

    let all_redis = !source_tags.is_empty() && source_tags.iter().all(|&t| t == "redis");
    let any_redis = source_tags.contains("redis");
    let source_tag = if merged.is_empty() { "empty" } else if all_redis { "redis" } else if any_redis { "mixed" } else { "postgres" };

    let mut response = response::build_response(merged, params.legacy, params.mode, is_csv);
    response.headers_mut().insert(
        HeaderName::from_static("x-data-source"),
        HeaderValue::from_static(source_tag),
    );
    response
}

// ── /tickers/group ──

pub async fn tickers_group(Query(params): Query<GroupQuery>) -> Response {
    let result: Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> = match params.mode {
        Mode::Vn => data_loader::load_vn_groups(),
        Mode::Crypto => data_loader::load_crypto_groups(),
        Mode::Yahoo => data_loader::load_yahoo_groups(),
        Mode::All => data_loader::load_all_groups(),
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

// ── /tickers/name ──

pub async fn tickers_name(Query(params): Query<GroupQuery>) -> Response {
    let result: Result<BTreeMap<String, String>, Box<dyn std::error::Error + Send + Sync>> = match params.mode {
        Mode::Vn => data_loader::load_vn_names(),
        Mode::Crypto => data_loader::load_crypto_names(),
        Mode::Yahoo => data_loader::load_yahoo_names(),
        Mode::All => data_loader::load_all_names(),
    };

    match result {
        Ok(names) => (StatusCode::OK, Json(names)).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": e.to_string() })),
        )
            .into_response(),
    }
}

// ── /tickers/info ──

#[derive(serde::Deserialize)]
pub struct InfoQuery {
    pub ticker: Option<String>,
}

pub async fn tickers_info(Query(params): Query<InfoQuery>) -> Response {
    let result: Result<Vec<serde_json::Value>, Box<dyn std::error::Error + Send + Sync>> = data_loader::load_merged_info();

    match result {
        Ok(data) => {
            if let Some(ref ticker) = params.ticker {
                let found = data.iter().find(|item| {
                    item.get("ticker")
                        .and_then(|t| t.as_str())
                        .map(|t| t.eq_ignore_ascii_case(ticker))
                        .unwrap_or(false)
                });
                match found {
                    Some(entry) => (StatusCode::OK, Json(entry.clone())).into_response(),
                    None => (
                        StatusCode::NOT_FOUND,
                        Json(serde_json::json!({ "error": format!("Ticker '{}' not found", ticker) })),
                    )
                        .into_response(),
                }
            } else {
                (StatusCode::OK, Json(data)).into_response()
            }
        }
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
