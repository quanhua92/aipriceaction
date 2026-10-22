use sqlx::PgPool;

use crate::models::ohlcv::OhlcvRow;

/// Bulk upsert OHLCV rows using PostgreSQL UNNEST.
///
/// Collects each column into a typed Vec and sends a single INSERT … SELECT * FROM UNNEST(…)
/// statement per call, instead of N individual queries.
pub async fn bulk_upsert_ohlcv(pool: &PgPool, rows: &[OhlcvRow]) -> sqlx::Result<()> {
    if rows.is_empty() {
        return Ok(());
    }

    let n = rows.len();
    let ticker_ids: Vec<i32> = rows.iter().map(|r| r.ticker_id).collect();
    let intervals: Vec<&str> = rows.iter().map(|r| r.interval.as_str()).collect();
    let times: Vec<_> = rows.iter().map(|r| r.time).collect();
    let opens: Vec<f64> = rows.iter().map(|r| r.open).collect();
    let highs: Vec<f64> = rows.iter().map(|r| r.high).collect();
    let lows: Vec<f64> = rows.iter().map(|r| r.low).collect();
    let closes: Vec<f64> = rows.iter().map(|r| r.close).collect();
    let volumes: Vec<i64> = rows.iter().map(|r| r.volume).collect();

    sqlx::query(
        r#"INSERT INTO ohlcv (ticker_id, interval, time, open, high, low, close, volume, updated_at)
           SELECT * FROM UNNEST(
               $1::int[], $2::text[], $3::timestamptz[], $4::float8[], $5::float8[],
               $6::float8[], $7::float8[], $8::bigint[], $9::timestamptz[]
           )
           ON CONFLICT (ticker_id, interval, time) DO UPDATE SET
             open = EXCLUDED.open, high = EXCLUDED.high,
             low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume,
             updated_at = NOW()"#,
    )
    .bind(&ticker_ids)
    .bind(&intervals)
    .bind(&times)
    .bind(&opens)
    .bind(&highs)
    .bind(&lows)
    .bind(&closes)
    .bind(&volumes)
    .bind(&vec![chrono::Utc::now(); n])
    .execute(pool)
    .await?;

    Ok(())
}

/// Upsert OHLCV rows preserving the existing `open` value on conflict.
///
/// Used for SJC live price updates: the first tick of the day sets `open`,
/// subsequent ticks only widen high/low and update close/volume.
pub async fn bulk_upsert_ohlcv_preserve_open(pool: &PgPool, rows: &[OhlcvRow]) -> sqlx::Result<()> {
    if rows.is_empty() {
        return Ok(());
    }

    let n = rows.len();
    let ticker_ids: Vec<i32> = rows.iter().map(|r| r.ticker_id).collect();
    let intervals: Vec<&str> = rows.iter().map(|r| r.interval.as_str()).collect();
    let times: Vec<_> = rows.iter().map(|r| r.time).collect();
    let opens: Vec<f64> = rows.iter().map(|r| r.open).collect();
    let highs: Vec<f64> = rows.iter().map(|r| r.high).collect();
    let lows: Vec<f64> = rows.iter().map(|r| r.low).collect();
    let closes: Vec<f64> = rows.iter().map(|r| r.close).collect();
    let volumes: Vec<i64> = rows.iter().map(|r| r.volume).collect();

    sqlx::query(
        r#"INSERT INTO ohlcv (ticker_id, interval, time, open, high, low, close, volume, updated_at)
           SELECT * FROM UNNEST(
               $1::int[], $2::text[], $3::timestamptz[], $4::float8[], $5::float8[],
               $6::float8[], $7::float8[], $8::bigint[], $9::timestamptz[]
           )
           ON CONFLICT (ticker_id, interval, time) DO UPDATE SET
             high = GREATEST(ohlcv.high, EXCLUDED.high),
             low = LEAST(ohlcv.low, EXCLUDED.low),
             close = EXCLUDED.close,
             volume = EXCLUDED.volume,
             updated_at = NOW()"#,
    )
    .bind(&ticker_ids)
    .bind(&intervals)
    .bind(&times)
    .bind(&opens)
    .bind(&highs)
    .bind(&lows)
    .bind(&closes)
    .bind(&volumes)
    .bind(&vec![chrono::Utc::now(); n])
    .execute(pool)
    .await?;

    Ok(())
}
