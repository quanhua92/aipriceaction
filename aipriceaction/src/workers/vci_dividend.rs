use rand::seq::SliceRandom;
use sqlx::PgPool;
use tokio::time::{sleep, Duration};

use crate::constants::vci_worker;
use crate::providers::vci::VciProvider;
use crate::queries::ohlcv;
use crate::workers::vci_shared;

pub async fn run(pool: PgPool, redis_client: Option<crate::redis::RedisClient>) {
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
        // ── Pass 1: claim a fresh ticker (dividend-detected / full-download-requested) ──
        let candidate = pick_fresh_ticker(&pool).await;

        // ── Pass 2: resume a crashed/abandoned download (full-download-processing) ──
        let candidate = if candidate.is_none() {
            pick_resumable_ticker(&pool).await
        } else {
            candidate
        };

        let Some((ticker_entry, is_fresh)) = candidate else {
            sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await;
            continue;
        };

        {
            let ticker = &ticker_entry.ticker;
            let ticker_id = ticker_entry.id;

            if is_fresh {
                // Transition to full-download-processing (upsert, no delete)
                tracing::warn!(
                    "[DIVIDEND] ticker={}, ticker_id={}, claiming — upserting (no delete)",
                    ticker, ticker_id
                );
                if let Err(e) = ohlcv::update_ticker_status(&pool, ticker_id, "full-download-processing").await {
                    tracing::error!("[DIVIDEND] ticker={}, ticker_id={}, FAILED to claim: {}", ticker, ticker_id, e);
                    continue;
                }
            } else {
                tracing::warn!(
                    "[DIVIDEND] ticker={}, ticker_id={}, resuming abandoned full-download",
                    ticker, ticker_id
                );
            }

            let mut all_saved = 0usize;

            // Re-download history forward from cutoff, saving each chunk immediately
            let mut hm_start = hm_start; // default fallback: 2023
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
                let mut last_newest_ts: i64 = i64::MIN;
                let mut stall_count: u32 = 0;

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
                            vci_shared::enhance_and_save(&pool, ticker_id, &data, interval, "vn", &ticker, &redis_client).await;
                            let newest_ts = data.last().unwrap().time.timestamp();
                            tracing::info!(ticker, interval, chunk = fetched, total = total_saved, %oldest, %newest, "saved dividend chunk");

                            // Stall detection: if newest timestamp didn't advance, we're in a gap
                            // (holiday, weekend, suspension) — skip forward with 10% increase per stall
                            if newest_ts <= last_newest_ts {
                                stall_count += 1;
                                let base_skip = chunk_size as i64 * interval_secs;
                                let skip = base_skip + (base_skip * stall_count as i64 * vci_worker::DIVIDEND_STALL_INCREASE_PCT as i64 / 100);
                                tracing::info!(ticker, interval, %newest, stall_count, skip_secs = skip, "stall detected (gap/holiday), skipping forward");
                                to_ts += skip;
                                if to_ts >= now_ts {
                                    to_ts = now_ts;
                                }
                                last_newest_ts = newest_ts;
                                continue;
                            }
                            last_newest_ts = newest_ts;
                            stall_count = 0;

                            // Advance forward: next chunk ends after newest record + full window
                            to_ts = newest_ts + (chunk_size as i64 * interval_secs);
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

                // After daily download, use oldest daily as start for hourly/minute,
                // but never earlier than DIVIDEND_HM_FLOOR — VCI has no minute data before that.
                if *interval == "1D" && total_saved > 0 {
                    if let Ok(Some(earliest_daily)) = ohlcv::get_earliest_time(&pool, ticker_id, "1D").await {
                        let hm_floor = chrono::NaiveDate::from_ymd_opt(
                            vci_worker::DIVIDEND_HM_FLOOR_YEAR as i32,
                            vci_worker::DIVIDEND_HM_FLOOR_MONTH as u32,
                            1,
                        )
                        .unwrap()
                        .and_hms_opt(0, 0, 0)
                        .unwrap()
                        .and_utc();
                        if earliest_daily > hm_floor {
                            tracing::info!(ticker, earliest_daily = %earliest_daily.format("%Y-%m-%d"), "using oldest daily as hm start");
                            hm_start = earliest_daily;
                        } else {
                            tracing::info!(ticker, "oldest daily before {}-{}, capping hm start to floor",
                                vci_worker::DIVIDEND_HM_FLOOR_YEAR, vci_worker::DIVIDEND_HM_FLOOR_MONTH);
                            hm_start = hm_floor;
                        }
                    }
                }
            }

            if all_saved == 0 {
                tracing::error!("[DIVIDEND] ticker={}, ticker_id={}, RECOVERY FAILED — no data saved for any interval, status left unchanged", ticker, ticker_id);
                continue;
            }

            // Mark as ready again and reset priority schedule
            if let Err(e) = ohlcv::update_ticker_status(&pool, ticker_id, "ready").await {
                tracing::error!("[DIVIDEND] ticker={}, ticker_id={}, FAILED to set status 'ready': {}", ticker, ticker_id, e);
            } else {
                if let Err(e) = ohlcv::reset_ticker_schedule(&pool, ticker_id).await {
                    tracing::warn!("[DIVIDEND] ticker={}, ticker_id={}, status set to 'ready' but schedule reset failed: {}", ticker, ticker_id, e);
                }
                tracing::warn!("[DIVIDEND] ticker={}, ticker_id={}, recovery COMPLETE — total_rows={}, status='ready'", ticker, ticker_id, all_saved);
            }
        }

        sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await;
    }
}

/// Pass 1: Pick a random ticker from `dividend-detected` or `full-download-requested`.
/// Re-checks status before claiming to avoid racing with another instance.
/// Returns `(ticker, is_fresh = true)` if a fresh candidate was found and still pending.
async fn pick_fresh_ticker(pool: &PgPool) -> Option<(ohlcv::Ticker, bool)> {
    let tickers = match ohlcv::get_tickers_by_statuses(pool, "vn", &["dividend-detected", "full-download-requested"]).await {
        Ok(t) => t,
        Err(e) => {
            tracing::warn!("VCI dividend worker: failed to load flagged tickers: {e}");
            return None;
        }
    };

    if tickers.is_empty() {
        return None;
    }

    let ticker_entry = tickers.choose(&mut rand::thread_rng())?;
    let ticker_id = ticker_entry.id;

    // Re-check status to avoid duplicate work if another instance already grabbed it
    if let Ok(Some(current)) = ohlcv::get_ticker_by_id(pool, ticker_id).await {
        let still_pending = current.status.as_deref()
            .map(|s| s == "dividend-detected" || s == "full-download-requested")
            .unwrap_or(false);
        if !still_pending {
            tracing::info!("ticker={}, already being handled (status={:?}), skipping", ticker_entry.ticker, current.status);
            return None;
        }
    }

    Some((ticker_entry.clone(), true))
}

/// Pass 2: Pick a random ticker from `full-download-processing` to resume an abandoned download.
/// Re-checks status because another instance may have finished and set it to `ready`.
/// Returns `(ticker, is_fresh = false)` — caller should skip the delete step.
async fn pick_resumable_ticker(pool: &PgPool) -> Option<(ohlcv::Ticker, bool)> {
    let tickers = match ohlcv::get_tickers_by_status(pool, "vn", "full-download-processing").await {
        Ok(t) => t,
        Err(e) => {
            tracing::warn!("VCI dividend worker: failed to load processing tickers: {e}");
            return None;
        }
    };

    if tickers.is_empty() {
        return None;
    }

    let ticker_entry = tickers.choose(&mut rand::thread_rng())?;
    let ticker_id = ticker_entry.id;

    // Re-check status — another instance may have already completed this download
    if let Ok(Some(current)) = ohlcv::get_ticker_by_id(pool, ticker_id).await {
        let still_processing = current.status.as_deref() == Some("full-download-processing");
        if !still_processing {
            tracing::info!("ticker={}, already completed (status={:?}), skipping resume", ticker_entry.ticker, current.status);
            return None;
        }
    }

    Some((ticker_entry.clone(), false))
}
