use sqlx::PgPool;
use tokio::time::{sleep, Duration};

use crate::constants::vci_worker;
use crate::providers::vci::VciProvider;
use crate::queries::ohlcv;
use crate::workers::vci_shared;

pub async fn run(pool: PgPool) {
    tracing::info!("VCI dividend worker started");

    let provider = match VciProvider::new(60) {
        Ok(p) => p,
        Err(e) => {
            tracing::error!("VCI dividend worker: failed to create provider: {e}");
            return;
        }
    };

    // Historical cutoff dates — start downloading from these dates
    let daily_start = chrono::NaiveDate::from_ymd_opt(2015, 1, 1)
        .unwrap()
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_utc();
    let hm_start = chrono::NaiveDate::from_ymd_opt(2023, 1, 1)
        .unwrap()
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_utc();

    loop {
        // Find tickers flagged for full re-download
        let tickers = match ohlcv::get_tickers_by_statuses(&pool, "vn", &["dividend-detected", "full-download-requested"]).await {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("VCI dividend worker: failed to load flagged tickers: {e}");
                sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await;
                continue;
            }
        };

        if tickers.is_empty() {
            sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await;
            continue;
        }

        for ticker_entry in &tickers {
            let ticker = &ticker_entry.ticker;
            let ticker_id = ticker_entry.id;
            tracing::info!(ticker, ticker_id, "starting dividend recovery");

            // Delete all existing data for this ticker
            if let Err(e) = ohlcv::delete_indicators_for_ticker(&pool, ticker_id).await {
                tracing::error!(ticker, ticker_id, "delete indicators failed: {e}");
                continue;
            }
            if let Err(e) = ohlcv::delete_ohlcv_for_ticker(&pool, ticker_id).await {
                tracing::error!(ticker, ticker_id, "delete ohlcv failed: {e}");
                continue;
            }

            tracing::info!(ticker, "deleted all existing data, re-downloading");

            let mut all_saved = 0usize;

            // Re-download history forward from cutoff, saving each chunk immediately
            for interval in &["1D", "1h", "1m"] {
                let chunk_size = match *interval {
                    "1m" => vci_worker::DIVIDEND_CHUNK_SIZE_MINUTE,
                    "1h" => vci_worker::DIVIDEND_CHUNK_SIZE_HOURLY,
                    _ => vci_worker::DIVIDEND_CHUNK_SIZE_DAILY,
                };
                // Seconds per record in this interval (for to_ts window calculation)
                let interval_secs: i64 = match *interval {
                    "1m" => 60,
                    "1h" => 3600,
                    _ => 86400,
                };
                let api_interval = match *interval {
                    "1h" => "1H",
                    other => other,
                };
                let start = match *interval {
                    "1D" => daily_start,
                    _ => hm_start,
                };
                let now_ts = chrono::Utc::now().timestamp();
                // to_ts is the end boundary for VCI API (countBack from this point)
                let mut to_ts = start.timestamp() + (chunk_size as i64 * interval_secs);
                let mut total_saved = 0usize;

                let mut final_attempt = false;

                loop {
                    if to_ts >= now_ts {
                        if final_attempt {
                            break;
                        }
                        // One last try at now_ts before giving up (catches recently listed tickers)
                        final_attempt = true;
                        to_ts = now_ts;
                    }

                    let to_date = chrono::DateTime::from_timestamp(to_ts, 0)
                        .map(|d| d.format("%Y-%m-%d %H:%M").to_string())
                        .unwrap_or_else(|| to_ts.to_string());
                    tracing::info!(ticker, interval, count_back = chunk_size, to_ts, %to_date, total = total_saved, "dividend fetch");

                    match provider.get_history(ticker, api_interval, chunk_size, Some(to_ts)).await {
                        Ok(data) => {
                            if data.is_empty() {
                                tracing::info!(ticker, interval, "empty response, stopping");
                                break;
                            }
                            let oldest = data.first().unwrap().time.format("%Y-%m-%d %H:%M").to_string();
                            let newest = data.last().unwrap().time.format("%Y-%m-%d %H:%M").to_string();
                            let fetched = data.len();
                            total_saved += fetched;

                            // Save immediately — avoids OOM from accumulating all chunks
                            vci_shared::enhance_and_save(&pool, ticker_id, &data, interval).await;
                            tracing::info!(ticker, interval, chunk = fetched, total = total_saved, %oldest, %newest, "saved dividend chunk");

                            // Advance forward: next chunk ends after newest record + full window
                            to_ts = data.last().unwrap().time.timestamp() + (chunk_size as i64 * interval_secs);
                        }
                        Err(e) => {
                            match e {
                                crate::providers::vci::VciError::NoData => {
                                    // Genuinely no data at this point — skip forward by chunk window
                                    let skip = chunk_size as i64 * interval_secs;
                                    tracing::debug!(ticker, interval, %to_date, skip, "no data, skip forward");
                                    to_ts += skip;
                                    if to_ts >= now_ts {
                                        to_ts = now_ts;
                                        continue;
                                    }
                                    continue;
                                }
                                crate::providers::vci::VciError::RateLimit => {
                                    // Rate limited — wait and retry same to_ts until it works
                                    tracing::warn!(ticker, interval, %to_date, "rate limited, waiting 60s");
                                    sleep(Duration::from_secs(60)).await;
                                    continue;
                                }
                                _ => {
                                    // Other errors (network, parse) — skip forward
                                    let skip = chunk_size as i64 * interval_secs;
                                    tracing::warn!(ticker, interval, %to_date, skip, error = %e, "fetch failed, skip forward");
                                    to_ts += skip;
                                    if to_ts >= now_ts {
                                        to_ts = now_ts;
                                        continue;
                                    }
                                    continue;
                                }
                            }
                        }
                    }

                    sleep(Duration::from_secs(vci_worker::DIVIDEND_CHUNK_SLEEP_SECS)).await;
                }

                tracing::info!(ticker, interval, total = total_saved, "dividend re-download done");
                all_saved += total_saved;
            }

            if all_saved == 0 {
                tracing::error!(ticker, ticker_id, "dividend recovery: no data saved for any interval, NOT marking ready");
                continue;
            }

            // Mark as ready again and reset priority schedule
            if let Err(e) = ohlcv::update_ticker_status(&pool, ticker_id, "ready").await {
                tracing::error!(ticker, ticker_id, "failed to set status ready: {e}");
            } else {
                if let Err(e) = ohlcv::reset_ticker_schedule(&pool, ticker_id).await {
                    tracing::warn!(ticker, ticker_id, "failed to reset schedule: {e}");
                }
                tracing::info!(ticker, "dividend recovery complete");
            }
        }

        sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await;
    }
}
