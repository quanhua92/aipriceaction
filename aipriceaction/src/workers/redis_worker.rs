use fred::prelude::*;
use sqlx::PgPool;

use crate::constants::redis_ts as c;
use crate::models::ohlcv::OhlcvRow;
use crate::redis::RedisClient;

/// Minimal ticker info for Redis cache (no need for id, status, next_* fields).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct TickerInfo {
    pub source: String,
    pub ticker: String,
}

/// Build a Redis ZSET key for a given source, ticker, and interval.
/// One key per ticker/interval (all 5 OHLCV fields packed into the member string).
pub fn zset_key(source: &str, ticker: &str, interval: &str) -> String {
    format!("ohlcv:{source}:{ticker}:{interval}")
}

/// Get max ZSET size (retention) for a given interval.
pub fn max_size(interval: &str) -> usize {
    match interval {
        "1h" => c::hourly_max_size(),
        "1m" => c::minute_max_size(),
        _ => c::daily_max_size(),
    }
}

/// Format an OHLCV row as a pipe-delimited member string for ZSET storage.
/// Format: "{ts_ms}|{open}|{high}|{low}|{close}|{volume}|{crawl_ts_ms}"
pub fn format_row_as_member(row: &OhlcvRow) -> String {
    let crawl_ts = chrono::Utc::now().timestamp_millis();
    format!(
        "{}|{}|{}|{}|{}|{}|{}",
        row.time.timestamp_millis(),
        row.open,
        row.high,
        row.low,
        row.close,
        row.volume,
        crawl_ts,
    )
}

/// Parse a pipe-delimited ZSET member string back into an OhlcvRow.
/// Returns None if parsing fails.
/// Also returns the crawl timestamp if present (7-field format).
pub fn parse_member(member: &str, interval: &str) -> Option<(OhlcvRow, Option<i64>)> {
    let parts: Vec<&str> = member.splitn(7, c::MEMBER_SEP).collect();
    if parts.len() < 6 {
        return None;
    }
    let ts_ms: i64 = parts[0].parse().ok()?;
    let open: f64 = parts[1].parse().ok()?;
    let high: f64 = parts[2].parse().ok()?;
    let low: f64 = parts[3].parse().ok()?;
    let close: f64 = parts[4].parse().ok()?;
    let volume: i64 = parts[5].parse().ok()?;
    let crawl_ts = if parts.len() == 7 { parts[6].parse().ok() } else { None };
    let time = chrono::DateTime::from_timestamp_millis(ts_ms)?;

    Some((
        OhlcvRow {
            ticker_id: 0,
            interval: interval.to_string(),
            time,
            open,
            high,
            low,
            close,
            volume,
        },
        crawl_ts,
    ))
}

// ---------------------------------------------------------------------------
// Snapshot cache: pre-computed limit=N responses stored in Redis HASHes.
// Key: `snap:{source}:{ticker}:{interval}`, Field: `{limit}:{ma_type}`
// ---------------------------------------------------------------------------

/// Build a Redis HASH key for a snapshot cache entry.
pub fn snap_key(source: &str, ticker: &str, interval: &str) -> String {
    format!("{}:{source}:{ticker}:{interval}", c::snapshot::KEY_PREFIX)
}

/// Build a HASH field name for a specific limit and MA type.
/// Example: `snap_field(1, "sma")` returns `"1:sma"`.
pub fn snap_field(limit: i64, ma_type: &str) -> String {
    format!("{limit}:{ma_type}")
}

/// Batch-read snapshot fields for multiple tickers via pipelined HMGET.
/// Returns `Vec<Option<String>>` in the same order as `tickers`.
/// Returns `None` if Redis is unavailable or on timeout.
pub async fn batch_read_snapshots(
    client: &RedisClient,
    source: &str,
    tickers: &[String],
    interval: &str,
    limit: i64,
    ma_type: &str,
) -> Option<Vec<Option<String>>> {
    if tickers.is_empty() {
        return Some(Vec::new());
    }

    let field = snap_field(limit, ma_type);
    let pipe = client.pipeline();

    for ticker in tickers {
        let key = snap_key(source, ticker, interval);
        if let Err(e) = pipe.hmget::<(), _, _>(&key, &field).await {
            tracing::warn!(%key, "pipeline hmget enqueue error: {e}");
            continue;
        }
    }

    let results: Vec<FredResult<Value>> =
        match tokio::time::timeout(std::time::Duration::from_secs(2), pipe.try_all::<Value>()).await {
            Ok(r) => r,
            Err(_) => {
                tracing::warn!("[SNAP] pipeline hmget timed out");
                return None;
            }
        };

    let mut out: Vec<Option<String>> = Vec::with_capacity(tickers.len());
    for result in results {
        match result {
            Ok(Value::Array(arr)) => {
                // HMGET returns array of values for each field requested.
                // We request 1 field, so take the first.
                let s = arr.into_iter().next().and_then(|v| match v {
                    Value::String(s) => Some(s.to_string()),
                    _ => None,
                });
                out.push(s);
            }
            Ok(Value::Null) => out.push(None),
            Ok(_) => out.push(None),
            Err(e) => {
                tracing::warn!("[SNAP] hmget error: {e}");
                out.push(None);
            }
        }
    }

    Some(out)
}

/// Batch-write snapshot fields for multiple tickers via pipelined HSET + EXPIRE.
/// Fire-and-forget — errors are logged but not propagated.
pub async fn batch_write_snapshots(
    client: &RedisClient,
    source: &str,
    tickers: &[String],
    interval: &str,
    limit: i64,
    ma_type: &str,
    responses: &std::collections::BTreeMap<String, Vec<crate::server::types::StockDataResponse>>,
) {
    if responses.is_empty() {
        return;
    }

    let field = snap_field(limit, ma_type);
    let ttl = c::snapshot::TTL_SECS as i64;
    let pipe = client.pipeline();

    for (ticker, bars) in responses {
        if tickers.iter().any(|t| t == ticker) {
            match serde_json::to_string(bars) {
                Ok(json) => {
                    let key = snap_key(source, ticker, interval);
                    let mut values = std::collections::HashMap::new();
                    values.insert(field.clone(), json);
                    if let Err(e) = pipe.hset::<(), _, _>(&key, values).await {
                        tracing::warn!(%key, "pipeline hset enqueue error: {e}");
                    }
                    if let Err(e) = pipe.expire::<(), _>(&key, ttl, None).await {
                        tracing::warn!(%key, "pipeline expire enqueue error: {e}");
                    }
                }
                Err(e) => {
                    tracing::warn!(ticker, "snapshot serialize error: {e}");
                }
            }
        }
    }

    match tokio::time::timeout(std::time::Duration::from_secs(2), pipe.try_all::<Value>()).await {
        Ok(_) => {}
        Err(_) => {
            tracing::warn!("[SNAP] pipeline hset+expire timed out");
        }
    }
}

/// Delete the snapshot HASH for a given ticker/interval.
/// Invalidates all limit/ma_type variants at once.
/// No-op if client is None.
pub async fn invalidate_snapshot(client: &Option<RedisClient>, source: &str, ticker: &str, interval: &str) {
    let client = match client {
        Some(c) => c,
        None => return,
    };
    let key = snap_key(source, ticker, interval);
    match tokio::time::timeout(
        std::time::Duration::from_secs(2),
        client.del::<Value, _>(&key),
    )
    .await
    {
        Ok(Ok(Value::Integer(n))) if n > 0 => {
            tracing::debug!(%key, "snapshot invalidated");
        }
        Ok(Ok(_)) => {}
        Ok(Err(e)) => {
            tracing::warn!(%key, "snapshot del error: {e}");
        }
        Err(_) => {
            tracing::warn!(%key, "snapshot del timed out");
        }
    }
}

/// Write the full ticker list to Redis as a JSON string with TTL.
/// Called at the end of each backfill_full() cycle.
pub async fn write_ticker_list(client: &RedisClient, tickers: &[TickerInfo]) {
    let value = match serde_json::to_string(tickers) {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("Failed to serialize ticker list: {e}");
            return;
        }
    };

    match tokio::time::timeout(
        std::time::Duration::from_secs(2),
        client.set::<Value, _, _>(
            c::TICKER_LIST_KEY,
            &value,
            Some(fred::types::Expiration::EX(c::TICKER_LIST_TTL_SECS as i64)),
            None,
            false,
        ),
    )
    .await
    {
        Ok(Ok(_)) => {
            tracing::info!("Cached {} tickers in Redis ({})", tickers.len(), c::TICKER_LIST_KEY);
        }
        Ok(Err(e)) => {
            tracing::warn!("Failed to write ticker list to Redis: {e}");
        }
        Err(_) => {
            tracing::warn!("Timeout writing ticker list to Redis");
        }
    }
}

/// Read the cached ticker list from Redis.
/// Returns None if the key doesn't exist, parsing fails, or on timeout.
pub async fn read_ticker_list(client: &RedisClient) -> Option<Vec<TickerInfo>> {
    let value: String = match tokio::time::timeout(
        std::time::Duration::from_secs(2),
        client.get(c::TICKER_LIST_KEY),
    )
    .await
    {
        Ok(Ok(v)) => v,
        Ok(Err(_)) => return None,
        Err(_) => return None,
    };

    serde_json::from_str(&value).ok()
}

/// Write OHLCV rows to Redis ZSET (fire-and-forget).
/// Formats rows as pipe-delimited members, uses ZADD (pipelined), then trims with ZREMRANGEBYRANK.
pub async fn write_ohlcv_to_redis(
    client: &Option<RedisClient>,
    source: &str,
    ticker: &str,
    interval: &str,
    rows: &[OhlcvRow],
) {
    let client = match client {
        Some(c) => c,
        None => return,
    };

    if rows.is_empty() {
        return;
    }

    let key = zset_key(source, ticker, interval);
    let values: Vec<(f64, String)> = rows
        .iter()
        .map(|row| {
            let member = format_row_as_member(row);
            (row.time.timestamp_millis() as f64, member)
        })
        .collect();

    // Remove existing members in the timestamp range to prevent duplicates
    // (same score + different crawl_ts creates distinct members in ZSET).
    if let (Some(min_ts), Some(max_ts)) = (
        values.iter().map(|(s, _)| *s).fold(None, |acc, s| Some(acc.map_or(s, |a: f64| a.min(s)))),
        values.iter().map(|(s, _)| *s).fold(None, |acc, s| Some(acc.map_or(s, |a: f64| a.max(s)))),
    ) {
        match tokio::time::timeout(
            std::time::Duration::from_secs(2),
            client.zremrangebyscore::<i64, _, _, _>(&key, min_ts, max_ts),
        )
        .await
        {
            Ok(Ok(removed)) if removed > 0 => {
                tracing::debug!(key, removed, min = min_ts, max = max_ts, "write dedup: removed stale members");
            }
            Ok(Ok(_)) => {}
            Ok(Err(e)) => {
                tracing::warn!(key, "write dedup zremrangebyscore failed: {e}");
            }
            Err(_) => {
                tracing::warn!(key, "write dedup zremrangebyscore timed out");
            }
        }
    }

    // Batch ZADD (supports multi-member add natively)
    match tokio::time::timeout(
        std::time::Duration::from_secs(2),
        client.zadd::<Value, _, _>(
            &key,
            None,  // options
            None,  // ordering
            false, // changed
            false, // incr
            values,
        ),
    )
    .await
    {
        Ok(Ok(_)) => {}
        Ok(Err(e)) => {
            tracing::warn!(key, "zadd failed: {e}");
            return;
        }
        Err(_) => {
            tracing::warn!(key, "zadd timed out");
            return;
        }
    }

    // Trim to retention limit: keep top MAX entries by score (highest timestamps)
    let limit = max_size(interval);
    match tokio::time::timeout(
        std::time::Duration::from_secs(2),
        client.zremrangebyrank::<Value, _>(&key, 0, -(limit as i64 + 1)),
    )
    .await
    {
        Ok(Ok(_)) => {}
        Ok(Err(e)) => {
            tracing::warn!(key, "zremrangebyrank failed: {e}");
        }
        Err(_) => {
            tracing::warn!(key, "zremrangebyrank timed out");
        }
    }
}

/// Backfill worker: populates Redis ZSETs from PostgreSQL.
///
/// Every cycle (every 6h): full backfill ALL ticker/interval groups from PG,
/// then trim all ZSETs to retention limits to prevent OOM.
pub async fn run(pool: PgPool, client: RedisClient) {
    tracing::info!("Redis ZSET backfill worker started");

    loop {
        backfill_full(&pool, &client).await;
        tokio::time::sleep(std::time::Duration::from_secs(c::BACKFILL_LOOP_SECS)).await;
    }
}

/// Full backfill: enumerate all tickers from PG, backfill all 3 intervals with full history.
pub async fn backfill_full(pool: &PgPool, client: &RedisClient) {
    tracing::info!("Redis ZSET backfill FULL cycle: starting");

    let tickers = match crate::queries::ohlcv::list_all_tickers(pool).await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("Redis ZSET backfill: failed to list tickers: {e}");
            return;
        }
    };

    tracing::info!("Redis ZSET backfill FULL: found {} tickers from PG", tickers.len());

    let mut groups: Vec<(String, String, String, i64)> = Vec::new();
    for ticker in &tickers {
        for &interval in &["1D", "1h", "1m"] {
            let limit: i64 = match interval {
                "1D" => c::daily_backfill_limit(),
                "1h" => c::hourly_backfill_limit(),
                "1m" => c::minute_backfill_limit(),
                _ => c::daily_backfill_limit(),
            };
            groups.push((ticker.source.clone(), ticker.ticker.clone(), interval.to_string(), limit));
        }
    }

    process_groups(&pool, client, groups, "FULL").await;

    // Trim all ZSETs to retention limits to prevent unbounded memory growth
    if let Some(all_keys) = discover_keys(client).await {
        trim_all_keys(client, &all_keys).await;
    }

    // Cache ticker list in Redis for PG-outage resilience
    let ticker_infos: Vec<TickerInfo> = tickers
        .iter()
        .map(|t| TickerInfo { source: t.source.clone(), ticker: t.ticker.clone() })
        .collect();
    write_ticker_list(client, &ticker_infos).await;
}

/// Process a list of groups with concurrency control.
/// Each group is (source, ticker, interval, pg_limit).
async fn process_groups(
    pool: &PgPool,
    client: &RedisClient,
    groups: Vec<(String, String, String, i64)>,
    cycle_label: &str,
) {
    let total_groups = groups.len();

    tracing::info!(
        "Redis ZSET backfill {cycle_label}: {total_groups} groups, concurrency={}",
        c::BACKFILL_CONCURRENCY
    );

    let pool = pool.clone();
    let client = client.clone();
    let sem = std::sync::Arc::new(tokio::sync::Semaphore::new(c::BACKFILL_CONCURRENCY));
    let handles: Vec<_> = groups
        .into_iter()
        .map(|(source, ticker, interval, pg_limit)| {
            let pool = pool.clone();
            let client = client.clone();
            let source2 = source.clone();
            let ticker2 = ticker.clone();
            let interval2 = interval.clone();
            let permit = sem.clone();
            tokio::spawn(async move {
                let _guard = match permit.acquire().await {
                    Ok(guard) => guard,
                    Err(_) => {
                        tracing::error!("backfill semaphore closed unexpectedly");
                        return Err("semaphore closed".into());
                    }
                };
                match backfill_ticker(&pool, &client, &source, &ticker, &interval, pg_limit).await {
                    Ok(result) => Ok(result),
                    Err(e) => {
                        tracing::warn!(
                            source = %source2, ticker = %ticker2, interval = %interval2,
                            "Redis ZSET backfill error: {e}"
                        );
                        Err(e)
                    }
                }
            })
        })
        .collect();

    let mut backfilled = 0usize;
    let mut skipped = 0usize;
    let mut errors = 0usize;
    for handle in handles {
        match handle.await {
            Ok(Ok(true)) => backfilled += 1,
            Ok(Ok(false)) => skipped += 1,
            Ok(Err(_)) => errors += 1,
            Err(_) => errors += 1,
        }
    }

    tracing::info!(
        "Redis ZSET backfill {cycle_label} done: {total_groups} groups, backfilled={backfilled}, skipped={skipped}, errors={errors}",
    );
}

/// Discover all Redis ZSET keys using SCAN with pattern "ohlcv:*".
async fn discover_keys(client: &RedisClient) -> Option<Vec<String>> {
    let mut all_keys = Vec::new();
    let mut cursor: u64 = 0;

    loop {
        let (next_cursor, keys): (u64, Vec<String>) = client
            .scan_page::<(u64, Vec<String>), _, _>(cursor.to_string(), "ohlcv:*", Some(1000), None)
            .await
            .ok()?;

        all_keys.extend(keys);

        if next_cursor == 0 {
            break;
        }
        cursor = next_cursor;
    }

    tracing::info!("Redis ZSET backfill: discovered {} total keys", all_keys.len());
    Some(all_keys)
}

/// Backfill a single ticker/interval from PostgreSQL to Redis.
/// `pg_limit` controls how many rows to read from PG.
/// Returns true if data was written, false if skipped (up-to-date).
async fn backfill_ticker(
    pool: &PgPool,
    client: &RedisClient,
    source: &str,
    ticker: &str,
    interval: &str,
    pg_limit: i64,
) -> Result<bool, Box<dyn std::error::Error + Send + Sync>> {
    let ticker_id: i32 = sqlx::query_scalar(
        "SELECT id FROM tickers WHERE source = $1 AND ticker = $2",
    )
    .bind(source)
    .bind(ticker)
    .fetch_optional(pool)
    .await?
    .ok_or_else(|| format!("ticker not found: {source}:{ticker}"))?;

    let rows = crate::queries::ohlcv::get_ohlcv(pool, ticker_id, interval, Some(pg_limit)).await?;

    if rows.is_empty() {
        tracing::info!(source, ticker, interval, "Redis ZSET backfill skipped: no data in PG");
        return Ok(false);
    }

    let key = zset_key(source, ticker, interval);

    // Remove stale members for the timestamp range being written.
    // Fire-and-forget writes with same timestamp but different close/volume
    // create duplicate members that ZSET doesn't deduplicate (only by string).
    // ZREMRANGEBYSCORE clears the entire range; ZADD then writes back PG data.
    let min_ts = rows.iter().map(|r| r.time.timestamp_millis() as f64).fold(f64::INFINITY, f64::min);
    let max_ts = rows.iter().map(|r| r.time.timestamp_millis() as f64).fold(f64::NEG_INFINITY, f64::max);
    match client
        .zremrangebyscore::<i64, _, _, _>(&key, min_ts, max_ts)
        .await
    {
        Ok(removed) if removed > 0 => {
            tracing::debug!(key, removed, min = min_ts, max = max_ts, "backfill dedup: removed stale members");
        }
        Ok(_) => {}
        Err(e) => {
            tracing::warn!(key, "backfill zremrangebyscore failed: {e}");
        }
    }

    write_ohlcv_to_redis(&Some(client.clone()), source, ticker, interval, &rows).await;
    invalidate_snapshot(&Some(client.clone()), source, ticker, interval).await;

    tracing::info!(
        source, ticker, interval,
        written = rows.len(),
        pg_limit,
        "Redis ZSET backfill"
    );

    Ok(true)
}

/// Trim all discovered ZSET keys to their retention limits.
/// Runs every incremental cycle to ensure memory doesn't grow unbounded.
async fn trim_all_keys(client: &RedisClient, keys: &[String]) {
    let mut trimmed = 0usize;
    let mut errors = 0usize;

    for key in keys {
        // Parse interval from key: ohlcv:{source}:{ticker}:{interval}
        let interval = match key.rsplit(':').next() {
            Some(iv) if matches!(iv, "1D" | "1h" | "1m") => iv,
            _ => continue,
        };

        let limit = max_size(interval);
        match client
            .zremrangebyrank::<i64, _>(key, 0, -(limit as i64 + 1))
            .await
        {
            Ok(removed) if removed > 0 => trimmed += 1,
            Ok(_) => {}
            Err(e) => {
                errors += 1;
                tracing::warn!(key, "trim zremrangebyrank error: {e}");
            }
        }
    }

    if trimmed > 0 || errors > 0 {
        tracing::info!(
            "Redis ZSET trim: keys={}, trimmed={trimmed}, errors={errors}",
            keys.len()
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};

    fn make_row(ts_ms: i64, open: f64, high: f64, low: f64, close: f64, volume: i64) -> OhlcvRow {
        OhlcvRow {
            ticker_id: 0,
            interval: "1D".to_string(),
            time: Utc.timestamp_millis_opt(ts_ms).unwrap(),
            open,
            high,
            low,
            close,
            volume,
        }
    }

    #[test]
    fn test_zset_key_format() {
        assert_eq!(zset_key("vn", "VCB", "1D"), "ohlcv:vn:VCB:1D");
        assert_eq!(zset_key("crypto", "BTCUSDT", "1h"), "ohlcv:crypto:BTCUSDT:1h");
    }

    #[test]
    fn test_max_size() {
        assert_eq!(max_size("1D"), c::daily_max_size());
        assert_eq!(max_size("1h"), c::hourly_max_size());
        assert_eq!(max_size("1m"), c::minute_max_size());
    }

    #[test]
    fn test_format_and_parse_member() {
        let row = make_row(1700000000000, 1500.5, 1510.0, 1490.0, 1505.25, 100000);
        let member = format_row_as_member(&row);
        // New format: 7 fields with crawl_ts at the end
        let parts: Vec<&str> = member.splitn(7, '|').collect();
        assert_eq!(parts.len(), 7);
        assert_eq!(parts[0], "1700000000000");
        assert_eq!(parts[1], "1500.5");
        assert_eq!(parts[6].parse::<i64>().is_ok(), true); // crawl_ts

        let (parsed, crawl_ts) = parse_member(&member, "1D").unwrap();
        assert_eq!(parsed.time.timestamp_millis(), 1700000000000);
        assert_eq!(parsed.open, 1500.5);
        assert_eq!(parsed.high, 1510.0);
        assert_eq!(parsed.low, 1490.0);
        assert_eq!(parsed.close, 1505.25);
        assert_eq!(parsed.volume, 100000);
        assert!(crawl_ts.is_some());

        // Old 6-field format should still parse (backward compat)
        let old_member = "1700000000000|1500.5|1510|1490|1505.25|100000";
        let (parsed_old, crawl_ts_old) = parse_member(old_member, "1D").unwrap();
        assert_eq!(parsed_old.time.timestamp_millis(), 1700000000000);
        assert_eq!(parsed_old.close, 1505.25);
        assert!(crawl_ts_old.is_none());
    }

    #[test]
    fn test_parse_member_invalid() {
        assert!(parse_member("garbage", "1D").is_none());
        assert!(parse_member("1|2|3|4", "1D").is_none());
    }

    #[test]
    fn test_snap_key_and_field() {
        assert_eq!(snap_key("vn", "VCB", "1D"), "snap:vn:VCB:1D");
        assert_eq!(snap_key("crypto", "BTCUSDT", "1h"), "snap:crypto:BTCUSDT:1h");
        assert_eq!(snap_field(1, "sma"), "1:sma");
        assert_eq!(snap_field(40, "ema"), "40:ema");
    }
}
