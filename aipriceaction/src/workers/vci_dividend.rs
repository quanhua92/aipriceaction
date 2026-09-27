use std::collections::BTreeMap;

use chrono::{DateTime, Duration as ChronoDuration, Utc};
use sqlx::PgPool;
use tokio::time::{Duration, sleep};

use crate::constants::vci_worker;
use crate::models::ohlcv::{OhlcvRow, Ticker};
use crate::providers::ohlcv::OhlcvData;
use crate::providers::vn_history::{VnHistoryError, VnHistoryProvider};
use crate::queries::{ohlcv, vci_recovery};
use crate::redis::RedisClient;
use crate::workers::{redis_worker, vci_shared};

pub async fn run(pool: PgPool, redis_client: Option<RedisClient>) {
    tracing::info!("VN dividend recovery worker started (VPS first, VCI fallback)");

    let provider = match VnHistoryProvider::new(60, false) {
        Ok(provider) => provider,
        Err(e) => {
            tracing::error!("VN dividend recovery worker: failed to create VPS/VCI provider: {e}");
            return;
        }
    };

    loop {
        // Give daily repair priority, then process one intraday page so a
        // persistent daily failure cannot starve every backfill job.
        let mut did_daily = false;
        match vci_recovery::next_daily_recovery(&pool).await {
            Ok(Some(ticker)) => {
                recover_daily(&pool, &redis_client, &provider, &ticker).await;
                did_daily = true;
            }
            Ok(None) => {}
            Err(e) => {
                tracing::warn!("failed to load VN daily recovery queue: {e}");
                sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await;
                continue;
            }
        }

        match vci_recovery::next_backfill_job(&pool).await {
            Ok(Some(job)) => {
                backfill_one_page(&pool, &redis_client, &provider, &job).await;
                sleep(Duration::from_secs(vci_worker::DIVIDEND_CHUNK_SLEEP_SECS)).await;
            }
            Ok(None) if did_daily => {
                sleep(Duration::from_secs(vci_worker::DIVIDEND_CHUNK_SLEEP_SECS)).await
            }
            Ok(None) => sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await,
            Err(e) => {
                tracing::warn!("failed to load VN intraday backfill queue: {e}");
                sleep(Duration::from_secs(vci_worker::DIVIDEND_LOOP_SECS)).await;
            }
        }
    }
}

async fn recover_daily(
    pool: &PgPool,
    redis_client: &Option<RedisClient>,
    provider: &VnHistoryProvider,
    ticker: &Ticker,
) {
    let ticker_id = ticker.id;
    let symbol = &ticker.ticker;

    if ticker.status.as_deref() != Some("daily-recovered-pending-cache") {
        if ticker.status.as_deref() != Some("full-download-processing") {
            if let Err(e) =
                ohlcv::update_ticker_status(pool, ticker_id, "full-download-processing").await
            {
                tracing::error!(%symbol, "failed to claim daily recovery: {e}");
                return;
            }
        }

        let previous_latest = match ohlcv::get_last_time(pool, ticker_id, "1D").await {
            Ok(time) => time,
            Err(e) => {
                fail_daily(
                    pool,
                    ticker_id,
                    symbol,
                    &format!("failed to read latest daily time: {e}"),
                )
                .await;
                return;
            }
        };

        let (rows, source) = match fetch_daily_history(provider, symbol, ticker_id).await {
            Ok(result) => result,
            Err(e) => {
                fail_daily(pool, ticker_id, symbol, &e).await;
                return;
            }
        };

        let latest = rows.last().map(|row| row.time);
        if previous_latest.is_some_and(|previous| latest.is_none_or(|new| new < previous)) {
            fail_daily(
                pool,
                ticker_id,
                symbol,
                "VN provider daily history ends before existing data",
            )
            .await;
            return;
        }

        match vci_recovery::replace_daily_and_queue(pool, ticker_id, &rows).await {
            Ok(removed) => {
                tracing::info!(%symbol, provider = source, daily_rows = rows.len(), old_rows_removed = removed,
                    latest = ?latest, "daily history replaced; intraday backfill queued");
            }
            Err(e) => {
                fail_daily(
                    pool,
                    ticker_id,
                    symbol,
                    &format!("daily replacement failed: {e}"),
                )
                .await;
                return;
            }
        }
    }

    // Daily replacement leaves the ticker blocked until every old Redis key
    // is cleared. A restart here resumes cache clearing without refetching history.
    for interval in ["1D", "1h", "1m"] {
        if !redis_worker::clear_ohlcv_cache(redis_client, "vn", symbol, interval).await {
            fail_daily(pool, ticker_id, symbol, "Redis cache invalidation failed").await;
            return;
        }
    }

    match vci_recovery::mark_daily_ready(pool, ticker_id).await {
        Ok(()) => {
            tracing::warn!(%symbol, "daily recovery complete; intraday history rebuilding in background")
        }
        Err(e) => {
            fail_daily(
                pool,
                ticker_id,
                symbol,
                &format!("failed to mark daily ready: {e}"),
            )
            .await
        }
    }
}

async fn fail_daily(pool: &PgPool, ticker_id: i32, symbol: &str, reason: &str) {
    tracing::warn!(%symbol, %reason, "daily recovery deferred");
    if let Err(e) = vci_recovery::defer_daily(pool, ticker_id).await {
        tracing::error!(%symbol, "failed to defer daily recovery: {e}");
    }
}

/// Keep a complete daily rebuild on one provider. If VPS fails partway
/// through, restart the whole rebuild from VCI rather than mix adjustments.
async fn fetch_daily_history(
    provider: &VnHistoryProvider,
    symbol: &str,
    ticker_id: i32,
) -> Result<(Vec<OhlcvRow>, &'static str), String> {
    match fetch_daily_history_from(provider, "vps", symbol, ticker_id).await {
        Ok(rows) => Ok((rows, "vps")),
        Err(vps_error) => {
            tracing::warn!(%symbol, %vps_error, "VPS full daily recovery failed; retrying whole history from VCI");
            fetch_daily_history_from(provider, "vci", symbol, ticker_id)
                .await
                .map(|rows| (rows, "vci"))
                .map_err(|vci_error| format!("VPS failed ({vps_error}); VCI failed ({vci_error})"))
        }
    }
}

async fn fetch_daily_history_from(
    provider: &VnHistoryProvider,
    source: &'static str,
    symbol: &str,
    ticker_id: i32,
) -> Result<Vec<OhlcvRow>, String> {
    let cutoff = chrono::NaiveDate::from_ymd_opt(2015, 1, 1)
        .expect("valid daily cutoff")
        .and_hms_opt(0, 0, 0)
        .expect("valid midnight")
        .and_utc();
    let mut to_ts = Utc::now().timestamp();
    let mut bars = BTreeMap::new();
    let mut reached_end = false;

    for _ in 0..64 {
        let page = match provider
            .get_history_from(
                source,
                symbol,
                "1D",
                vci_worker::DIVIDEND_CHUNK_SIZE_DAILY,
                Some(to_ts),
            )
            .await
        {
            Ok(page) => page,
            Err(VnHistoryError::NoData) if !bars.is_empty() => {
                reached_end = true;
                break;
            }
            Err(e) => return Err(format!("{source} daily fetch failed at {to_ts}: {e}")),
        };
        let Some(oldest) = page.first().map(|bar| bar.time) else {
            return Err(format!("{source} returned an empty daily page"));
        };
        if oldest.timestamp() >= to_ts {
            return Err(format!("{source} daily cursor did not move before {to_ts}"));
        }

        for bar in page {
            if !valid_bar(&bar) {
                return Err(format!("{source} returned an invalid daily bar at {}", bar.time));
            }
            bars.insert(bar.time.date_naive(), bar);
        }

        if oldest <= cutoff {
            reached_end = true;
            break;
        }
        to_ts = oldest.timestamp() - 1;
        sleep(Duration::from_secs(vci_worker::DIVIDEND_CHUNK_SLEEP_SECS)).await;
    }

    if !reached_end || bars.is_empty() {
        return Err(format!("{source} daily history did not reach its available beginning"));
    }

    bars.into_values()
        .map(|bar| {
            let time = bar
                .time
                .date_naive()
                .and_hms_opt(0, 0, 0)
                .expect("valid midnight")
                .and_utc();
            let volume = i64::try_from(bar.volume)
                .map_err(|_| format!("{source} daily volume exceeds i64 at {time}"))?;
            Ok(OhlcvRow {
                ticker_id,
                interval: "1D".to_string(),
                time,
                open: bar.open,
                high: bar.high,
                low: bar.low,
                close: bar.close,
                volume,
            })
        })
        .collect()
}

fn valid_bar(bar: &OhlcvData) -> bool {
    [bar.open, bar.high, bar.low, bar.close]
        .iter()
        .all(|price| price.is_finite() && *price > 0.0)
}

async fn backfill_one_page(
    pool: &PgPool,
    redis_client: &Option<RedisClient>,
    provider: &VnHistoryProvider,
    job: &vci_recovery::BackfillJob,
) {
    let cutoff = chrono::NaiveDate::from_ymd_opt(
        vci_worker::DIVIDEND_HM_FLOOR_YEAR,
        vci_worker::DIVIDEND_HM_FLOOR_MONTH,
        1,
    )
    .expect("valid intraday cutoff")
    .and_hms_opt(0, 0, 0)
    .expect("valid midnight")
    .and_utc();
    let to_time = job.before_time.unwrap_or_else(Utc::now);
    if to_time <= cutoff {
        finish_backfill(pool, job).await;
        return;
    }

    let count = if job.interval == "1m" {
        vci_worker::DIVIDEND_CHUNK_SIZE_MINUTE
    } else {
        vci_worker::DIVIDEND_CHUNK_SIZE_HOURLY
    };
    let api_interval = if job.interval == "1h" { "1H" } else { "1m" };
    let result = match provider
        .get_history(&job.ticker, api_interval, count, Some(to_time.timestamp()))
        .await
    {
        Ok(page) => page,
        Err(VnHistoryError::NoData) => {
            finish_backfill(pool, job).await;
            return;
        }
        Err(e) => {
            defer_backfill(pool, job, &format!("VN history fetch failed: {e}")).await;
            return;
        }
    };
    let page = result.bars;

    let Some(oldest) = page.first().map(|bar| bar.time) else {
        defer_backfill(pool, job, "VN provider returned an empty intraday page").await;
        return;
    };
    if oldest >= to_time || page.iter().any(|bar| !valid_bar(bar)) {
        defer_backfill(
            pool,
            job,
            "VN provider returned invalid bars or a non-advancing cursor",
        )
        .await;
        return;
    }
    if !vci_shared::enhance_and_save(
        pool,
        job.ticker_id,
        &page,
        &job.interval,
        "vn",
        &job.ticker,
        redis_client,
    )
    .await
    {
        defer_backfill(pool, job, "intraday database upsert failed").await;
        return;
    }

    tracing::info!(ticker = %job.ticker, interval = %job.interval, provider = result.source, count = page.len(),
        oldest = %oldest, "saved intraday backfill page");

    if oldest <= cutoff {
        finish_backfill(pool, job).await;
    } else {
        let Some(next_to) = DateTime::<Utc>::from_timestamp(oldest.timestamp() - 1, 0) else {
            defer_backfill(pool, job, "intraday cursor is outside timestamp range").await;
            return;
        };
        if let Err(e) = vci_recovery::advance_backfill(pool, job, next_to).await {
            tracing::error!(ticker = %job.ticker, interval = %job.interval, "failed to save backfill cursor: {e}");
        }
    }
}

async fn defer_backfill(pool: &PgPool, job: &vci_recovery::BackfillJob, reason: &str) {
    let exponent = job.attempts.clamp(0, 5) as u32;
    let delay = (15_i64 * (1_i64 << exponent)).min(480);
    let retry_at = Utc::now() + ChronoDuration::seconds(delay);
    tracing::warn!(ticker = %job.ticker, interval = %job.interval, %reason, %retry_at,
        "intraday backfill deferred");
    if let Err(e) = vci_recovery::defer_backfill(pool, job, retry_at).await {
        tracing::error!(ticker = %job.ticker, interval = %job.interval, "failed to defer backfill: {e}");
    }
}

async fn finish_backfill(pool: &PgPool, job: &vci_recovery::BackfillJob) {
    match vci_recovery::complete_backfill(pool, job).await {
        Ok(()) => {
            tracing::info!(ticker = %job.ticker, interval = %job.interval, "intraday backfill complete")
        }
        Err(e) => {
            tracing::error!(ticker = %job.ticker, interval = %job.interval, "failed to complete backfill: {e}")
        }
    }
}
