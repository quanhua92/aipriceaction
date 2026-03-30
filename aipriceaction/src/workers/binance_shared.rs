use chrono::{DateTime, Utc};
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
/// New tickers are upserted with source='crypto' but status is left NULL
/// (they need a full download before becoming 'ready').
/// Returns the number of tickers processed.
pub async fn sync_crypto_tickers(pool: &PgPool) -> usize {
    tracing::info!("sync_crypto_tickers: starting");
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
        // New tickers get 'full-download-requested' so the bootstrap worker picks them up
        let result = sqlx::query!(
            "UPDATE tickers SET status = 'full-download-requested' WHERE source = 'crypto' AND ticker = $1 AND status IS NULL",
            ticker
        )
        .execute(pool)
        .await;
        match result {
            Ok(rows) => {
                if rows.rows_affected() > 0 {
                    tracing::info!(ticker, "sync_crypto_tickers: set status = full-download-requested (new ticker)");
                }
            }
            Err(e) => {
                tracing::warn!(ticker, "failed to set full-download-requested: {e}");
            }
        }
        added += 1;
    }
    tracing::info!("sync_crypto_tickers: done, processed {added} tickers");
    added
}

/// Ensure a crypto ticker exists in the database, return its id.
///
/// Crypto-specific version — does NOT set status (unlike VN's set_ticker_ready_if_new).
/// New crypto tickers are handled by sync_crypto_tickers which sets full-download-requested.
pub async fn ensure_crypto_ticker(pool: &PgPool, source: &str, ticker: &str) -> i32 {
    let ticker_id = ohlcv::upsert_ticker(pool, source, ticker, None)
        .await
        .expect("failed to upsert ticker");
    tracing::debug!(ticker, ticker_id, source, "ensure_crypto_ticker: upsert done (no status change)");
    ticker_id
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
) -> Result<DateTime<Utc>, sqlx::Error> {
    assert!(
        matches!(next_col, "next_1d" | "next_1h" | "next_1m"),
        "next_col must be one of: next_1d, next_1h, next_1m"
    );

    let sql = format!(
        "UPDATE tickers SET {next_col} = NOW() + ($2 || ' seconds')::INTERVAL WHERE id = $1 RETURNING {next_col}"
    );

    let row: (DateTime<Utc>,) = sqlx::query_as(&sql)
        .bind(ticker_id)
        .bind(secs)
        .fetch_one(pool)
        .await?;

    Ok(row.0)
}

/// Re-export commonly used vci_shared functions for convenience.
pub use vci_shared::enhance_and_save;
