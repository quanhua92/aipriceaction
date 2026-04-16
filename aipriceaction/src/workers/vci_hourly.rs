use chrono::Utc;
use sqlx::PgPool;
use std::sync::Arc;
use tokio::time::{sleep, Duration};

use crate::constants::{MAJOR_SCHEDULE_SECS, MAJOR_VN, vci_worker};
use crate::constants::vci_worker::priority;
use crate::providers::vci::VciProvider;
use crate::queries::ohlcv;
use crate::workers::{binance_shared, vci_shared};

pub async fn run(pool: PgPool, redis_client: Option<crate::redis::RedisClient>) {
    // Initial delay before first sync (2 minutes)
    tracing::info!("VCI hourly worker: waiting {} seconds before first sync...", vci_worker::HOURLY_INITIAL_DELAY_SECS);
    sleep(Duration::from_secs(vci_worker::HOURLY_INITIAL_DELAY_SECS)).await;

    let provider = match VciProvider::new(30) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            tracing::error!("VCI hourly worker: failed to create provider: {e}");
            return;
        }
    };

    tracing::info!("VCI hourly worker started (clients={}, concurrency={})", provider.client_count(), vci_worker::concurrent_batches(provider.client_count()));

    loop {
        let trading = vci_shared::is_trading_hours();

        let mut tickers = match ohlcv::get_due_tickers(&pool, "vn", "next_1h").await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("VCI hourly worker: failed to load due tickers: {e}");
                let sleep_secs = if trading {
                    vci_worker::HOURLY_LOOP_TRADE_SECS
                } else {
                    vci_worker::HOURLY_LOOP_OFF_SECS
                };
                sleep(Duration::from_secs(sleep_secs)).await;
                continue;
            }
        };
        use rand::seq::SliceRandom;
        tickers.shuffle(&mut rand::thread_rng());
        tickers.truncate((tickers.len() as f64 * crate::constants::due_ticker_fraction()) as usize);

        if !tickers.is_empty() {
            tracing::info!("VCI hourly worker: syncing {} due tickers (trading={})", tickers.len(), trading);

            let mult = if trading { 1 } else { vci_worker::OFF_HOURS_MULTIPLIER };
            let tier_secs: [i64; 4] = priority::HOURLY_SECS.map(|s| (s * mult).min(vci_worker::MAX_SCHEDULE_SECS));
            let concurrency = vci_worker::concurrent_batches(provider.client_count());
            for chunk in tickers.chunks(concurrency) {
                let mut handles = tokio::task::JoinSet::new();
                for ticker_entry in chunk {
                    let pool = pool.clone();
                    let provider = provider.clone();
                    let redis_client = redis_client.clone();
                    let ticker = ticker_entry.ticker.clone();
                    handles.spawn(async move {
                        let ticker_id = vci_shared::ensure_vn_ticker(&pool, "vn", &ticker).await;
                        let last_time = vci_shared::get_last_time(&pool, ticker_id, "1h").await;

                        let count_back = match last_time {
                            Some(t) if (Utc::now() - t).num_days() < vci_worker::HOURLY_GAP_THRESHOLD_DAYS => {
                                vci_worker::HOURLY_COUNTBACK_RECENT
                            }
                            _ => vci_worker::HOURLY_COUNTBACK_GAP,
                        };

                        match provider.get_history(&ticker, "1H", count_back, None).await {
                            Ok(data) => {
                                vci_shared::enhance_and_save(&pool, ticker_id, &data, "1h", "vn", &ticker, &redis_client).await;

                                // Major VN tickers get fixed 60s schedule; others use money-flow tier
                                if MAJOR_VN.contains(&ticker.as_str()) {
                                    match binance_shared::schedule_fixed_interval(
                                        &pool, ticker_id, "next_1h", MAJOR_SCHEDULE_SECS,
                                    ).await {
                                        Ok(next_run) => tracing::info!(ticker, count = data.len(), next = %next_run, "hourly sync OK (major)"),
                                        Err(e) => tracing::warn!(ticker, count = data.len(), "hourly sync OK but scheduling failed: {e}"),
                                    }
                                } else {
                                    match ohlcv::schedule_next_run(
                                        &pool, ticker_id, "next_1h",
                                        &priority::THRESHOLDS, &tier_secs,
                                    ).await {
                                        Ok(next_run) => tracing::info!(ticker, count = data.len(), next = %next_run, "hourly sync OK"),
                                        Err(e) => tracing::warn!(ticker, count = data.len(), "hourly sync OK but scheduling failed: {e}"),
                                    }
                                }
                                false
                            }
                            Err(e) => {
                                let rate_limited = e.to_string().contains("429");
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
                    tracing::warn!(rate_limited, total = chunk.len(), "rate limited in batch, cooling down {}s", vci_worker::RATE_LIMIT_COOLDOWN_SECS);
                    sleep(Duration::from_secs(vci_worker::RATE_LIMIT_COOLDOWN_SECS)).await;
                }
            }
        } else {
            tracing::debug!("VCI hourly worker: no due tickers");
        }

        let sleep_secs = if trading {
            vci_worker::HOURLY_LOOP_TRADE_SECS
        } else {
            vci_worker::HOURLY_LOOP_OFF_SECS
        };
        sleep(Duration::from_secs(sleep_secs)).await;
    }
}
