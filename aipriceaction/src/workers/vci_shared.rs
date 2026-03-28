use std::collections::{HashMap, HashSet};

use chrono::{Datelike, DateTime, Timelike, Utc};
use sqlx::PgPool;

use crate::constants::vci_worker;
use crate::models::indicators::{calculate_ma_score, calculate_sma};
use crate::models::ohlcv::{IndicatorRow, OhlcvRow};
use crate::providers::vci::OhlcvData;
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

/// Ensure a ticker exists in the database, return its id.
pub async fn ensure_ticker(pool: &PgPool, source: &str, ticker: &str) -> i32 {
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

/// Calculate technical indicators and bulk-upsert OHLCV + indicator rows.
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

    let closes: Vec<f64> = sorted.iter().map(|d| d.close).collect();
    let ma10 = calculate_sma(&closes, 10);
    let ma20 = calculate_sma(&closes, 20);
    let ma50 = calculate_sma(&closes, 50);
    let ma100 = calculate_sma(&closes, 100);
    let ma200 = calculate_sma(&closes, 200);

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
                ma10: make_opt(&ma10, i),
                ma20: make_opt(&ma20, i),
                ma50: make_opt(&ma50, i),
                ma100: make_opt(&ma100, i),
                ma200: make_opt(&ma200, i),
                ma10_score: make_opt(&ma10, i).map(|v| calculate_ma_score(d.close, v)),
                ma20_score: make_opt(&ma20, i).map(|v| calculate_ma_score(d.close, v)),
                ma50_score: make_opt(&ma50, i).map(|v| calculate_ma_score(d.close, v)),
                ma100_score: make_opt(&ma100, i).map(|v| calculate_ma_score(d.close, v)),
                ma200_score: make_opt(&ma200, i).map(|v| calculate_ma_score(d.close, v)),
                close_changed: if i > 0 && closes[i - 1] > 0.0 {
                    Some(((d.close - closes[i - 1]) / closes[i - 1]) * 100.0)
                } else {
                    None
                },
                volume_changed: if i > 0 {
                    let prev_vol = sorted[i - 1].volume as f64;
                    if prev_vol > 0.0 {
                        Some(((d.volume as f64 - prev_vol) / prev_vol) * 100.0)
                    } else {
                        None
                    }
                } else {
                    None
                },
                total_money_changed: if i > 0 {
                    Some((d.close - closes[i - 1]) * d.volume as f64)
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
