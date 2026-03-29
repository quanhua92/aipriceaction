use std::collections::{HashMap, HashSet};

use chrono::{Datelike, DateTime, Timelike, Utc};
use sqlx::PgPool;

use crate::constants::vci_worker;
use crate::models::indicators::{calculate_ma_score, calculate_sma};
use crate::models::ohlcv::{IndicatorRow, OhlcvRow};
use crate::providers::ohlcv::OhlcvData;
use crate::queries;

/// Load the VN ticker list from ticker_group.json.
///
/// Flattens all sector groups, prepends VNINDEX/VN30, deduplicates.
pub fn load_vn_tickers() -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string("ticker_group.json")?;
    let groups: HashMap<String, Vec<String>> = serde_json::from_str(&content)?;

    let mut seen = HashSet::new();
    let mut tickers = Vec::new();

    // Prepend index tickers
    for &idx in &["VNINDEX", "VN30"] {
        if seen.insert(idx.to_string()) {
            tickers.push(idx.to_string());
        }
    }

    // Flatten all groups
    for (_sector, symbols) in &groups {
        for sym in symbols {
            if seen.insert(sym.clone()) {
                tickers.push(sym.clone());
            }
        }
    }

    Ok(tickers)
}

/// Sync tickers from ticker_group.json into the database.
///
/// Any ticker present in the JSON file but missing from the DB is upserted
/// with status 'ready'.  Returns the number of newly added tickers.
pub async fn sync_tickers_from_json(pool: &PgPool) -> usize {
    let json_tickers = match load_vn_tickers() {
        Ok(t) => t,
        Err(e) => {
            tracing::warn!("failed to load ticker_group.json: {e}");
            return 0;
        }
    };

    let mut added = 0usize;
    for ticker in &json_tickers {
        if let Err(e) = queries::ohlcv::upsert_ticker(pool, "vn", ticker, None).await {
            tracing::warn!(ticker, "failed to upsert ticker from json: {e}");
            continue;
        }
        // If the ticker was freshly inserted it will have no status (NULL) or a
        // non-ready status — the upsert_ticker function doesn't set status.
        // We rely on the ON CONFLICT path which won't change an existing status,
        // so we only need to care about brand-new rows.  A simple UPDATE is cheap
        // enough to run unconditionally for ready.
        // NOTE: set_ticker_ready_if_new hardcodes source='vn' — will NOT affect crypto tickers.
        if let Err(e) = queries::ohlcv::set_ticker_ready_if_new(pool, ticker).await {
            tracing::warn!(ticker, source = "vn", "failed to set ticker ready: {e}");
        }
        added += 1;
    }
    added
}

/// Ensure a VN ticker exists in the database, return its id.
pub async fn ensure_vn_ticker(pool: &PgPool, source: &str, ticker: &str) -> i32 {
    queries::ohlcv::upsert_ticker(pool, source, ticker, None)
        .await
        .expect("failed to upsert ticker")
}

/// Get the latest timestamp for a ticker + interval.
pub async fn get_last_time(
    pool: &PgPool,
    ticker_id: i32,
    interval: &str,
) -> Option<DateTime<Utc>> {
    queries::ohlcv::get_latest_time(pool, ticker_id, interval)
        .await
        .unwrap_or(None)
}

/// Get historical closes and volumes from DB for accurate indicator calculation.
/// Returns (closes, volumes) in chronological order (oldest first).
async fn get_historical(pool: &PgPool, ticker_id: i32, interval: &str, limit: usize) -> (Vec<f64>, Vec<f64>) {
    match queries::ohlcv::get_ohlcv(pool, ticker_id, interval, Some(limit as i64)).await {
        Ok(rows) => {
            let mut closes: Vec<f64> = rows.iter().map(|r| r.close).collect();
            let mut volumes: Vec<f64> = rows.iter().map(|r| r.volume as f64).collect();
            closes.reverse();
            volumes.reverse();
            (closes, volumes)
        }
        Err(_) => (Vec::new(), Vec::new()),
    }
}

/// Maximum SMA period — fetch this many historical closes from DB
/// to ensure all moving averages are accurate.
const SMA_MAX_PERIOD: usize = 200;

/// Calculate technical indicators and bulk-upsert OHLCV + indicator rows.
///
/// Fetches historical closes from DB to ensure SMA(200) and other indicators
/// are calculated accurately even when the new data window is small.
pub async fn enhance_and_save(
    pool: &PgPool,
    ticker_id: i32,
    data: &[OhlcvData],
    interval: &str,
) {
    if data.is_empty() {
        return;
    }

    // Sort chronologically (should already be sorted, but be safe)
    let mut sorted = data.to_vec();
    sorted.sort_by_key(|d| d.time);

    // Fetch historical closes and volumes from DB for accurate SMA calculation
    let (historical_closes, historical_volumes) = get_historical(pool, ticker_id, interval, SMA_MAX_PERIOD).await;
    let offset = historical_closes.len();

    // Combine: historical + new data
    let mut all_closes = historical_closes;
    all_closes.extend(sorted.iter().map(|d| d.close));

    let mut all_volumes = historical_volumes;
    all_volumes.extend(sorted.iter().map(|d| d.volume as f64));

    // Calculate SMAs on the combined dataset
    let ma10 = calculate_sma(&all_closes, 10);
    let ma20 = calculate_sma(&all_closes, 20);
    let ma50 = calculate_sma(&all_closes, 50);
    let ma100 = calculate_sma(&all_closes, 100);
    let ma200 = calculate_sma(&all_closes, 200);

    let ohlcv_rows: Vec<OhlcvRow> = sorted
        .iter()
        .map(|d| OhlcvRow {
            ticker_id,
            interval: interval.to_string(),
            time: d.time,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
            volume: d.volume as i64,
        })
        .collect();

    let indicator_rows: Vec<IndicatorRow> = sorted
        .iter()
        .enumerate()
        .map(|(i, d)| {
            // Global index into all_closes (historical + new)
            let gi = offset + i;
            let make_opt = |vals: &[f64], idx: usize| -> Option<f64> {
                if idx < vals.len() && vals[idx] > 0.0 {
                    Some(vals[idx])
                } else {
                    None
                }
            };

            IndicatorRow {
                ticker_id,
                interval: interval.to_string(),
                time: d.time,
                ma10: make_opt(&ma10, gi),
                ma20: make_opt(&ma20, gi),
                ma50: make_opt(&ma50, gi),
                ma100: make_opt(&ma100, gi),
                ma200: make_opt(&ma200, gi),
                ma10_score: make_opt(&ma10, gi).map(|v| calculate_ma_score(d.close, v)),
                ma20_score: make_opt(&ma20, gi).map(|v| calculate_ma_score(d.close, v)),
                ma50_score: make_opt(&ma50, gi).map(|v| calculate_ma_score(d.close, v)),
                ma100_score: make_opt(&ma100, gi).map(|v| calculate_ma_score(d.close, v)),
                ma200_score: make_opt(&ma200, gi).map(|v| calculate_ma_score(d.close, v)),
                close_changed: if gi > 0 && all_closes[gi - 1] > 0.0 {
                    Some(((d.close - all_closes[gi - 1]) / all_closes[gi - 1]) * 100.0)
                } else {
                    None
                },
                volume_changed: if gi > 0 && all_volumes[gi - 1] > 0.0 {
                    Some(((d.volume as f64 - all_volumes[gi - 1]) / all_volumes[gi - 1]) * 100.0)
                } else {
                    None
                },
                total_money_changed: if gi > 0 && all_closes[gi - 1] > 0.0 {
                    Some((d.close - all_closes[gi - 1]) * d.volume as f64)
                } else {
                    None
                },
            }
        })
        .collect();

    if let Err(e) = queries::import::bulk_upsert_ohlcv(pool, &ohlcv_rows).await {
        tracing::error!(ticker_id, interval, "bulk_upsert_ohlcv failed: {e}");
        return;
    }

    if let Err(e) = queries::import::bulk_upsert_indicators(pool, &indicator_rows).await {
        tracing::error!(ticker_id, interval, "bulk_upsert_indicators failed: {e}");
    }
}

/// Detect dividend by comparing new API data with existing DB data.
///
/// Returns true if a dividend was detected (and status updated).
pub async fn detect_dividend(
    pool: &PgPool,
    ticker_id: i32,
    ticker: &str,
    new_data: &[OhlcvData],
) -> bool {
    // Skip for index tickers
    if vci_worker::INDEX_TICKERS.contains(&ticker) {
        return false;
    }

    if new_data.is_empty() {
        return false;
    }

    // Get existing daily data from DB
    let existing = match queries::ohlcv::get_ohlcv(pool, ticker_id, "1D", Some(vci_worker::DIVIDEND_CHECK_BARS)).await {
        Ok(rows) => rows,
        Err(_) => return false,
    };

    if existing.is_empty() {
        return false;
    }

    // Build a map of date -> close from existing DB data
    let mut existing_map: HashMap<String, f64> = HashMap::new();
    for row in &existing {
        let date_key = row.time.format("%Y-%m-%d").to_string();
        existing_map.insert(date_key, row.close);
    }

    // Compare overlapping dates
    for d in new_data {
        let date_key = d.time.format("%Y-%m-%d").to_string();
        if let Some(&existing_close) = existing_map.get(&date_key) {
            if existing_close > 0.0 && d.close > 0.0 {
                let ratio = existing_close / d.close;
                if ratio > vci_worker::DIVIDEND_RATIO_THRESHOLD {
                    tracing::warn!(
                        ticker,
                        date = %date_key,
                        existing = existing_close,
                        api = d.close,
                        ratio = ratio,
                        "dividend detected"
                    );
                    if let Err(e) = queries::ohlcv::update_ticker_status(pool, ticker_id, "dividend-detected").await {
                        tracing::error!(ticker_id, "failed to update ticker status: {e}");
                    }
                    return true;
                }
            }
        }
    }

    false
}

/// Check if current time is within VN trading hours (9:00-15:00 ICT = 2:00-8:00 UTC).
pub fn is_trading_hours() -> bool {
    let now = chrono::Utc::now();
    // VN trading hours: 9:00-15:00 ICT = 2:00-8:00 UTC
    let hour = now.hour();
    // Only on weekdays (Mon=1 .. Fri=5)
    let weekday = now.weekday().num_days_from_monday(); // 0=Mon, 6=Sun
    weekday < 5 && hour >= 2 && hour < 8
}
