use chrono::Utc;
use sqlx::PgPool;
use std::sync::Arc;
use tokio::time::{sleep, Duration};

use crate::constants::vci_worker;
use crate::constants::vci_worker::priority;
use crate::providers::vci::VciProvider;
use crate::queries::ohlcv;
use crate::workers::vci_shared;

pub async fn run(pool: PgPool) {
    // Initial delay before first sync (3 minutes)
    tracing::info!("VCI minute worker: waiting {} seconds before first sync...", vci_worker::MINUTE_INITIAL_DELAY_SECS);
    sleep(Duration::from_secs(vci_worker::MINUTE_INITIAL_DELAY_SECS)).await;

    let provider = match VciProvider::new(20) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            tracing::error!("VCI minute worker: failed to create provider: {e}");
            return;
        }
    };

    tracing::info!("VCI minute worker started (clients={}, concurrency={})", provider.client_count(), vci_worker::concurrent_batches(provider.client_count()));

    loop {
        let trading = vci_shared::is_trading_hours();

        let mut tickers = match ohlcv::get_due_tickers(&pool, "vn", "next_1m").await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("VCI minute worker: failed to load due tickers: {e}");
                let sleep_secs = if trading {
                    vci_worker::MINUTE_LOOP_TRADE_SECS
                } else {
                    vci_worker::MINUTE_LOOP_OFF_SECS
                };
                sleep(Duration::from_secs(sleep_secs)).await;
                continue;
            }
        };
        use rand::seq::SliceRandom;
        tickers.shuffle(&mut rand::thread_rng());
        tickers.truncate(vci_worker::DUE_TICKER_BATCH_SIZE);

        if !tickers.is_empty() {
            tracing::info!("VCI minute worker: syncing {} due tickers (trading={})", tickers.len(), trading);

            let mult = if trading { 1 } else { vci_worker::OFF_HOURS_MULTIPLIER };
            let tier_secs: [i64; 4] = priority::MINUTE_SECS.map(|s| (s * mult).min(vci_worker::MAX_SCHEDULE_SECS));
            let concurrency = vci_worker::concurrent_batches(provider.client_count());
            for chunk in tickers.chunks(concurrency) {
                let mut handles = tokio::task::JoinSet::new();
                for ticker_entry in chunk {
                    let pool = pool.clone();
                    let provider = provider.clone();
                    let ticker = ticker_entry.ticker.clone();
                    handles.spawn(async move {
                        let ticker_id = vci_shared::ensure_vn_ticker(&pool, "vn", &ticker).await;
                        let last_time = vci_shared::get_last_time(&pool, ticker_id, "1m").await;

                        let count_back = match last_time {
                            Some(t) if (Utc::now() - t).num_days() < vci_worker::MINUTE_GAP_THRESHOLD_DAYS => {
                                vci_worker::MINUTE_COUNTBACK_RECENT
                            }
                            _ => vci_worker::MINUTE_COUNTBACK_GAP,
                        };

                        match provider.get_history(&ticker, "1m", count_back, None).await {
                            Ok(data) => {
                                vci_shared::enhance_and_save(&pool, ticker_id, &data, "1m").await;

                                // Schedule next minute run based on money-flow tier
                                match ohlcv::schedule_next_run(
                                    &pool, ticker_id, "next_1m",
                                    &priority::THRESHOLDS, &tier_secs,
                                ).await {
                                    Ok(next_run) => tracing::info!(ticker, count = data.len(), next = %next_run, "minute sync OK"),
                                    Err(e) => tracing::warn!(ticker, count = data.len(), "minute sync OK but scheduling failed: {e}"),
                                }
                                false
                            }
                            Err(e) => {
                                let rate_limited = e.to_string().contains("429");
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
                    tracing::warn!(rate_limited, total = chunk.len(), "rate limited in batch, cooling down {}s", vci_worker::RATE_LIMIT_COOLDOWN_SECS);
                    sleep(Duration::from_secs(vci_worker::RATE_LIMIT_COOLDOWN_SECS)).await;
                }
            }
        } else {
            tracing::debug!("VCI minute worker: no due tickers");
        }

        let sleep_secs = if trading {
            vci_worker::MINUTE_LOOP_TRADE_SECS
        } else {
            vci_worker::MINUTE_LOOP_OFF_SECS
        };
        sleep(Duration::from_secs(sleep_secs)).await;
    }
}
