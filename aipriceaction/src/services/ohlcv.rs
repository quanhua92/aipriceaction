use chrono::{Duration, Utc};
use sqlx::PgPool;

use crate::models::ohlcv::{OhlcvJoined, OhlcvRow, Ticker};
use crate::queries::{import, ohlcv};

/// Ensure ticker exists, return its id.
pub async fn ensure_ticker(pool: &PgPool, source: &str, ticker: &str) -> sqlx::Result<i32> {
    ohlcv::upsert_ticker(pool, source, ticker, None).await
}

/// Upsert OHLCV data for a ticker. Caller must provide rows with correct ticker_id.
pub async fn save_ohlcv(pool: &PgPool, rows: &[OhlcvRow]) -> sqlx::Result<()> {
    import::bulk_upsert_ohlcv(pool, rows).await
}

// ── Read methods ──

/// Find a ticker by source + symbol.
pub async fn get_ticker(pool: &PgPool, source: &str, ticker: &str) -> sqlx::Result<Option<Ticker>> {
    ohlcv::get_ticker(pool, source, ticker).await
}

/// List all tickers for a source.
pub async fn list_tickers(pool: &PgPool, source: &str) -> sqlx::Result<Vec<Ticker>> {
    ohlcv::list_tickers(pool, source).await
}

/// Get OHLCV rows for a ticker + interval, newest first.
pub async fn get_ohlcv(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
    limit: Option<i64>,
) -> sqlx::Result<Vec<OhlcvRow>> {
    ohlcv::get_ohlcv(pool, ticker_id, interval, limit).await
}

/// Get joined OHLCV + indicators matching the 20-column CSV format.
///
/// Indicators are calculated in-memory from OHLCV data at query time.
///
/// For `1h` and `1m` intervals without an explicit date range, uses a
/// progressive date-range heuristic: queries a small window first, then
/// expands until enough rows are collected. This enables PostgreSQL
/// partition pruning and avoids expensive merge-sorts across year partitions.
///
/// For `1D` (no sub-partitions) or when no limit is requested, delegates
/// directly to the simple query.
pub async fn get_ohlcv_joined(
    pool: &PgPool,
    source: &str,
    ticker: &str,
    interval: &str,
    limit: Option<i64>,
) -> sqlx::Result<Vec<OhlcvJoined>> {
    if matches!(interval, "1h" | "1m") && limit.is_some() {
        return progressive_query(pool, source, ticker, interval, limit, None, None).await;
    }
    ohlcv::get_ohlcv_joined(pool, source, ticker, interval, limit).await
}

/// Get joined OHLCV + indicators with optional date range.
///
/// When a broad range + limit is provided for `1h`/`1m` intervals, uses
/// progressive window expansion (clamped to the user's original bounds)
/// to enable partition pruning.
pub async fn get_ohlcv_joined_range(
    pool: &PgPool,
    source: &str,
    ticker: &str,
    interval: &str,
    limit: Option<i64>,
    start_time: Option<chrono::DateTime<chrono::Utc>>,
    end_time: Option<chrono::DateTime<chrono::Utc>>,
) -> sqlx::Result<Vec<OhlcvJoined>> {
    if matches!(interval, "1h" | "1m") && limit.is_some() {
        return progressive_query(pool, source, ticker, interval, limit, start_time, end_time).await;
    }
    ohlcv::get_ohlcv_joined_range(pool, source, ticker, interval, limit, start_time, end_time).await
}

/// Progressive date-range expansion for `1h`/`1m` intervals.
///
/// Starts with a narrow window anchored to `end_time` (defaults to now) and
/// expands until `>= limit` rows are found. The window is clamped to the
/// user's `[start_time, end_time]` bounds if provided.
fn progressive_windows(
    interval: &str,
    end_time: chrono::DateTime<chrono::Utc>,
    start_time: Option<chrono::DateTime<chrono::Utc>>,
) -> Vec<chrono::DateTime<chrono::Utc>> {
    let windows: &[i64] = if interval == "1m" {
        &[30, 90, 365, 730]
    } else {
        &[30, 365]
    };

    windows
        .iter()
        .map(|&days| {
            let w = end_time - Duration::days(days);
            match start_time {
                Some(s) if w < s => s,
                _ => w,
            }
        })
        .collect()
}

async fn progressive_query(
    pool: &PgPool,
    source: &str,
    ticker: &str,
    interval: &str,
    limit: Option<i64>,
    start_time: Option<chrono::DateTime<chrono::Utc>>,
    end_time: Option<chrono::DateTime<chrono::Utc>>,
) -> sqlx::Result<Vec<OhlcvJoined>> {
    let want = limit.unwrap() as usize;
    let end = end_time.unwrap_or_else(Utc::now);

    for effective_start in progressive_windows(interval, end, start_time) {
        // Skip if window is inverted (user range narrower than smallest window)
        if effective_start >= end {
            continue;
        }

        let rows = ohlcv::get_ohlcv_joined_range(
            pool, source, ticker, interval,
            Some(want as i64), Some(effective_start), end_time,
        )
        .await?;

        if rows.len() >= want {
            return Ok(rows);
        }
    }

    // Fallback: use the user's original range (or no range if none given).
    ohlcv::get_ohlcv_joined_range(pool, source, ticker, interval, limit, start_time, end_time).await
}

/// Count tickers for a source.
pub async fn count_tickers(pool: &PgPool, source: &str) -> sqlx::Result<i64> {
    ohlcv::count_tickers(pool, source).await
}

/// Count OHLCV rows for a source, optionally filtered by ticker/interval.
pub async fn count_ohlcv(
    pool: &PgPool,
    source: &str,
    ticker: Option<&str>,
    interval: Option<&str>,
) -> sqlx::Result<i64> {
    ohlcv::count_ohlcv(pool, source, ticker, interval).await
}

/// Batch-fetch joined OHLCV + indicators for tickers of a source + interval.
///
/// When `symbols` is empty, fetches ALL tickers for the source.
/// When `symbols` is non-empty, fetches only the specified tickers.
pub async fn get_ohlcv_joined_batch(
    pool: &PgPool,
    source: &str,
    symbols: &[String],
    interval: &str,
    limit: Option<i64>,
    start_time: Option<chrono::DateTime<chrono::Utc>>,
    end_time: Option<chrono::DateTime<chrono::Utc>>,
) -> sqlx::Result<std::collections::HashMap<String, Vec<OhlcvJoined>>> {
    ohlcv::get_ohlcv_joined_batch(pool, source, symbols, interval, limit, start_time, end_time).await
}
