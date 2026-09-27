use chrono::{DateTime, Utc};
use sqlx::{FromRow, PgPool};

use crate::models::ohlcv::{OhlcvRow, Ticker};

#[derive(Debug, FromRow)]
pub struct BackfillJob {
    pub ticker_id: i32,
    pub ticker: String,
    pub interval: String,
    pub before_time: Option<DateTime<Utc>>,
    pub attempts: i32,
}

/// Work on stale daily data before any historical intraday job. The oldest
/// daily tail wins within each priority group; new ticker bootstraps are last.
pub async fn next_daily_recovery(pool: &PgPool) -> sqlx::Result<Option<Ticker>> {
    sqlx::query_as::<_, Ticker>(
        r#"SELECT t.id, t.source, t.ticker, t.name, t.status, t.next_1d
           FROM tickers t
           WHERE t.source = 'vn'
             AND t.status IN ('daily-recovered-pending-cache', 'dividend-detected',
                              'full-download-processing', 'full-download-requested')
             AND t.next_1d <= NOW()
           ORDER BY CASE t.status
                      WHEN 'daily-recovered-pending-cache' THEN 0
                      WHEN 'dividend-detected' THEN 1
                      WHEN 'full-download-processing' THEN 2
                      ELSE 3 END,
                    (SELECT MAX(o.time) FROM ohlcv o
                     WHERE o.ticker_id = t.id AND o.interval = '1D') ASC NULLS LAST,
                    t.ticker
           LIMIT 1"#,
    )
    .fetch_optional(pool)
    .await
}

/// Atomically replace the daily series, remove old-adjustment intraday bars,
/// and create durable background jobs. The ticker remains blocked until its
/// Redis data has been invalidated, including after a process restart.
pub async fn replace_daily_and_queue(
    pool: &PgPool,
    ticker_id: i32,
    rows: &[OhlcvRow],
) -> sqlx::Result<u64> {
    if rows.is_empty() {
        return Err(sqlx::Error::RowNotFound);
    }

    let mut tx = pool.begin().await?;
    let status: Option<String> =
        sqlx::query_scalar("SELECT status FROM tickers WHERE id = $1 FOR UPDATE")
            .bind(ticker_id)
            .fetch_one(&mut *tx)
            .await?;
    if status.as_deref() != Some("full-download-processing") {
        return Err(sqlx::Error::RowNotFound);
    }

    let removed =
        sqlx::query("DELETE FROM ohlcv WHERE ticker_id = $1 AND interval IN ('1D', '1h', '1m')")
            .bind(ticker_id)
            .execute(&mut *tx)
            .await?
            .rows_affected();

    let times: Vec<DateTime<Utc>> = rows.iter().map(|r| r.time).collect();
    let opens: Vec<f64> = rows.iter().map(|r| r.open).collect();
    let highs: Vec<f64> = rows.iter().map(|r| r.high).collect();
    let lows: Vec<f64> = rows.iter().map(|r| r.low).collect();
    let closes: Vec<f64> = rows.iter().map(|r| r.close).collect();
    let volumes: Vec<i64> = rows.iter().map(|r| r.volume).collect();

    sqlx::query(
        r#"INSERT INTO ohlcv
              (ticker_id, interval, time, open, high, low, close, volume, updated_at)
           SELECT $1, '1D', u.time, u.open, u.high, u.low, u.close, u.volume, NOW()
           FROM UNNEST($2::timestamptz[], $3::float8[], $4::float8[],
                       $5::float8[], $6::float8[], $7::bigint[])
                AS u(time, open, high, low, close, volume)"#,
    )
    .bind(ticker_id)
    .bind(&times)
    .bind(&opens)
    .bind(&highs)
    .bind(&lows)
    .bind(&closes)
    .bind(&volumes)
    .execute(&mut *tx)
    .await?;

    sqlx::query(
        r#"INSERT INTO vci_backfill_jobs (ticker_id, interval)
           VALUES ($1, '1h'), ($1, '1m')
           ON CONFLICT (ticker_id, interval) DO UPDATE SET
             before_time = NULL, retry_at = NOW(), attempts = 0,
             completed_at = NULL, updated_at = NOW()"#,
    )
    .bind(ticker_id)
    .execute(&mut *tx)
    .await?;

    sqlx::query("UPDATE tickers SET status = 'daily-recovered-pending-cache' WHERE id = $1")
        .bind(ticker_id)
        .execute(&mut *tx)
        .await?;

    tx.commit().await?;
    Ok(removed)
}

pub async fn mark_daily_ready(pool: &PgPool, ticker_id: i32) -> sqlx::Result<()> {
    let result = sqlx::query(
        r#"UPDATE tickers
           SET status = 'ready', next_1d = NOW(), next_1h = NOW(), next_1m = NOW()
           WHERE id = $1 AND status = 'daily-recovered-pending-cache'"#,
    )
    .bind(ticker_id)
    .execute(pool)
    .await?;
    if result.rows_affected() != 1 {
        return Err(sqlx::Error::RowNotFound);
    }
    Ok(())
}

pub async fn defer_daily(pool: &PgPool, ticker_id: i32) -> sqlx::Result<()> {
    sqlx::query("UPDATE tickers SET next_1d = NOW() + INTERVAL '60 seconds' WHERE id = $1")
        .bind(ticker_id)
        .execute(pool)
        .await?;
    Ok(())
}

/// One page is processed per worker loop, allowing new daily repairs to take
/// priority even when a long minute job is in progress.
pub async fn next_backfill_job(pool: &PgPool) -> sqlx::Result<Option<BackfillJob>> {
    sqlx::query_as::<_, BackfillJob>(
        r#"SELECT j.ticker_id, t.ticker, j.interval, j.before_time, j.attempts
           FROM vci_backfill_jobs j
           JOIN tickers t ON t.id = j.ticker_id
           WHERE t.source = 'vn' AND t.status = 'ready'
             AND j.completed_at IS NULL AND j.retry_at <= NOW()
           ORDER BY CASE WHEN j.interval = '1h' THEN 0 ELSE 1 END,
                    j.updated_at, t.ticker
           LIMIT 1"#,
    )
    .fetch_optional(pool)
    .await
}

pub async fn advance_backfill(
    pool: &PgPool,
    job: &BackfillJob,
    before_time: DateTime<Utc>,
) -> sqlx::Result<()> {
    sqlx::query(
        r#"UPDATE vci_backfill_jobs
           SET before_time = $3, retry_at = NOW(), attempts = 0, updated_at = NOW()
           WHERE ticker_id = $1 AND interval = $2 AND completed_at IS NULL"#,
    )
    .bind(job.ticker_id)
    .bind(&job.interval)
    .bind(before_time)
    .execute(pool)
    .await?;
    Ok(())
}

pub async fn defer_backfill(
    pool: &PgPool,
    job: &BackfillJob,
    retry_at: DateTime<Utc>,
) -> sqlx::Result<()> {
    sqlx::query(
        r#"UPDATE vci_backfill_jobs
           SET attempts = attempts + 1, retry_at = $3, updated_at = NOW()
           WHERE ticker_id = $1 AND interval = $2 AND completed_at IS NULL"#,
    )
    .bind(job.ticker_id)
    .bind(&job.interval)
    .bind(retry_at)
    .execute(pool)
    .await?;
    Ok(())
}

pub async fn complete_backfill(pool: &PgPool, job: &BackfillJob) -> sqlx::Result<()> {
    sqlx::query(
        r#"UPDATE vci_backfill_jobs
           SET completed_at = NOW(), updated_at = NOW()
           WHERE ticker_id = $1 AND interval = $2 AND completed_at IS NULL"#,
    )
    .bind(job.ticker_id)
    .bind(&job.interval)
    .execute(pool)
    .await?;
    Ok(())
}
