use sqlx::PgPool;
use std::sync::Arc;
use std::time::Duration;

use crate::server::HealthSnapshot;

/// Refresh interval for the health stats snapshot.
const REFRESH_INTERVAL_SECS: u64 = 30;

pub async fn run(
    pool: PgPool,
    snapshot: Arc<tokio::sync::RwLock<HealthSnapshot>>,
) {
    loop {
        match refresh(&pool).await {
            Ok(snap) => {
                let mut guard = snapshot.write().await;
                *guard = snap;
            }
            Err(e) => {
                tracing::warn!("health worker: failed to refresh snapshot: {e}");
            }
        }

        tokio::time::sleep(Duration::from_secs(REFRESH_INTERVAL_SECS)).await;
    }
}

#[derive(sqlx::FromRow)]
struct HealthRow {
    ticker_count: i64,
    active_tickers: i64,
    daily_records: i64,
    hourly_records: i64,
    minute_records: i64,
}

async fn refresh(pool: &PgPool) -> Result<HealthSnapshot, sqlx::Error> {
    use chrono::{Datelike, Timelike};

    let now = chrono::Utc::now();
    let is_trading = now.weekday().num_days_from_monday() < 5
        && now.hour() >= 2 && now.hour() < 8;

    let year = now.year();
    let hourly_table = format!("ohlcv_hourly_{year}");
    let minute_table = format!("ohlcv_minute_{year}");

    // Resolve major ticker IDs
    let major_tickers: Vec<i32> = if is_trading {
        sqlx::query_scalar(
            "SELECT id FROM tickers WHERE source = 'vn' AND ticker = ANY($1) ORDER BY id",
        )
        .bind(&crate::constants::MAJOR_VN[..])
        .fetch_all(pool)
        .await
        .unwrap_or_default()
    } else {
        let mut ids: Vec<i32> = sqlx::query_scalar(
            "SELECT id FROM tickers WHERE ticker = ANY($1) ORDER BY id",
        )
        .bind(&crate::constants::MAJOR_GLOBAL[..])
        .fetch_all(pool)
        .await
        .unwrap_or_default();
        ids.extend(
            sqlx::query_scalar::<_, i32>(
                "SELECT id FROM tickers WHERE ticker = ANY($1) ORDER BY id",
            )
            .bind(&crate::constants::MAJOR_CRYPTO[..])
            .fetch_all(pool)
            .await
            .unwrap_or_default(),
        );
        ids
    };

    // Run stats query + sync-time queries in parallel
    let stats_fut = sqlx::query_as::<_, HealthRow>(
        r#"SELECT
               array_length(t.ids, 1)::bigint AS ticker_count,
               (SELECT COUNT(DISTINCT o2.ticker_id)::bigint FROM ohlcv_daily o2
                JOIN tickers t2 ON t2.id = o2.ticker_id AND t2.source = t.source
                WHERE o2.time > NOW() - INTERVAL '7 days') AS active_tickers,
               (SELECT GREATEST(reltuples, 0)::bigint FROM pg_class
                WHERE relname = 'ohlcv_daily') AS daily_records,
               (SELECT COALESCE(SUM(GREATEST(reltuples, 0)), 0)::bigint
                FROM pg_class WHERE relname LIKE 'ohlcv_hourly_%' AND relkind = 'r')
               AS hourly_records,
               (SELECT COALESCE(SUM(GREATEST(reltuples, 0)), 0)::bigint
                FROM pg_class WHERE relname LIKE 'ohlcv_minute_%' AND relkind = 'r')
               AS minute_records
           FROM (SELECT source, array_agg(id) AS ids FROM tickers GROUP BY source) t"#,
    )
    .fetch_all(pool);

    let daily_sync_fut = sqlx::query_scalar::<_, Option<chrono::DateTime<chrono::Utc>>>(
        "SELECT MAX(updated_at) FROM (SELECT DISTINCT ON (ticker_id) updated_at FROM ohlcv_daily WHERE ticker_id = ANY($1) AND interval = '1D' ORDER BY ticker_id, time DESC) s",
    )
    .bind(&major_tickers)
    .fetch_optional(pool);

    let hourly_sql = format!("SELECT MAX(updated_at) FROM (SELECT DISTINCT ON (ticker_id) updated_at FROM {hourly_table} WHERE ticker_id = ANY($1) AND interval = '1h' ORDER BY ticker_id, time DESC) s");
    let hourly_sync_fut = sqlx::query_scalar::<_, Option<chrono::DateTime<chrono::Utc>>>(
        &hourly_sql,
    )
    .bind(&major_tickers)
    .fetch_optional(pool);

    let minute_sql = format!("SELECT MAX(updated_at) FROM (SELECT DISTINCT ON (ticker_id) updated_at FROM {minute_table} WHERE ticker_id = ANY($1) AND interval = '1m' ORDER BY ticker_id, time DESC) s");
    let minute_sync_fut = sqlx::query_scalar::<_, Option<chrono::DateTime<chrono::Utc>>>(
        &minute_sql,
    )
    .bind(&major_tickers)
    .fetch_optional(pool);

    let (stats_res, daily_sync_res, hourly_sync_res, minute_sync_res) =
        tokio::join!(stats_fut, daily_sync_fut, hourly_sync_fut, minute_sync_fut);

    let stats = stats_res?;

    let mut total_tickers = 0i64;
    let mut active_tickers = 0i64;
    let mut daily_records = 0i64;
    let mut hourly_records = 0i64;
    let mut minute_records = 0i64;

    for row in &stats {
        total_tickers += row.ticker_count;
        active_tickers += row.active_tickers;
        daily_records += row.daily_records;
        hourly_records += row.hourly_records;
        minute_records += row.minute_records;
    }

    let daily_last_sync = daily_sync_res.ok().flatten().flatten();
    let hourly_last_sync = hourly_sync_res.ok().flatten().flatten();
    let minute_last_sync = minute_sync_res.ok().flatten().flatten();

    Ok(HealthSnapshot {
        total_tickers,
        active_tickers,
        daily_records,
        hourly_records,
        minute_records,
        daily_last_sync,
        hourly_last_sync,
        minute_last_sync,
    })
}
