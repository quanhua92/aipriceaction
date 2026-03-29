use sqlx::PgPool;
use std::sync::Arc;
use tokio::time::{sleep, Duration};

use crate::constants::binance_worker;
use crate::providers::binance::BinanceProvider;
use crate::queries::ohlcv;
use crate::workers::binance_shared;

pub async fn run(pool: PgPool) {
    let provider = match BinanceProvider::new(120) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            tracing::error!("Binance daily worker: failed to create provider: {e}");
            return;
        }
    };

    let api_clients = provider.client_count().saturating_sub(1);
    tracing::info!(
        "Binance daily worker started (api_clients={}, concurrency={})",
        api_clients,
        binance_worker::concurrent_batches(api_clients),
    );

    loop {
        // 1. Discover new tickers from binance_tickers.json
        let added = binance_shared::sync_crypto_tickers(&pool).await;
        if added > 0 {
            tracing::info!("Binance daily worker: synced {added} crypto tickers from binance_tickers.json");
        }

        // 2. Normal incremental sync for 'ready' tickers
        let mut tickers = match ohlcv::get_due_tickers(&pool, "crypto", "next_1d").await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("Binance daily worker: failed to load due tickers: {e}");
                sleep(Duration::from_secs(binance_worker::DAILY_LOOP_SECS)).await;
                continue;
            }
        };

        use rand::seq::SliceRandom;
        tickers.shuffle(&mut rand::thread_rng());
        tickers.truncate(binance_worker::DUE_TICKER_BATCH_SIZE);

        if !tickers.is_empty() {
            tracing::info!("Binance daily worker: syncing {} due tickers", tickers.len());

            let concurrency = binance_worker::concurrent_batches(api_clients);
            for chunk in tickers.chunks(concurrency) {
                let mut handles = tokio::task::JoinSet::new();
                for ticker_entry in chunk {
                    let pool = pool.clone();
                    let provider = provider.clone();
                    let ticker = ticker_entry.ticker.clone();
                    handles.spawn(async move {
                        let ticker_id = binance_shared::ensure_crypto_ticker(&pool, "crypto", &ticker).await;

                        match provider.get_history(&ticker, "1d", binance_worker::DAILY_LIMIT).await {
                            Ok(data) => {
                                binance_shared::enhance_and_save(&pool, ticker_id, &data, "1D").await;

                                // Flag for full download if daily data is insufficient
                                if let Ok(count) = ohlcv::count_ohlcv(&pool, "crypto", Some(&ticker), Some("1D")).await {
                                    if count < 3 {
                                        tracing::warn!(ticker, count, ticker_id, "daily: records < 3, requesting full download");
                                        let _ = ohlcv::update_ticker_status(&pool, ticker_id, "full-download-requested").await;
                                    }
                                }

                                if let Err(e) = binance_shared::schedule_fixed_interval(
                                    &pool, ticker_id, "next_1d",
                                    binance_worker::SCHEDULE_DAILY_SECS,
                                )
                                .await
                                {
                                    tracing::warn!(ticker, "failed to schedule next daily run: {e}");
                                }

                                tracing::info!(ticker, count = data.len(), "daily sync OK");
                                false
                            }
                            Err(e) => {
                                let rate_limited =
                                    e.to_string().contains("429") || e.to_string().contains("403");
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
                    tracing::warn!(
                        rate_limited,
                        total = chunk.len(),
                        "rate limited in batch, cooling down {}s",
                        binance_worker::RATE_LIMIT_COOLDOWN_SECS
                    );
                    sleep(Duration::from_secs(binance_worker::RATE_LIMIT_COOLDOWN_SECS)).await;
                }
            }
        } else {
            tracing::debug!("Binance daily worker: no due tickers");
        }

        sleep(Duration::from_secs(binance_worker::DAILY_LOOP_SECS)).await;
    }
}
