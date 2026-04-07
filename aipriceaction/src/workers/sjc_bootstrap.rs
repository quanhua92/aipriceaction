use sqlx::PgPool;
use tokio::time::{sleep, Duration as TokioDuration};

use crate::constants::sjc_worker;
use crate::queries::ohlcv;
use crate::workers::binance_shared::schedule_fixed_interval;
use crate::workers::sjc_shared;

/// Bootstrap worker for SJC gold price data.
///
/// 1. Find tickers with status='waiting-import' and source='sjc'
/// 2. Import historical data from sjc-batch.csv
/// 3. Mark as 'ready' and schedule normal daily sync
pub async fn run(pool: PgPool) {
    tracing::info!("SJC bootstrap worker started");

    loop {
        // Find SJC tickers flagged for import
        let tickers = match ohlcv::get_tickers_by_statuses(
            &pool,
            sjc_worker::SOURCE,
            &["waiting-import"],
        )
        .await
        {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("SJC bootstrap worker: failed to load flagged tickers: {e}");
                sleep(TokioDuration::from_secs(sjc_worker::DAILY_LOOP_OFF_SECS)).await;
                continue;
            }
        };

        if tickers.is_empty() {
            sleep(TokioDuration::from_secs(sjc_worker::DAILY_LOOP_OFF_SECS)).await;
            continue;
        }

        for ticker_entry in &tickers {
            let ticker = &ticker_entry.ticker;
            let ticker_id = ticker_entry.id;
            tracing::info!(ticker, ticker_id, "SJC bootstrap: starting CSV import");

            match sjc_shared::import_csv_to_ohlcv(&pool, ticker_id).await {
                Ok(count) => {
                    tracing::info!(ticker, ticker_id, count, "SJC bootstrap: import successful");

                    // Mark as ready
                    if let Err(e) = ohlcv::update_ticker_status(&pool, ticker_id, "ready").await {
                        tracing::error!(ticker, ticker_id, "SJC bootstrap: failed to set status ready: {e}");
                    } else {
                        schedule_fixed_interval(
                            &pool,
                            ticker_id,
                            "next_1d",
                            sjc_worker::SCHEDULE_DAILY_SECS,
                        )
                        .await
                        .ok();
                        tracing::info!(ticker, ticker_id, "SJC bootstrap: marked ready");
                    }
                }
                Err(e) => {
                    tracing::warn!(
                        ticker,
                        ticker_id,
                        error = %e,
                        "SJC bootstrap: CSV import failed (file may be missing), will retry later"
                    );
                }
            }
        }

        sleep(TokioDuration::from_secs(sjc_worker::DAILY_LOOP_OFF_SECS)).await;
    }
}
