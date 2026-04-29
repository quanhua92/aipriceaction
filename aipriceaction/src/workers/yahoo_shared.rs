use serde::Deserialize;
use sqlx::PgPool;
use std::collections::HashMap;

use crate::constants::yahoo_worker;
use crate::providers::ohlcv::OhlcvData;
use crate::queries::ohlcv;
use crate::workers::vci_shared;

/// Strip `:US` suffix for Yahoo API calls.
/// Yahoo doesn't use `:XX` format; our `:US` suffix is a namespace convention
/// to disambiguate from VN tickers (e.g., VFS in VN = Viet First Securities,
/// VFS:US in Yahoo = VinFast Auto Ltd.).
pub fn yahoo_symbol(db_ticker: &str) -> &str {
    db_ticker.strip_suffix(":US").unwrap_or(db_ticker)
}

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
pub async fn ensure_yahoo_ticker(pool: &PgPool, source: &str, ticker: &str) -> sqlx::Result<i32> {
    let ticker_id = ohlcv::upsert_ticker(pool, source, ticker, None)
        .await?;
    tracing::debug!(ticker, ticker_id, source, "ensure_yahoo_ticker: upsert done (no status change)");
    Ok(ticker_id)
}

/// Re-export commonly used functions for convenience.
pub use vci_shared::enhance_and_save;
pub use crate::workers::binance_shared::schedule_fixed_interval;

/// Detect stock splits / data corruption by comparing newly fetched daily bars
/// against existing DB data. If prices diverge beyond a threshold, sets
/// `dividend-detected` status so the bootstrap worker re-downloads full history.
///
/// Follows the same logic as `vci_shared::detect_dividend` but uses Yahoo-specific
/// constants and skips the index-ticker filter (not applicable to Yahoo).
pub async fn detect_dividend(
    pool: &PgPool,
    ticker_id: i32,
    ticker: &str,
    new_data: &[OhlcvData],
) -> bool {
    if new_data.len() < 2 {
        return false;
    }

    // Exclude the last candle — it may be today's intraday data and can
    // diverge due to normal price movement.
    let compare_data = &new_data[..new_data.len() - 1];

    let existing = match ohlcv::get_ohlcv(pool, ticker_id, "1D", Some(yahoo_worker::DIVIDEND_CHECK_BARS)).await {
        Ok(rows) => rows,
        Err(_) => return false,
    };

    if existing.is_empty() {
        return false;
    }

    // Build a map of date -> close from existing DB data
    let mut existing_map: HashMap<String, f64> = HashMap::new();
    for row in &existing {
        let date_key = row.time.format("%Y-%m-%d").to_string();
        existing_map.insert(date_key, row.close);
    }

    // Compare overlapping dates — find the worst (largest) divergence
    let mut max_ratio: f64 = 0.0;
    let mut worst_date = String::new();
    let mut worst_existing_close = 0.0;
    let mut worst_api_close = 0.0;
    let mut divergence_count = 0usize;

    for d in compare_data {
        let date_key = d.time.format("%Y-%m-%d").to_string();
        if let Some(&existing_close) = existing_map.get(&date_key) {
            if existing_close > 0.0 && d.close > 0.0 {
                let ratio = existing_close / d.close;
                if ratio > yahoo_worker::DIVIDEND_RATIO_THRESHOLD {
                    divergence_count += 1;
                    if ratio > max_ratio {
                        max_ratio = ratio;
                        worst_date = date_key;
                        worst_existing_close = existing_close;
                        worst_api_close = d.close;
                    }
                }
            }
        }
    }

    if max_ratio > yahoo_worker::DIVIDEND_RATIO_THRESHOLD {
        if divergence_count < yahoo_worker::DIVIDEND_MIN_DIVERGING_BARS {
            tracing::warn!(
                "[YAHOO-DIVIDEND] ticker={}, SUSPECTED but REJECTED — diverging_dates={} < min_required={}, worst_ratio={:.4}, worst_date={}",
                ticker, divergence_count, yahoo_worker::DIVIDEND_MIN_DIVERGING_BARS, max_ratio, worst_date
            );
            return false;
        }
        let price_drop_pct = (1.0 - worst_api_close / worst_existing_close) * 100.0;
        tracing::warn!(
            "[YAHOO-DIVIDEND] ticker={}, date={}, db_close={}, api_close={}, ratio={:.4}, drop={:.2}%, diverging_dates={}, min_required={}, threshold={:.2}, compared_bars={}, db_bars={}",
            ticker, worst_date, worst_existing_close, worst_api_close, max_ratio, price_drop_pct,
            divergence_count, yahoo_worker::DIVIDEND_MIN_DIVERGING_BARS, yahoo_worker::DIVIDEND_RATIO_THRESHOLD, compare_data.len(), existing.len()
        );
        tracing::warn!(
            "[YAHOO-DIVIDEND] ticker={}, action=set status 'dividend-detected' → bootstrap worker will re-download full history",
            ticker
        );
        if let Err(e) = ohlcv::update_ticker_status(pool, ticker_id, "dividend-detected").await {
            tracing::error!("[YAHOO-DIVIDEND] ticker={}, ticker_id={}, FAILED to set dividend-detected status: {}", ticker, ticker_id, e);
        } else {
            tracing::warn!("[YAHOO-DIVIDEND] ticker={}, ticker_id={}, status updated to 'dividend-detected'", ticker, ticker_id);
        }
        return true;
    }

    false
}
