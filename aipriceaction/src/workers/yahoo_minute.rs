use sqlx::PgPool;
use std::sync::Arc;
use tokio::time::{sleep, Duration};

use crate::constants::yahoo_worker;
use crate::providers::yahoo::YahooProvider;
use crate::queries::ohlcv;
use crate::workers::yahoo_shared;

pub async fn run(pool: PgPool, redis_client: Option<crate::redis::RedisClient>) {
    tracing::info!(
        "Yahoo minute worker: waiting {} seconds before first sync...",
        yahoo_worker::MINUTE_INITIAL_DELAY_SECS
    );
    sleep(Duration::from_secs(yahoo_worker::MINUTE_INITIAL_DELAY_SECS)).await;

    let provider = match YahooProvider::with_options(60, true, true) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            tracing::error!("Yahoo minute worker: failed to create provider: {e}");
            return;
        }
    };

    let api_clients = provider.client_count();
    tracing::info!(
        "Yahoo minute worker started (api_clients={}, concurrency={})",
        api_clients,
        yahoo_worker::concurrent_batches(api_clients),
    );

    loop {
        let mut tickers = match ohlcv::get_due_tickers(&pool, "yahoo", "next_1m").await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("Yahoo minute worker: failed to load due tickers: {e}");
                sleep(Duration::from_secs(yahoo_worker::MINUTE_LOOP_SECS)).await;
                continue;
            }
        };

        use rand::seq::SliceRandom;
        tickers.shuffle(&mut rand::thread_rng());
        tickers.truncate((tickers.len() as f64 * crate::constants::due_ticker_fraction()) as usize);

        if !tickers.is_empty() {
            tracing::info!("Yahoo minute worker: syncing {} due tickers", tickers.len());

            let concurrency = yahoo_worker::concurrent_batches(api_clients);
            for chunk in tickers.chunks(concurrency) {
                let mut handles = tokio::task::JoinSet::new();
                for ticker_entry in chunk {
                    let pool = pool.clone();
                    let provider = provider.clone();
                    let redis_client = redis_client.clone();
                    let ticker = ticker_entry.ticker.clone();
                    handles.spawn(async move {
                        let ticker_id = match yahoo_shared::ensure_yahoo_ticker(&pool, "yahoo", &ticker).await {
                            Ok(id) => id,
                            Err(e) => {
                                tracing::warn!(ticker, "failed to upsert ticker: {e}");
                                return false;
                            }
                        };

                        let _start_time = ohlcv::get_last_time(&pool, ticker_id, "1m").await.ok().flatten();

                        let range = yahoo_worker::MINUTE_RANGE;

                        match provider.get_history(&ticker, "1m", range).await {
                            Ok(data) => {
                                if yahoo_shared::enhance_and_save(&pool, ticker_id, &data, "1m", "yahoo", &ticker, &redis_client).await {
                                    match yahoo_shared::schedule_fixed_interval(
                                        &pool,
                                        ticker_id,
                                        "next_1m",
                                        yahoo_worker::schedule_secs(&ticker, yahoo_worker::SCHEDULE_MINUTE_SECS),
                                    )
                                    .await
                                    {
                                        Ok(next_run) => tracing::info!(ticker, count = data.len(), next = %next_run, "minute sync OK"),
                                        Err(e) => tracing::warn!(ticker, count = data.len(), "minute sync OK but scheduling failed: {e}"),
                                    }
                                } else {
                                    tracing::warn!(ticker, count = data.len(), "minute sync upsert failed, skipping schedule");
                                }
                                false
                            }
                            Err(e) => {
                                let rate_limited =
                                    e.to_string().contains("429") || e.to_string().contains("Too many requests");
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
                        yahoo_worker::RATE_LIMIT_COOLDOWN_SECS
                    );
                    sleep(Duration::from_secs(yahoo_worker::RATE_LIMIT_COOLDOWN_SECS)).await;
                }
            }
        } else {
            tracing::debug!("Yahoo minute worker: no due tickers");
        }

        sleep(Duration::from_secs(yahoo_worker::MINUTE_LOOP_SECS)).await;
    }
}
