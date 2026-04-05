use serde::Deserialize;
use sqlx::PgPool;

use crate::queries::ohlcv;
use crate::workers::vci_shared;

// ---------------------------------------------------------------------------
// yahoo_tickers.json schema
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
struct YahooTickerEntry {
    symbol: String,
}

#[derive(Deserialize)]
struct YahooTickerFile {
    data: Vec<YahooTickerEntry>,
}

/// Load tickers from yahoo_tickers.json.
pub fn load_yahoo_tickers() -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string("global_tickers.json")?;
    let file: YahooTickerFile = serde_json::from_str(&content)?;
    Ok(file.data.into_iter().map(|e| e.symbol).collect())
}

/// Sync tickers from yahoo_tickers.json into the database.
///
/// New tickers are upserted with source='yahoo' and get status='full-download-requested'
/// so the bootstrap worker picks them up.
pub async fn sync_yahoo_tickers(pool: &PgPool) -> usize {
    tracing::info!("sync_yahoo_tickers: starting");
    let tickers = match load_yahoo_tickers() {
        Ok(t) => t,
        Err(e) => {
            tracing::warn!("failed to load global_tickers.json: {e}");
            return 0;
        }
    };

    let mut added = 0usize;
    for ticker in &tickers {
        if let Err(e) = ohlcv::upsert_ticker(pool, "yahoo", ticker, None).await {
            tracing::warn!(ticker, "failed to upsert yahoo ticker: {e}");
            continue;
        }
        let result = sqlx::query!(
            "UPDATE tickers SET status = 'full-download-requested' WHERE source = 'yahoo' AND ticker = $1 AND status IS NULL",
            ticker
        )
        .execute(pool)
        .await;
        match result {
            Ok(rows) => {
                if rows.rows_affected() > 0 {
                    tracing::info!(ticker, "sync_yahoo_tickers: set status = full-download-requested (new ticker)");
                }
            }
            Err(e) => {
                tracing::warn!(ticker, "failed to set full-download-requested: {e}");
            }
        }
        added += 1;
    }
    tracing::info!("sync_yahoo_tickers: done, processed {added} tickers");
    added
}

/// Ensure a yahoo ticker exists in the database, return its id.
pub async fn ensure_yahoo_ticker(pool: &PgPool, source: &str, ticker: &str) -> i32 {
    let ticker_id = ohlcv::upsert_ticker(pool, source, ticker, None)
        .await
        .expect("failed to upsert ticker");
    tracing::debug!(ticker, ticker_id, source, "ensure_yahoo_ticker: upsert done (no status change)");
    ticker_id
}

/// Re-export commonly used functions for convenience.
pub use vci_shared::enhance_and_save;
pub use crate::workers::binance_shared::schedule_fixed_interval;
