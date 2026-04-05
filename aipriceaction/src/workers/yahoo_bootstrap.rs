use rand::seq::SliceRandom;
use sqlx::PgPool;
use tokio::time::{sleep, Duration};

use crate::constants::yahoo_worker;
use crate::providers::yahoo::YahooProvider;
use crate::queries::ohlcv;
use crate::workers::yahoo_shared;

/// Full-download worker for Yahoo Finance tickers.
///
/// 1. Find tickers with status='dividend-detected' or 'full-download-requested'
/// 2. Delete all existing data
/// 3. Download full history chunked by time windows for 1D, 1h, 1m
/// 4. Mark as 'ready' when all intervals are done
pub async fn run(pool: PgPool) {
    let provider = match YahooProvider::with_options(60, true, true) {
        Ok(p) => p,
        Err(e) => {
            tracing::error!("Yahoo bootstrap worker: failed to create provider: {e}");
            return;
        }
    };

    tracing::info!("Yahoo bootstrap worker started");

    loop {
        // Log DB status counts for debugging
        if let Ok(rows) = sqlx::query!(
            "SELECT status, count(*)::bigint as cnt FROM tickers WHERE source = 'yahoo' GROUP BY status"
        )
        .fetch_all(&pool)
        .await
        {
            for row in &rows {
                tracing::info!(status = %row.status.as_deref().unwrap_or("NULL"), count = row.cnt, "bootstrap: ticker status count");
            }
        }

        // ── Pass 1: claim a fresh ticker ──
        let candidate = pick_fresh_ticker(&pool).await;

        // ── Pass 2: resume a crashed/abandoned download ──
        let candidate = if candidate.is_none() {
            pick_resumable_ticker(&pool).await
        } else {
            candidate
        };

        let Some((ticker_entry, is_fresh)) = candidate else {
            sleep(Duration::from_secs(yahoo_worker::BOOTSTRAP_LOOP_SECS)).await;
            continue;
        };

        {
            let ticker = &ticker_entry.ticker;
            let ticker_id = ticker_entry.id;

            if is_fresh {
                tracing::warn!(
                    "[YAHOO-BOOTSTRAP] ticker={}, ticker_id={}, claiming — deleting ALL existing data",
                    ticker, ticker_id
                );
                if let Err(e) = ohlcv::delete_ohlcv_and_set_status(&pool, ticker_id, "full-download-processing").await {
                    tracing::error!("[YAHOO-BOOTSTRAP] ticker={}, ticker_id={}, FAILED to claim: {}", ticker, ticker_id, e);
                    continue;
                }
            } else {
                tracing::warn!(
                    "[YAHOO-BOOTSTRAP] ticker={}, ticker_id={}, resuming abandoned download",
                    ticker, ticker_id
                );
            }

            let mut all_saved = 0usize;

            // Historical cutoff dates
            let daily_start = chrono::NaiveDate::from_ymd_opt(2010, 1, 1)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap()
                .and_utc();
            let hm_floor = chrono::NaiveDate::from_ymd_opt(yahoo_worker::BOOTSTRAP_HM_FLOOR_YEAR, 1, 1)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap()
                .and_utc();

            let mut hm_start = hm_floor;

            // Yahoo Finance only serves hourly/minute data within the last N days.
            // Cap hm_start so we never request data that will be rejected.
            let max_lookback = chrono::Utc::now() - chrono::Duration::days(yahoo_worker::BOOTSTRAP_HM_LOOKBACK_DAYS);
            if hm_start < max_lookback {
                hm_start = max_lookback;
            }

            for interval in &["1D", "1h", "1m"] {
                let (chunk_days, yahoo_interval, db_interval) = match *interval {
                    "1D" => (yahoo_worker::BOOTSTRAP_DAILY_CHUNK_DAYS, "1d", "1D"),
                    "1h" => (yahoo_worker::BOOTSTRAP_HOURLY_CHUNK_DAYS, "1h", "1h"),
                    "1m" => (yahoo_worker::BOOTSTRAP_MINUTE_CHUNK_DAYS, "1m", "1m"),
                    _ => unreachable!(),
                };

                let start = match *interval {
                    "1D" => daily_start,
                    _ => hm_start,
                };

                let now = chrono::Utc::now();
                let mut chunk_start = start;
                let mut total_saved = 0usize;
                let mut last_newest_ts: i64 = i64::MIN;
                let mut stall_count: u32 = 0;

                loop {
                    let chunk_end = chunk_start + chrono::Duration::days(chunk_days);
                    let fetch_end = if chunk_end > now { now } else { chunk_end };

                    if chunk_start >= now {
                        break;
                    }

                    tracing::info!(
                        ticker,
                        interval = db_interval,
                        chunk_start = %chunk_start.format("%Y-%m-%d"),
                        chunk_end = %fetch_end.format("%Y-%m-%d"),
                        total = total_saved,
                        "yahoo bootstrap fetch"
                    );

                    match provider.get_history_interval(ticker, yahoo_interval, chunk_start, fetch_end).await {
                        Ok(data) => {
                            if data.is_empty() {
                                tracing::info!(ticker, interval = db_interval, "empty chunk response, skipping forward");
                                chunk_start = chunk_end;
                                continue;
                            }

                            let oldest = data.first().unwrap().time.format("%Y-%m-%d %H:%M").to_string();
                            let newest = data.last().unwrap().time.format("%Y-%m-%d %H:%M").to_string();
                            let fetched = data.len();
                            total_saved += fetched;

                            yahoo_shared::enhance_and_save(&pool, ticker_id, &data, db_interval).await;
                            let newest_ts = data.last().unwrap().time.timestamp();
                            tracing::info!(
                                ticker,
                                interval = db_interval,
                                chunk = fetched,
                                total = total_saved,
                                %oldest,
                                %newest,
                                "saved bootstrap chunk"
                            );

                            // Stall detection
                            if newest_ts <= last_newest_ts {
                                stall_count += 1;
                                let skip_days = chunk_days + (chunk_days * stall_count as i64 / 10);
                                tracing::info!(
                                    ticker,
                                    interval = db_interval,
                                    %newest,
                                    stall_count,
                                    skip_days,
                                    "stall detected (gap/holiday), skipping forward"
                                );
                                chunk_start += chrono::Duration::days(skip_days);
                                if chunk_start >= now {
                                    chunk_start = now;
                                }
                                last_newest_ts = newest_ts;
                                continue;
                            }
                            last_newest_ts = newest_ts;
                            stall_count = 0;

                            // Advance to next chunk starting after the newest record
                            chunk_start = data.last().unwrap().time + chrono::Duration::seconds(1);
                        }
                        Err(e) => {
                            let err_str = e.to_string();
                            if err_str.contains("429") || err_str.contains("Too many requests") {
                                tracing::warn!(
                                    ticker,
                                    interval = db_interval,
                                    "rate limited, waiting 60s"
                                );
                                sleep(Duration::from_secs(60)).await;
                                continue;
                            }
                            // Other errors: skip forward
                            tracing::warn!(
                                ticker,
                                interval = db_interval,
                                error = %err_str,
                                "fetch failed, skipping forward"
                            );
                            chunk_start = chunk_end;
                        }
                    }

                    sleep(Duration::from_secs(yahoo_worker::BOOTSTRAP_LOOP_SECS)).await;
                }

                tracing::info!(ticker, interval = db_interval, total = total_saved, "yahoo bootstrap interval done");
                all_saved += total_saved;

                // After daily download, use oldest daily as start for hourly/minute
                if *interval == "1D" && total_saved > 0 {
                    if let Ok(Some(earliest_daily)) = ohlcv::get_earliest_time(&pool, ticker_id, "1D").await {
                        if earliest_daily > hm_floor {
                            tracing::info!(ticker, earliest_daily = %earliest_daily.format("%Y-%m-%d"), "using oldest daily as hm start");
                            hm_start = earliest_daily;
                        } else {
                            tracing::info!(ticker, "oldest daily before {}-01-01, capping hm start to floor",
                                yahoo_worker::BOOTSTRAP_HM_FLOOR_YEAR);
                            hm_start = hm_floor;
                        }
                    }
                }
            }

            if all_saved == 0 {
                tracing::error!(
                    "[YAHOO-BOOTSTRAP] ticker={}, ticker_id={}, RECOVERY FAILED — no data saved",
                    ticker, ticker_id
                );
                continue;
            }

            // Mark as ready and reset schedule
            if let Err(e) = ohlcv::update_ticker_status(&pool, ticker_id, "ready").await {
                tracing::error!("[YAHOO-BOOTSTRAP] ticker={}, ticker_id={}, FAILED to set status 'ready': {}", ticker, ticker_id, e);
            } else {
                if let Err(e) = ohlcv::reset_ticker_schedule(&pool, ticker_id).await {
                    tracing::warn!("[YAHOO-BOOTSTRAP] ticker={}, ticker_id={}, status set to 'ready' but schedule reset failed: {}", ticker, ticker_id, e);
                }
                tracing::warn!(
                    "[YAHOO-BOOTSTRAP] ticker={}, ticker_id={}, bootstrap COMPLETE — total_rows={}",
                    ticker, ticker_id, all_saved
                );
            }
        }

        sleep(Duration::from_secs(yahoo_worker::BOOTSTRAP_LOOP_SECS)).await;
    }
}

/// Pass 1: Pick a random ticker from `dividend-detected` or `full-download-requested`.
async fn pick_fresh_ticker(pool: &PgPool) -> Option<(ohlcv::Ticker, bool)> {
    let tickers = match ohlcv::get_tickers_by_statuses(pool, "yahoo", &["dividend-detected", "full-download-requested"]).await {
        Ok(t) => t,
        Err(e) => {
            tracing::warn!("Yahoo bootstrap worker: failed to load flagged tickers: {e}");
            return None;
        }
    };

    if tickers.is_empty() {
        return None;
    }

    let ticker_entry = tickers.choose(&mut rand::thread_rng())?;
    let ticker_id = ticker_entry.id;

    // Re-check status
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

/// Pass 2: Pick a random ticker from `full-download-processing` to resume.
async fn pick_resumable_ticker(pool: &PgPool) -> Option<(ohlcv::Ticker, bool)> {
    let tickers = match ohlcv::get_tickers_by_status(pool, "yahoo", "full-download-processing").await {
        Ok(t) => t,
        Err(e) => {
            tracing::warn!("Yahoo bootstrap worker: failed to load processing tickers: {e}");
            return None;
        }
    };

    if tickers.is_empty() {
        return None;
    }

    let ticker_entry = tickers.choose(&mut rand::thread_rng())?;
    let ticker_id = ticker_entry.id;

    if let Ok(Some(current)) = ohlcv::get_ticker_by_id(pool, ticker_id).await {
        let still_processing = current.status.as_deref() == Some("full-download-processing");
        if !still_processing {
            tracing::info!("ticker={}, already completed (status={:?}), skipping", ticker_entry.ticker, current.status);
            return None;
        }
    }

    Some((ticker_entry.clone(), false))
}
