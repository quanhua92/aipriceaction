use sqlx::PgPool;

use crate::models::ohlcv::{IndicatorRow, OhlcvRow};

/// Insert ticker if not exists, return the id.
pub async fn upsert_ticker(
    pool: &PgPool,
    source: &str,
    ticker: &str,
    name: Option<&str>,
) -> sqlx::Result<i32> {
    let row = sqlx::query_scalar!(
        r#"INSERT INTO tickers (source, ticker, name) VALUES ($1, $2, $3)
           ON CONFLICT (source, ticker) DO UPDATE SET name = COALESCE($3, tickers.name)
           RETURNING id"#,
        source,
        ticker,
        name
    )
    .fetch_one(pool)
    .await?;
    Ok(row)
}

/// Upsert a single OHLCV row.
pub async fn upsert_ohlcv(pool: &PgPool, row: &OhlcvRow) -> sqlx::Result<()> {
    sqlx::query!(
        r#"INSERT INTO ohlcv (ticker_id, interval, time, open, high, low, close, volume, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
           ON CONFLICT (ticker_id, interval, time) DO UPDATE SET
             open = EXCLUDED.open, high = EXCLUDED.high,
             low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume,
             updated_at = NOW()"#,
        row.ticker_id,
        row.interval,
        row.time,
        row.open,
        row.high,
        row.low,
        row.close,
        row.volume
    )
    .execute(pool)
    .await?;
    Ok(())
}

/// Upsert a batch of OHLCV rows.
pub async fn upsert_ohlcv_batch(pool: &PgPool, rows: &[OhlcvRow]) -> sqlx::Result<()> {
    for row in rows {
        upsert_ohlcv(pool, row).await?;
    }
    Ok(())
}

/// Upsert a single indicator row.
pub async fn upsert_indicator(pool: &PgPool, row: &IndicatorRow) -> sqlx::Result<()> {
    sqlx::query!(
        r#"INSERT INTO ohlcv_indicators (ticker_id, interval, time,
             ma10, ma20, ma50, ma100, ma200,
             ma10_score, ma20_score, ma50_score, ma100_score, ma200_score,
             close_changed, volume_changed, total_money_changed, processed_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,NOW())
           ON CONFLICT (ticker_id, interval, time) DO UPDATE SET
             ma10=EXCLUDED.ma10, ma20=EXCLUDED.ma20, ma50=EXCLUDED.ma50,
             ma100=EXCLUDED.ma100, ma200=EXCLUDED.ma200,
             ma10_score=EXCLUDED.ma10_score, ma20_score=EXCLUDED.ma20_score,
             ma50_score=EXCLUDED.ma50_score, ma100_score=EXCLUDED.ma100_score,
             ma200_score=EXCLUDED.ma200_score,
             close_changed=EXCLUDED.close_changed, volume_changed=EXCLUDED.volume_changed,
             total_money_changed=EXCLUDED.total_money_changed,
             processed_at=NOW()"#,
        row.ticker_id,
        row.interval,
        row.time,
        row.ma10,
        row.ma20,
        row.ma50,
        row.ma100,
        row.ma200,
        row.ma10_score,
        row.ma20_score,
        row.ma50_score,
        row.ma100_score,
        row.ma200_score,
        row.close_changed,
        row.volume_changed,
        row.total_money_changed
    )
    .execute(pool)
    .await?;
    Ok(())
}

/// Upsert a batch of indicator rows.
pub async fn upsert_indicators_batch(pool: &PgPool, rows: &[IndicatorRow]) -> sqlx::Result<()> {
    for row in rows {
        upsert_indicator(pool, row).await?;
    }
    Ok(())
}
