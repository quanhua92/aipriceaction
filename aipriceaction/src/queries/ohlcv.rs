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
        r#"SELECT id, source, ticker, name, status
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
        r#"SELECT id, source, ticker, name, status
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
pub async fn count_ohlcv(
    pool: &PgPool,
    source: &str,
    ticker: Option<&str>,
    interval: Option<&str>,
) -> sqlx::Result<i64> {
    match (ticker, interval) {
        (Some(ticker), Some(interval)) => {
            sqlx::query_scalar!(
                r#"SELECT COUNT(*) as "count!"
                   FROM ohlcv o
                   JOIN tickers t ON t.id = o.ticker_id
                   WHERE t.source = $1 AND t.ticker = $2 AND o.interval = $3"#,
                source,
                ticker,
                interval
            )
            .fetch_one(pool)
            .await
        }
        (Some(ticker), None) => {
            sqlx::query_scalar!(
                r#"SELECT COUNT(*) as "count!"
                   FROM ohlcv o
                   JOIN tickers t ON t.id = o.ticker_id
                   WHERE t.source = $1 AND t.ticker = $2"#,
                source,
                ticker
            )
            .fetch_one(pool)
            .await
        }
        (None, Some(interval)) => {
            sqlx::query_scalar!(
                r#"SELECT COUNT(*) as "count!"
                   FROM ohlcv o
                   JOIN tickers t ON t.id = o.ticker_id
                   WHERE t.source = $1 AND o.interval = $2"#,
                source,
                interval
            )
            .fetch_one(pool)
            .await
        }
        (None, None) => {
            sqlx::query_scalar!(
                r#"SELECT COUNT(*) as "count!"
                   FROM ohlcv o
                   JOIN tickers t ON t.id = o.ticker_id
                   WHERE t.source = $1"#,
                source
            )
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
            sqlx::query_scalar!(
                r#"SELECT COUNT(*) as "count!"
                   FROM ohlcv_indicators i
                   JOIN tickers t ON t.id = i.ticker_id
                   WHERE t.source = $1 AND t.ticker = $2 AND i.interval = $3"#,
                source,
                ticker,
                interval
            )
            .fetch_one(pool)
            .await
        }
        (Some(ticker), None) => {
            sqlx::query_scalar!(
                r#"SELECT COUNT(*) as "count!"
                   FROM ohlcv_indicators i
                   JOIN tickers t ON t.id = i.ticker_id
                   WHERE t.source = $1 AND t.ticker = $2"#,
                source,
                ticker
            )
            .fetch_one(pool)
            .await
        }
        (None, Some(interval)) => {
            sqlx::query_scalar!(
                r#"SELECT COUNT(*) as "count!"
                   FROM ohlcv_indicators i
                   JOIN tickers t ON t.id = i.ticker_id
                   WHERE t.source = $1 AND i.interval = $2"#,
                source,
                interval
            )
            .fetch_one(pool)
            .await
        }
        (None, None) => {
            sqlx::query_scalar!(
                r#"SELECT COUNT(*) as "count!"
                   FROM ohlcv_indicators i
                   JOIN tickers t ON t.id = i.ticker_id
                   WHERE t.source = $1"#,
                source
            )
            .fetch_one(pool)
            .await
        }
    }
}
