use std::collections::{HashMap, HashSet};

use chrono::{Datelike, DateTime, Timelike, Utc};
use sqlx::PgPool;

use crate::constants::vci_worker;
use crate::models::ohlcv::OhlcvRow;
use crate::providers::ohlcv::OhlcvData;
use crate::queries;

/// Load the VN ticker list from ticker_group.json.
///
/// Flattens all sector groups, prepends INDEX_TICKERS, deduplicates.
pub fn load_vn_tickers() -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string("ticker_group.json")?;
    let groups: HashMap<String, Vec<String>> = serde_json::from_str(&content)?;

    let mut seen = HashSet::new();
    let mut tickers = Vec::new();

    // Prepend index tickers
    for &idx in vci_worker::INDEX_TICKERS {
        if seen.insert(idx.to_string()) {
            tickers.push(idx.to_string());
        }
    }

    // Flatten all groups
    for (_sector, symbols) in &groups {
        for sym in symbols {
            if seen.insert(sym.clone()) {
                tickers.push(sym.clone());
            }
        }
    }

    Ok(tickers)
}

/// Sync tickers from ticker_group.json into the database.
///
/// Any ticker present in the JSON file but missing from the DB is upserted
/// with status 'ready'.  Returns the number of newly added tickers.
pub async fn sync_tickers_from_json(pool: &PgPool) -> usize {
    let json_tickers = match load_vn_tickers() {
        Ok(t) => t,
        Err(e) => {
            tracing::warn!("failed to load ticker_group.json: {e}");
            return 0;
        }
    };

    let mut added = 0usize;
    for ticker in &json_tickers {
        if let Err(e) = queries::ohlcv::upsert_ticker(pool, "vn", ticker, None).await {
            tracing::warn!(ticker, "failed to upsert ticker from json: {e}");
            continue;
        }
        // If the ticker was freshly inserted it will have no status (NULL) or a
        // non-ready status — the upsert_ticker function doesn't set status.
        // We rely on the ON CONFLICT path which won't change an existing status,
        // so we only need to care about brand-new rows.  A simple UPDATE is cheap
        // enough to run unconditionally for ready.
        // NOTE: set_ticker_ready_if_new hardcodes source='vn' — will NOT affect crypto tickers.
        if let Err(e) = queries::ohlcv::set_ticker_ready_if_new(pool, ticker).await {
            tracing::warn!(ticker, source = "vn", "failed to set ticker ready: {e}");
        }
        added += 1;
    }
    added
}

/// Ensure a VN ticker exists in the database, return its id.
pub async fn ensure_vn_ticker(pool: &PgPool, source: &str, ticker: &str) -> sqlx::Result<i32> {
    queries::ohlcv::upsert_ticker(pool, source, ticker, None)
        .await
}

/// Get the latest timestamp for a ticker + interval.
pub async fn get_last_time(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
) -> Option<DateTime<Utc>> {
    queries::ohlcv::get_latest_time(pool, ticker_id, interval)
        .await
        .unwrap_or(None)
}

/// Bulk-upsert OHLCV rows.
///
/// Indicators are no longer stored in the database — they are calculated
/// on-the-fly at query time.
pub async fn enhance_and_save(
    pool: &PgPool,
    ticker_id: i32,
    data: &[OhlcvData],
    interval: &str,
    source: &str,
    ticker: &str,
    redis_client: &Option<crate::redis::RedisClient>,
) -> bool {
    if data.is_empty() {
        return true;
    }

    // Sort chronologically (should already be sorted, but be safe)
    let mut sorted = data.to_vec();
    sorted.sort_by_key(|d| d.time);

    let ohlcv_rows: Vec<OhlcvRow> = sorted
        .iter()
        .map(|d| OhlcvRow {
            ticker_id,
            interval: interval.to_string(),
            time: normalize_time(d.time, interval),
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
            volume: d.volume as i64,
        })
        .collect();

    // Deduplicate: some providers (e.g. Yahoo) return multiple rows that
    // normalize to the same timestamp. ON CONFLICT DO UPDATE can only
    // resolve one conflict per unique key per statement, so we must
    // collapse duplicates before upserting. Keep the last occurrence.
    let orig_len = ohlcv_rows.len();

    let deduped: Vec<OhlcvRow> = {
        let mut map = HashMap::new();
        for row in ohlcv_rows {
            map.insert(row.time, row);
        }
        let mut rows: Vec<_> = map.into_values().collect();
        rows.sort_by_key(|r| r.time);
        rows
    };

    if deduped.len() < orig_len {
        tracing::warn!(
            ticker_id, interval,
            dropped = orig_len - deduped.len(),
            "deduplicated normalized timestamps before upsert"
        );
    }

    if let Err(e) = queries::import::bulk_upsert_ohlcv(pool, &deduped).await {
        tracing::error!(ticker_id, interval, "bulk_upsert_ohlcv failed: {e}");
        return false;
    } else if redis_client.is_some() {
        // Fire-and-forget Redis TS write.
        // Errors are logged internally by write_ohlcv_to_redis.
        let redis = redis_client.clone();
        let src = source.to_string();
        let tk = ticker.to_string();
        let iv = interval.to_string();
        let rows = deduped.clone();
        tokio::spawn(async move {
            crate::workers::redis_worker::write_ohlcv_to_redis(&redis, &src, &tk, &iv, &rows).await;
            crate::workers::redis_worker::invalidate_snapshot(&redis, &src, &tk, &iv).await;
        });
    }
    true
}

/// Normalize a timestamp to the start of its interval boundary.
///
/// This ensures consistent timestamps so that the `(ticker_id, interval, time)` PK
/// constraint deduplicates rows correctly. Some providers (e.g. Yahoo Finance) return
/// slightly different timestamptz values for the same daily bar on each fetch, causing
/// duplicate rows that only differ by seconds.
///
/// - `"1D"` → midnight UTC
/// - `"1h"` → top of the hour (seconds/nanoseconds zeroed)
/// - `"1m"` → top of the minute (seconds/nanoseconds zeroed)
fn normalize_time(time: DateTime<Utc>, interval: &str) -> DateTime<Utc> {
    match interval {
        "1D" => time
            .with_hour(0).unwrap()
            .with_minute(0).unwrap()
            .with_second(0).unwrap()
            .with_nanosecond(0).unwrap(),
        "1h" => time
            .with_minute(0).unwrap()
            .with_second(0).unwrap()
            .with_nanosecond(0).unwrap(),
        "1m" => time
            .with_second(0).unwrap()
            .with_nanosecond(0).unwrap(),
        _ => time,
    }
}

/// Detect dividend by comparing new API data with existing DB data.
///
/// Returns true if a dividend was detected (and status updated).
pub async fn detect_dividend(
    pool: &PgPool,
    ticker_id: i32,
    ticker: &str,
    new_data: &[OhlcvData],
) -> bool {
    // Skip for index tickers
    if vci_worker::INDEX_TICKERS.contains(&ticker) {
        return false;
    }

    if new_data.len() < 2 {
        return false;
    }

    // Exclude the last candle — it may be today's intraday data saved by a
    // previous sync cycle and can diverge due to normal price movement.
    let compare_data = &new_data[..new_data.len() - 1];

    // Get existing daily data from DB
    let existing = match queries::ohlcv::get_ohlcv(pool, ticker_id, "1D", Some(vci_worker::DIVIDEND_CHECK_BARS)).await {
        Ok(rows) => rows,
        Err(_) => return false,
    };

    if existing.is_empty() {
        return false;
    }

    // Build a map of date -> close from existing DB data
    let mut existing_map: HashMap<String, f64> = HashMap::new();
    for row in &existing {
        let date_key = row.time.format("%Y-%m-%d").to_string();
        existing_map.insert(date_key, row.close);
    }

    // Compare overlapping dates — find the worst (largest) divergence
    let mut max_ratio: f64 = 0.0;
    let mut worst_date = String::new();
    let mut worst_existing_close = 0.0;
    let mut worst_api_close = 0.0;
    let mut divergence_count = 0usize;

    for d in compare_data {
        let date_key = d.time.format("%Y-%m-%d").to_string();
        if let Some(&existing_close) = existing_map.get(&date_key) {
            if existing_close > 0.0 && d.close > 0.0 {
                let ratio = existing_close / d.close;
                if ratio > vci_worker::DIVIDEND_RATIO_THRESHOLD {
                    divergence_count += 1;
                    if ratio > max_ratio {
                        max_ratio = ratio;
                        worst_date = date_key;
                        worst_existing_close = existing_close;
                        worst_api_close = d.close;
                    }
                }
            }
        }
    }

    if max_ratio > vci_worker::DIVIDEND_RATIO_THRESHOLD {
        if divergence_count < vci_worker::DIVIDEND_MIN_DIVERGING_BARS {
            tracing::warn!(
                "[DIVIDEND] ticker={}, SUSPECTED but REJECTED — diverging_dates={} < min_required={}, worst_ratio={:.4}, worst_date={}",
                ticker, divergence_count, vci_worker::DIVIDEND_MIN_DIVERGING_BARS, max_ratio, worst_date
            );
            return false;
        }
        let price_drop_pct = (1.0 - worst_api_close / worst_existing_close) * 100.0;
        tracing::warn!(
            "[DIVIDEND] ticker={}, date={}, db_close={}, api_close={}, ratio={:.4}, drop={:.2}%, diverging_dates={}, min_required={}, threshold={:.2}, compared_bars={}, db_bars={}",
            ticker, worst_date, worst_existing_close, worst_api_close, max_ratio, price_drop_pct,
            divergence_count, vci_worker::DIVIDEND_MIN_DIVERGING_BARS, vci_worker::DIVIDEND_RATIO_THRESHOLD, compare_data.len(), existing.len()
        );
        tracing::warn!(
            "[DIVIDEND] ticker={}, action=set status 'dividend-detected' → dividend worker will delete ALL data and re-download full history (1D from 2015, 1h/1m from 2023)",
            ticker
        );
        if let Err(e) = queries::ohlcv::update_ticker_status(pool, ticker_id, "dividend-detected").await {
            tracing::error!("[DIVIDEND] ticker={}, ticker_id={}, FAILED to set dividend-detected status: {}", ticker, ticker_id, e);
        } else {
            tracing::warn!("[DIVIDEND] ticker={}, ticker_id={}, status updated to 'dividend-detected' successfully", ticker, ticker_id);
        }
        return true;
    }

    false
}

/// Check if current time is within VN trading hours (9:00-15:00 ICT = 2:00-8:00 UTC).
pub fn is_trading_hours() -> bool {
    let now = chrono::Utc::now();
    // VN trading hours: 9:00-15:00 ICT = 2:00-8:00 UTC
    let hour = now.hour();
    // Only on weekdays (Mon=1 .. Fri=5)
    let weekday = now.weekday().num_days_from_monday(); // 0=Mon, 6=Sun
    weekday < 5 && hour >= 2 && hour < 8
}
