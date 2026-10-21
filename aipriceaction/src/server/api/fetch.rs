use chrono::NaiveDate;
use sqlx::PgPool;
use std::collections::{BTreeMap, HashMap, HashSet};

use crate::server::redis_reader;
use crate::server::types::{Mode, NormalizedInterval, StockDataResponse, TickersQuery};
use crate::services::ohlcv;

/// Find exact ticker identities in the requested sources without applying the
/// legacy global source-priority rule. Used to distinguish a literal comma in
/// a ticker from the comma-joined list emitted by the web backtest caller.
pub(crate) async fn exact_symbols_in_sources(
    redis_client: &Option<crate::redis::RedisClient>,
    pool: &PgPool,
    symbols: &[String],
    allowed_sources: &[String],
) -> HashSet<String> {
    let requested: HashSet<&str> = symbols.iter().map(String::as_str).collect();
    let mut found = HashSet::new();

    if let Some(tickers) = redis_reader::read_ticker_list_from_redis(redis_client).await {
        for ticker in tickers {
            if requested.contains(ticker.ticker.as_str())
                && allowed_sources
                    .iter()
                    .any(|source| source == &ticker.source)
            {
                found.insert(ticker.ticker);
            }
        }
    }

    let missing: Vec<String> = symbols
        .iter()
        .filter(|symbol| !found.contains(*symbol))
        .cloned()
        .collect();
    if missing.is_empty() {
        return found;
    }

    let query = sqlx::query_as::<_, (String, String)>(
        "SELECT ticker, source FROM tickers WHERE ticker = ANY($1) AND source = ANY($2)",
    )
    .bind(&missing)
    .bind(allowed_sources.to_vec());
    match tokio::time::timeout(std::time::Duration::from_secs(3), query.fetch_all(pool)).await {
        Ok(Ok(rows)) => found.extend(rows.into_iter().map(|(symbol, _)| symbol)),
        Ok(Err(error)) => tracing::warn!("Failed to resolve exact comma symbols: {error}"),
        Err(_) => tracing::warn!("Timed out resolving exact comma symbols"),
    }
    found
}

/// PG fallback for list_tickers_with_extra when Redis doesn't have data.
/// Returns a Vec of ticker strings, or an empty Vec on error/timeout.
#[tracing::instrument(skip(pool))]
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

fn source_priority(source: &str) -> u8 {
    match source {
        "vn" => 0,
        "yahoo" => 1,
        "sjc" => 2,
        "crypto" => 3,
        _ => 4,
    }
}

fn prioritize_source_map(source_map: HashMap<String, Vec<String>>) -> HashMap<String, Vec<String>> {
    let mut selected: HashMap<String, String> = HashMap::new();
    for (source, symbols) in source_map {
        for symbol in symbols {
            let replace = selected
                .get(&symbol)
                .map_or(true, |current| source_priority(&source) < source_priority(current));
            if replace {
                selected.insert(symbol, source.clone());
            }
        }
    }

    let mut grouped: HashMap<String, Vec<String>> = HashMap::new();
    for (symbol, source) in selected {
        grouped.entry(source).or_default().push(symbol);
    }
    for symbols in grouped.values_mut() {
        symbols.sort();
    }
    grouped
}

/// Resolve symbols to their source groups using Redis (fast) or PG fallback.
/// Returns a map of source → Vec<symbol>.
#[tracing::instrument(skip(redis_client, pool, symbols))]
pub(crate) async fn resolve_source_map(
    redis_client: &Option<crate::redis::RedisClient>,
    pool: &PgPool,
    symbols: Option<&[String]>,
) -> HashMap<String, Vec<String>> {
    if let Some(syms) = symbols {
        // With explicit symbols: look them up in Redis cached ticker list first
        if let Some(redis_tickers) = redis_reader::read_ticker_list_from_redis(redis_client).await {
            let sym_set: std::collections::HashSet<&str> =
                syms.iter().map(|s| s.as_str()).collect();
            let mut grouped: HashMap<String, Vec<String>> = HashMap::new();
            for t in redis_tickers {
                if sym_set.contains(t.ticker.as_str()) {
                    grouped.entry(t.source).or_default().push(t.ticker);
                }
            }
            let selected = prioritize_source_map(grouped);
            let requested_count = sym_set.len();
            let selected_count: usize = selected.values().map(Vec::len).sum();
            if selected_count == requested_count {
                tracing::debug!("Resolved {requested_count} symbols from Redis ticker list");
                selected
            } else {
                prioritize_source_map(drop_redis_resolve_pg(pool, syms).await)
            }
        } else {
            prioritize_source_map(drop_redis_resolve_pg(pool, syms).await)
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
                prioritize_source_map(grouped)
            } else {
                prioritize_source_map(drop_redis_list_all(pool).await)
            }
        } else {
            prioritize_source_map(drop_redis_list_all(pool).await)
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
        // JSON encodes the sorted list unambiguously (including empty lists and
        // literal commas inside a symbol). Hex-encode it so the component is
        // safe inside the pipe-delimited cache key.
        let encoded =
            serde_json::to_vec(&syms).expect("serializing ticker symbols to JSON cannot fail");
        let mut component = String::with_capacity(encoded.len() * 2);
        use std::fmt::Write as _;
        for byte in encoded {
            write!(&mut component, "{byte:02x}").expect("writing to a String cannot fail");
        }
        component
    };

    let limit = effective_limit.map(|l| l.to_string()).unwrap_or_default();
    let start = params.start_date.as_deref().unwrap_or("");
    let end = params.end_date.as_deref().unwrap_or("");

    format!(
        "{source}|{interval_str}|{sorted_symbols}|{limit}|{start}|{end}|ma={}|ema={}",
        params.ma, params.ema
    )
}

#[cfg(test)]
mod cache_key_tests {
    use super::build_cache_key;
    use crate::server::types::{Mode, NormalizedInterval, TickersQuery};
    use std::collections::HashMap;

    fn cache_enabled_query() -> TickersQuery {
        TickersQuery {
            symbol: None,
            interval: Some("1D".to_owned()),
            start_date: None,
            end_date: None,
            limit: Some(1),
            legacy: false,
            format: "json".to_owned(),
            cache: true,
            mode: Mode::Yahoo,
            redis: true,
            ma: true,
            ema: false,
            snap: true,
        }
    }

    #[test]
    fn literal_comma_symbol_does_not_alias_repeated_symbols_in_cache() {
        let params = cache_enabled_query();
        let interval = NormalizedInterval::Native("1D");
        let literal_comma = vec!["DUPL,FPT".to_owned()];
        let repeated_symbols = vec!["DUPL".to_owned(), "FPT".to_owned()];

        // Exercise both possible first-request orders: a warmed entry for one
        // request must never satisfy the other request's cache lookup.
        for (first, second) in [
            (&literal_comma, &repeated_symbols),
            (&repeated_symbols, &literal_comma),
        ] {
            let first_key = build_cache_key(&params, &interval, first, Some(1));
            let second_key = build_cache_key(&params, &interval, second, Some(1));
            assert_ne!(first_key, second_key);

            let cache = HashMap::from([(first_key, "first request response")]);
            assert!(cache.get(&second_key).is_none());
        }
    }
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
#[tracing::instrument(skip(pool, redis_client, symbols, extra_sources))]
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
    with_ma: bool,
    use_ema: bool,
    skip_snap: bool,
) -> (
    BTreeMap<String, Vec<StockDataResponse>>,
    &'static str,
    Option<redis_reader::RedisReadResult>,
) {
    let is_daily = interval == "1D";

    // Redis shortcut: native interval, Redis client available
    // When a date range is given, check if Redis has data covering start_time
    let redis_allowed = use_redis && !symbols.is_empty() && redis_client.is_some();

    // --- Snapshot fast path ---
    // Try reading pre-computed responses from Redis HASH snapshots.
    // Only eligible when: no date range (or end_date >= today), MA enabled, limit is set, and not a recursive call.
    let today = chrono::Utc::now().date_naive();
    let end_is_today = end_time.map_or(true, |t| t.date_naive() >= today);
    let snap_eligible = !skip_snap
        && redis_allowed
        && limit.is_some()
        && start_time.is_none()
        && end_is_today
        && with_ma;
    let ma_type: &str = if use_ema { "ema" } else { "sma" };
    let limit_val = limit.unwrap_or(1);

    if snap_eligible {
        if let Some(redis) = &*redis_client {
            if let Some(raw_values) = crate::workers::redis_worker::batch_read_snapshots(
                redis, source, &symbols, interval, limit_val, ma_type,
            )
            .await
            {
                // Separate hits from misses
                let mut result: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();
                let mut missed_symbols: Vec<String> = Vec::new();

                for (i, raw) in raw_values.iter().enumerate() {
                    if let Some(json) = raw {
                        if let Ok(bars) = serde_json::from_str::<Vec<StockDataResponse>>(json) {
                            if !bars.is_empty() {
                                result.insert(symbols[i].clone(), bars);
                                continue;
                            }
                        }
                    }
                    missed_symbols.push(symbols[i].clone());
                }

                // Compute missing tickers via existing path (skip_snap=true to avoid recursion)
                if !missed_symbols.is_empty() {
                    let (missed_result, _tag, _meta) = Box::pin(fetch_native_tickers(
                        pool,
                        redis_client,
                        source,
                        missed_symbols.clone(),
                        interval,
                        None,
                        None,
                        limit,
                        extra_sources,
                        use_redis,
                        with_ma,
                        use_ema,
                        true,
                    ))
                    .await;

                    // Write back snapshots for the newly computed tickers
                    if !missed_result.is_empty() {
                        let redis = redis.clone();
                        let ma_type_owned = ma_type.to_string();
                        let source_owned = source.to_string();
                        let interval_owned = interval.to_string();
                        let missed_clone = missed_result.clone();
                        let missed_syms = missed_symbols;
                        tokio::spawn(async move {
                            crate::workers::redis_worker::batch_write_snapshots(
                                &redis,
                                &source_owned,
                                &missed_syms,
                                &interval_owned,
                                limit_val,
                                &ma_type_owned,
                                &missed_clone,
                                None,
                            )
                            .await;
                        });
                    }

                    // Merge misses into result
                    for (ticker, bars) in missed_result {
                        result.insert(ticker, bars);
                    }
                }

                if !result.is_empty() {
                    tracing::info!(path = "redis-snap", tickers = result.len());
                    return (result, "redis-snap", None);
                }
            }
        } // end if let Some(ref redis)
    }

    if redis_allowed {
        let start_ok = if let Some(st) = start_time {
            redis_reader::redis_covers_range(redis_client, source, &symbols[0], interval, st).await
        } else {
            true
        };

        if start_ok {
            let effective_limit = limit.unwrap_or(crate::constants::api::DEFAULT_LIMIT);
            // When a start date is given, fetch all ZSET rows so the range
            // can be in the middle of history (not just at the tail).
            // For end-only, use ZREVRANGEBYSCORE to start from end_time,
            // so a small limit + SMA_MAX_PERIOD suffices.
            let need_full_scan = start_time.is_some();
            let max_score = if need_full_scan {
                None
            } else {
                end_time.map(|t| t.timestamp_millis())
            };
            let buffer = if with_ma {
                if use_ema {
                    crate::constants::api::EMA_LOOKBACK
                } else {
                    crate::constants::api::SMA_MAX_PERIOD
                }
            } else {
                1
            };
            let total_limit = if need_full_scan {
                (crate::workers::redis_worker::max_size(interval) as i64) + buffer
            } else {
                effective_limit + buffer
            };
            let req_detail = match (limit, &start_time, &end_time) {
                (_, Some(s), Some(e)) => format!(
                    "tickers limit={effective_limit} start={}..end={}",
                    s.format("%Y-%m-%d"),
                    e.format("%Y-%m-%d")
                ),
                (_, Some(s), None) => format!(
                    "tickers limit={effective_limit} start={}..",
                    s.format("%Y-%m-%d")
                ),
                (_, None, Some(e)) => format!(
                    "tickers limit={effective_limit} ..end={} score={}",
                    e.format("%Y-%m-%d"),
                    e.timestamp_millis()
                ),
                (Some(l), None, None) => format!("tickers limit={l}"),
                (None, None, None) => format!("tickers limit={effective_limit}"),
            };

            // Try reading from primary source and any extra sources
            let mut redis_map: HashMap<String, redis_reader::RedisReadResult> = HashMap::new();
            if let Some(map) = redis_reader::batch_read_ohlcv_from_redis(
                redis_client,
                source,
                &symbols,
                interval,
                total_limit,
                &req_detail,
                max_score,
            )
            .await
            {
                redis_map = map;
            }
            // Also read extra source ZSETs (e.g. sjc for mode=yahoo)
            for &extra_src in extra_sources {
                if let Some(extra_map) = redis_reader::batch_read_ohlcv_from_redis(
                    redis_client,
                    extra_src,
                    &symbols,
                    interval,
                    total_limit,
                    &format!("{req_detail}/extra"),
                    max_score,
                )
                .await
                {
                    for (k, v) in extra_map {
                        redis_map.entry(k).or_insert(v);
                    }
                }
            }
            if !redis_map.is_empty() {
                let mut result = BTreeMap::new();
                let first_meta = first_redis_metadata(&symbols, &redis_map);
                for (ticker, redis_result) in redis_map {
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
                        &ticker,
                        redis_result.rows,
                        enhance_limit,
                        start_time,
                        with_ma,
                        use_ema,
                    );
                    let mut enhanced = enhanced;
                    // Apply end_time filter when date range was provided
                    if let Some(et) = end_time {
                        enhanced.retain(|r| r.time <= et);
                    }
                    // Apply limit after date filtering (enhanced is newest-first)
                    if let Some(l) = limit {
                        let l = l as usize;
                        if start_time.is_some() {
                            // When start_date is set, keep the N OLDEST rows
                            enhanced.reverse();
                            enhanced.truncate(l);
                            enhanced.reverse();
                        } else if enhanced.len() > l {
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
                    // Write snapshots for future requests (fire-and-forget)
                    if snap_eligible {
                        if let Some(redis) = redis_client.as_ref() {
                            let redis = redis.clone();
                            let ma_type_owned = ma_type.to_string();
                            let source_owned = source.to_string();
                            let interval_owned = interval.to_string();
                            let result_clone = result.clone();
                            let syms_owned: Vec<String> = symbols.clone();
                            let snap_limit = limit_val;
                            tokio::spawn(async move {
                                crate::workers::redis_worker::batch_write_snapshots(
                                    &redis,
                                    &source_owned,
                                    &syms_owned,
                                    &interval_owned,
                                    snap_limit,
                                    &ma_type_owned,
                                    &result_clone,
                                    None,
                                )
                                .await;
                            });
                        } // end if let Some(redis)
                    }
                    tracing::info!(path = "redis", tickers = result.len());
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
                pool, source, &symbols, interval, limit, start_time, end_time, with_ma, use_ema,
            ),
        )
        .await
    } else {
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            ohlcv::get_ohlcv_joined_batch_with_extra(
                pool,
                source,
                &symbols,
                interval,
                limit,
                start_time,
                end_time,
                extra_sources,
                with_ma,
                use_ema,
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
    tracing::info!(path = "postgres", tickers = result.len());
    (result, "postgres", None)
}

#[cfg(test)]
mod source_resolution_tests {
    use super::prioritize_source_map;
    use std::collections::HashMap;

    #[test]
    fn mode_all_collapses_duplicate_identities_by_legacy_source_priority() {
        let source_map = HashMap::from([
            ("crypto".to_owned(), vec!["BTCUSDT".to_owned(), "DUP".to_owned()]),
            ("vn".to_owned(), vec!["VCB".to_owned(), "DUP".to_owned()]),
            ("yahoo".to_owned(), vec!["AAPL".to_owned(), "DUP".to_owned()]),
        ]);

        let selected = prioritize_source_map(source_map);

        assert_eq!(
            selected.get("vn").unwrap(),
            &vec![String::from("DUP"), String::from("VCB")]
        );
        assert_eq!(selected.get("yahoo").unwrap(), &vec![String::from("AAPL")]);
        assert_eq!(
            selected.get("crypto").unwrap(),
            &vec![String::from("BTCUSDT")]
        );
    }
}

#[cfg(test)]
mod redis_metadata_tests {
    use super::first_redis_metadata;
    use crate::server::redis_reader::RedisReadResult;
    use std::collections::HashMap;

    fn result(
        symbol: &str,
        raw_close_count: usize,
        aligned_count: usize,
        requested_limit: i64,
        base_interval: &str,
    ) -> (String, RedisReadResult) {
        (
            symbol.to_owned(),
            RedisReadResult {
                rows: Vec::new(),
                raw_close_count,
                aligned_count,
                requested_limit,
                base_interval: base_interval.to_owned(),
            },
        )
    }

    #[test]
    fn first_metadata_uses_symbol_request_order_and_preserves_redis_limit() {
        let redis_map = HashMap::from([
            (result("B", 20, 18, 25, "1m")),
            (result("A", 10, 9, 15, "1m")),
        ]);

        let selected = first_redis_metadata(&["A".to_owned(), "B".to_owned()], &redis_map)
            .expect("a requested symbol has Redis metadata");
        assert_eq!(selected.raw_close_count, 10);
        assert_eq!(selected.aligned_count, 9);
        assert_eq!(selected.requested_limit, 15);
        assert_eq!(selected.base_interval, "1m");
        assert!(selected.rows.is_empty());

        let reversed = first_redis_metadata(&["B".to_owned(), "A".to_owned()], &redis_map)
            .expect("a requested symbol has Redis metadata");
        assert_eq!(reversed.raw_close_count, 20);
        assert_eq!(reversed.aligned_count, 18);
        assert_eq!(reversed.requested_limit, 25);
    }

    #[test]
    fn first_metadata_ignores_unrequested_redis_symbols() {
        let redis_map = HashMap::from([result("UNREQUESTED", 10, 9, 15, "1D")]);
        assert!(first_redis_metadata(&["VCB".to_owned()], &redis_map).is_none());
    }
}

/// Select diagnostic metadata using the caller's symbol order, not the
/// unspecified iteration order of the Redis result HashMap.
fn first_redis_metadata(
    symbols: &[String],
    redis_map: &HashMap<String, redis_reader::RedisReadResult>,
) -> Option<redis_reader::RedisReadResult> {
    symbols.iter().find_map(|symbol| {
        redis_map
            .get(symbol)
            .map(redis_reader::RedisReadResult::metadata_only)
    })
}

/// Aggregated interval: fetch source data, aggregate, enhance, trim.
/// Returns (data, source_tag, redis_meta).
#[tracing::instrument(skip(pool, redis_client, symbols, extra_sources))]
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
    with_ma: bool,
    use_ema: bool,
) -> (
    BTreeMap<String, Vec<StockDataResponse>>,
    &'static str,
    Option<redis_reader::RedisReadResult>,
) {
    use crate::services::aggregator::{AggregatedOhlcv, Aggregator};

    let base_interval = agg.base_interval().as_str();

    // Fetch source data with lookback buffer for MA200 (skip when with_ma=false).
    // When ma=false, still need enough base bars to form the requested aggregated candles
    // plus one extra bucket for the partial leading candle.
    let agg_buffer = if with_ma {
        crate::constants::api::AGGREGATED_LOOKBACK
    } else {
        agg.base_bars_per_candle() // e.g. 15 for 15m-from-1m, 4 for 4h-from-1h, 7 for 1W-from-1D
    };
    let lookback = limit * agg.base_bars_per_candle() + agg_buffer;

    let is_daily = base_interval == "1D";

    // Hourly offset: VN stocks align to market open (09:00 ICT = 02:00 UTC),
    // crypto aligns to midnight UTC.
    let hourly_offset: i64 = if source == "vn" { 2 } else { 0 };

    // Redis shortcut: aggregated interval, no extra sources
    // When a date range is given, check if Redis has data covering start_time
    let redis_allowed =
        use_redis && !symbols.is_empty() && extra_sources.is_empty() && redis_client.is_some();
    // Keep the SQL fallback on the same retained source window when Redis has
    // enough history for a dated aggregate request. This makes cached and
    // uncached responses use the same bucket/indicator context.
    let start_range_covered = if start_time.is_some()
        && !symbols.is_empty()
        && extra_sources.is_empty()
    {
        redis_reader::redis_covers_range(
            redis_client,
            source,
            &symbols[0],
            base_interval,
            start_time.expect("checked above"),
        )
        .await
    } else {
        false
    };

    if redis_allowed {
        let start_ok = start_time.is_none() || start_range_covered;

        if start_ok {
            // When a start date is given, fetch all ZSET rows so the range
            // can be in the middle of history.
            // For end-only, use ZREVRANGEBYSCORE to start from end_time.
            let need_full_scan = start_time.is_some();
            let max_score = if need_full_scan {
                None
            } else {
                end_time.map(|t| t.timestamp_millis())
            };
            let effective_lookback = if need_full_scan {
                (crate::workers::redis_worker::max_size(base_interval) as i64) + agg_buffer
            } else {
                lookback
            };
            let agg_detail = match (limit, &start_time, &end_time) {
                (_, Some(s), Some(e)) => format!(
                    "tickers/agg {} limit={limit} start={}..end={}",
                    agg.to_str(),
                    s.format("%Y-%m-%d"),
                    e.format("%Y-%m-%d")
                ),
                (_, Some(s), None) => format!(
                    "tickers/agg {} limit={limit} start={}..",
                    agg.to_str(),
                    s.format("%Y-%m-%d")
                ),
                (_, None, Some(e)) => format!(
                    "tickers/agg {} limit={limit} ..end={} score={}",
                    agg.to_str(),
                    e.format("%Y-%m-%d"),
                    e.timestamp_millis()
                ),
                _ => format!("tickers/agg {} limit={limit}", agg.to_str()),
            };

            if let Some(redis_map) = redis_reader::batch_read_ohlcv_from_redis(
                redis_client,
                source,
                &symbols,
                base_interval,
                effective_lookback,
                &agg_detail,
                max_score,
            )
            .await
            {
                let mut per_ticker: HashMap<String, Vec<AggregatedOhlcv>> = HashMap::new();
                let first_meta = first_redis_metadata(&symbols, &redis_map);

                for (ticker, redis_result) in redis_map {
                    let aggregated = match agg.base_interval() {
                        crate::models::interval::Interval::Daily => {
                            Aggregator::aggregate_daily_data(&ticker, redis_result.rows, agg)
                        }
                        crate::models::interval::Interval::Hourly => {
                            Aggregator::aggregate_hourly_data(
                                &ticker,
                                redis_result.rows,
                                agg,
                                hourly_offset,
                            )
                        }
                        _ => Aggregator::aggregate_minute_data(&ticker, redis_result.rows, agg),
                    };
                    per_ticker.insert(ticker, aggregated);
                }

                let enhanced = Aggregator::enhance_aggregated_data(per_ticker, with_ma, use_ema);
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
                    let trimmed: Vec<StockDataResponse> = if start_time.is_some() {
                        // When start_date is set, keep first N = N oldest from start_date
                        let end = (limit as usize).min(len);
                        filtered[..end]
                            .iter()
                            .map(|d| {
                                super::response::map_aggregated_to_response(d, is_daily, Mode::All)
                            })
                            .collect()
                    } else {
                        // Default: keep last N = N newest
                        let start = if len > limit as usize {
                            len - limit as usize
                        } else {
                            0
                        };
                        filtered[start..]
                            .iter()
                            .map(|d| {
                                super::response::map_aggregated_to_response(d, is_daily, Mode::All)
                            })
                            .collect()
                    };
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

    // Fall through to PostgreSQL. If Redis covers this dated range, read the
    // same retained raw context, aggregate/enhance it, and filter bucket labels
    // below just as the Redis route does. If Redis cannot cover the range, keep
    // the historical date-bounded SQL behavior.
    let (postgres_limit, postgres_start, postgres_end) = if start_range_covered {
        (
            Some(crate::workers::redis_worker::max_size(base_interval) as i64),
            None,
            None,
        )
    } else {
        (Some(lookback), start_time, end_time)
    };
    let raw_result = if extra_sources.is_empty() {
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            ohlcv::get_ohlcv_batch_raw(
                pool,
                source,
                &symbols,
                base_interval,
                postgres_limit,
                postgres_start,
                postgres_end,
            ),
        )
        .await
    } else {
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            ohlcv::get_ohlcv_batch_raw_with_extra(
                pool,
                source,
                &symbols,
                base_interval,
                postgres_limit,
                postgres_start,
                postgres_end,
                extra_sources,
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
    let enhanced = Aggregator::enhance_aggregated_data(per_ticker, with_ma, use_ema);

    // Trim to requested limit and map to response
    let mut result: BTreeMap<String, Vec<StockDataResponse>> = BTreeMap::new();

    for (ticker, data) in enhanced {
        let trimmed: Vec<StockDataResponse> = if start_range_covered {
            let mut filtered: Vec<_> = data.iter().collect();
            if let Some(st) = start_time {
                filtered.retain(|row| row.time >= st);
            }
            if let Some(et) = end_time {
                filtered.retain(|row| row.time <= et);
            }
            let end = (limit as usize).min(filtered.len());
            filtered[..end]
                .iter()
                .map(|d| super::response::map_aggregated_to_response(d, is_daily, Mode::All))
                .collect()
        } else if start_time.is_some() {
            let len = data.len();
            // When start_date is set, keep first N = N oldest from start_date
            let end = (limit as usize).min(len);
            data[..end]
                .iter()
                .map(|d| super::response::map_aggregated_to_response(d, is_daily, Mode::All))
                .collect()
        } else {
            let len = data.len();
            // Default: keep last N = N newest
            let start = if len > limit as usize {
                len - limit as usize
            } else {
                0
            };
            data[start..]
                .iter()
                .map(|d| super::response::map_aggregated_to_response(d, is_daily, Mode::All))
                .collect()
        };

        result.insert(ticker, trimmed);
    }

    result.retain(|_, v| !v.is_empty());
    (result, "postgres", None)
}
