use chrono::{DateTime, Duration, Utc};
use sqlx::PgPool;

use crate::models::indicators::{calculate_ma_score, calculate_sma};
pub use crate::models::ohlcv::{OhlcvJoined, OhlcvRow, Ticker};

/// Maximum SMA period — fetch this many extra rows before the requested range
/// to ensure all moving averages are accurate.
use crate::constants::api::SMA_MAX_PERIOD;

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
    list_tickers_with_extra(pool, source, &[]).await
}

/// Like `list_tickers` but also includes tickers from `extra_sources`.
pub async fn list_tickers_with_extra(
    pool: &PgPool,
    source: &str,
    extra_sources: &[&str],
) -> sqlx::Result<Vec<Ticker>> {
    if extra_sources.is_empty() {
        return sqlx::query_as!(
            Ticker,
            r#"SELECT id, source, ticker, name, status, next_1d, next_1h, next_1m
               FROM tickers WHERE source = $1
               ORDER BY ticker"#,
            source
        )
        .fetch_all(pool)
        .await;
    }

    sqlx::query_as::<_, Ticker>(
        r#"SELECT id, source, ticker, name, status, next_1d, next_1h, next_1m
           FROM tickers WHERE source = $1 OR source = ANY($2)
           ORDER BY ticker"#,
    )
    .bind(source)
    .bind(extra_sources)
    .fetch_all(pool)
    .await
}

/// List all tickers across all sources.
pub async fn list_all_tickers(pool: &PgPool) -> sqlx::Result<Vec<Ticker>> {
    sqlx::query_as!(
        Ticker,
        r#"SELECT id, source, ticker, name, status, next_1d, next_1h, next_1m
           FROM tickers
           ORDER BY ticker"#,
    )
    .fetch_all(pool)
    .await
}

/// Resolve which DB source each symbol belongs to.
///
/// Returns a map of `symbol -> source`. When a symbol exists in multiple sources,
/// priority is vn > yahoo > crypto (first match wins).
pub async fn resolve_ticker_sources(
    pool: &PgPool,
    symbols: &[String],
) -> sqlx::Result<std::collections::HashMap<String, String>> {
    let rows: Vec<(String, String)> = sqlx::query_as(
        "SELECT ticker, source FROM tickers WHERE ticker = ANY($1)",
    )
    .bind(symbols)
    .fetch_all(pool)
    .await?;

    // Priority order: vn > yahoo > sjc > crypto
    let priority = |source: &str| match source {
        "vn" => 0,
        "yahoo" => 1,
        "sjc" => 2,
        "crypto" => 3,
        _ => 4,
    };

    let mut map: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for (ticker, source) in rows {
        // Keep lowest priority number (vn=0 wins over yahoo=1 wins over crypto=2)
        let should_insert = match map.get(&ticker) {
            Some(existing) if priority(&source) < priority(existing) => true,
            Some(_) => false,
            None => true,
        };
        if should_insert {
            map.insert(ticker, source);
        }
    }
    Ok(map)
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

/// Get joined OHLCV + indicators for a ticker symbol + interval.
/// Returns rows matching the 20-column CSV format, ordered by time DESC.
///
/// Indicators (MA, scores, changes) are calculated in-memory from OHLCV data.
pub async fn get_ohlcv_joined(
    pool: &PgPool,
    source: &str,
    ticker: &str,
    interval: &str,
    limit: Option<i64>,
) -> sqlx::Result<Vec<OhlcvJoined>> {
    // Resolve ticker_id
    let ticker_id: i32 = sqlx::query_scalar(
        r#"SELECT id FROM tickers WHERE source = $1 AND ticker = $2"#
    )
    .bind(source)
    .bind(ticker)
    .fetch_optional(pool)
    .await?
    .ok_or_else(|| sqlx::Error::RowNotFound)?;

    // Fetch extra rows for SMA accuracy: limit + SMA_MAX_PERIOD
    let effective_limit = limit.map(|l| (l + SMA_MAX_PERIOD) as i64);
    let rows = get_ohlcv(pool, ticker_id, interval, effective_limit).await?;
    if rows.is_empty() {
        return Ok(Vec::new());
    }

    let ticker_str = ticker.to_string();
    let result = enhance_rows(&ticker_str, rows, limit, None);
    Ok(result)
}

/// Get joined OHLCV + indicators for a ticker symbol + interval with optional date range.
/// This mirrors the /tickers API query pattern.
///
/// Indicators are calculated in-memory from OHLCV data.
pub async fn get_ohlcv_joined_range(
    pool: &PgPool,
    source: &str,
    ticker: &str,
    interval: &str,
    limit: Option<i64>,
    start_time: Option<DateTime<Utc>>,
    end_time: Option<DateTime<Utc>>,
) -> sqlx::Result<Vec<OhlcvJoined>> {
    // Resolve ticker_id first to avoid bad join plans on partitioned tables
    let ticker_id: i32 = sqlx::query_scalar(
        r#"SELECT id FROM tickers WHERE source = $1 AND ticker = $2"#
    )
    .bind(source)
    .bind(ticker)
    .fetch_optional(pool)
    .await?
    .ok_or_else(|| sqlx::Error::RowNotFound)?;

    // Calculate effective start time: shift back by SMA_MAX_PERIOD rows worth of time
    // for accurate SMA calculation at the beginning of the requested range.
    let effective_start = start_time.map(|st| {
        let lookback_minutes = interval_duration(interval) * SMA_MAX_PERIOD;
        st - Duration::minutes(lookback_minutes)
    });

    // Build conditions dynamically
    let mut conditions = vec!["ticker_id = $1".to_string(), "interval = $2".to_string()];
    let mut param_idx = 3u32;

    if effective_start.is_some() {
        conditions.push(format!("time >= ${param_idx}"));
        param_idx += 1;
    }
    if end_time.is_some() {
        conditions.push(format!("time <= ${param_idx}"));
        param_idx += 1;
    }

    let where_clause = conditions.join(" AND ");

    // Fetch ohlcv rows with expanded range for SMA accuracy
    let effective_limit = limit.map(|l| (l + SMA_MAX_PERIOD) as i64);
    let mut sql = format!(
        r#"SELECT ticker_id, interval, time, open, high, low, close, volume
           FROM ohlcv WHERE {where_clause} ORDER BY time DESC"#
    );

    if let Some(_) = effective_limit {
        sql.push_str(&format!(" LIMIT ${param_idx}"));
    }

    let mut q = sqlx::query_as::<_, OhlcvRow>(&sql)
        .bind(ticker_id).bind(interval);
    if let Some(s) = effective_start { q = q.bind(s); }
    if let Some(e) = end_time { q = q.bind(e); }
    if let Some(l) = effective_limit { q = q.bind(l); }

    let rows: Vec<OhlcvRow> = q.fetch_all(pool).await?;

    if rows.is_empty() {
        return Ok(Vec::new());
    }

    let ticker_str = ticker.to_string();
    let result = enhance_rows(&ticker_str, rows, limit, start_time);
    Ok(result)
}

/// Enhance a set of OHLCV rows with in-memory calculated indicators.
///
/// The rows must be in time DESC order (as returned from the DB).
/// All rows are used for SMA calculation, then filtered to `start_time`
/// if provided, and trimmed to `limit` from the newest end.
pub fn enhance_rows(
    ticker: &str,
    rows: Vec<OhlcvRow>,
    limit: Option<i64>,
    start_time: Option<DateTime<Utc>>,
) -> Vec<OhlcvJoined> {
    if rows.is_empty() {
        return Vec::new();
    }

    // Reverse to chronological order (oldest first) for SMA calculation
    let mut chrono_rows = rows;
    chrono_rows.reverse();

    // Extract closes and volumes in chronological order
    let closes: Vec<f64> = chrono_rows.iter().map(|r| r.close).collect();
    let volumes: Vec<f64> = chrono_rows.iter().map(|r| r.volume as f64).collect();

    // Calculate SMAs on the full dataset
    let ma10 = calculate_sma(&closes, 10);
    let ma20 = calculate_sma(&closes, 20);
    let ma50 = calculate_sma(&closes, 50);
    let ma100 = calculate_sma(&closes, 100);
    let ma200 = calculate_sma(&closes, 200);

    // Build joined rows with indicators
    let mut joined: Vec<OhlcvJoined> = chrono_rows
        .iter()
        .enumerate()
        .map(|(i, r)| {
            let make_opt = |vals: &[f64], idx: usize| -> Option<f64> {
                if idx < vals.len() && vals[idx] > 0.0 {
                    Some(vals[idx])
                } else {
                    None
                }
            };

            OhlcvJoined {
                ticker: ticker.to_string(),
                time: r.time,
                open: r.open,
                high: r.high,
                low: r.low,
                close: r.close,
                volume: r.volume,
                ma10: make_opt(&ma10, i),
                ma20: make_opt(&ma20, i),
                ma50: make_opt(&ma50, i),
                ma100: make_opt(&ma100, i),
                ma200: make_opt(&ma200, i),
                ma10_score: make_opt(&ma10, i).map(|v| calculate_ma_score(r.close, v)),
                ma20_score: make_opt(&ma20, i).map(|v| calculate_ma_score(r.close, v)),
                ma50_score: make_opt(&ma50, i).map(|v| calculate_ma_score(r.close, v)),
                ma100_score: make_opt(&ma100, i).map(|v| calculate_ma_score(r.close, v)),
                ma200_score: make_opt(&ma200, i).map(|v| calculate_ma_score(r.close, v)),
                close_changed: if i > 0 && closes[i - 1] > 0.0 {
                    Some(((r.close - closes[i - 1]) / closes[i - 1]) * 100.0)
                } else {
                    None
                },
                volume_changed: if i > 0 && volumes[i - 1] > 0.0 {
                    Some(((volumes[i] - volumes[i - 1]) / volumes[i - 1]) * 100.0)
                } else {
                    None
                },
                total_money_changed: if i > 0 && closes[i - 1] > 0.0 {
                    Some((r.close - closes[i - 1]) * r.volume as f64)
                } else {
                    None
                },
            }
        })
        .collect();

    // Reverse back to newest-first order
    joined.reverse();

    // Post-filter: remove rows before the user's original start_time
    if let Some(st) = start_time {
        joined.retain(|r| r.time >= st);
    }

    // Apply limit (trim from the newest end)
    if let Some(l) = limit {
        let l = l as usize;
        if joined.len() > l {
            joined.truncate(l);
        }
    }

    joined
}

/// Core batch fetch: query OHLCV rows for multiple tickers in a single SQL
/// query and group by ticker name. Returns raw `OhlcvRow` without indicators.
///
/// When `symbols` is empty, fetches ALL tickers for the source.
/// When `symbols` is non-empty, fetches only the specified tickers.
///
/// `per_ticker_limit` caps rows per ticker (no cap if None).
/// `lookback_minutes` shifts `start_time` backwards for SMA accuracy.
async fn fetch_ohlcv_batch_raw(
    pool: &PgPool,
    source: &str,
    symbols: &[String],
    extra_sources: &[&str],
    interval: &str,
    per_ticker_limit: Option<i64>,
    start_time: Option<DateTime<Utc>>,
    end_time: Option<DateTime<Utc>>,
    lookback_minutes: Option<i64>,
) -> sqlx::Result<std::collections::HashMap<String, Vec<OhlcvRow>>> {
    use std::collections::HashMap;

    // Fetch ticker IDs + names
    let tickers: Vec<Ticker> = if symbols.is_empty() {
        list_tickers_with_extra(pool, source, extra_sources).await?
    } else if extra_sources.is_empty() {
        sqlx::query_as!(
            Ticker,
            r#"SELECT id, source, ticker, name, status, next_1d, next_1h, next_1m
               FROM tickers WHERE source = $1 AND ticker = ANY($2)
               ORDER BY ticker"#,
            source,
            symbols
        )
        .fetch_all(pool)
        .await?
    } else {
        sqlx::query_as::<_, Ticker>(
            r#"SELECT id, source, ticker, name, status, next_1d, next_1h, next_1m
               FROM tickers WHERE (source = $1 OR source = ANY($2)) AND ticker = ANY($3)
               ORDER BY ticker"#,
        )
        .bind(source)
        .bind(extra_sources)
        .bind(symbols)
        .fetch_all(pool)
        .await?
    };

    if tickers.is_empty() {
        return Ok(HashMap::new());
    }

    let ticker_ids: Vec<i32> = tickers.iter().map(|t| t.id).collect();
    let ticker_names: HashMap<i32, String> = tickers
        .into_iter()
        .map(|t| (t.id, t.ticker))
        .collect();

    // Shift start_time back for SMA lookback
    let effective_start = match (start_time, lookback_minutes) {
        (Some(st), Some(lb)) => Some(st - Duration::minutes(lb)),
        (st, _) => st,
    };

    // Build query
    if let Some(per_ticker) = per_ticker_limit {
        // LATERAL join lets PG use the PK index (ticker_id, interval, time) to
        // do a backward index scan per ticker and stop after `per_ticker` rows,
        // avoiding the expensive ROW_NUMBER() materialisation of all matching rows.
        let mut lateral_conditions = vec![
            "ticker_id = t.id".to_string(),
            "interval = $2".to_string(),
        ];
        let mut param_idx: u32 = 3;
        if effective_start.is_some() {
            lateral_conditions.push(format!("time >= ${param_idx}"));
            param_idx += 1;
        }
        if end_time.is_some() {
            lateral_conditions.push(format!("time <= ${param_idx}"));
        }
        let lateral_where = lateral_conditions.join(" AND ");

        let sql = format!(
            r#"SELECT o.ticker_id, o.interval, o.time, o.open, o.high, o.low, o.close, o.volume
               FROM unnest($1::int[]) AS t(id)
               CROSS JOIN LATERAL (
                   SELECT ticker_id, interval, time, open, high, low, close, volume
                   FROM ohlcv
                   WHERE {lateral_where}
                   ORDER BY time DESC
                   LIMIT {per_ticker}
               ) AS o
               ORDER BY o.ticker_id, o.time DESC"#
        );

        let mut q = sqlx::query_as::<_, OhlcvRow>(&sql)
            .bind(&ticker_ids)
            .bind(interval);
        if let Some(s) = effective_start {
            q = q.bind(s);
        }
        if let Some(e) = end_time {
            q = q.bind(e);
        }

        let rows: Vec<OhlcvRow> = q.fetch_all(pool).await?;

        let mut by_ticker: HashMap<String, Vec<OhlcvRow>> = HashMap::new();
        for row in rows {
            let name = ticker_names
                .get(&row.ticker_id)
                .cloned()
                .unwrap_or_default();
            by_ticker.entry(name).or_default().push(row);
        }
        return Ok(by_ticker);
    }

    // No per-ticker limit path
    let mut conditions = vec![
        "ticker_id = ANY($1)".to_string(),
        "interval = $2".to_string(),
    ];
    let mut param_idx: u32 = 3;

    if effective_start.is_some() {
        conditions.push(format!("time >= ${param_idx}"));
        param_idx += 1;
    }
    if end_time.is_some() {
        conditions.push(format!("time <= ${param_idx}"));
        param_idx += 1;
    }

    let where_clause = conditions.join(" AND ");

    let sql = format!(
        r#"SELECT ticker_id, interval, time, open, high, low, close, volume
           FROM ohlcv WHERE {where_clause}
           ORDER BY ticker_id, time DESC"#
    );

    let mut q = sqlx::query_as::<_, OhlcvRow>(&sql)
        .bind(&ticker_ids)
        .bind(interval);
    if let Some(s) = effective_start {
        q = q.bind(s);
    }
    if let Some(e) = end_time {
        q = q.bind(e);
    }

    let rows: Vec<OhlcvRow> = q.fetch_all(pool).await?;

    // Group by ticker name
    let mut by_ticker: HashMap<String, Vec<OhlcvRow>> = HashMap::new();
    for row in rows {
        let name = ticker_names
            .get(&row.ticker_id)
            .cloned()
            .unwrap_or_default();
        by_ticker.entry(name).or_default().push(row);
    }

    Ok(by_ticker)
}

/// Batch-fetch joined OHLCV + indicators for tickers of a source + interval.
///
/// When `symbols` is empty, fetches ALL tickers for the source.
/// When `symbols` is non-empty, fetches only the specified tickers.
///
/// For `limit`, each ticker gets at most `limit` result rows. The underlying query
/// fetches up to `limit + SMA_MAX_PERIOD` rows per ticker for accurate SMA.
pub async fn get_ohlcv_joined_batch(
    pool: &PgPool,
    source: &str,
    symbols: &[String],
    interval: &str,
    limit: Option<i64>,
    start_time: Option<DateTime<Utc>>,
    end_time: Option<DateTime<Utc>>,
) -> sqlx::Result<std::collections::HashMap<String, Vec<OhlcvJoined>>> {
    get_ohlcv_joined_batch_with_extra(pool, source, symbols, interval, limit, start_time, end_time, &[]).await
}

pub async fn get_ohlcv_joined_batch_with_extra(
    pool: &PgPool,
    source: &str,
    symbols: &[String],
    interval: &str,
    limit: Option<i64>,
    start_time: Option<DateTime<Utc>>,
    end_time: Option<DateTime<Utc>>,
    extra_sources: &[&str],
) -> sqlx::Result<std::collections::HashMap<String, Vec<OhlcvJoined>>> {
    use std::collections::HashMap;

    let per_ticker = limit.map(|l| (l + SMA_MAX_PERIOD) as i64);
    let lookback = limit.map(|_| interval_duration(interval) * SMA_MAX_PERIOD);

    let raw = fetch_ohlcv_batch_raw(
        pool, source, symbols, extra_sources, interval,
        per_ticker, start_time, end_time, lookback,
    ).await?;

    // Enhance each group
    let mut result: HashMap<String, Vec<OhlcvJoined>> = HashMap::new();
    for (ticker, ticker_rows) in raw {
        let joined = enhance_rows(&ticker, ticker_rows, limit, start_time);
        result.insert(ticker, joined);
    }

    Ok(result)
}

/// Batch-fetch raw OHLCV rows (no indicators) for tickers of a source + interval.
///
/// Unlike `get_ohlcv_joined_batch`, this does not compute indicators, making it
/// suitable for aggregation pipelines that recompute indicators on aggregated data.
pub async fn get_ohlcv_batch_raw(
    pool: &PgPool,
    source: &str,
    symbols: &[String],
    interval: &str,
    per_ticker_limit: Option<i64>,
    start_time: Option<DateTime<Utc>>,
    end_time: Option<DateTime<Utc>>,
) -> sqlx::Result<std::collections::HashMap<String, Vec<OhlcvRow>>> {
    get_ohlcv_batch_raw_with_extra(pool, source, symbols, interval, per_ticker_limit, start_time, end_time, &[]).await
}

pub async fn get_ohlcv_batch_raw_with_extra(
    pool: &PgPool,
    source: &str,
    symbols: &[String],
    interval: &str,
    per_ticker_limit: Option<i64>,
    start_time: Option<DateTime<Utc>>,
    end_time: Option<DateTime<Utc>>,
    extra_sources: &[&str],
) -> sqlx::Result<std::collections::HashMap<String, Vec<OhlcvRow>>> {
    fetch_ohlcv_batch_raw(
        pool, source, symbols, extra_sources, interval,
        per_ticker_limit, start_time, end_time, None,
    ).await
}

/// Get the duration of one row for the given interval.
/// For daily, uses 1.4 days per trading day to account for weekends/holidays
/// (200 trading days ≈ 280 calendar days).
fn interval_duration(interval: &str) -> i64 {
    match interval {
        "1m" => 1,          // 1 minute
        "1h" => 60,         // 60 minutes
        _ => 24 * 60 * 14 / 10,  // 2010 minutes (~1.4 days) for daily
    }
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

/// Resolve all ticker IDs for a given source.
async fn source_ticker_ids(pool: &PgPool, source: &str) -> sqlx::Result<Vec<i32>> {
    sqlx::query_scalar("SELECT id FROM tickers WHERE source = $1")
        .bind(source)
        .fetch_all(pool)
        .await
}

/// Get the latest daily record for each ticker of a given source.
/// Uses DISTINCT ON for a single efficient query.
///
/// Indicators are calculated in-memory from the last 200+ daily rows per ticker.
pub async fn get_latest_daily_per_ticker(
    pool: &PgPool,
    source: &str,
) -> sqlx::Result<Vec<OhlcvJoined>> {
    // Fetch latest 200+1 daily rows per ticker for accurate SMA(200)
    let rows = sqlx::query_as::<_, OhlcvRow>(
        r#"SELECT ticker_id, interval, time, open, high, low, close, volume
           FROM ohlcv
           WHERE ticker_id = ANY(
               SELECT id FROM tickers WHERE source = $1
           ) AND interval = '1D'
           ORDER BY ticker_id, time DESC"#,
    )
    .bind(source)
    .fetch_all(pool)
    .await?;

    // Group by ticker_id, keep at most SMA_MAX_PERIOD + 1 rows per ticker (for accuracy)
    let mut by_ticker: std::collections::HashMap<i32, Vec<OhlcvRow>> = std::collections::HashMap::new();
    for row in rows {
        by_ticker
            .entry(row.ticker_id)
            .or_default()
            .push(row);
    }

    // Also fetch ticker strings
    let tickers: Vec<Ticker> = list_tickers(pool, source).await?;
    let ticker_names: std::collections::HashMap<i32, String> = tickers
        .into_iter()
        .map(|t| (t.id, t.ticker))
        .collect();

    let mut result = Vec::new();
    for (ticker_id, mut ticker_rows) in by_ticker {
        // Already in DESC order, trim to SMA_MAX_PERIOD + 1
        if ticker_rows.len() > SMA_MAX_PERIOD as usize + 1 {
            ticker_rows.truncate(SMA_MAX_PERIOD as usize + 1);
        }

        if ticker_rows.is_empty() {
            continue;
        }

        let ticker_str = ticker_names
            .get(&ticker_id)
            .cloned()
            .unwrap_or_default();

        // Enhance with all rows for accurate indicators
        let joined = enhance_rows(&ticker_str, ticker_rows, Some(1), None);
        result.extend(joined);
    }

    Ok(result)
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

/// Get a single ticker by ID, returning its current status.
pub async fn get_ticker_by_id(pool: &PgPool, ticker_id: i32) -> sqlx::Result<Option<Ticker>> {
    sqlx::query_as!(
        Ticker,
        r#"SELECT id, source, ticker, name, status, next_1d, next_1h, next_1m
           FROM tickers WHERE id = $1"#,
        ticker_id
    )
    .fetch_optional(pool)
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

/// Set a new ticker's status to 'full-download-requested' so the dividend worker
/// picks it up for a full historical download. Only applies to source='vn' tickers.
/// Returns the number of rows updated (0 or 1).
pub async fn set_ticker_ready_if_new(pool: &PgPool, ticker: &str) -> sqlx::Result<u64> {
    let result = sqlx::query!(
        "UPDATE tickers SET status = 'full-download-requested' WHERE source = 'vn' AND ticker = $1 AND status IS NULL",
        ticker
    )
    .execute(pool)
    .await?;
    let affected = result.rows_affected();
    if affected > 0 {
        tracing::info!(ticker, source = "vn", "set_ticker_ready_if_new: set status = full-download-requested");
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

/// Get earliest time for a ticker + interval. Returns None if no data exists.
pub async fn get_earliest_time(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
) -> sqlx::Result<Option<DateTime<Utc>>> {
    sqlx::query_scalar!(
        r#"SELECT time FROM ohlcv
           WHERE ticker_id = $1 AND interval = $2
           ORDER BY time ASC LIMIT 1"#,
        ticker_id,
        interval
    )
    .fetch_optional(pool)
    .await
}

/// Get latest time for a ticker + interval. Returns None if no data exists.
pub async fn get_last_time(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
) -> sqlx::Result<Option<DateTime<Utc>>> {
    sqlx::query_scalar(
        r#"SELECT time FROM ohlcv
           WHERE ticker_id = $1 AND interval = $2
           ORDER BY time DESC LIMIT 1"#,
    )
    .bind(ticker_id)
    .bind(interval)
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
) -> sqlx::Result<DateTime<Utc>> {
    assert!(
        matches!(next_col, "next_1d" | "next_1h" | "next_1m"),
        "next_col must be one of: next_1d, next_1h, next_1m"
    );

    let sql = format!(
        r#"UPDATE tickers SET {next_col} = NOW() + (
            CASE
                WHEN daily_cv IS NULL THEN $8::INTERVAL
                WHEN daily_cv >= $2 THEN $3::INTERVAL
                WHEN daily_cv >= $4 THEN $5::INTERVAL
                WHEN daily_cv >= $6 THEN $7::INTERVAL
                ELSE $8::INTERVAL
            END
        )
        FROM (
            SELECT daily_cv FROM (
                SELECT (close * volume) as daily_cv
                FROM ohlcv WHERE ticker_id = $1 AND interval = '1D'
                ORDER BY time DESC LIMIT 1 OFFSET 1
            ) t
            UNION ALL SELECT NULL
            LIMIT 1
        ) sub
        WHERE id = $1
        RETURNING {next_col}"#
    );

    let row: (DateTime<Utc>,) = sqlx::query_as(&sql)
        .bind(ticker_id)
        .bind(thresholds[0])
        .bind(format!("{} seconds", tier_secs[0]))
        .bind(thresholds[1])
        .bind(format!("{} seconds", tier_secs[1]))
        .bind(thresholds[2])
        .bind(format!("{} seconds", tier_secs[2]))
        .bind(format!("{} seconds", tier_secs[3]))
        .fetch_one(pool)
        .await?;

    Ok(row.0)
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
