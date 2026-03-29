use chrono::{DateTime, Utc};
use sqlx::PgPool;

use crate::models::ohlcv::{IndicatorRow, OhlcvJoined, OhlcvRow, Ticker};

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

// ── Read queries ──

/// Find a ticker by source + symbol, returns None if not found.
pub async fn get_ticker(pool: &PgPool, source: &str, ticker: &str) -> sqlx::Result<Option<Ticker>> {
    sqlx::query_as!(
        Ticker,
        r#"SELECT id, source, ticker, name, status, next_1d, next_1h, next_1m
           FROM tickers WHERE source = $1 AND ticker = $2"#,
        source,
        ticker
    )
    .fetch_optional(pool)
    .await
}

/// List all tickers for a given source.
pub async fn list_tickers(pool: &PgPool, source: &str) -> sqlx::Result<Vec<Ticker>> {
    sqlx::query_as!(
        Ticker,
        r#"SELECT id, source, ticker, name, status, next_1d, next_1h, next_1m
           FROM tickers WHERE source = $1
           ORDER BY ticker"#,
        source
    )
    .fetch_all(pool)
    .await
}

/// Get OHLCV rows for a ticker_id + interval, ordered by time DESC.
/// Optionally limit the number of rows.
pub async fn get_ohlcv(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
    limit: Option<i64>,
) -> sqlx::Result<Vec<OhlcvRow>> {
    sqlx::query_as!(
        OhlcvRow,
        r#"SELECT ticker_id, interval, time, open, high, low, close, volume
           FROM ohlcv
           WHERE ticker_id = $1 AND interval = $2
           ORDER BY time DESC
           LIMIT $3"#,
        ticker_id,
        interval,
        limit
    )
    .fetch_all(pool)
    .await
}

/// Get indicator rows for a ticker_id + interval, ordered by time DESC.
/// Optionally limit the number of rows.
pub async fn get_indicators(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
    limit: Option<i64>,
) -> sqlx::Result<Vec<IndicatorRow>> {
    sqlx::query_as!(
        IndicatorRow,
        r#"SELECT ticker_id, interval, time,
             ma10, ma20, ma50, ma100, ma200,
             ma10_score, ma20_score, ma50_score, ma100_score, ma200_score,
             close_changed, volume_changed, total_money_changed
           FROM ohlcv_indicators
           WHERE ticker_id = $1 AND interval = $2
           ORDER BY time DESC
           LIMIT $3"#,
        ticker_id,
        interval,
        limit
    )
    .fetch_all(pool)
    .await
}

/// Get joined OHLCV + indicators for a ticker symbol + interval.
/// Returns rows matching the 20-column CSV format, ordered by time DESC.
pub async fn get_ohlcv_joined(
    pool: &PgPool,
    source: &str,
    ticker: &str,
    interval: &str,
    limit: Option<i64>,
) -> sqlx::Result<Vec<OhlcvJoined>> {
    sqlx::query_as!(
        OhlcvJoined,
        r#"SELECT
             t.ticker,
             o.time,
             o.open, o.high, o.low, o.close, o.volume,
             i.ma10, i.ma20, i.ma50, i.ma100, i.ma200,
             i.ma10_score, i.ma20_score, i.ma50_score, i.ma100_score, i.ma200_score,
             i.close_changed, i.volume_changed, i.total_money_changed
           FROM tickers t
           JOIN ohlcv o ON o.ticker_id = t.id
           LEFT JOIN ohlcv_indicators i
             ON i.ticker_id = t.id AND i.interval = o.interval AND i.time = o.time
           WHERE t.source = $1 AND t.ticker = $2 AND o.interval = $3
           ORDER BY o.time DESC
           LIMIT $4"#,
        source,
        ticker,
        interval,
        limit
    )
    .fetch_all(pool)
    .await
}

/// Get joined OHLCV + indicators for a ticker symbol + interval with optional date range.
/// This mirrors the /tickers API query pattern.
pub async fn get_ohlcv_joined_range(
    pool: &PgPool,
    source: &str,
    ticker: &str,
    interval: &str,
    limit: Option<i64>,
    start_time: Option<chrono::DateTime<chrono::Utc>>,
    end_time: Option<chrono::DateTime<chrono::Utc>>,
) -> sqlx::Result<Vec<OhlcvJoined>> {
    let mut sql = String::from(r#"SELECT
             t.ticker,
             o.time,
             o.open, o.high, o.low, o.close, o.volume,
             i.ma10, i.ma20, i.ma50, i.ma100, i.ma200,
             i.ma10_score, i.ma20_score, i.ma50_score, i.ma100_score, i.ma200_score,
             i.close_changed, i.volume_changed, i.total_money_changed
           FROM tickers t
           JOIN ohlcv o ON o.ticker_id = t.id
           LEFT JOIN ohlcv_indicators i
             ON i.ticker_id = t.id AND i.interval = o.interval AND i.time = o.time
           WHERE t.source = $1 AND t.ticker = $2 AND o.interval = $3"#);

    match (start_time, end_time) {
        (Some(_), Some(_)) => sql.push_str(" AND o.time >= $4 AND o.time <= $5"),
        (Some(_), None)    => sql.push_str(" AND o.time >= $4"),
        (None, Some(_))    => sql.push_str(" AND o.time <= $4"),
        (None, None)       => {}
    }
    sql.push_str(" ORDER BY o.time DESC");

    // Add LIMIT only when a specific limit is requested
    if limit.is_some() {
        let param_idx = match (start_time, end_time) {
            (Some(_), Some(_)) => "$6",
            (Some(_), None) | (None, Some(_)) => "$5",
            (None, None) => "$4",
        };
        sql.push_str(&format!(" LIMIT {param_idx}"));
    }

    let mut q = sqlx::query_as::<_, OhlcvJoined>(&sql)
        .bind(source).bind(ticker).bind(interval);

    if let Some(s) = start_time { q = q.bind(s); }
    if let Some(e) = end_time { q = q.bind(e); }
    if let Some(l) = limit { q = q.bind(l); }

    q.fetch_all(pool).await
}

/// Count total tickers for a source.
pub async fn count_tickers(pool: &PgPool, source: &str) -> sqlx::Result<i64> {
    sqlx::query_scalar!(
        r#"SELECT COUNT(*) as "count!" FROM tickers WHERE source = $1"#,
        source
    )
    .fetch_one(pool)
    .await
}

/// Count OHLCV rows for a source, optionally filtered by ticker and/or interval.
///
/// Uses `ticker_id = ANY($1)` instead of a JOIN so PostgreSQL can use the
/// PK index on each partition directly.
pub async fn count_ohlcv(
    pool: &PgPool,
    source: &str,
    ticker: Option<&str>,
    interval: Option<&str>,
) -> sqlx::Result<i64> {
    match (ticker, interval) {
        (Some(ticker), Some(interval)) => {
            let sql = format!(
                "SELECT COUNT(*) FROM ohlcv WHERE ticker_id = (SELECT id FROM tickers WHERE source = '{source}' AND ticker = '{ticker}') AND interval = '{interval}'"
            );
            sqlx::query_scalar(&sql).fetch_one(pool).await
        }
        (Some(ticker), None) => {
            let sql = format!(
                "SELECT COUNT(*) FROM ohlcv WHERE ticker_id = (SELECT id FROM tickers WHERE source = '{source}' AND ticker = '{ticker}')"
            );
            sqlx::query_scalar(&sql).fetch_one(pool).await
        }
        (None, Some(interval)) => {
            let ids = source_ticker_ids(pool, source).await?;
            let sql = format!(
                "SELECT COUNT(*) FROM ohlcv WHERE ticker_id = ANY($1) AND interval = '{interval}'"
            );
            sqlx::query_scalar(&sql)
                .bind(&ids)
                .fetch_one(pool)
                .await
        }
        (None, None) => {
            let ids = source_ticker_ids(pool, source).await?;
            let sql = "SELECT COUNT(*) FROM ohlcv WHERE ticker_id = ANY($1)".to_string();
            sqlx::query_scalar(&sql)
                .bind(&ids)
                .fetch_one(pool)
                .await
        }
    }
}

/// Count indicator rows for a source, optionally filtered by ticker and/or interval.
pub async fn count_indicators(
    pool: &PgPool,
    source: &str,
    ticker: Option<&str>,
    interval: Option<&str>,
) -> sqlx::Result<i64> {
    match (ticker, interval) {
        (Some(ticker), Some(interval)) => {
            let sql = format!(
                "SELECT COUNT(*) FROM ohlcv_indicators WHERE ticker_id = (SELECT id FROM tickers WHERE source = '{source}' AND ticker = '{ticker}') AND interval = '{interval}'"
            );
            sqlx::query_scalar(&sql).fetch_one(pool).await
        }
        (Some(ticker), None) => {
            let sql = format!(
                "SELECT COUNT(*) FROM ohlcv_indicators WHERE ticker_id = (SELECT id FROM tickers WHERE source = '{source}' AND ticker = '{ticker}')"
            );
            sqlx::query_scalar(&sql).fetch_one(pool).await
        }
        (None, Some(interval)) => {
            let ids = source_ticker_ids(pool, source).await?;
            let sql = format!(
                "SELECT COUNT(*) FROM ohlcv_indicators WHERE ticker_id = ANY($1) AND interval = '{interval}'"
            );
            sqlx::query_scalar(&sql)
                .bind(&ids)
                .fetch_one(pool)
                .await
        }
        (None, None) => {
            let ids = source_ticker_ids(pool, source).await?;
            let sql = "SELECT COUNT(*) FROM ohlcv_indicators WHERE ticker_id = ANY($1)".to_string();
            sqlx::query_scalar(&sql)
                .bind(&ids)
                .fetch_one(pool)
                .await
        }
    }
}

/// Resolve all ticker IDs for a given source.
async fn source_ticker_ids(pool: &PgPool, source: &str) -> sqlx::Result<Vec<i32>> {
    sqlx::query_scalar("SELECT id FROM tickers WHERE source = $1")
        .bind(source)
        .fetch_all(pool)
        .await
}

/// Get the latest daily record for each ticker of a given source.
/// Uses DISTINCT ON for a single efficient query.
pub async fn get_latest_daily_per_ticker(
    pool: &PgPool,
    source: &str,
) -> sqlx::Result<Vec<OhlcvJoined>> {
    sqlx::query_as::<_, OhlcvJoined>(
        r#"SELECT DISTINCT ON (t.ticker)
            t.ticker,
            o.time,
            o.open, o.high, o.low, o.close, o.volume,
            i.ma10, i.ma20, i.ma50, i.ma100, i.ma200,
            i.ma10_score, i.ma20_score, i.ma50_score, i.ma100_score, i.ma200_score,
            i.close_changed, i.volume_changed, i.total_money_changed
        FROM tickers t
        JOIN ohlcv o ON o.ticker_id = t.id AND o.interval = '1D'
        LEFT JOIN ohlcv_indicators i
            ON i.ticker_id = t.id AND i.interval = '1D' AND i.time = o.time
        WHERE t.source = $1
        ORDER BY t.ticker, o.time DESC"#,
    )
    .bind(source)
    .fetch_all(pool)
    .await
}

// ── Worker queries ──

/// Get tickers by status for a source.
pub async fn get_tickers_by_status(
    pool: &PgPool,
    source: &str,
    status: &str,
) -> sqlx::Result<Vec<Ticker>> {
    get_tickers_by_statuses(pool, source, &[status]).await
}

/// Get tickers matching any of the given statuses for a source.
pub async fn get_tickers_by_statuses(
    pool: &PgPool,
    source: &str,
    statuses: &[&str],
) -> sqlx::Result<Vec<Ticker>> {
    let statuses: Vec<String> = statuses.iter().map(|s| s.to_string()).collect();
    sqlx::query_as!(
        Ticker,
        r#"SELECT id, source, ticker, name, status, next_1d, next_1h, next_1m
           FROM tickers WHERE source = $1 AND status = ANY($2)
           ORDER BY ticker"#,
        source,
        &statuses
    )
    .fetch_all(pool)
    .await
}

/// Update ticker status.
pub async fn update_ticker_status(
    pool: &PgPool,
    ticker_id: i32,
    status: &str,
) -> sqlx::Result<()> {
    let result = sqlx::query!(
        "UPDATE tickers SET status = $1 WHERE id = $2",
        status,
        ticker_id
    )
    .execute(pool)
    .await?;
    tracing::info!(ticker_id, new_status = status, rows_affected = result.rows_affected(), "update_ticker_status");
    Ok(())
}

/// Delete all OHLCV data for a ticker. Returns number of deleted rows.
pub async fn delete_ohlcv_for_ticker(pool: &PgPool, ticker_id: i32) -> sqlx::Result<u64> {
    let result = sqlx::query("DELETE FROM ohlcv WHERE ticker_id = $1")
        .bind(ticker_id)
        .execute(pool)
        .await?;
    Ok(result.rows_affected())
}

/// Delete all indicators for a ticker. Returns number of deleted rows.
pub async fn delete_indicators_for_ticker(pool: &PgPool, ticker_id: i32) -> sqlx::Result<u64> {
    let result = sqlx::query("DELETE FROM ohlcv_indicators WHERE ticker_id = $1")
        .bind(ticker_id)
        .execute(pool)
        .await?;
    Ok(result.rows_affected())
}

/// Set a ticker's status to 'ready' only if it is currently NULL (newly inserted).
/// Returns the number of rows updated (0 or 1).
/// Only applies to source='vn' tickers (VN stocks get ready immediately; crypto uses full-download-requested).
pub async fn set_ticker_ready_if_new(pool: &PgPool, ticker: &str) -> sqlx::Result<u64> {
    let result = sqlx::query!(
        "UPDATE tickers SET status = 'ready' WHERE source = 'vn' AND ticker = $1 AND status IS NULL",
        ticker
    )
    .execute(pool)
    .await?;
    let affected = result.rows_affected();
    if affected > 0 {
        tracing::info!(ticker, source = "vn", "set_ticker_ready_if_new: set status = ready");
    }
    Ok(affected)
}

/// Get latest time for a ticker + interval. Returns None if no data exists.
pub async fn get_latest_time(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
) -> sqlx::Result<Option<DateTime<Utc>>> {
    sqlx::query_scalar!(
        r#"SELECT time FROM ohlcv
           WHERE ticker_id = $1 AND interval = $2
           ORDER BY time DESC LIMIT 1"#,
        ticker_id,
        interval
    )
    .fetch_optional(pool)
    .await
}

// ── Priority scheduling queries ──

/// Fetch all tickers that are due for processing based on a `next_*` column.
pub async fn get_due_tickers(
    pool: &PgPool,
    source: &str,
    next_col: &str,
) -> sqlx::Result<Vec<Ticker>> {
    // Whitelist the column name to prevent SQL injection
    assert!(
        matches!(next_col, "next_1d" | "next_1h" | "next_1m"),
        "next_col must be one of: next_1d, next_1h, next_1m"
    );

    let sql = format!(
        r#"SELECT id, source, ticker, name, status, next_1d, next_1h, next_1m
           FROM tickers
           WHERE source = $1 AND status = 'ready' AND {next_col} < NOW()
           ORDER BY {next_col} ASC"#
    );

    sqlx::query_as::<_, Ticker>(&sql)
        .bind(source)
        .fetch_all(pool)
        .await
}

/// Schedule the next run for a ticker based on its money-flow tier.
/// Uses the previous day's close*volume (not today's incomplete bar) to determine the tier.
pub async fn schedule_next_run(
    pool: &PgPool,
    ticker_id: i32,
    next_col: &str,
    thresholds: &[f64; 3],
    tier_secs: &[i64; 4],
) -> sqlx::Result<()> {
    assert!(
        matches!(next_col, "next_1d" | "next_1h" | "next_1m"),
        "next_col must be one of: next_1d, next_1h, next_1m"
    );

    let sql = format!(
        r#"UPDATE tickers SET {next_col} = NOW() + (
            CASE
                WHEN daily_cv IS NULL THEN '1 second'::INTERVAL
                WHEN daily_cv >= $2 THEN $3::INTERVAL
                WHEN daily_cv >= $4 THEN $5::INTERVAL
                WHEN daily_cv >= $6 THEN $7::INTERVAL
                ELSE $8::INTERVAL
            END
        )
        FROM (
            SELECT (close * volume) as daily_cv
            FROM ohlcv WHERE ticker_id = $1 AND interval = '1D'
            ORDER BY time DESC LIMIT 1 OFFSET 1
        ) sub
        WHERE id = $1"#
    );

    sqlx::query(&sql)
        .bind(ticker_id)
        .bind(thresholds[0])
        .bind(format!("{} seconds", tier_secs[0]))
        .bind(thresholds[1])
        .bind(format!("{} seconds", tier_secs[1]))
        .bind(thresholds[2])
        .bind(format!("{} seconds", tier_secs[2]))
        .bind(format!("{} seconds", tier_secs[3]))
        .execute(pool)
        .await?;

    Ok(())
}

/// Reset all next_* columns to NOW() for a ticker (used after dividend recovery).
pub async fn reset_ticker_schedule(pool: &PgPool, ticker_id: i32) -> sqlx::Result<()> {
    sqlx::query(
        "UPDATE tickers SET next_1d = NOW(), next_1h = NOW(), next_1m = NOW() WHERE id = $1"
    )
    .bind(ticker_id)
    .execute(pool)
    .await?;
    Ok(())
}
