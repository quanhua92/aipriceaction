use sqlx::PgPool;
use std::sync::Arc;
use std::time::Duration;

use crate::server::HealthSnapshot;

/// Refresh interval for the health stats snapshot.
const REFRESH_INTERVAL_SECS: u64 = 30;

pub async fn run(pool: PgPool, snapshot: Arc<tokio::sync::RwLock<HealthSnapshot>>) {
    loop {
        if let Err(e) = refresh_snapshot(&pool, &snapshot).await {
            tracing::warn!("health worker: failed to refresh snapshot: {e}");
        }

        tokio::time::sleep(Duration::from_secs(REFRESH_INTERVAL_SECS)).await;
    }
}

/// Refresh a snapshot atomically, retaining the last good value on failure.
pub async fn refresh_snapshot(
    pool: &PgPool,
    snapshot: &Arc<tokio::sync::RwLock<HealthSnapshot>>,
) -> Result<(), sqlx::Error> {
    let current = refresh(pool).await?;
    *snapshot.write().await = current;
    Ok(())
}

#[derive(sqlx::FromRow)]
struct HealthRow {
    ticker_count: i64,
    active_tickers: i64,
    daily_records: i64,
    hourly_records: i64,
    minute_records: i64,
}

fn summarize_stats(stats: &[HealthRow]) -> (i64, i64, i64, i64, i64) {
    let mut total_tickers = 0;
    let mut active_tickers = 0;

    // Exact record counts are global subqueries repeated for each source row.
    // Sum only the per-source ticker counts; taking these values from the
    // first row avoids multiplying OHLCV counts by the number of sources.
    for row in stats {
        total_tickers += row.ticker_count;
        active_tickers += row.active_tickers;
    }
    let records = stats.first();

    (
        total_tickers,
        active_tickers,
        records.map_or(0, |row| row.daily_records),
        records.map_or(0, |row| row.hourly_records),
        records.map_or(0, |row| row.minute_records),
    )
}

async fn refresh(pool: &PgPool) -> Result<HealthSnapshot, sqlx::Error> {
    use chrono::{Datelike, Timelike};

    let now = chrono::Utc::now();
    let is_trading = now.weekday().num_days_from_monday() < 5 && now.hour() >= 2 && now.hour() < 8;

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
        .await?
    } else {
        let mut ids: Vec<i32> =
            sqlx::query_scalar("SELECT id FROM tickers WHERE ticker = ANY($1) ORDER BY id")
                .bind(&crate::constants::MAJOR_GLOBAL[..])
                .fetch_all(pool)
                .await?;
        ids.extend(
            sqlx::query_scalar::<_, i32>(
                "SELECT id FROM tickers WHERE ticker = ANY($1) ORDER BY id",
            )
            .bind(&crate::constants::MAJOR_CRYPTO[..])
            .fetch_all(pool)
            .await?,
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
               (SELECT COALESCE(SUM(row_count), 0)::bigint FROM ohlcv_health_counts
                WHERE interval = '1D') AS daily_records,
               (SELECT COALESCE(SUM(row_count), 0)::bigint FROM ohlcv_health_counts
                WHERE interval = '1h') AS hourly_records,
               (SELECT COALESCE(SUM(row_count), 0)::bigint FROM ohlcv_health_counts
                WHERE interval = '1m') AS minute_records
           FROM (SELECT source, array_agg(id) AS ids FROM tickers GROUP BY source) t"#,
    )
    .fetch_all(pool);

    let daily_sync_fut = sqlx::query_scalar::<_, Option<chrono::DateTime<chrono::Utc>>>(
        "SELECT MAX(updated_at) FROM (SELECT DISTINCT ON (ticker_id) updated_at FROM ohlcv_daily WHERE ticker_id = ANY($1) AND interval = '1D' ORDER BY ticker_id, time DESC) s",
    )
    .bind(&major_tickers)
    .fetch_optional(pool);

    let hourly_sql = format!(
        "SELECT MAX(updated_at) FROM (SELECT DISTINCT ON (ticker_id) updated_at FROM {hourly_table} WHERE ticker_id = ANY($1) AND interval = '1h' ORDER BY ticker_id, time DESC) s"
    );
    let hourly_sync_fut =
        sqlx::query_scalar::<_, Option<chrono::DateTime<chrono::Utc>>>(&hourly_sql)
            .bind(&major_tickers)
            .fetch_optional(pool);

    let minute_sql = format!(
        "SELECT MAX(updated_at) FROM (SELECT DISTINCT ON (ticker_id) updated_at FROM {minute_table} WHERE ticker_id = ANY($1) AND interval = '1m' ORDER BY ticker_id, time DESC) s"
    );
    let minute_sync_fut =
        sqlx::query_scalar::<_, Option<chrono::DateTime<chrono::Utc>>>(&minute_sql)
            .bind(&major_tickers)
            .fetch_optional(pool);

    let (stats_res, daily_sync_res, hourly_sync_res, minute_sync_res) =
        tokio::join!(stats_fut, daily_sync_fut, hourly_sync_fut, minute_sync_fut);

    let stats = stats_res?;

    let (total_tickers, active_tickers, daily_records, hourly_records, minute_records) =
        summarize_stats(&stats);

    let daily_last_sync = daily_sync_res?.flatten();
    let hourly_last_sync = hourly_sync_res?.flatten();
    let minute_last_sync = minute_sync_res?.flatten();

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

#[cfg(test)]
mod tests {
    use super::{HealthRow, refresh_snapshot, summarize_stats};
    use crate::server::HealthSnapshot;
    use sqlx::postgres::PgPoolOptions;
    use std::sync::Arc;
    use std::time::Duration;

    #[test]
    fn exact_global_record_counts_are_not_multiplied_by_source_rows() {
        let stats = [
            HealthRow {
                ticker_count: 4,
                active_tickers: 3,
                daily_records: 2_344,
                hourly_records: 24,
                minute_records: 30,
            },
            HealthRow {
                ticker_count: 2,
                active_tickers: 1,
                daily_records: 2_344,
                hourly_records: 24,
                minute_records: 30,
            },
            HealthRow {
                ticker_count: 2,
                active_tickers: 2,
                daily_records: 2_344,
                hourly_records: 24,
                minute_records: 30,
            },
        ];

        assert_eq!(summarize_stats(&stats), (8, 6, 2_344, 24, 30));
        assert_eq!(summarize_stats(&[]), (0, 0, 0, 0, 0));
    }

    #[tokio::test]
    async fn failed_refresh_keeps_the_last_complete_snapshot() {
        let pool = PgPoolOptions::new()
            .acquire_timeout(Duration::from_millis(100))
            .connect_lazy("postgresql://aipa:aipa@127.0.0.1:1/aipa")
            .expect("lazy pool URL should be valid");
        let snapshot = Arc::new(tokio::sync::RwLock::new(HealthSnapshot {
            total_tickers: 17,
            active_tickers: 11,
            daily_records: 8_765,
            hourly_records: 432,
            minute_records: 123,
            daily_last_sync: None,
            hourly_last_sync: None,
            minute_last_sync: None,
        }));

        assert!(refresh_snapshot(&pool, &snapshot).await.is_err());

        let current = snapshot.read().await;
        assert_eq!(current.total_tickers, 17);
        assert_eq!(current.active_tickers, 11);
        assert_eq!(current.daily_records, 8_765);
        assert_eq!(current.hourly_records, 432);
        assert_eq!(current.minute_records, 123);
    }
}
