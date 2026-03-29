use chrono::Datelike;
use sqlx::PgPool;
use tokio::time::{sleep, Duration as TokioDuration};

use crate::constants::binance_worker;
use crate::providers::binance::BinanceProvider;
use crate::queries::ohlcv;
use crate::workers::binance_shared;

/// Full-download worker for crypto tickers.
///
/// 1. Find tickers with status='full-download-requested'
/// 2. For 1d/1h: download Vision monthly ZIPs from 2010, save each immediately
/// 3. For 1m: download Vision daily ZIPs from 2017, save each immediately
/// 4. Fill the gap with live API klines
/// 5. Mark as 'ready' when all intervals are done
pub async fn run(pool: PgPool) {
    let provider = match BinanceProvider::new(120) {
        Ok(p) => p,
        Err(e) => {
            tracing::error!("Binance bootstrap worker: failed to create provider: {e}");
            return;
        }
    };

    tracing::info!("Binance bootstrap worker started");

    loop {
        // Log DB status counts for debugging
        if let Ok(rows) = sqlx::query!(
            "SELECT status, count(*)::bigint as cnt FROM tickers WHERE source = 'crypto' GROUP BY status"
        )
        .fetch_all(&pool)
        .await
        {
            for row in &rows {
                tracing::info!(status = %row.status.as_deref().unwrap_or("NULL"), count = row.cnt, "bootstrap: ticker status count");
            }
        }

        // Find tickers flagged for full download
        let tickers = match ohlcv::get_tickers_by_statuses(
            &pool,
            "crypto",
            &["full-download-requested"],
        )
        .await
        {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("Binance bootstrap worker: failed to load flagged tickers: {e}");
                sleep(TokioDuration::from_secs(binance_worker::DAILY_LOOP_SECS)).await;
                continue;
            }
        };

        if tickers.is_empty() {
            sleep(TokioDuration::from_secs(binance_worker::DAILY_LOOP_SECS)).await;
            continue;
        }

        tracing::info!(
            "Binance bootstrap worker: {} tickers need full download",
            tickers.len()
        );

        for ticker_entry in &tickers {
            let ticker = &ticker_entry.ticker;
            let ticker_id = ticker_entry.id;
            tracing::info!(ticker, ticker_id, "starting full download");

            // Delete any existing data (in case of retry)
            if let Err(e) = ohlcv::delete_ohlcv_for_ticker(&pool, ticker_id).await {
                tracing::warn!(ticker, ticker_id, "delete ohlcv failed: {e}");
            }

            // ── 1d + 1h + 1m: Vision monthly ZIPs, fill current-month gap with daily ZIPs ──
            let now = chrono::Utc::now();
            let today = now.date_naive();
            for (binance_interval, db_interval, start_year) in
                &[("1d", "1D", 2010i32), ("1h", "1h", 2010i32), ("1m", "1m", 2017i32)]
            {
                let mut total_saved = 0usize;

                // 1. Monthly ZIPs
                let mut year = *start_year;
                loop {
                    for month in 1..=12 {
                        let y = year.to_string();
                        let m = format!("{:02}", month);

                        // Skip future months
                        let month_start = chrono::NaiveDate::from_ymd_opt(year, month, 1)
                            .unwrap()
                            .and_hms_opt(0, 0, 0)
                            .unwrap()
                            .and_utc();
                        if month_start > now {
                            break;
                        }

                        match provider.download_vision_month(ticker, binance_interval, &y, &m).await {
                            Ok(data) if data.is_empty() => {}
                            Ok(data) => {
                                let count = data.len();
                                binance_shared::enhance_and_save(&pool, ticker_id, &data, db_interval).await;
                                total_saved += count;
                                tracing::info!(ticker, interval = db_interval, year = %y, month = %m, count, total = total_saved, "saved vision month");
                            }
                            Err(e) => {
                                // 404 = current month not yet available, fall through to daily ZIPs
                                if e.to_string().contains("404") {
                                    tracing::info!(ticker, interval = db_interval, year = %y, month = %m, "monthly ZIP not available (current month?), will use daily fallback");
                                } else {
                                    tracing::warn!(ticker, interval = db_interval, year = %y, month = %m, "vision month failed: {e}");
                                }
                            }
                        }
                    }
                    if year >= now.year() {
                        break;
                    }
                    year += 1;
                }

                // 2. Daily ZIPs for current month (fill gap where monthly ZIP is 404)
                let cur_year = today.year();
                let cur_month = today.month();
                let max_day = today.day().max(2) - 1; // up to yesterday

                for day in 1..=max_day {
                    let y = cur_year.to_string();
                    let m = format!("{:02}", cur_month);
                    let d = format!("{:02}", day);
                    match provider.download_vision_day(ticker, binance_interval, &y, &m, &d).await {
                        Ok(data) if data.is_empty() => {}
                        Ok(data) => {
                            let count = data.len();
                            binance_shared::enhance_and_save(&pool, ticker_id, &data, db_interval).await;
                            total_saved += count;
                        }
                        Err(_) => {} // 404 expected for future/weekend days
                    }
                }

                tracing::info!(ticker, interval = db_interval, total = total_saved, "vision download done (monthly + current-month daily)");
            }

            // ── Fill gap with live klines ──
            for (binance_interval, db_interval, limit) in &[
                ("1d", "1D", binance_worker::DAILY_LIMIT),
                ("1h", "1h", binance_worker::HOURLY_LIMIT),
                ("1m", "1m", binance_worker::MINUTE_LIMIT),
            ] {
                match provider.get_klines_after(ticker, binance_interval, *limit, 0).await {
                    Ok(data) if data.is_empty() => {}
                    Ok(data) => {
                        let count = data.len();
                        binance_shared::enhance_and_save(&pool, ticker_id, &data, db_interval).await;
                        tracing::info!(ticker, interval = db_interval, count, "saved live klines gap");
                    }
                    Err(e) => {
                        tracing::warn!(ticker, interval = binance_interval, "live klines failed: {e}");
                    }
                }
            }

            // Mark as ready and schedule normal sync
            if let Err(e) = ohlcv::update_ticker_status(&pool, ticker_id, "ready").await {
                tracing::error!(ticker, ticker_id, "bootstrap: failed to set status ready: {e}");
            } else {
                binance_shared::schedule_fixed_interval(&pool, ticker_id, "next_1d", binance_worker::SCHEDULE_DAILY_SECS).await.ok();
                binance_shared::schedule_fixed_interval(&pool, ticker_id, "next_1h", binance_worker::SCHEDULE_HOURLY_SECS).await.ok();
                binance_shared::schedule_fixed_interval(&pool, ticker_id, "next_1m", binance_worker::SCHEDULE_MINUTE_SECS).await.ok();
                tracing::info!(ticker, ticker_id, "bootstrap: full download complete, marked ready");
            }
        }

        sleep(TokioDuration::from_secs(binance_worker::DAILY_LOOP_SECS)).await;
    }
}
