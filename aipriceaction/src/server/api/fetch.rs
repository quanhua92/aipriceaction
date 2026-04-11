use chrono::NaiveDate;
use sqlx::PgPool;
use std::collections::{BTreeMap, HashMap};

use crate::server::redis_reader;
use crate::server::types::{Mode, NormalizedInterval, StockDataResponse, TickersQuery};
use crate::services::ohlcv;

/// PG fallback for list_tickers_with_extra when Redis doesn't have data.
/// Returns a Vec of ticker strings, or an empty Vec on error/timeout.
pub(crate) async fn pg_list_tickers(
    pool: &PgPool,
    source: &str,
    extra_sources: &[&str],
) -> Vec<String> {
    match tokio::time::timeout(
        std::time::Duration::from_secs(3),
        ohlcv::list_tickers_with_extra(pool, source, extra_sources),
    )
    .await
    {
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
pub(crate) async fn drop_redis_resolve_pg(
    pool: &PgPool,
    syms: &[String],
) -> HashMap<String, Vec<String>> {
    match tokio::time::timeout(
        std::time::Duration::from_secs(3),
        ohlcv::resolve_ticker_sources(pool, syms),
    )
    .await
    {
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
pub(crate) async fn drop_redis_list_all(pool: &PgPool) -> HashMap<String, Vec<String>> {
    match tokio::time::timeout(
        std::time::Duration::from_secs(3),
        ohlcv::list_all_tickers(pool),
    )
    .await
    {
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

/// Resolve symbols to their source groups using Redis (fast) or PG fallback.
/// Returns a map of source → Vec<symbol>.
pub(crate) async fn resolve_source_map(
    redis_client: &Option<crate::redis::RedisClient>,
    pool: &PgPool,
    symbols: Option<&[String]>,
) -> HashMap<String, Vec<String>> {
    if let Some(syms) = symbols {
        // With explicit symbols: look them up in Redis cached ticker list first
        if let Some(redis_tickers) = redis_reader::read_ticker_list_from_redis(redis_client).await {
            let mut grouped: HashMap<String, Vec<String>> = HashMap::new();
            let sym_set: std::collections::HashSet<&str> = syms.iter().map(|s| s.as_str()).collect();
            for t in redis_tickers {
                if sym_set.contains(t.ticker.as_str()) {
                    grouped.entry(t.source).or_default().push(t.ticker);
                }
            }
            if grouped.keys().count() == syms.len() {
                tracing::debug!("Resolved {} symbols from Redis ticker list", syms.len());
                grouped
            } else {
                drop_redis_resolve_pg(pool, syms).await
            }
        } else {
            drop_redis_resolve_pg(pool, syms).await
        }
    } else {
        // No symbols → try Redis cached ticker list first
        if let Some(redis_tickers) = redis_reader::read_ticker_list_from_redis(redis_client).await {
            if !redis_tickers.is_empty() {
                let ticker_count = redis_tickers.len();
                let mut grouped: HashMap<String, Vec<String>> = HashMap::new();
                for t in redis_tickers {
                    grouped.entry(t.source).or_default().push(t.ticker);
                }
                tracing::debug!(
                    "Using Redis ticker list: {ticker_count} tickers across {} sources",
                    grouped.len()
                );
                grouped
            } else {
                drop_redis_list_all(pool).await
            }
        } else {
            drop_redis_list_all(pool).await
        }
    }
}

/// Build a cache key from the query parameters (excludes view-layer params).
pub(crate) fn build_cache_key(
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

/// Parse a date string as start-of-day UTC.
pub(crate) fn parse_date(s: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    NaiveDate::parse_from_str(s, "%Y-%m-%d")
        .ok()
        .and_then(|d| d.and_hms_opt(0, 0, 0))
        .map(|dt| dt.and_utc())
}

/// Parse a date string as end-of-day UTC.
pub(crate) fn parse_date_end(s: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    NaiveDate::parse_from_str(s, "%Y-%m-%d")
        .ok()
        .and_then(|d| d.and_hms_opt(23, 59, 59))
        .map(|dt| dt.and_utc())
}

/// Native interval: query Redis then PG directly.
/// Returns (data, source_tag, redis_meta).
///
/// When called from `handle_mode_all`, pass `use_redis=true` to enable the
/// Redis-first path (no `params.redis` check). The single-mode `tickers` handler
/// passes `use_redis=params.redis` to respect the user's redis flag.
pub(crate) async fn fetch_native_tickers(
    pool: &PgPool,
    redis_client: &Option<crate::redis::RedisClient>,
    source: &str,
    symbols: Vec<String>,
    interval: &str,
    start_time: Option<chrono::DateTime<chrono::Utc>>,
    end_time: Option<chrono::DateTime<chrono::Utc>>,
    limit: Option<i64>,
    extra_sources: &[&str],
    use_redis: bool,
) -> (BTreeMap<String, Vec<StockDataResponse>>, &'static str, Option<redis_reader::RedisReadResult>) {
    let is_daily = interval == "1D";

    // Redis shortcut: native interval, no extra sources
    // When a date range is given, check if Redis has data covering start_time
    let redis_allowed = use_redis
        && !symbols.is_empty()
        && extra_sources.is_empty()
        && redis_client.is_some();

    if redis_allowed {
        let start_ok = if start_time.is_some() {
            redis_reader::redis_covers_range(
                redis_client, source, &symbols[0], interval, start_time.unwrap(),
            )
            .await
        } else {
            true
        };

        if start_ok {
            let effective_limit = limit.unwrap_or(crate::constants::api::DEFAULT_LIMIT);
            // When a date range is given, fetch all ZSET rows so the range
            // can be in the middle of history (not just at the tail).
            let total_limit = if start_time.is_some() || end_time.is_some() {
                (crate::workers::redis_worker::max_size(interval) as i64)
                    + crate::constants::api::SMA_MAX_PERIOD
            } else {
                effective_limit + crate::constants::api::SMA_MAX_PERIOD
            };

            if let Some(redis_map) = redis_reader::batch_read_ohlcv_from_redis(
                redis_client, source, &symbols, interval, total_limit,
            ).await {
                let mut result = BTreeMap::new();
                let mut first_meta: Option<redis_reader::RedisReadResult> = None;
                for (ticker, redis_result) in redis_map {
                    let meta = redis_reader::RedisReadResult {
                        raw_close_count: redis_result.raw_close_count,
                        aligned_count: redis_result.aligned_count,
                        requested_limit: redis_result.requested_limit,
                        base_interval: redis_result.base_interval.clone(),
                        rows: Vec::new(),
                    };
                    if first_meta.is_none() {
                        first_meta = Some(meta);
                    }
                    // When a date range is given, don't let enhance_rows truncate
                    // to limit (it would keep the N newest rows, potentially
                    // outside the requested range). Pass None so we get all rows,
                    // then filter by range and apply limit ourselves.
                    let enhance_limit = if start_time.is_some() || end_time.is_some() {
                        None
                    } else {
                        limit
                    };
                    let enhanced = crate::queries::ohlcv::enhance_rows(
                        &ticker, redis_result.rows, enhance_limit, start_time,
                    );
                    let mut enhanced = enhanced;
                    // Apply end_time filter when date range was provided
                    if let Some(et) = end_time {
                        enhanced.retain(|r| r.time <= et);
                    }
                    // Apply limit after date filtering (enhanced is newest-first)
                    if let Some(l) = limit {
                        let l = l as usize;
                        if enhanced.len() > l {
                            enhanced.truncate(l);
                        }
                    }
                    if !enhanced.is_empty() {
                        let mut mapped: Vec<StockDataResponse> = enhanced
                            .into_iter()
                            .map(|r| super::response::map_ohlcv_to_response(r, is_daily, Mode::All))
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
    let batch_map = if extra_sources.is_empty() {
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            ohlcv::get_ohlcv_joined_batch(
                pool, source, &symbols, interval, limit, start_time, end_time,
            ),
        )
        .await
    } else {
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            ohlcv::get_ohlcv_joined_batch_with_extra(
                pool, source, &symbols, interval, limit, start_time, end_time, extra_sources,
            ),
        )
        .await
    };

    let batch_map = match batch_map {
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
            .map(|r| super::response::map_ohlcv_to_response(r, is_daily, Mode::All))
            .collect();

        // DB returns newest first (DESC index scan), but API contract is oldest first
        mapped.reverse();

        result.insert(ticker, mapped);
    }

    result.retain(|_, v| !v.is_empty());
    (result, "postgres", None)
}

/// Aggregated interval: fetch source data, aggregate, enhance, trim.
/// Returns (data, source_tag, redis_meta).
pub(crate) async fn fetch_aggregated_tickers(
    pool: &PgPool,
    redis_client: &Option<crate::redis::RedisClient>,
    source: &str,
    symbols: Vec<String>,
    agg: crate::models::aggregated_interval::AggregatedInterval,
    start_time: Option<chrono::DateTime<chrono::Utc>>,
    end_time: Option<chrono::DateTime<chrono::Utc>>,
    limit: i64,
    extra_sources: &[&str],
    use_redis: bool,
) -> (BTreeMap<String, Vec<StockDataResponse>>, &'static str, Option<redis_reader::RedisReadResult>) {
    use crate::services::aggregator::{AggregatedOhlcv, Aggregator};

    let base_interval = agg.base_interval().as_str();

    // Fetch source data with lookback buffer for MA200
    let lookback = limit + crate::constants::api::AGGREGATED_LOOKBACK;

    let is_daily = base_interval == "1D";

    // Hourly offset: VN stocks align to market open (09:00 ICT = 02:00 UTC),
    // crypto aligns to midnight UTC.
    let hourly_offset: i64 = if source == "vn" { 2 } else { 0 };

    // Redis shortcut: aggregated interval, no extra sources
    // When a date range is given, check if Redis has data covering start_time
    let redis_allowed = use_redis
        && !symbols.is_empty()
        && extra_sources.is_empty()
        && redis_client.is_some();

    if redis_allowed {
        let start_ok = if start_time.is_some() {
            redis_reader::redis_covers_range(
                redis_client, source, &symbols[0], base_interval, start_time.unwrap(),
            )
            .await
        } else {
            true
        };

        if start_ok {
            // When a date range is given, fetch all ZSET rows so the range
            // can be in the middle of history.
            let effective_lookback = if start_time.is_some() || end_time.is_some() {
                (crate::workers::redis_worker::max_size(base_interval) as i64)
                    + crate::constants::api::AGGREGATED_LOOKBACK
            } else {
                lookback
            };

            if let Some(redis_map) = redis_reader::batch_read_ohlcv_from_redis(
                redis_client, source, &symbols, base_interval,
                effective_lookback,
            ).await {
                let mut per_ticker: HashMap<String, Vec<AggregatedOhlcv>> = HashMap::new();
                let mut first_meta: Option<redis_reader::RedisReadResult> = None;

                for (ticker, redis_result) in redis_map {
                    let meta = redis_reader::RedisReadResult {
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
                    let mut filtered: Vec<_> = data.iter().collect();
                    // Apply start_time filter when date range was provided
                    if let Some(st) = start_time {
                        filtered.retain(|d| d.time >= st);
                    }
                    if let Some(et) = end_time {
                        filtered.retain(|d| d.time <= et);
                    }
                    let len = filtered.len();
                    let start = if len > limit as usize { len - limit as usize } else { 0 };
                    let trimmed: Vec<StockDataResponse> = filtered[start..]
                        .iter()
                        .map(|d| super::response::map_aggregated_to_response(d, is_daily, Mode::All))
                        .collect();
                    if !trimmed.is_empty() {
                        result.insert(ticker.clone(), trimmed);
                    }
                }

                if !result.is_empty() {
                    return (result, "redis", first_meta);
                }
            }
        }
    }

    // Fall through to PostgreSQL
    let raw_result = if extra_sources.is_empty() {
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            ohlcv::get_ohlcv_batch_raw(
                pool, source, &symbols, base_interval,
                Some(lookback), start_time, end_time,
            ),
        )
        .await
    } else {
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            ohlcv::get_ohlcv_batch_raw_with_extra(
                pool, source, &symbols, base_interval,
                Some(lookback), start_time, end_time, extra_sources,
            ),
        )
        .await
    };

    let raw_map = match raw_result {
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
            .map(|d| super::response::map_aggregated_to_response(d, is_daily, Mode::All))
            .collect();

        result.insert(ticker, trimmed);
    }

    result.retain(|_, v| !v.is_empty());
    (result, "postgres", None)
}
