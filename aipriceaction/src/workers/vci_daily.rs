use sqlx::PgPool;
use std::sync::Arc;
use tokio::time::{sleep, Duration};

use crate::constants::vci_worker;
use crate::providers::vci::VciProvider;
use crate::queries::ohlcv;
use crate::workers::vci_shared;

pub async fn run(pool: PgPool) {
    let provider = match VciProvider::new(60) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            tracing::error!("VCI daily worker: failed to create provider: {e}");
            return;
        }
    };

    tracing::info!("VCI daily worker started (clients={}, concurrency={})", provider.client_count(), vci_worker::concurrent_batches(provider.client_count()));

    loop {
        let trading = vci_shared::is_trading_hours();

        let tickers = match ohlcv::get_tickers_by_status(&pool, "vn", "ready").await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("VCI daily worker: failed to load tickers: {e}");
                let sleep_secs = if trading {
                    vci_worker::DAILY_LOOP_TRADE_SECS
                } else {
                    vci_worker::DAILY_LOOP_OFF_SECS
                };
                sleep(Duration::from_secs(sleep_secs)).await;
                continue;
            }
        };

        if !tickers.is_empty() {
            tracing::info!("VCI daily worker: syncing {} tickers (trading={})", tickers.len(), trading);

            let mut ticker_symbols: Vec<String> = tickers.iter().map(|t| t.ticker.clone()).collect();
            {
                use rand::seq::SliceRandom;
                let mut rng = rand::thread_rng();
                ticker_symbols.shuffle(&mut rng);
            }

            let concurrency = vci_worker::concurrent_batches(provider.client_count());
            for chunk in ticker_symbols.chunks(concurrency) {
                let mut handles = tokio::task::JoinSet::new();
                for ticker in chunk {
                    let pool = pool.clone();
                    let provider = provider.clone();
                    let ticker = ticker.clone();
                    handles.spawn(async move {
                        let ticker_id = vci_shared::ensure_ticker(&pool, "vn", &ticker).await;

                        match provider.get_history(&ticker, "1D", vci_worker::DAILY_COUNTBACK, None).await {
                            Ok(data) => {
                                if vci_shared::detect_dividend(&pool, ticker_id, &ticker, &data).await {
                                    tracing::warn!(ticker, "dividend detected, skipping");
                                    return false;
                                }
                                vci_shared::enhance_and_save(&pool, ticker_id, &data, "1D").await;

                                // Flag for full download if daily data is insufficient
                                if let Ok(count) = ohlcv::count_ohlcv(&pool, "vn", Some(&ticker), Some("1D")).await {
                                    if count < 3 {
                                        tracing::warn!(ticker, count, "daily records < 3, requesting full download");
                                        let _ = ohlcv::update_ticker_status(&pool, ticker_id, "full-download-requested").await;
                                    }
                                }

                                tracing::info!(ticker, count = data.len(), "daily sync OK");
                                false
                            }
                            Err(e) => {
                                let rate_limited = e.to_string().contains("429");
                                tracing::warn!(ticker, "daily fetch failed: {e}");
                                rate_limited
                            }
                        }
                    });
                }

                let mut rate_limited = 0usize;
                while let Some(result) = handles.join_next().await {
                    if result.unwrap_or(false) {
                        rate_limited += 1;
                    }
                }

                if rate_limited > 0 {
                    tracing::warn!(rate_limited, total = chunk.len(), "rate limited in batch, cooling down {}s", vci_worker::RATE_LIMIT_COOLDOWN_SECS);
                    sleep(Duration::from_secs(vci_worker::RATE_LIMIT_COOLDOWN_SECS)).await;
                }
            }
        } else {
            tracing::debug!("VCI daily worker: no ready tickers");
        }

        let sleep_secs = if trading {
            vci_worker::DAILY_LOOP_TRADE_SECS
        } else {
            vci_worker::DAILY_LOOP_OFF_SECS
        };
        sleep(Duration::from_secs(sleep_secs)).await;
    }
}
