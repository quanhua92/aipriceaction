use serde::Deserialize;
use sqlx::PgPool;

use crate::queries::ohlcv;
use crate::workers::vci_shared;

// ---------------------------------------------------------------------------
// binance_tickers.json schema
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
struct BinanceTickerEntry {
    symbol: String,
}

#[derive(Deserialize)]
struct BinanceTickerFile {
    data: Vec<BinanceTickerEntry>,
}

/// Load tickers from binance_tickers.json.
///
/// Returns paired symbols (e.g. "BTCUSDT", "ETHUSDT").
pub fn load_binance_tickers() -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string("binance_tickers.json")?;
    let file: BinanceTickerFile = serde_json::from_str(&content)?;
    Ok(file.data.into_iter().map(|e| e.symbol).collect())
}

/// Sync tickers from binance_tickers.json into the database.
///
/// Any ticker present in the JSON file but missing from the DB is upserted
/// with source='crypto' and status='ready'. Returns the number of newly added tickers.
pub async fn sync_crypto_tickers(pool: &PgPool) -> usize {
    let tickers = match load_binance_tickers() {
        Ok(t) => t,
        Err(e) => {
            tracing::warn!("failed to load binance_tickers.json: {e}");
            return 0;
        }
    };

    let mut added = 0usize;
    for ticker in &tickers {
        if let Err(e) = ohlcv::upsert_ticker(pool, "crypto", ticker, None).await {
            tracing::warn!(ticker, "failed to upsert crypto ticker: {e}");
            continue;
        }
        // set status='ready' for newly inserted tickers (status IS NULL)
        // Note: set_ticker_ready_if_new is hardcoded to source='vn', so we do it directly
        if let Err(e) = sqlx::query!(
            "UPDATE tickers SET status = 'ready' WHERE source = 'crypto' AND ticker = $1 AND status IS NULL",
            ticker
        )
        .execute(pool)
        .await
        {
            tracing::warn!(ticker, "failed to set crypto ticker ready: {e}");
        }
        added += 1;
    }
    added
}

/// Schedule the next run for a ticker at a fixed interval.
///
/// Simple alternative to `schedule_next_run` — all crypto tickers get the same
/// delay regardless of volume tier.
pub async fn schedule_fixed_interval(
    pool: &PgPool,
    ticker_id: i32,
    next_col: &str,
    secs: i64,
) -> Result<(), sqlx::Error> {
    assert!(
        matches!(next_col, "next_1d" | "next_1h" | "next_1m"),
        "next_col must be one of: next_1d, next_1h, next_1m"
    );

    let sql = format!(
        "UPDATE tickers SET {next_col} = NOW() + ($2 || ' seconds')::INTERVAL WHERE id = $1"
    );

    sqlx::query(&sql)
        .bind(ticker_id)
        .bind(secs)
        .execute(pool)
        .await?;

    Ok(())
}

/// Re-export commonly used vci_shared functions for convenience.
pub use vci_shared::{enhance_and_save, ensure_ticker};
