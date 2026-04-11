use axum::extract::State;
use axum::http::{HeaderName, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Json, Response};
use axum_extra::extract::Query;
use chrono::{Datelike, NaiveDate, Timelike};
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use crate::server::types::{
    GroupQuery, Mode, NormalizedInterval, StockDataResponse,
    TickersQuery, is_vn_ticker,
};
use crate::services::ohlcv;

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
    let effective_limit = params.limit.unwrap_or_else(|| {
        if params.symbol.as_ref().map(|s| s.len()) == Some(1) {
            crate::constants::api::DEFAULT_LIMIT
        } else {
            1
        }
    });

    let is_csv = params.format.eq_ignore_ascii_case("csv");

    // Build cache key with symbols available so far:
    // - explicit symbols → use them directly
    // - no symbols → empty vec (build_cache_key converts to "__ALL__")
    let cache_key_symbols = params.symbol.as_deref().unwrap_or(&[]);
    let cache_key = build_cache_key(&params, &interval, cache_key_symbols, Some(effective_limit));

    // Try cache BEFORE any DB call
    if params.cache {
        let mut guard = state.tickers_cache.write().await;
        if let Some(cached) = guard.get(&cache_key) {
            let mut resp = build_response(cached, params.legacy, params.mode, is_csv);
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
                    // Redis has no data for this source — fall through to PG
                    pg_list_tickers(&state.pool, source, extra_sources).await
                }
            } else {
                // Redis unavailable — fall through to PG
                pg_list_tickers(&state.pool, source, extra_sources).await
            }
        }
    };
    let (result, source_tag, redis_meta) = match interval {
        NormalizedInterval::Native(db_interval) => {
            fetch_native_tickers(&state, symbols, db_interval, &params, Some(effective_limit), extra_sources).await
        }
        NormalizedInterval::Aggregated(agg) => {
            fetch_aggregated_tickers(&state, symbols, agg, &params, effective_limit, extra_sources).await
        }
    };

    // Store in cache
    if params.cache {
        let mut guard = state.tickers_cache.write().await;
        guard.put(cache_key, &result);
    }

    let mut response = build_response(result, params.legacy, params.mode, is_csv);
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

/// PG fallback for list_tickers_with_extra when Redis doesn't have data.
/// Returns a Vec of ticker strings, or an empty Vec on error/timeout.
async fn pg_list_tickers(
    pool: &sqlx::PgPool,
    source: &str,
    extra_sources: &[&str],
) -> Vec<String> {
    match tokio::time::timeout(
        std::time::Duration::from_secs(3),
        ohlcv::list_tickers_with_extra(pool, source, extra_sources),
    ).await {
        Ok(Ok(tickers)) => tickers.into_iter().map(|t| t.ticker).collect(),
        Ok(Err(e)) => {
            tracing::warn!("Failed to list tickers for {source}: {e}");
            Vec::new()
        }
        Err(_) => {
            tracing::warn!("Timeout listing tickers for {source}");
            Vec::new()
        }
    }
}

/// PG fallback for resolve_ticker_sources when Redis doesn't have enough data.
async fn drop_redis_resolve_pg(
    pool: &sqlx::PgPool,
    syms: &[String],
) -> HashMap<String, Vec<String>> {
    match tokio::time::timeout(
        std::time::Duration::from_secs(3),
        ohlcv::resolve_ticker_sources(pool, syms),
    ).await {
        Ok(Ok(map)) => {
            let mut grouped: HashMap<String, Vec<String>> = HashMap::new();
            for (sym, source) in &map {
                grouped.entry(source.clone()).or_default().push(sym.clone());
            }
            grouped
        }
        Ok(Err(e)) => {
            tracing::warn!("Failed to resolve ticker sources: {e}");
            HashMap::new()
        }
        Err(_) => {
            tracing::warn!("Timeout resolving ticker sources");
            HashMap::new()
        }
    }
}

/// PG fallback for list_all_tickers when Redis doesn't have data.
async fn drop_redis_list_all(pool: &sqlx::PgPool) -> HashMap<String, Vec<String>> {
    match tokio::time::timeout(
        std::time::Duration::from_secs(3),
        ohlcv::list_all_tickers(pool),
    ).await {
        Ok(Ok(tickers)) => {
            let mut grouped: HashMap<String, Vec<String>> = HashMap::new();
            for t in tickers {
                grouped.entry(t.source).or_default().push(t.ticker);
            }
            grouped
        }
        Ok(Err(e)) => {
            tracing::warn!("Failed to list all tickers: {e}");
            HashMap::new()
        }
        Err(_) => {
            tracing::warn!("Timeout listing all tickers");
            HashMap::new()
        }
    }
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
    let effective_limit = params.limit.unwrap_or_else(|| {
        if has_explicit_symbols && params.symbol.as_ref().map(|s| s.len()) == Some(1) {
            crate::constants::api::DEFAULT_LIMIT
        } else {
            1
        }
    });

    let is_csv = params.format.eq_ignore_ascii_case("csv");

    // Build cache key before any DB call
    let cache_key_symbols = params.symbol.as_deref().unwrap_or(&[]);
    let cache_key = build_cache_key(&params, &interval, cache_key_symbols, Some(effective_limit));

    // Check cache BEFORE any DB call
    if params.cache {
        let mut guard = state.tickers_cache.write().await;
        if let Some(cached) = guard.get(&cache_key) {
            return build_response(cached, params.legacy, params.mode, is_csv);
        }
        drop(guard);
    }

    // Cache miss — resolve symbols → sources
    // Try Redis first (fast, no PG dependency), fall back to PG on miss
    let source_map: HashMap<String, Vec<String>> = if let Some(ref syms) = params.symbol {
        // With explicit symbols: look them up in Redis cached ticker list first
        if let Some(redis_tickers) = super::redis_reader::read_ticker_list_from_redis(&state.redis_client).await {
            let mut grouped: HashMap<String, Vec<String>> = HashMap::new();
            let sym_set: std::collections::HashSet<&str> = syms.iter().map(|s| s.as_str()).collect();
            for t in redis_tickers {
                if sym_set.contains(t.ticker.as_str()) {
                    grouped.entry(t.source).or_default().push(t.ticker);
                }
            }
            if grouped.keys().count() == syms.len() {
                // All symbols resolved from Redis
                tracing::debug!("Resolved {} symbols from Redis ticker list", syms.len());
                grouped
            } else {
                // Some symbols not found — fall back to PG
                drop_redis_resolve_pg(&state.pool, syms).await
            }
        } else {
            // Redis unavailable — fall back to PG
            drop_redis_resolve_pg(&state.pool, syms).await
        }
    } else {
        // No symbols → try Redis cached ticker list first
        if let Some(redis_tickers) = super::redis_reader::read_ticker_list_from_redis(&state.redis_client).await {
            if !redis_tickers.is_empty() {
                let ticker_count = redis_tickers.len();
                let mut grouped: HashMap<String, Vec<String>> = HashMap::new();
                for t in redis_tickers {
                    grouped.entry(t.source).or_default().push(t.ticker);
                }
                tracing::debug!("Using Redis ticker list: {ticker_count} tickers across {} sources", grouped.len());
                grouped
            } else {
                // Redis empty — fall back to PG
                drop_redis_list_all(&state.pool).await
            }
        } else {
            // Redis unavailable — fall back to PG
            drop_redis_list_all(&state.pool).await
        }
    };

    // 2. Fetch per source in parallel
    let (native_interval_str, agg_interval) = match &interval {
        NormalizedInterval::Native(s) => (Some(s.to_string()), None),
        NormalizedInterval::Aggregated(a) => (None, Some(*a)),
    };
    let base_interval_str = agg_interval.map(|a| a.base_interval().as_str().to_string());
    let lookback = effective_limit + crate::constants::api::AGGREGATED_LOOKBACK;
    let is_daily_native = native_interval_str.as_deref() == Some("1D");

    let mut handles = Vec::new();
    for (source, syms) in &source_map {
        let pool = state.pool.clone();
        let redis_client = state.redis_client.clone();
        let syms = syms.clone();
        let source = source.clone();
        let limit = effective_limit;
        let start_time = params.start_date.as_deref().and_then(parse_date);
        let end_time = params.end_date.as_deref().and_then(parse_date_end);

        if let Some(ref db_interval) = native_interval_str {
            let db_interval = db_interval.clone();
            let is_daily = is_daily_native;
            handles.push(tokio::spawn(async move {
                // Redis first when no date range
                if start_time.is_none() && end_time.is_none() && redis_client.is_some() {
                    let effective_limit = limit;
                    let total_limit = effective_limit + crate::constants::api::SMA_MAX_PERIOD;
                    if let Some(redis_map) = super::redis_reader::batch_read_ohlcv_from_redis(
                        &redis_client, &source, &syms, &db_interval, total_limit,
                    ).await {
                        let mut result: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();
                        for (ticker, redis_result) in redis_map {
                            let enhanced = crate::queries::ohlcv::enhance_rows(
                                &ticker, redis_result.rows, Some(effective_limit), None,
                            );
                            if !enhanced.is_empty() {
                                let mut mapped: Vec<StockDataResponse> = enhanced
                                    .into_iter()
                                    .map(|r| {
                                        let time_str = if is_daily {
                                            r.time.format("%Y-%m-%d").to_string()
                                        } else {
                                            r.time.format("%Y-%m-%dT%H:%M:%S").to_string()
                                        };
                                        StockDataResponse {
                                            time: time_str,
                                            open: r.open, high: r.high, low: r.low, close: r.close,
                                            volume: r.volume as u64,
                                            symbol: r.ticker,
                                            ma10: r.ma10, ma20: r.ma20, ma50: r.ma50,
                                            ma100: r.ma100, ma200: r.ma200,
                                            ma10_score: r.ma10_score, ma20_score: r.ma20_score,
                                            ma50_score: r.ma50_score, ma100_score: r.ma100_score,
                                            ma200_score: r.ma200_score,
                                            close_changed: r.close_changed,
                                            volume_changed: r.volume_changed,
                                            total_money_changed: r.total_money_changed,
                                        }
                                    })
                                    .collect();
                                mapped.reverse();
                                result.insert(ticker, mapped);
                            }
                        }
                        if !result.is_empty() {
                            result.retain(|_, v| !v.is_empty());
                            return (source, result);
                        }
                    }
                }

                // Fallback to PG
                let batch_map = ohlcv::get_ohlcv_joined_batch(
                    &pool, &source, &syms, &db_interval,
                    Some(limit), start_time, end_time,
                ).await.unwrap_or_default();
                let mut result: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();
                for (ticker, rows) in batch_map {
                    let mapped: Vec<StockDataResponse> = rows
                        .into_iter()
                        .map(|r| {
                            let time_str = if is_daily {
                                r.time.format("%Y-%m-%d").to_string()
                            } else {
                                r.time.format("%Y-%m-%dT%H:%M:%S").to_string()
                            };
                            StockDataResponse {
                                time: time_str,
                                open: r.open, high: r.high, low: r.low, close: r.close,
                                volume: r.volume as u64,
                                symbol: r.ticker,
                                ma10: r.ma10, ma20: r.ma20, ma50: r.ma50,
                                ma100: r.ma100, ma200: r.ma200,
                                ma10_score: r.ma10_score, ma20_score: r.ma20_score,
                                ma50_score: r.ma50_score, ma100_score: r.ma100_score,
                                ma200_score: r.ma200_score,
                                close_changed: r.close_changed,
                                volume_changed: r.volume_changed,
                                total_money_changed: r.total_money_changed,
                            }
                        })
                        .collect();
                    result.insert(ticker, mapped);
                }
                result.retain(|_, v| !v.is_empty());
                (source, result)
            }));
        } else if let Some(agg) = agg_interval {
            let base_interval = base_interval_str.clone().unwrap();
            let lb = lookback;
            handles.push(tokio::spawn(async move {
                use crate::services::aggregator::{AggregatedOhlcv, Aggregator};
                let hourly_offset: i64 = if source == "vn" { 2 } else { 0 };
                let is_daily = base_interval == "1D";

                // Redis first when no date range
                let mut result: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();
                if start_time.is_none() && end_time.is_none() && redis_client.is_some() {
                    if let Some(redis_map) = super::redis_reader::batch_read_ohlcv_from_redis(
                        &redis_client, &source, &syms, &base_interval, lb,
                    ).await {
                        let mut per_ticker: HashMap<String, Vec<AggregatedOhlcv>> = HashMap::new();
                        for (ticker, redis_result) in redis_map {
                            let aggregated = match agg.base_interval() {
                                crate::models::interval::Interval::Daily => {
                                    Aggregator::aggregate_daily_data(&ticker, redis_result.rows, agg)
                                }
                                crate::models::interval::Interval::Hourly => {
                                    Aggregator::aggregate_hourly_data(&ticker, redis_result.rows, agg, hourly_offset)
                                }
                                _ => Aggregator::aggregate_minute_data(&ticker, redis_result.rows, agg),
                            };
                            per_ticker.insert(ticker, aggregated);
                        }
                        let enhanced = Aggregator::enhance_aggregated_data(per_ticker);
                        for (ticker, data) in &enhanced {
                            let len = data.len();
                            let start = if len > limit as usize { len - limit as usize } else { 0 };
                            let trimmed: Vec<StockDataResponse> = data[start..]
                                .iter()
                                .map(|d| {
                                    let time_str = if is_daily {
                                        d.time.format("%Y-%m-%d").to_string()
                                    } else {
                                        d.time.format("%Y-%m-%dT%H:%M:%S").to_string()
                                    };
                                    StockDataResponse {
                                        time: time_str,
                                        open: d.open, high: d.high, low: d.low, close: d.close,
                                        volume: d.volume as u64,
                                        symbol: d.ticker.clone(),
                                        ma10: d.ma10, ma20: d.ma20, ma50: d.ma50,
                                        ma100: d.ma100, ma200: d.ma200,
                                        ma10_score: d.ma10_score, ma20_score: d.ma20_score,
                                        ma50_score: d.ma50_score, ma100_score: d.ma100_score,
                                        ma200_score: d.ma200_score,
                                        close_changed: d.close_changed,
                                        volume_changed: d.volume_changed,
                                        total_money_changed: d.total_money_changed,
                                    }
                                })
                                .collect();
                            result.insert(ticker.clone(), trimmed);
                        }
                        result.retain(|_, v| !v.is_empty());
                    }
                }

                // Fallback to PG if Redis had no data
                if result.is_empty() {
                    let raw_map = ohlcv::get_ohlcv_batch_raw(
                        &pool, &source, &syms, &base_interval,
                        Some(lb), start_time, end_time,
                    ).await.unwrap_or_default();

                    let mut per_ticker: HashMap<String, Vec<AggregatedOhlcv>> = HashMap::new();
                    for (ticker, rows) in raw_map {
                        let aggregated = match base_interval.as_str() {
                            "1D" => Aggregator::aggregate_daily_data(&ticker, rows, agg),
                            "1h" => Aggregator::aggregate_hourly_data(&ticker, rows, agg, hourly_offset),
                            _ => Aggregator::aggregate_minute_data(&ticker, rows, agg),
                        };
                        per_ticker.insert(ticker, aggregated);
                    }
                    let enhanced = Aggregator::enhance_aggregated_data(per_ticker);

                    for (ticker, data) in enhanced {
                        let len = data.len();
                        let start = if len > limit as usize { len - limit as usize } else { 0 };
                        let trimmed: Vec<StockDataResponse> = data[start..]
                            .iter()
                            .map(|d| {
                                let time_str = if is_daily {
                                    d.time.format("%Y-%m-%d").to_string()
                                } else {
                                    d.time.format("%Y-%m-%dT%H:%M:%S").to_string()
                                };
                                StockDataResponse {
                                    time: time_str,
                                    open: d.open, high: d.high, low: d.low, close: d.close,
                                    volume: d.volume as u64,
                                    symbol: d.ticker.clone(),
                                    ma10: d.ma10, ma20: d.ma20, ma50: d.ma50,
                                    ma100: d.ma100, ma200: d.ma200,
                                    ma10_score: d.ma10_score, ma20_score: d.ma20_score,
                                    ma50_score: d.ma50_score, ma100_score: d.ma100_score,
                                    ma200_score: d.ma200_score,
                                    close_changed: d.close_changed,
                                    volume_changed: d.volume_changed,
                                    total_money_changed: d.total_money_changed,
                                }
                            })
                            .collect();
                        result.insert(ticker, trimmed);
                    }
                    result.retain(|_, v| !v.is_empty());
                }

                (source, result)
            }));
        }
    }

    // 3. Merge results from all sources
    let mut merged: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();
    for handle in handles {
        match handle.await {
            Ok((_source, mut source_data)) => {
                // For native intervals, reverse each ticker's rows to oldest-first
                if matches!(interval, NormalizedInterval::Native(_)) {
                    for rows in source_data.values_mut() {
                        rows.reverse();
                    }
                }
                merged.append(&mut source_data);
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

    build_response(merged, params.legacy, params.mode, is_csv)
}

/// Build a cache key from the query parameters (excludes view-layer params).
fn build_cache_key(
    params: &TickersQuery,
    interval: &NormalizedInterval,
    symbols: &[String],
    effective_limit: Option<i64>,
) -> String {
    let source = params.mode.source_label();
    let interval_str = match interval {
        NormalizedInterval::Native(s) => *s,
        NormalizedInterval::Aggregated(a) => a.to_str(),
    };

    let sorted_symbols = {
        let mut syms: Vec<&str> = symbols.iter().map(|s| s.as_str()).collect();
        syms.sort();
        if syms.is_empty() {
            "__ALL__".to_string()
        } else {
            syms.join(",")
        }
    };

    let limit = effective_limit.map(|l| l.to_string()).unwrap_or_default();
    let start = params.start_date.as_deref().unwrap_or("");
    let end = params.end_date.as_deref().unwrap_or("");

    format!("{source}|{interval_str}|{sorted_symbols}|{limit}|{start}|{end}")
}

/// Native interval: query DB directly. Returns full VND prices (no legacy scaling).
/// Returns (data, source_tag, redis_meta).
async fn fetch_native_tickers(
    state: &Arc<AppState>,
    symbols: Vec<String>,
    interval: &str,
    params: &TickersQuery,
    limit: Option<i64>,
    extra_sources: &[&str],
) -> (BTreeMap<String, Vec<StockDataResponse>>, &'static str, Option<super::redis_reader::RedisReadResult>) {
    let start_time = params.start_date.as_deref().and_then(parse_date);
    let end_time = params.end_date.as_deref().and_then(parse_date_end);
    let source = params.mode.source_label();
    let is_daily = interval == "1D";

    // Redis shortcut: native interval, no date range, no extra sources
    if params.redis && !symbols.is_empty() && start_time.is_none() && end_time.is_none() && extra_sources.is_empty() {
        if state.redis_client.is_some() {
            let effective_limit = limit.unwrap_or(crate::constants::api::DEFAULT_LIMIT);
            let total_limit = effective_limit + crate::constants::api::SMA_MAX_PERIOD;

            if let Some(redis_map) = super::redis_reader::batch_read_ohlcv_from_redis(
                &state.redis_client, source, &symbols, interval, total_limit,
            ).await {
                let mut result = BTreeMap::new();
                let mut first_meta: Option<super::redis_reader::RedisReadResult> = None;
                for (ticker, redis_result) in redis_map {
                    let meta = super::redis_reader::RedisReadResult {
                        raw_close_count: redis_result.raw_close_count,
                        aligned_count: redis_result.aligned_count,
                        requested_limit: redis_result.requested_limit,
                        base_interval: redis_result.base_interval.clone(),
                        rows: Vec::new(),
                    };
                    if first_meta.is_none() {
                        first_meta = Some(meta);
                    }
                    let enhanced = crate::queries::ohlcv::enhance_rows(
                        &ticker, redis_result.rows, limit, None,
                    );
                    if !enhanced.is_empty() {
                        let mut mapped: Vec<StockDataResponse> = enhanced
                            .into_iter()
                            .map(|r| map_ohlcv_to_response(r, is_daily, params.mode))
                            .collect();
                        // Redis returns newest-first, API contract is oldest-first
                        mapped.reverse();
                        result.insert(ticker, mapped);
                    }
                }
                if !result.is_empty() {
                    return (result, "redis", first_meta);
                }
            }
        }
    }

    // Fall through to PostgreSQL
    // Use batch query — single SQL query for all tickers instead of N sequential queries.
    let batch_map = match tokio::time::timeout(
        std::time::Duration::from_secs(5),
        ohlcv::get_ohlcv_joined_batch_with_extra(
            &state.pool,
            source,
            &symbols,
            interval,
            limit,
            start_time,
            end_time,
            extra_sources,
        ),
    )
    .await
    {
        Ok(Ok(m)) => m,
        Ok(Err(e)) => {
            tracing::warn!("Failed to batch-fetch tickers ({interval}): {e}");
            return (BTreeMap::new(), "postgres", None);
        }
        Err(_) => {
            tracing::warn!("Timeout batch-fetching tickers ({interval})");
            return (BTreeMap::new(), "postgres", None);
        }
    };

    let mut result: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();

    for (ticker, rows) in batch_map {
        let mut mapped: Vec<StockDataResponse> = rows
            .into_iter()
            .map(|r| map_ohlcv_to_response(r, is_daily, params.mode))
            .collect();

        // DB returns newest first (DESC index scan), but API contract is oldest first
        mapped.reverse();

        result.insert(ticker, mapped);
    }

    // Remove tickers with no data (matches production behavior)
    result.retain(|_, v| !v.is_empty());
    (result, "postgres", None)
}

/// Aggregated interval: fetch source data, aggregate, enhance, trim.
/// Returns full VND prices (no legacy scaling).
/// Returns (data, source_tag, redis_meta).
async fn fetch_aggregated_tickers(
    state: &Arc<AppState>,
    symbols: Vec<String>,
    agg: crate::models::aggregated_interval::AggregatedInterval,
    params: &TickersQuery,
    limit: i64,
    extra_sources: &[&str],
) -> (BTreeMap<String, Vec<StockDataResponse>>, &'static str, Option<super::redis_reader::RedisReadResult>) {
    use crate::services::aggregator::{AggregatedOhlcv, Aggregator};

    let base_interval = agg.base_interval().as_str();
    let source = params.mode.source_label();

    // Fetch source data with lookback buffer for MA200
    let lookback = limit + crate::constants::api::AGGREGATED_LOOKBACK;
    let start_time = params.start_date.as_deref().and_then(parse_date);
    let end_time = params.end_date.as_deref().and_then(parse_date_end);

    let is_daily = base_interval == "1D";

    // Hourly offset: VN stocks align to market open (09:00 ICT = 02:00 UTC),
    // crypto aligns to midnight UTC.
    let hourly_offset: i64 = if params.mode == Mode::Vn { 2 } else { 0 };

    // Redis shortcut: aggregated interval, no date range, no extra sources
    if params.redis && !symbols.is_empty() && start_time.is_none() && end_time.is_none() && extra_sources.is_empty() {
        if state.redis_client.is_some() {
            // Use same lookback as PG path (limit + AGGREGATED_LOOKBACK) to ensure
            // identical row counts. SMA extra is handled post-aggregation, not during base read.
            if let Some(redis_map) = super::redis_reader::batch_read_ohlcv_from_redis(
                &state.redis_client, source, &symbols, base_interval,
                lookback,
            ).await {
                let mut per_ticker: HashMap<String, Vec<AggregatedOhlcv>> = HashMap::new();
                let mut first_meta: Option<super::redis_reader::RedisReadResult> = None;

                for (ticker, redis_result) in redis_map {
                    let meta = super::redis_reader::RedisReadResult {
                        raw_close_count: redis_result.raw_close_count,
                        aligned_count: redis_result.aligned_count,
                        requested_limit: redis_result.requested_limit,
                        base_interval: redis_result.base_interval.clone(),
                        rows: Vec::new(),
                    };
                    if first_meta.is_none() {
                        first_meta = Some(meta);
                    }

                    let aggregated = match agg.base_interval() {
                        crate::models::interval::Interval::Daily => {
                            Aggregator::aggregate_daily_data(&ticker, redis_result.rows, agg)
                        }
                        crate::models::interval::Interval::Hourly => {
                            Aggregator::aggregate_hourly_data(&ticker, redis_result.rows, agg, hourly_offset)
                        }
                        _ => Aggregator::aggregate_minute_data(&ticker, redis_result.rows, agg),
                    };
                    per_ticker.insert(ticker, aggregated);
                }

                let enhanced = Aggregator::enhance_aggregated_data(per_ticker);
                let mut result = BTreeMap::new();

                for (ticker, data) in &enhanced {
                    let len = data.len();
                    let start = if len > limit as usize { len - limit as usize } else { 0 };
                    let trimmed: Vec<StockDataResponse> = data[start..]
                        .iter()
                        .map(|d| map_aggregated_to_response(d, is_daily, params.mode))
                        .collect();
                    result.insert(ticker.clone(), trimmed);
                }

                if !result.is_empty() {
                    return (result, "redis", first_meta);
                }
            }
        }
    }

    // Fall through to PostgreSQL
    // Batch-fetch raw OHLCV rows for all target tickers in a single query
    let raw_map = match tokio::time::timeout(
        std::time::Duration::from_secs(5),
        ohlcv::get_ohlcv_batch_raw_with_extra(
            &state.pool,
            source,
            &symbols,
            base_interval,
            Some(lookback),
            start_time,
            end_time,
            extra_sources,
        ),
    )
    .await
    {
        Ok(Ok(m)) => m,
        Ok(Err(e)) => {
            tracing::warn!("Failed to batch-fetch for aggregation ({base_interval}): {e}");
            return (BTreeMap::new(), "postgres", None);
        }
        Err(_) => {
            tracing::warn!("Timeout batch-fetching for aggregation ({base_interval})");
            return (BTreeMap::new(), "postgres", None);
        }
    };

    let mut per_ticker: HashMap<String, Vec<AggregatedOhlcv>> = HashMap::new();

    for (ticker, rows) in raw_map {
        let aggregated = match agg.base_interval() {
            crate::models::interval::Interval::Daily => {
                Aggregator::aggregate_daily_data(&ticker, rows, agg)
            }
            crate::models::interval::Interval::Hourly => {
                Aggregator::aggregate_hourly_data(&ticker, rows, agg, hourly_offset)
            }
            _ => Aggregator::aggregate_minute_data(&ticker, rows, agg),
        };
        per_ticker.insert(ticker, aggregated);
    }

    // Enhance with indicators
    let enhanced = Aggregator::enhance_aggregated_data(per_ticker);

    // Trim to requested limit and map to response
    let mut result: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();

    for (ticker, data) in enhanced {
        let len = data.len();
        let start = if len > limit as usize { len - limit as usize } else { 0 };
        let trimmed: Vec<StockDataResponse> = data[start..]
            .iter()
            .map(|d| map_aggregated_to_response(d, is_daily, params.mode))
            .collect();

        result.insert(ticker, trimmed);
    }

    // Remove tickers with no data (matches production behavior)
    result.retain(|_, v| !v.is_empty());
    (result, "postgres", None)
}

/// Apply legacy price scaling and format the response.
fn build_response(
    mut data: BTreeMap<String, Vec<StockDataResponse>>,
    legacy: bool,
    mode: Mode,
    is_csv: bool,
) -> Response {
    if legacy {
        let divisor = crate::constants::api::LEGACY_DIVISOR;
        for rows in data.values_mut() {
            for row in rows {
                let apply = if mode == Mode::Vn {
                    !crate::server::types::is_index_ticker(&row.symbol)
                } else if mode == Mode::All && is_vn_ticker(&row.symbol) {
                    !crate::server::types::is_index_ticker(&row.symbol)
                } else {
                    false
                };
                if apply {
                    row.open /= divisor;
                    row.high /= divisor;
                    row.low /= divisor;
                    row.close /= divisor;
                }
            }
        }
    }

    if is_csv {
        csv_response(&data)
    } else {
        (StatusCode::OK, Json(data)).into_response()
    }
}

// ── /tickers/group ──

pub async fn tickers_group(Query(params): Query<GroupQuery>) -> Response {
    let result: Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error>> = match params.mode {
        Mode::Vn => load_vn_groups(),
        Mode::Crypto => load_crypto_groups(),
        Mode::Yahoo => load_yahoo_groups(),
        Mode::All => load_all_groups(),
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
    let result: Result<BTreeMap<String, String>, Box<dyn std::error::Error>> = match params.mode {
        Mode::Vn => load_vn_names(),
        Mode::Crypto => load_crypto_names(),
        Mode::Yahoo => load_yahoo_names(),
        Mode::All => load_all_names(),
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
    let result: Result<Vec<serde_json::Value>, Box<dyn std::error::Error>> = load_merged_info();

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

/// Load vn.csv rows as a HashMap keyed by symbol (uppercase).
fn load_vn_csv() -> Result<HashMap<String, serde_json::Value>, Box<dyn std::error::Error>> {
    let path = resolve_data_file("vn.csv")?;
    let content = std::fs::read_to_string(&path)?;
    let mut map = HashMap::new();

    let mut rdr = csv::Reader::from_reader(content.as_bytes());
    for result in rdr.records() {
        let record = result?;
        let symbol = record.get(0).unwrap_or("").trim();
        if symbol.is_empty() {
            continue;
        }
        let organ_name = record.get(1).unwrap_or("").trim();
        let en_organ_name = record.get(2).unwrap_or("").trim();
        let exchange = record.get(3).unwrap_or("").trim();
        let stock_type = record.get(4).unwrap_or("").trim();
        let val = serde_json::json!({
            "ticker": symbol,
            "organ_name": organ_name,
            "en_organ_name": en_organ_name,
            "exchange": exchange,
            "type": stock_type,
        });
        map.insert(symbol.to_uppercase(), val);
    }

    Ok(map)
}

/// Load company_info.json array.
fn load_company_info() -> Result<Vec<serde_json::Value>, Box<dyn std::error::Error>> {
    let path = resolve_data_file("company_info.json")?;
    let content = std::fs::read_to_string(&path)?;
    let data: Vec<serde_json::Value> = serde_json::from_str(&content)?;
    Ok(data)
}

/// Merge vn.csv baseline with company_info.json details.
/// Only tickers present in both vn.csv and company_info.json are included.
/// vn.csv provides name/exchange; company_info.json adds profile + financial_ratios.
fn load_merged_info() -> Result<Vec<serde_json::Value>, Box<dyn std::error::Error>> {
    let vn_map = load_vn_csv()
        .map_err(|e| {
            tracing::warn!("vn.csv not available: {e}");
        })
        .unwrap_or_default();

    let company_entries = load_company_info()
        .map_err(|e| {
            tracing::warn!("company_info.json not available: {e}");
        })
        .unwrap_or_default();

    let mut result = Vec::new();

    for entry in &company_entries {
        let ticker = entry
            .get("ticker")
            .and_then(|t| t.as_str())
            .unwrap_or("")
            .to_uppercase();

        let mut merged = if let Some(base) = vn_map.get(&ticker) {
            base.clone()
        } else {
            // Not in vn.csv — skip this entry
            continue;
        };

        // Merge company_info fields into base (company fields take precedence)
        if let Some(obj) = entry.as_object() {
            if let Some(merged_obj) = merged.as_object_mut() {
                for (key, val) in obj {
                    merged_obj.insert(key.clone(), val.clone());
                }
            }
        }

        result.push(merged);
    }

    result.sort_by(|a, b| {
        a.get("ticker")
            .and_then(|t| t.as_str())
            .unwrap_or("")
            .cmp(b.get("ticker").and_then(|t| t.as_str()).unwrap_or(""))
    });
    Ok(result)
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
    _mode: Mode,
) -> StockDataResponse {
    let time_str = if is_daily {
        row.time.format("%Y-%m-%d").to_string()
    } else {
        row.time.format("%Y-%m-%dT%H:%M:%S").to_string()
    };

    StockDataResponse {
        time: time_str,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
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
    _mode: Mode,
) -> StockDataResponse {
    let time_str = if is_daily {
        row.time.format("%Y-%m-%d").to_string()
    } else {
        row.time.format("%Y-%m-%dT%H:%M:%S").to_string()
    };

    StockDataResponse {
        time: time_str,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
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

fn load_yahoo_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error>> {
    let mut map = load_groups_from_source("global")?;

    // Merge additional sources (e.g. SJC)
    for source in crate::constants::MERGE_WITH_YAHOO {
        let extra = load_groups_from_source(source)?;
        for (category, symbols) in extra {
            map.entry(category).or_insert_with(Vec::new).extend(symbols);
        }
    }

    Ok(map)
}

fn load_groups_from_source(source: &str) -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error>> {
    let filename = format!("{source}_tickers.json");
    let path = resolve_data_file(&filename)?;
    let content = std::fs::read_to_string(&path)?;

    let raw: serde_json::Value = serde_json::from_str(&content)?;

    let mut map = BTreeMap::new();
    if let Some(data) = raw["data"].as_array() {
        for item in data {
            let (symbol, category) = match (item["symbol"].as_str(), item["category"].as_str()) {
                (Some(s), Some(c)) => (s.to_string(), c.to_string()),
                (Some(s), None) => (s.to_string(), "Other".to_string()),
                _ => continue,
            };
            map.entry(category).or_insert_with(Vec::new).push(symbol);
        }
    }
    Ok(map)
}

// ── Name loaders ──

fn load_vn_names() -> Result<BTreeMap<String, String>, Box<dyn std::error::Error>> {
    // Load valid tickers from ticker_group.json
    let path = resolve_data_file("ticker_group.json")?;
    let content = std::fs::read_to_string(&path)?;
    let groups: BTreeMap<String, Vec<String>> = serde_json::from_str(&content)?;
    let valid_tickers: std::collections::HashSet<String> = groups
        .values()
        .flat_map(|v| v.iter().cloned())
        .collect();

    // Load names from vn.csv, only keeping valid tickers
    let vn_map = load_vn_csv()?;
    Ok(vn_map
        .into_iter()
        .filter(|(ticker, _)| valid_tickers.contains(ticker))
        .filter_map(|(ticker, val)| {
            val.get("organ_name")
                .and_then(|v| v.as_str())
                .map(|name| (ticker, name.to_string()))
        })
        .collect())
}

fn load_names_from_file(filename: &str) -> Result<BTreeMap<String, String>, Box<dyn std::error::Error>> {
    let path = resolve_data_file(filename)?;
    let content = std::fs::read_to_string(&path)?;
    let raw: serde_json::Value = serde_json::from_str(&content)?;

    let mut names = BTreeMap::new();
    if let Some(data) = raw["data"].as_array() {
        for item in data {
            if let (Some(symbol), Some(name)) = (item["symbol"].as_str(), item["name"].as_str()) {
                names.insert(symbol.to_string(), name.to_string());
            }
        }
    }
    Ok(names)
}

fn load_crypto_names() -> Result<BTreeMap<String, String>, Box<dyn std::error::Error>> {
    load_names_from_file("binance_tickers.json")
}

fn load_yahoo_names() -> Result<BTreeMap<String, String>, Box<dyn std::error::Error>> {
    let mut names = load_names_from_file("global_tickers.json")?;

    // Merge additional sources (e.g. SJC)
    for source in crate::constants::MERGE_WITH_YAHOO {
        let extra = load_names_from_file(&format!("{source}_tickers.json"))?;
        for (symbol, name) in extra {
            names.entry(symbol).or_insert(name);
        }
    }

    Ok(names)
}

/// Merge groups from all sources (vn > yahoo > crypto priority on key conflicts).
fn load_all_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error>> {
    type LoadFn = fn() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error>>;
    let load_fns: [LoadFn; 3] = [load_vn_groups, load_yahoo_groups, load_crypto_groups];
    let mut merged = BTreeMap::new();
    for load_fn in load_fns {
        let groups = load_fn()?;
        for (k, v) in groups {
            merged.entry(k).or_insert(v);
        }
    }
    Ok(merged)
}

/// Merge names from all sources (vn > yahoo > crypto priority on symbol conflicts).
fn load_all_names() -> Result<BTreeMap<String, String>, Box<dyn std::error::Error>> {
    type LoadFn = fn() -> Result<BTreeMap<String, String>, Box<dyn std::error::Error>>;
    let load_fns: [LoadFn; 3] = [load_vn_names, load_yahoo_names, load_crypto_names];
    let mut merged = BTreeMap::new();
    for load_fn in load_fns {
        let names = load_fn()?;
        for (k, v) in names {
            merged.entry(k).or_insert(v);
        }
    }
    Ok(merged)
}
