use sqlx::PgPool;

use crate::models::ohlcv::{IndicatorRow, OhlcvRow};

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

/// Bulk upsert indicator rows using PostgreSQL UNNEST.
pub async fn bulk_upsert_indicators(pool: &PgPool, rows: &[IndicatorRow]) -> sqlx::Result<()> {
    if rows.is_empty() {
        return Ok(());
    }

    let n = rows.len();
    let ticker_ids: Vec<i32> = rows.iter().map(|r| r.ticker_id).collect();
    let intervals: Vec<&str> = rows.iter().map(|r| r.interval.as_str()).collect();
    let times: Vec<_> = rows.iter().map(|r| r.time).collect();
    let ma10: Vec<Option<f64>> = rows.iter().map(|r| r.ma10).collect();
    let ma20: Vec<Option<f64>> = rows.iter().map(|r| r.ma20).collect();
    let ma50: Vec<Option<f64>> = rows.iter().map(|r| r.ma50).collect();
    let ma100: Vec<Option<f64>> = rows.iter().map(|r| r.ma100).collect();
    let ma200: Vec<Option<f64>> = rows.iter().map(|r| r.ma200).collect();
    let ma10_score: Vec<Option<f64>> = rows.iter().map(|r| r.ma10_score).collect();
    let ma20_score: Vec<Option<f64>> = rows.iter().map(|r| r.ma20_score).collect();
    let ma50_score: Vec<Option<f64>> = rows.iter().map(|r| r.ma50_score).collect();
    let ma100_score: Vec<Option<f64>> = rows.iter().map(|r| r.ma100_score).collect();
    let ma200_score: Vec<Option<f64>> = rows.iter().map(|r| r.ma200_score).collect();
    let close_changed: Vec<Option<f64>> = rows.iter().map(|r| r.close_changed).collect();
    let volume_changed: Vec<Option<f64>> = rows.iter().map(|r| r.volume_changed).collect();
    let total_money_changed: Vec<Option<f64>> = rows.iter().map(|r| r.total_money_changed).collect();

    sqlx::query(
        r#"INSERT INTO ohlcv_indicators (ticker_id, interval, time,
               ma10, ma20, ma50, ma100, ma200,
               ma10_score, ma20_score, ma50_score, ma100_score, ma200_score,
               close_changed, volume_changed, total_money_changed, processed_at)
           SELECT * FROM UNNEST(
               $1::int[], $2::text[], $3::timestamptz[],
               $4::float8[], $5::float8[], $6::float8[], $7::float8[], $8::float8[],
               $9::float8[], $10::float8[], $11::float8[], $12::float8[], $13::float8[],
               $14::float8[], $15::float8[], $16::float8[], $17::timestamptz[]
           )
           ON CONFLICT (ticker_id, interval, time) DO UPDATE SET
             ma10=EXCLUDED.ma10, ma20=EXCLUDED.ma20, ma50=EXCLUDED.ma50,
             ma100=EXCLUDED.ma100, ma200=EXCLUDED.ma200,
             ma10_score=EXCLUDED.ma10_score, ma20_score=EXCLUDED.ma20_score,
             ma50_score=EXCLUDED.ma50_score, ma100_score=EXCLUDED.ma100_score,
             ma200_score=EXCLUDED.ma200_score,
             close_changed=EXCLUDED.close_changed, volume_changed=EXCLUDED.volume_changed,
             total_money_changed=EXCLUDED.total_money_changed,
             processed_at=NOW()"#,
    )
    .bind(&ticker_ids)
    .bind(&intervals)
    .bind(&times)
    .bind(&ma10)
    .bind(&ma20)
    .bind(&ma50)
    .bind(&ma100)
    .bind(&ma200)
    .bind(&ma10_score)
    .bind(&ma20_score)
    .bind(&ma50_score)
    .bind(&ma100_score)
    .bind(&ma200_score)
    .bind(&close_changed)
    .bind(&volume_changed)
    .bind(&total_money_changed)
    .bind(&vec![chrono::Utc::now(); n])
    .execute(pool)
    .await?;

    Ok(())
}
