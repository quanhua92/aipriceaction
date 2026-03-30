use sqlx::PgPool;
use std::sync::Arc;
use tokio::time::{sleep, Duration};

use crate::constants::binance_worker;
use crate::providers::binance::BinanceProvider;
use crate::queries::ohlcv;
use crate::workers::binance_shared;

pub async fn run(pool: PgPool) {
    // Initial delay before first sync
    tracing::info!(
        "Binance hourly worker: waiting {} seconds before first sync...",
        binance_worker::HOURLY_INITIAL_DELAY_SECS
    );
    sleep(Duration::from_secs(binance_worker::HOURLY_INITIAL_DELAY_SECS)).await;

    let provider = match BinanceProvider::new(120) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            tracing::error!("Binance hourly worker: failed to create provider: {e}");
            return;
        }
    };

    let api_clients = provider.client_count().saturating_sub(1);
    tracing::info!(
        "Binance hourly worker started (api_clients={}, concurrency={})",
        api_clients,
        binance_worker::concurrent_batches(api_clients),
    );

    loop {
        let mut tickers = match ohlcv::get_due_tickers(&pool, "crypto", "next_1h").await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("Binance hourly worker: failed to load due tickers: {e}");
                sleep(Duration::from_secs(binance_worker::HOURLY_LOOP_SECS)).await;
                continue;
            }
        };

        use rand::seq::SliceRandom;
        tickers.shuffle(&mut rand::thread_rng());
        tickers.truncate(binance_worker::DUE_TICKER_BATCH_SIZE);

        if !tickers.is_empty() {
            tracing::info!("Binance hourly worker: syncing {} due tickers", tickers.len());

            let concurrency = binance_worker::concurrent_batches(api_clients);
            for chunk in tickers.chunks(concurrency) {
                let mut handles = tokio::task::JoinSet::new();
                for ticker_entry in chunk {
                    let pool = pool.clone();
                    let provider = provider.clone();
                    let ticker = ticker_entry.ticker.clone();
                    handles.spawn(async move {
                        let ticker_id = binance_shared::ensure_crypto_ticker(&pool, "crypto", &ticker).await;

                        let start_time = ohlcv::get_last_time(&pool, ticker_id, "1h").await.ok().flatten();

                        match provider.get_history_since(&ticker, "1h", binance_worker::HOURLY_LIMIT, start_time).await {
                            Ok(data) => {
                                binance_shared::enhance_and_save(&pool, ticker_id, &data, "1h").await;

                                match binance_shared::schedule_fixed_interval(
                                    &pool,
                                    ticker_id,
                                    "next_1h",
                                    binance_worker::SCHEDULE_HOURLY_SECS,
                                )
                                .await
                                {
                                    Ok(next_run) => tracing::info!(ticker, count = data.len(), next = %next_run, "hourly sync OK"),
                                    Err(e) => tracing::warn!(ticker, count = data.len(), "hourly sync OK but scheduling failed: {e}"),
                                }
                                false
                            }
                            Err(e) => {
                                let rate_limited =
                                    e.to_string().contains("429") || e.to_string().contains("403");
                                tracing::warn!(ticker, "hourly fetch failed: {e}");
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
            tracing::debug!("Binance hourly worker: no due tickers");
        }

        sleep(Duration::from_secs(binance_worker::HOURLY_LOOP_SECS)).await;
    }
}
