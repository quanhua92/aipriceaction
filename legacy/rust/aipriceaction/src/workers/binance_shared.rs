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
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    copy_from: Option<String>,
}

#[derive(Deserialize)]
struct BinanceTickerFile {
    data: Vec<BinanceTickerEntry>,
}

/// Load tickers from binance_tickers.json.
///
/// Returns paired symbols (e.g. "BTCUSDT", "ETHUSDT").
pub fn load_binance_tickers() -> Result<Vec<String>, Box<dyn std::error::Error>> {
    Ok(load_binance_tickers_with_meta()?.into_iter().map(|(s, _, _)| s).collect())
}

/// Load tickers with full metadata from binance_tickers.json.
///
/// Returns `(symbol, status, copy_from)` tuples. Used by the bootstrap worker
/// to look up `copy_from` for newly-seeded tickers.
pub fn load_binance_tickers_with_meta()
-> Result<Vec<(String, Option<String>, Option<String>)>, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string("binance_tickers.json")?;
    let file: BinanceTickerFile = serde_json::from_str(&content)?;
    Ok(file.data.into_iter().map(|e| (e.symbol, e.status, e.copy_from)).collect())
}

/// Sync tickers from binance_tickers.json into the database.
///
/// Three sequential passes:
///   1. Upsert active tickers (status is None or non-"delisted"); new rows get
///      `status='full-download-requested'` so the bootstrap worker picks them up.
///   2. Apply explicit status overrides (e.g. `"status": "delisted"`).
///   3. Orphan-delist safety net: any DB crypto ticker not present in the JSON
///      is marked `delisted`.
///
/// Returns the number of active tickers processed (Pass 1).
pub async fn sync_crypto_tickers(pool: &PgPool) -> usize {
    tracing::info!("sync_crypto_tickers: starting");
    let entries = match load_binance_tickers_with_meta() {
        Ok(e) => e,
        Err(e) => {
            tracing::warn!("failed to load binance_tickers.json: {e}");
            return 0;
        }
    };

    // PASS 1: upsert active tickers (skip entries with explicit "delisted" status)
    let mut processed = 0usize;
    for (ticker, status, _copy_from) in &entries {
        if status.as_deref() == Some("delisted") {
            continue;
        }
        if let Err(e) = ohlcv::upsert_ticker(pool, "crypto", ticker, None).await {
            tracing::warn!(ticker, "failed to upsert crypto ticker: {e}");
            continue;
        }
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
        processed += 1;
    }

    // PASS 2: apply explicit status overrides (e.g. "delisted")
    for (ticker, status, _copy_from) in &entries {
        if let Some(st) = status {
            match sqlx::query!(
                "UPDATE tickers SET status = $1 WHERE source = 'crypto' AND ticker = $2",
                st,
                ticker
            )
            .execute(pool)
            .await
            {
                Ok(rows) => {
                    if rows.rows_affected() > 0 {
                        tracing::info!(ticker, status = %st, "sync_crypto_tickers: applied explicit status");
                    }
                }
                Err(e) => {
                    tracing::warn!(ticker, "failed to apply status {st}: {e}");
                }
            }
        }
    }

    // PASS 3: orphan-delist safety net
    let all_syms: Vec<String> = entries.iter().map(|(s, _, _)| s.clone()).collect();
    let orphan_result = sqlx::query!(
        "UPDATE tickers SET status = 'delisted'
         WHERE source = 'crypto'
           AND status IS DISTINCT FROM 'delisted'
           AND ticker <> ALL($1)",
        &all_syms
    )
    .execute(pool)
    .await;
    if let Ok(rows) = orphan_result {
        if rows.rows_affected() > 0 {
            tracing::warn!(orphans = rows.rows_affected(), "sync_crypto_tickers: delisted orphan tickers not in JSON");
        }
    }

    let explicit_count = entries.iter().filter(|(_, s, _)| s.is_some()).count();
    tracing::info!(
        "sync_crypto_tickers: done — {processed} active, {explicit_count} explicit-status, orphan-net applied"
    );
    processed
}

/// Ensure a crypto ticker exists in the database, return its id.
///
/// Crypto-specific version — does NOT set status (unlike VN's set_ticker_ready_if_new).
/// New crypto tickers are handled by sync_crypto_tickers which sets full-download-requested.
pub async fn ensure_crypto_ticker(pool: &PgPool, source: &str, ticker: &str) -> sqlx::Result<i32> {
    let ticker_id = ohlcv::upsert_ticker(pool, source, ticker, None)
        .await?;
    tracing::debug!(ticker, ticker_id, source, "ensure_crypto_ticker: upsert done (no status change)");
    Ok(ticker_id)
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
