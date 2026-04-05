use sqlx::PgPool;
use std::sync::Arc;
use tokio::time::{sleep, Duration};

use crate::constants::yahoo_worker;
use crate::providers::yahoo::YahooProvider;
use crate::queries::ohlcv;
use crate::workers::yahoo_shared;

pub async fn run(pool: PgPool) {
    let provider = match YahooProvider::with_options(60, true, true) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            tracing::error!("Yahoo daily worker: failed to create provider: {e}");
            return;
        }
    };

    let api_clients = provider.client_count().saturating_sub(1);
    tracing::info!(
        "Yahoo daily worker started (api_clients={}, concurrency={})",
        api_clients,
        yahoo_worker::concurrent_batches(api_clients),
    );

    loop {
        // 1. Discover new tickers from yahoo_tickers.json
        let added = yahoo_shared::sync_yahoo_tickers(&pool).await;
        if added > 0 {
            tracing::info!("Yahoo daily worker: synced {added} yahoo tickers from global_tickers.json");
        }

        // 2. Normal incremental sync for 'ready' tickers
        let mut tickers = match ohlcv::get_due_tickers(&pool, "yahoo", "next_1d").await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("Yahoo daily worker: failed to load due tickers: {e}");
                sleep(Duration::from_secs(yahoo_worker::DAILY_LOOP_SECS)).await;
                continue;
            }
        };

        use rand::seq::SliceRandom;
        tickers.shuffle(&mut rand::thread_rng());
        tickers.truncate(yahoo_worker::DUE_TICKER_BATCH_SIZE);

        if !tickers.is_empty() {
            tracing::info!("Yahoo daily worker: syncing {} due tickers", tickers.len());

            let concurrency = yahoo_worker::concurrent_batches(api_clients);
            for chunk in tickers.chunks(concurrency) {
                let mut handles = tokio::task::JoinSet::new();
                for ticker_entry in chunk {
                    let pool = pool.clone();
                    let provider = provider.clone();
                    let ticker = ticker_entry.ticker.clone();
                    handles.spawn(async move {
                        let ticker_id = yahoo_shared::ensure_yahoo_ticker(&pool, "yahoo", &ticker).await;

                        // Check existing daily count before fetching
                        let needs_full_download = match ohlcv::count_ohlcv(&pool, "yahoo", Some(&ticker), Some("1D")).await {
                            Ok(count) if count < 3 => {
                                tracing::warn!(ticker, count, ticker_id, "daily: records < 3, requesting full download");
                                let _ = ohlcv::update_ticker_status(&pool, ticker_id, "full-download-requested").await;
                                true
                            }
                            _ => false,
                        };

                        if needs_full_download {
                            return false;
                        }

                        let _start_time = ohlcv::get_last_time(&pool, ticker_id, "1D").await.ok().flatten();

                        match provider.get_history(&ticker, "1d", yahoo_worker::DAILY_RANGE).await {
                            Ok(data) => {
                                yahoo_shared::enhance_and_save(&pool, ticker_id, &data, "1D").await;

                                match yahoo_shared::schedule_fixed_interval(
                                    &pool, ticker_id, "next_1d",
                                    yahoo_worker::SCHEDULE_DAILY_SECS,
                                )
                                .await
                                {
                                    Ok(next_run) => tracing::info!(ticker, count = data.len(), next = %next_run, "daily sync OK"),
                                    Err(e) => tracing::warn!(ticker, count = data.len(), "daily sync OK but scheduling failed: {e}"),
                                }
                                false
                            }
                            Err(e) => {
                                let rate_limited =
                                    e.to_string().contains("429") || e.to_string().contains("Too many requests");
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
                        yahoo_worker::RATE_LIMIT_COOLDOWN_SECS
                    );
                    sleep(Duration::from_secs(yahoo_worker::RATE_LIMIT_COOLDOWN_SECS)).await;
                }
            }
        } else {
            tracing::debug!("Yahoo daily worker: no due tickers");
        }

        sleep(Duration::from_secs(yahoo_worker::DAILY_LOOP_SECS)).await;
    }
}
