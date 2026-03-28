use sqlx::PgPool;

use crate::models::ohlcv::{IndicatorRow, OhlcvRow};
use crate::queries::ohlcv;

/// Ensure ticker exists, return its id.
pub async fn ensure_ticker(pool: &PgPool, source: &str, ticker: &str) -> sqlx::Result<i32> {
    ohlcv::upsert_ticker(pool, source, ticker, None).await
}

/// Upsert OHLCV data for a ticker. Caller must provide rows with correct ticker_id.
pub async fn save_ohlcv(pool: &PgPool, rows: &[OhlcvRow]) -> sqlx::Result<()> {
    ohlcv::upsert_ohlcv_batch(pool, rows).await
}

/// Upsert indicator data. Caller must provide rows with correct ticker_id.
pub async fn save_indicators(pool: &PgPool, rows: &[IndicatorRow]) -> sqlx::Result<()> {
    ohlcv::upsert_indicators_batch(pool, rows).await
}
