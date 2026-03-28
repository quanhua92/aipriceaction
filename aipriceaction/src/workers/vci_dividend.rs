use sqlx::PgPool;
use tokio::time::{sleep, Duration};

use crate::constants::vci_worker;
use crate::providers::vci::VciProvider;
use crate::queries::ohlcv;
use crate::workers::vci_shared;

pub async fn run(pool: PgPool) {
    tracing::info!("VCI dividend worker started");

    let provider = match VciProvider::new(30) {
        Ok(p) => p,
        Err(e) => {
            tracing::error!("VCI dividend worker: failed to create provider: {e}");
            return;
        }
    };

    loop {
        // Find tickers flagged for full re-download
        let tickers = match ohlcv::get_tickers_by_statuses(&pool, "vn", &["dividend-detected", "full-download-requested"]).await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("VCI dividend worker: failed to load flagged tickers: {e}");
                sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await;
                continue;
            }
        };

        if tickers.is_empty() {
            sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await;
            continue;
        }

        for ticker_entry in &tickers {
            let ticker = &ticker_entry.ticker;
            let ticker_id = ticker_entry.id;
            tracing::info!(ticker, ticker_id, "starting dividend recovery");

            // Delete all existing data for this ticker
            if let Err(e) = ohlcv::delete_indicators_for_ticker(&pool, ticker_id).await {
                tracing::error!(ticker, ticker_id, "delete indicators failed: {e}");
                continue;
            }
            if let Err(e) = ohlcv::delete_ohlcv_for_ticker(&pool, ticker_id).await {
                tracing::error!(ticker, ticker_id, "delete ohlcv failed: {e}");
                continue;
            }

            tracing::info!(ticker, "deleted all existing data, re-downloading");

            // Re-download full history for each interval
            for interval in &["1D", "1h", "1m"] {
                let chunk_size = match *interval {
                    "1m" => vci_worker::DIVIDEND_CHUNK_SIZE_MINUTE,
                    "1h" => vci_worker::DIVIDEND_CHUNK_SIZE_HOURLY,
                    _ => vci_worker::DIVIDEND_CHUNK_SIZE_DAILY,
                };
                let api_interval = match *interval {
                    "1h" => "1H",
                    other => other,
                };
                let mut all_data = Vec::new();
                let mut end_ts = chrono::Utc::now().timestamp();

                loop {
                    match provider.get_history(ticker, api_interval, chunk_size, Some(end_ts)).await {
                        Ok(data) => {
                            if data.is_empty() {
                                break;
                            }
                            let earliest = data.first().unwrap().time.timestamp();
                            let fetched = data.len() as u32;
                            all_data.extend(data);

                            if fetched < chunk_size {
                                break;
                            }
                            end_ts = earliest - 1;
                        }
                        Err(e) => {
                            tracing::warn!(ticker, interval, "dividend chunk fetch failed: {e}");
                            break;
                        }
                    }

                    sleep(Duration::from_secs(vci_worker::DIVIDEND_CHUNK_SLEEP_SECS)).await;
                }

                if !all_data.is_empty() {
                    // Sort chronologically
                    all_data.sort_by(|a, b| a.time.cmp(&b.time));
                    vci_shared::enhance_and_save(&pool, ticker_id, &all_data, interval).await;
                    tracing::info!(ticker, interval, count = all_data.len(), "dividend re-download OK");
                }

                sleep(Duration::from_secs(vci_worker::DIVIDEND_CHUNK_SLEEP_SECS)).await;
            }

            // Mark as ready again
            if let Err(e) = ohlcv::update_ticker_status(&pool, ticker_id, "ready").await {
                tracing::error!(ticker, ticker_id, "failed to set status ready: {e}");
            } else {
                tracing::info!(ticker, "dividend recovery complete");
            }
        }

        sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await;
    }
}
