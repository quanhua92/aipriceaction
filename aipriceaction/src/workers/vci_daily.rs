use chrono::Utc;
use sqlx::PgPool;
use std::sync::Arc;
use tokio::time::{sleep, Duration};

use crate::constants::vci_worker;
use crate::providers::vci::VciProvider;
use crate::queries::ohlcv;
use crate::workers::vci_shared;

pub async fn run(pool: PgPool) {
    tracing::info!("VCI daily worker started (concurrency={})", vci_worker::concurrent_batches());

    let provider = match VciProvider::new(30) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            tracing::error!("VCI daily worker: failed to create provider: {e}");
            return;
        }
    };

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

            let concurrency = vci_worker::concurrent_batches();
            for chunk in ticker_symbols.chunks(concurrency) {
                let mut handles = tokio::task::JoinSet::new();
                for ticker in chunk {
                    let pool = pool.clone();
                    let provider = provider.clone();
                    let ticker = ticker.clone();
                    handles.spawn(async move {
                        let ticker_id = vci_shared::ensure_ticker(&pool, "vn", &ticker).await;
                        let last_time = vci_shared::get_last_time(&pool, ticker_id, "1D").await;

                        let count_back = match last_time {
                            Some(t) if (Utc::now() - t).num_days() < vci_worker::DAILY_GAP_THRESHOLD_DAYS => {
                                vci_worker::DAILY_COUNTBACK_RECENT
                            }
                            _ => vci_worker::DAILY_COUNTBACK_GAP,
                        };

                        match provider.get_history(&ticker, "1D", count_back, None).await {
                            Ok(data) => {
                                if vci_shared::detect_dividend(&pool, ticker_id, &ticker, &data).await {
                                    tracing::warn!(ticker, "dividend detected, skipping");
                                    return;
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
                            }
                            Err(e) => {
                                tracing::warn!(ticker, "daily fetch failed: {e}");
                            }
                        }
                    });
                }

                while let Some(_result) = handles.join_next().await {}
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
