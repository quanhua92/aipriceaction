use sqlx::PgPool;

use crate::models::ohlcv::{IndicatorRow, OhlcvJoined, OhlcvRow, Ticker};
use crate::queries::{import, ohlcv};

/// Ensure ticker exists, return its id.
pub async fn ensure_ticker(pool: &PgPool, source: &str, ticker: &str) -> sqlx::Result<i32> {
    ohlcv::upsert_ticker(pool, source, ticker, None).await
}

/// Upsert OHLCV data for a ticker. Caller must provide rows with correct ticker_id.
pub async fn save_ohlcv(pool: &PgPool, rows: &[OhlcvRow]) -> sqlx::Result<()> {
    import::bulk_upsert_ohlcv(pool, rows).await
}

/// Upsert indicator data. Caller must provide rows with correct ticker_id.
pub async fn save_indicators(pool: &PgPool, rows: &[IndicatorRow]) -> sqlx::Result<()> {
    import::bulk_upsert_indicators(pool, rows).await
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

/// Get indicator rows for a ticker + interval, newest first.
pub async fn get_indicators(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
    limit: Option<i64>,
) -> sqlx::Result<Vec<IndicatorRow>> {
    ohlcv::get_indicators(pool, ticker_id, interval, limit).await
}

/// Get joined OHLCV + indicators matching the 20-column CSV format.
pub async fn get_ohlcv_joined(
    pool: &PgPool,
    source: &str,
    ticker: &str,
    interval: &str,
    limit: Option<i64>,
) -> sqlx::Result<Vec<OhlcvJoined>> {
    ohlcv::get_ohlcv_joined(pool, source, ticker, interval, limit).await
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

/// Count indicator rows for a source, optionally filtered by ticker/interval.
pub async fn count_indicators(
    pool: &PgPool,
    source: &str,
    ticker: Option<&str>,
    interval: Option<&str>,
) -> sqlx::Result<i64> {
    ohlcv::count_indicators(pool, source, ticker, interval).await
}
