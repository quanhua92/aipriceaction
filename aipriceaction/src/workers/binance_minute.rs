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
        "Binance minute worker: waiting {} seconds before first sync...",
        binance_worker::MINUTE_INITIAL_DELAY_SECS
    );
    sleep(Duration::from_secs(binance_worker::MINUTE_INITIAL_DELAY_SECS)).await;

    let provider = match BinanceProvider::new(120) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            tracing::error!("Binance minute worker: failed to create provider: {e}");
            return;
        }
    };

    let api_clients = provider.client_count().saturating_sub(1);
    tracing::info!(
        "Binance minute worker started (api_clients={}, concurrency={})",
        api_clients,
        binance_worker::concurrent_batches(api_clients),
    );

    loop {
        let mut tickers = match ohlcv::get_due_tickers(&pool, "crypto", "next_1m").await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("Binance minute worker: failed to load due tickers: {e}");
                sleep(Duration::from_secs(binance_worker::MINUTE_LOOP_SECS)).await;
                continue;
            }
        };

        use rand::seq::SliceRandom;
        tickers.shuffle(&mut rand::thread_rng());
        tickers.truncate(binance_worker::DUE_TICKER_BATCH_SIZE);

        if !tickers.is_empty() {
            tracing::info!("Binance minute worker: syncing {} due tickers", tickers.len());

            let concurrency = binance_worker::concurrent_batches(api_clients);
            for chunk in tickers.chunks(concurrency) {
                let mut handles = tokio::task::JoinSet::new();
                for ticker_entry in chunk {
                    let pool = pool.clone();
                    let provider = provider.clone();
                    let ticker = ticker_entry.ticker.clone();
                    handles.spawn(async move {
                        let ticker_id = binance_shared::ensure_crypto_ticker(&pool, "crypto", &ticker).await;

                        // Only fetch from the last record we have
                        let start_time = ohlcv::get_last_time(&pool, ticker_id, "1m").await.ok().flatten();

                        match provider.get_history_since(&ticker, "1m", binance_worker::MINUTE_LIMIT, start_time).await {
                            Ok(data) => {
                                binance_shared::enhance_and_save(&pool, ticker_id, &data, "1m").await;

                                if let Err(e) = binance_shared::schedule_fixed_interval(
                                    &pool,
                                    ticker_id,
                                    "next_1m",
                                    binance_worker::SCHEDULE_MINUTE_SECS,
                                )
                                .await
                                {
                                    tracing::warn!(ticker, "failed to schedule next minute run: {e}");
                                }

                                tracing::info!(ticker, count = data.len(), "minute sync OK");
                                false
                            }
                            Err(e) => {
                                let rate_limited =
                                    e.to_string().contains("429") || e.to_string().contains("403");
                                tracing::warn!(ticker, "minute fetch failed: {e}");
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
            tracing::debug!("Binance minute worker: no due tickers");
        }

        sleep(Duration::from_secs(binance_worker::MINUTE_LOOP_SECS)).await;
    }
}
