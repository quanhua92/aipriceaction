use chrono::Utc;
use sqlx::PgPool;
use tokio::time::{sleep, Duration};

use crate::constants::vci_worker;
use crate::providers::vci::VciProvider;
use crate::queries::ohlcv;
use crate::workers::vci_shared;

pub async fn run(pool: PgPool) {
    tracing::info!("VCI hourly worker started");

    let provider = match VciProvider::new(30) {
        Ok(p) => p,
        Err(e) => {
            tracing::error!("VCI hourly worker: failed to create provider: {e}");
            return;
        }
    };

    loop {
        let trading = vci_shared::is_trading_hours();

        let tickers = match ohlcv::get_tickers_by_status(&pool, "vn", "ready").await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("VCI hourly worker: failed to load tickers: {e}");
                let sleep_secs = if trading {
                    vci_worker::HOURLY_LOOP_TRADE_SECS
                } else {
                    vci_worker::HOURLY_LOOP_OFF_SECS
                };
                sleep(Duration::from_secs(sleep_secs)).await;
                continue;
            }
        };

        if !tickers.is_empty() {
            tracing::info!("VCI hourly worker: syncing {} tickers (trading={})", tickers.len(), trading);

            let mut ticker_symbols: Vec<String> = tickers.iter().map(|t| t.ticker.clone()).collect();
            {
                use rand::seq::SliceRandom;
                let mut rng = rand::thread_rng();
                ticker_symbols.shuffle(&mut rng);
            }

            for ticker in &ticker_symbols {
                let ticker_id = vci_shared::ensure_ticker(&pool, "vn", ticker).await;
                let last_time = vci_shared::get_last_time(&pool, ticker_id, "1h").await;

                let count_back = match last_time {
                    Some(t) if (Utc::now() - t).num_days() < vci_worker::HOURLY_GAP_THRESHOLD_DAYS => {
                        vci_worker::HOURLY_COUNTBACK_RECENT
                    }
                    _ => vci_worker::HOURLY_COUNTBACK_GAP,
                };

                match provider.get_history(ticker, "1H", count_back, None).await {
                    Ok(data) => {
                        vci_shared::enhance_and_save(&pool, ticker_id, &data, "1h").await;
                        tracing::info!(ticker, count = data.len(), "hourly sync OK");
                    }
                    Err(e) => {
                        tracing::warn!(ticker, "hourly fetch failed: {e}");
                    }
                }

                sleep(Duration::from_secs(vci_worker::TICKER_SLEEP_SECS)).await;
            }
        } else {
            tracing::debug!("VCI hourly worker: no ready tickers");
        }

        let sleep_secs = if trading {
            vci_worker::HOURLY_LOOP_TRADE_SECS
        } else {
            vci_worker::HOURLY_LOOP_OFF_SECS
        };
        sleep(Duration::from_secs(sleep_secs)).await;
    }
}
