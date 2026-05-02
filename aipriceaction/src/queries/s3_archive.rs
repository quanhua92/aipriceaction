use chrono::{DateTime, NaiveDate, Utc};
use sqlx::PgPool;

// ── Data structures ──

/// Enriched ticker info for archive metadata (tickers.json).
#[derive(Debug, Clone, serde::Serialize)]
pub struct ArchiveTicker {
    pub source: String,
    pub ticker: String,
    pub name: Option<String>,
    pub exchange: Option<String>,
    #[serde(rename = "type")]
    pub ticker_type: Option<String>,
    pub category: Option<String>,
    pub group: Option<String>,
}

/// Fingerprint components for skip-if-unchanged (per-day).
#[derive(Debug, Clone)]
pub struct DayFingerprint {
    pub count: i64,
    pub max_time: DateTime<Utc>,
    pub sum_close_scaled: i64,
    pub sum_volume: i64,
}

impl DayFingerprint {
    /// Compute a SHA-256 fingerprint string from the components.
    pub fn to_hash(&self) -> String {
        use sha2::{Digest, Sha256};
        let input = format!(
            "{}:{}:{}:{}",
            self.count,
            self.max_time.format("%Y-%m-%dT%H:%M:%SZ"),
            self.sum_close_scaled,
            self.sum_volume
        );
        let hash = Sha256::digest(input.as_bytes());
        hex::encode(hash)
    }
}

/// Data range per ticker+interval for startup scan.
#[derive(Debug, Clone)]
pub struct DataRange {
    pub ticker_id: i32,
    pub interval: String,
    pub earliest: DateTime<Utc>,
    pub latest: DateTime<Utc>,
}

// ── Query functions ──

/// Fetch raw OHLCV rows for a specific ticker, interval, and date.
/// Returns rows sorted by `time ASC` for CSV output.
pub async fn get_ohlcv_for_day(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
    day_start: DateTime<Utc>,
    day_end: DateTime<Utc>,
) -> sqlx::Result<Vec<crate::models::ohlcv::OhlcvRow>> {
    sqlx::query_as!(
        crate::models::ohlcv::OhlcvRow,
        r#"SELECT ticker_id, interval, time, open, high, low, close, volume
           FROM ohlcv
           WHERE ticker_id = $1 AND interval = $2 AND time >= $3 AND time < $4
           ORDER BY time ASC"#,
        ticker_id,
        interval,
        day_start,
        day_end,
    )
    .fetch_all(pool)
    .await
}

/// Get the 4-column fingerprint for a day of OHLCV data.
/// Returns `None` if no data exists for that day.
pub async fn get_ohlcv_day_fingerprint(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
    day_start: DateTime<Utc>,
    day_end: DateTime<Utc>,
) -> sqlx::Result<Option<DayFingerprint>> {
    let row = sqlx::query!(
        r#"SELECT
               COUNT(*) as "count!",
               MAX(time) as max_time,
               COALESCE(SUM((close * 10000)::bigint), 0)::bigint as "sum_close_scaled!: i64",
               COALESCE(SUM(volume), 0)::bigint as "sum_volume!: i64"
           FROM ohlcv
           WHERE ticker_id = $1 AND interval = $2 AND time >= $3 AND time < $4"#,
        ticker_id,
        interval,
        day_start,
        day_end,
    )
    .fetch_one(pool)
    .await?;

    if row.count == 0 {
        return Ok(None);
    }

    Ok(Some(DayFingerprint {
        count: row.count,
        max_time: row.max_time.unwrap(),
        sum_close_scaled: row.sum_close_scaled,
        sum_volume: row.sum_volume,
    }))
}

/// Get data range (earliest/latest time) per ticker+interval for startup scan.
pub async fn get_data_ranges(pool: &PgPool) -> sqlx::Result<Vec<DataRange>> {
    let rows = sqlx::query!(
        r#"SELECT ticker_id, interval, MIN(time) as earliest, MAX(time) as latest
           FROM ohlcv
           GROUP BY ticker_id, interval"#,
    )
    .fetch_all(pool)
    .await?;

    let mut ranges = Vec::with_capacity(rows.len());
    for r in rows {
        let Some(earliest) = r.earliest else { continue };
        let Some(latest) = r.latest else { continue };
        ranges.push(DataRange {
            ticker_id: r.ticker_id,
            interval: r.interval,
            earliest,
            latest,
        });
    }
    Ok(ranges)
}

/// Get all tickers with (source, ticker, name) for the tickers.json export.
/// This is the base query — enrichment (exchange, type, category, group) is done
/// in the worker by reading local JSON/CSV source files.
pub async fn get_all_tickers_base(pool: &PgPool) -> sqlx::Result<Vec<ArchiveTicker>> {
    let rows = sqlx::query!(
        r#"SELECT source, ticker, name FROM tickers ORDER BY source, ticker"#,
    )
    .fetch_all(pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|r| ArchiveTicker {
            source: r.source,
            ticker: r.ticker,
            name: r.name,
            exchange: None,
            ticker_type: None,
            category: None,
            group: None,
        })
        .collect())
}

/// Convert a date to the start/end of that day in UTC.
pub fn day_range(date: NaiveDate) -> (DateTime<Utc>, DateTime<Utc>) {
    let start = date
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_utc();
    let end = start + chrono::Duration::days(1);
    (start, end)
}

/// Convert a year to the start/end of that year in UTC.
/// Returns `(Jan 1 00:00:00 UTC, Jan 1 00:00:00 UTC of next year)`.
pub fn year_range(year: i32) -> (DateTime<Utc>, DateTime<Utc>) {
    let start = NaiveDate::from_ymd_opt(year, 1, 1)
        .unwrap()
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_utc();
    let end = NaiveDate::from_ymd_opt(year + 1, 1, 1)
        .unwrap()
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_utc();
    (start, end)
}

/// Fetch all daily OHLCV rows for a specific ticker and year.
/// Returns rows sorted by `time ASC` for CSV output.
pub async fn get_ohlcv_for_year(
    pool: &PgPool,
    ticker_id: i32,
    year_start: DateTime<Utc>,
    year_end: DateTime<Utc>,
) -> sqlx::Result<Vec<crate::models::ohlcv::OhlcvRow>> {
    sqlx::query_as!(
        crate::models::ohlcv::OhlcvRow,
        r#"SELECT ticker_id, interval, time, open, high, low, close, volume
           FROM ohlcv
           WHERE ticker_id = $1 AND interval = '1D'
           AND time >= $2 AND time < $3
           ORDER BY time ASC"#,
        ticker_id,
        year_start,
        year_end,
    )
    .fetch_all(pool)
    .await
}

/// Get the 4-column fingerprint for a full year of daily OHLCV data.
/// Returns `None` if no data exists for that year.
pub async fn get_ohlcv_year_fingerprint(
    pool: &PgPool,
    ticker_id: i32,
    year_start: DateTime<Utc>,
    year_end: DateTime<Utc>,
) -> sqlx::Result<Option<DayFingerprint>> {
    let row = sqlx::query!(
        r#"SELECT
               COUNT(*) as "count!",
               MAX(time) as max_time,
               COALESCE(SUM((close * 10000)::bigint), 0)::bigint as "sum_close_scaled!: i64",
               COALESCE(SUM(volume), 0)::bigint as "sum_volume!: i64"
           FROM ohlcv
           WHERE ticker_id = $1 AND interval = '1D'
           AND time >= $2 AND time < $3"#,
        ticker_id,
        year_start,
        year_end,
    )
    .fetch_one(pool)
    .await?;

    if row.count == 0 {
        return Ok(None);
    }

    Ok(Some(DayFingerprint {
        count: row.count,
        max_time: row.max_time.unwrap(),
        sum_close_scaled: row.sum_close_scaled,
        sum_volume: row.sum_volume,
    }))
}
