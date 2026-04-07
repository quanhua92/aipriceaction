use sqlx::PgPool;
use tokio::time::{sleep, Duration as TokioDuration};

use crate::constants::sjc_worker;
use crate::providers::sjc::SjcProvider;
use crate::queries::ohlcv;
use crate::workers::sjc_shared;

/// Live price worker for SJC gold.
///
/// 1. Ensure SJC-GOLD ticker exists (creates with waiting-import if new)
/// 2. Wait for bootstrap worker to import CSV (status must be 'ready')
/// 3. Fetch SJC API immediately on startup, then every 5 min during
///    VN trading hours, 30 min off-hours
/// 4. Upsert today's daily candle preserving the opening price
pub async fn run(pool: PgPool) {
    let provider = match SjcProvider::new() {
        Ok(p) => p,
        Err(e) => {
            tracing::error!("SJC daily worker: failed to create provider: {e}");
            return;
        }
    };

    tracing::info!("SJC daily worker started");

    // Track whether we've done the first fetch — on startup we fetch
    // immediately regardless of next_1d scheduling.
    let mut first_fetch_done = false;

    loop {
        // 1. Ensure ticker exists
        let ticker_id = sjc_shared::ensure_sjc_ticker(&pool).await;

        // 2. Check if bootstrap is done
        let ticker = match ohlcv::get_ticker_by_id(&pool, ticker_id).await {
            Ok(Some(t)) => t,
            Ok(None) => {
                tracing::warn!("SJC daily worker: ticker {} not found", sjc_worker::TICKER);
                sleep(TokioDuration::from_secs(sjc_worker::DAILY_LOOP_OFF_SECS)).await;
                continue;
            }
            Err(e) => {
                tracing::warn!("SJC daily worker: failed to get ticker: {e}");
                sleep(TokioDuration::from_secs(sjc_worker::DAILY_LOOP_OFF_SECS)).await;
                continue;
            }
        };

        // 3. Skip if not ready yet (bootstrap still running or CSV missing)
        let status = ticker.status.as_deref().unwrap_or("");
        if status != "ready" {
            tracing::debug!(
                status,
                ticker = sjc_worker::TICKER,
                "SJC daily worker: ticker not ready, waiting for bootstrap"
            );
            sleep(TokioDuration::from_secs(sjc_worker::DAILY_LOOP_OFF_SECS)).await;
            continue;
        }

        // 4. Check if next_1d is in the future (skip this check on first fetch)
        if first_fetch_done && ticker.next_1d > chrono::Utc::now() {
            let wait_secs = (ticker.next_1d - chrono::Utc::now()).num_seconds().unsigned_abs().min(60) as u64;
            sleep(TokioDuration::from_secs(wait_secs)).await;
            continue;
        }

        // 5. Fetch live price
        match provider.fetch_today().await {
            Ok(price) => {
                tracing::info!(
                    buy = price.buy,
                    sell = price.sell,
                    mid = (price.buy + price.sell) / 2.0,
                    "SJC daily worker: fetched price"
                );

                if let Err(e) = sjc_shared::upsert_live_price(&pool, ticker_id, price.buy, price.sell).await {
                    tracing::warn!("SJC daily worker: failed to upsert price: {e}");
                } else {
                    first_fetch_done = true;
                    // Schedule next run
                    sjc_shared::schedule_next(&pool, ticker_id).await;
                }
            }
            Err(e) => {
                tracing::warn!("SJC daily worker: failed to fetch price: {e}");
                // Cooldown on error
                sleep(TokioDuration::from_secs(sjc_worker::API_ERROR_COOLDOWN_SECS)).await;
                continue;
            }
        }

        // 6. Sleep until next interval
        let interval = sjc_shared::loop_interval_secs();
        sleep(TokioDuration::from_secs(interval)).await;
    }
}
