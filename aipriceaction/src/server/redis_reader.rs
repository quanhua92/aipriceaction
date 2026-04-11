use std::collections::HashMap;

use fred::prelude::*;

use crate::models::ohlcv::OhlcvRow;
use crate::redis::RedisClient;
use crate::workers::redis_worker;

/// Result of reading OHLCV from Redis, with metadata for response headers.
pub struct RedisReadResult {
    pub rows: Vec<OhlcvRow>,
    /// How many raw members were returned from ZREVRANGE
    pub raw_close_count: usize,
    /// How many rows were successfully parsed
    pub aligned_count: usize,
    /// The requested limit
    pub requested_limit: i64,
    /// The base interval actually read from Redis
    pub base_interval: String,
}

/// Batch-read OHLCV from Redis for N tickers via pipelined ZREVRANGE.
/// Works for single ticker (N=1) and multi-ticker (N>1).
/// Returns `Some(HashMap<ticker, RedisReadResult>)` on success, or `None` if
/// Redis is unavailable or no data found for any ticker.
pub async fn batch_read_ohlcv_from_redis(
    client: &Option<RedisClient>,
    source: &str,
    tickers: &[String],
    interval: &str,
    total_limit: i64,
) -> Option<HashMap<String, RedisReadResult>> {
    let client = client.as_ref()?;

    if tickers.is_empty() {
        return None;
    }

    let t_read = std::time::Instant::now();

    // Pipeline N ZREVRANGE calls (1 per ticker, 1 network round-trip)
    let pipe = client.pipeline();
    for ticker in tickers {
        let key = redis_worker::zset_key(source, ticker, interval);
        // Pipeline commands are buffered (not sent until try_all).
        if let Err(e) = pipe.zrevrange::<(), _>(&key, 0, total_limit as i64 - 1, false).await {
            tracing::warn!(%key, "pipeline zrevrange enqueue error: {e}");
            continue;
        }
    }

    let results: Vec<FredResult<Value>> = pipe.try_all::<Value>().await;
    let read_ms = t_read.elapsed().as_millis();

    let t_parse = std::time::Instant::now();
    let mut parsed = HashMap::new();

    for (i, result) in results.into_iter().enumerate() {
        let value = match result {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!(ticker = %tickers[i], "ZREVRANGE error: {e}");
                continue;
            }
        };

        let members = match value {
            Value::Array(arr) => arr,
            Value::Null => continue,
            _ => continue,
        };

        let raw_count = members.len();
        // Track latest entry per bar timestamp using crawl_ts (when available).
        // Key: bar timestamp (ms), Value: (row, crawl_ts)
        let mut deduped: std::collections::HashMap<i64, (OhlcvRow, i64)> =
            std::collections::HashMap::with_capacity(raw_count);

        for member_val in &members {
            let member_str = match member_val {
                Value::Bytes(b) => std::str::from_utf8(b).ok(),
                Value::String(s) => std::str::from_utf8(s.as_bytes()).ok(),
                _ => None,
            };

            if let Some(member) = member_str {
                if let Some((row, crawl_ts)) = redis_worker::parse_member(member, interval) {
                    let ts_ms = row.time.timestamp_millis();
                    let crawl = crawl_ts.unwrap_or(0);
                    match deduped.get(&ts_ms) {
                        Some((_, prev_crawl)) if crawl > *prev_crawl => {
                            deduped.insert(ts_ms, (row, crawl));
                        }
                        None => {
                            deduped.insert(ts_ms, (row, crawl));
                        }
                        _ => {} // older entry, skip
                    }
                }
            }
        }

        // Sort by timestamp descending to match ZREVRANGE order
        let mut rows: Vec<OhlcvRow> = deduped.into_values().map(|(r, _)| r).collect();
        rows.sort_by(|a, b| b.time.cmp(&a.time));

        if rows.is_empty() {
            continue;
        }

        let aligned_count = rows.len();
        parsed.insert(
            tickers[i].clone(),
            RedisReadResult {
                rows,
                raw_close_count: raw_count,
                aligned_count,
                requested_limit: 0, // filled by caller
                base_interval: interval.to_string(),
            },
        );
    }

    let parse_ms = t_parse.elapsed().as_millis();

    if parsed.is_empty() {
        return None;
    }

    let total_rows: usize = parsed.values().map(|r| r.rows.len()).sum();
    tracing::info!(
        "[PERF] batch_read source={source} interval={interval} tickers={} total_limit={total_limit} \
         read={read_ms}ms parse={parse_ms}ms results={total_rows}",
        tickers.len(),
    );

    Some(parsed)
}
