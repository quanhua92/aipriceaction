use sqlx::PgPool;

use crate::constants::sjc_worker;
use crate::models::ohlcv::OhlcvRow;
use crate::queries::import;
use crate::queries::ohlcv;
use crate::workers::binance_shared::schedule_fixed_interval;
use crate::workers::vci_shared::is_trading_hours;

/// Resolve a data file by searching CWD then parent directory.
pub fn resolve_data_file(name: &str) -> Result<std::path::PathBuf, Box<dyn std::error::Error + Send + Sync>> {
    let cwd = std::path::Path::new(name);
    if cwd.exists() {
        return Ok(cwd.to_path_buf());
    }
    let parent = std::path::Path::new("..").join(name);
    if parent.exists() {
        return Ok(parent);
    }
    Err(format!("Data file not found: {name} (searched . and ../)").into())
}

/// Ensure the SJC ticker exists in the database.
///
/// Creates it with source='sjc' and sets status to 'waiting-import' if NULL,
/// so the bootstrap worker picks it up.
pub async fn ensure_sjc_ticker(pool: &PgPool) -> i32 {
    let ticker_id = ohlcv::upsert_ticker(pool, sjc_worker::SOURCE, sjc_worker::TICKER, Some(sjc_worker::NAME))
        .await
        .expect("failed to upsert SJC ticker");

    // Set status to waiting-import if NULL (new ticker)
    let result = sqlx::query!(
        "UPDATE tickers SET status = 'waiting-import' WHERE source = $1 AND ticker = $2 AND status IS NULL",
        sjc_worker::SOURCE,
        sjc_worker::TICKER
    )
    .execute(pool)
    .await;

    match result {
        Ok(rows) if rows.rows_affected() > 0 => {
            tracing::info!(
                ticker = sjc_worker::TICKER,
                "ensure_sjc_ticker: set status = waiting-import (new ticker)"
            );
        }
        Ok(_) => {}
        Err(e) => {
            tracing::warn!(ticker = sjc_worker::TICKER, "failed to set waiting-import: {e}");
        }
    }

    ticker_id
}

/// CSV row structure for sjc-batch.csv.
#[derive(Debug, serde::Deserialize)]
struct SjcCsvRow {
    date: String,
    branch: String,
    buy: f64,
    sell: f64,
    #[allow(dead_code)]
    buy_diff: f64,
    #[allow(dead_code)]
    sell_diff: f64,
}

/// Import historical SJC gold prices from CSV into OHLCV daily candles.
///
/// Algorithm: uses yesterday's close as today's open to create visible candle bodies.
/// Returns the number of rows imported.
pub async fn import_csv_to_ohlcv(
    pool: &PgPool,
    ticker_id: i32,
    redis_client: &Option<crate::redis::RedisClient>,
) -> Result<usize, Box<dyn std::error::Error + Send + Sync>> {
    let path = resolve_data_file(sjc_worker::CSV_PATH)?;

    let content = std::fs::read_to_string(&path)?;
    let mut rdr = csv::Reader::from_reader(content.as_bytes());

    // Collect rows sorted ascending by date, filtered to HCM branch
    let mut rows: Vec<SjcCsvRow> = Vec::new();
    for result in rdr.deserialize() {
        let row: SjcCsvRow = result?;
        if row.branch == sjc_worker::BRANCH {
            rows.push(row);
        }
    }
    // Already sorted ascending by date in CSV, but sort to be safe
    rows.sort_by(|a, b| a.date.cmp(&b.date));

    if rows.is_empty() {
        return Err(format!("No '{}' branch rows found in {}", sjc_worker::BRANCH, sjc_worker::CSV_PATH).into());
    }

    tracing::info!(
        total_rows = rows.len(),
        first_date = %rows.first().unwrap().date,
        last_date = %rows.last().unwrap().date,
        "import_csv_to_ohlcv: starting import"
    );

    // Build OHLCV candles using the Import Algorithm
    let mut candles: Vec<OhlcvRow> = Vec::with_capacity(rows.len());
    let mut prev_close: Option<f64> = None;

    for row in &rows {
        let mid_price = (row.buy + row.sell) / 2.0;

        let open: f64 = match prev_close {
            None => mid_price, // First candle: Doji
            Some(pc) => pc,    // Subsequent: yesterday's close
        };

        let high = row.sell;   // sell >= buy for gold
        let low = row.buy;     // buy <= sell for gold
        let close = mid_price;
        let volume = 1i64;     // synthetic

        let time = chrono::NaiveDate::parse_from_str(&row.date, "%Y-%m-%d")?
            .and_hms_opt(0, 0, 0)
            .unwrap()
            .and_utc();

        candles.push(OhlcvRow {
            ticker_id,
            interval: "1D".to_string(),
            time,
            open,
            high,
            low,
            close,
            volume,
        });

        prev_close = Some(close);
    }

    // Batch upsert
    let batch_size = sjc_worker::IMPORT_BATCH_SIZE;
    let mut total_imported = 0usize;
    for chunk in candles.chunks(batch_size) {
        import::bulk_upsert_ohlcv(pool, chunk).await?;
        total_imported += chunk.len();
    }

    // Fire-and-forget Redis ZSET write
    if let Some(client) = redis_client {
        let redis = client.clone();
        let rows = candles.clone();
        tokio::spawn(async move {
            crate::workers::redis_worker::write_ohlcv_to_redis(
                &Some(redis),
                sjc_worker::SOURCE,
                sjc_worker::TICKER,
                "1D",
                &rows,
            )
            .await;
        });
    }

    tracing::info!(total_imported, "import_csv_to_ohlcv: import complete");
    Ok(total_imported)
}

/// Upsert a live SJC price as today's daily candle.
///
/// Uses ON CONFLICT preserving open: first tick sets open, subsequent ticks
/// only widen high/low via GREATEST/LEAST and update close.
pub async fn upsert_live_price(pool: &PgPool, ticker_id: i32, buy: f64, sell: f64, redis_client: &Option<crate::redis::RedisClient>) -> Result<(), sqlx::Error> {
    let current_mid = (buy + sell) / 2.0;
    let today = chrono::Utc::now().date_naive().and_hms_opt(0, 0, 0).unwrap().and_utc();

    let row = OhlcvRow {
        ticker_id,
        interval: "1D".to_string(),
        time: today,
        open: current_mid,  // Only used on INSERT (first tick)
        high: sell,         // GREATEST preserves widest wick
        low: buy,           // LEAST preserves widest wick
        close: current_mid,
        volume: 1,
    };

    import::bulk_upsert_ohlcv_preserve_open(pool, &[row.clone()]).await?;

    // Fire-and-forget Redis ZSET write
    if let Some(client) = redis_client {
        let redis = client.clone();
        tokio::spawn(async move {
            crate::workers::redis_worker::write_ohlcv_to_redis(
                &Some(redis),
                sjc_worker::SOURCE,
                sjc_worker::TICKER,
                "1D",
                &[row],
            )
            .await;
        });
    }

    Ok(())
}

/// Get the appropriate loop interval based on VN trading hours.
pub fn loop_interval_secs() -> u64 {
    if is_trading_hours() {
        sjc_worker::DAILY_LOOP_TRADE_SECS
    } else {
        sjc_worker::DAILY_LOOP_OFF_SECS
    }
}

/// Schedule the next daily run for SJC.
pub async fn schedule_next(pool: &PgPool, ticker_id: i32) {
    let secs = if is_trading_hours() {
        sjc_worker::SCHEDULE_DAILY_SECS
    } else {
        // Off-hours: use longer interval
        sjc_worker::DAILY_LOOP_OFF_SECS as i64
    };

    if let Err(e) = schedule_fixed_interval(pool, ticker_id, "next_1d", secs).await {
        tracing::warn!(ticker_id, "schedule_next: failed: {e}");
    }
}
