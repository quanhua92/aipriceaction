use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use std::collections::BTreeMap;
use std::fmt::Write;

use crate::server::types::{Mode, StockDataResponse, is_vn_ticker};

/// Map an OhlcvJoined row to a StockDataResponse.
pub(crate) fn map_ohlcv_to_response(
    row: crate::models::ohlcv::OhlcvJoined,
    is_daily: bool,
    _mode: Mode,
) -> StockDataResponse {
    let time_str = if is_daily {
        row.time.format("%Y-%m-%d").to_string()
    } else {
        row.time.format("%Y-%m-%dT%H:%M:%S").to_string()
    };

    StockDataResponse {
        time: time_str,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume as u64,
        symbol: row.ticker,
        ma10: row.ma10,
        ma20: row.ma20,
        ma50: row.ma50,
        ma100: row.ma100,
        ma200: row.ma200,
        ma10_score: row.ma10_score,
        ma20_score: row.ma20_score,
        ma50_score: row.ma50_score,
        ma100_score: row.ma100_score,
        ma200_score: row.ma200_score,
        close_changed: row.close_changed,
        volume_changed: row.volume_changed,
        total_money_changed: row.total_money_changed,
    }
}

/// Map an AggregatedOhlcv row to a StockDataResponse.
pub(crate) fn map_aggregated_to_response(
    row: &crate::services::aggregator::AggregatedOhlcv,
    is_daily: bool,
    _mode: Mode,
) -> StockDataResponse {
    let time_str = if is_daily {
        row.time.format("%Y-%m-%d").to_string()
    } else {
        row.time.format("%Y-%m-%dT%H:%M:%S").to_string()
    };

    StockDataResponse {
        time: time_str,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume as u64,
        symbol: row.ticker.clone(),
        ma10: row.ma10,
        ma20: row.ma20,
        ma50: row.ma50,
        ma100: row.ma100,
        ma200: row.ma200,
        ma10_score: row.ma10_score,
        ma20_score: row.ma20_score,
        ma50_score: row.ma50_score,
        ma100_score: row.ma100_score,
        ma200_score: row.ma200_score,
        close_changed: row.close_changed,
        volume_changed: row.volume_changed,
        total_money_changed: row.total_money_changed,
    }
}

/// Apply legacy price scaling and format the response.
pub(crate) fn build_response(
    mut data: BTreeMap<String, Vec<StockDataResponse>>,
    legacy: bool,
    mode: Mode,
    is_csv: bool,
) -> Response {
    if legacy {
        let divisor = crate::constants::api::LEGACY_DIVISOR;
        for rows in data.values_mut() {
            for row in rows {
                let apply = if mode == Mode::Vn {
                    !crate::server::types::is_index_ticker(&row.symbol)
                } else if mode == Mode::All && is_vn_ticker(&row.symbol) {
                    !crate::server::types::is_index_ticker(&row.symbol)
                } else {
                    false
                };
                if apply {
                    row.open /= divisor;
                    row.high /= divisor;
                    row.low /= divisor;
                    row.close /= divisor;
                }
            }
        }
    }

    if is_csv {
        csv_response(&data)
    } else {
        (StatusCode::OK, Json(data)).into_response()
    }
}

// ── CSV response builder ──

/// Number of decimal places for price fields based on close price magnitude.
/// Tiny crypto (e.g. PEPE ~1e-5) needs more decimals; large stocks (e.g. 70000) need fewer.
fn price_decimals(close: f64) -> usize {
    if close == 0.0 {
        return 2;
    }
    let abs = close.abs();
    if abs < 1e-5 { 6 }
    else if abs < 1e-3 { 5 }
    else if abs < 1.0 { 4 }
    else if abs < 100.0 { 3 }
    else { 2 }
}

/// Write a formatted price directly into the buffer. Avoids intermediate String allocation.
fn write_price(buf: &mut String, v: f64, decimals: usize) {
    // SAFETY: write! to String never fails (std::fmt::Write for String is infallible)
    let _ = write!(buf, "{v:.decimals$}");
}

fn write_opt_price(buf: &mut String, v: Option<f64>, decimals: usize) {
    if let Some(n) = v {
        write_price(buf, n, decimals);
    }
}

/// Write a percentage value, trimming trailing zeros. Avoids intermediate String allocation.
fn write_pct(buf: &mut String, v: f64) {
    // Format with 4 decimal places, then trim trailing zeros in-place
    let start = buf.len();
    let _ = write!(buf, "{v:.4}");
    // Trim trailing zeros (and optional trailing dot) from the just-written portion
    let trimmed = buf[start..].trim_end_matches('0').trim_end_matches('.');
    let trimmed_len = trimmed.len();
    buf.truncate(start + trimmed_len);
}

fn write_opt_pct(buf: &mut String, v: Option<f64>) {
    if let Some(n) = v {
        write_pct(buf, n);
    }
}

fn csv_response(data: &BTreeMap<String, Vec<StockDataResponse>>) -> Response {
    // Estimate ~180 bytes per row (ticker + OHLCV + 5 MAs + 5 scores + 3 pct changes)
    let row_count: usize = data.values().map(|v| v.len()).sum();
    let mut buf = String::with_capacity(200 + row_count * 180);

    buf.push_str("symbol,time,open,high,low,close,volume,ma10,ma20,ma50,ma100,ma200,ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,close_changed,volume_changed,total_money_changed\n");

    for (symbol, rows) in data {
        for r in rows {
            let d = price_decimals(r.close);
            buf.push_str(symbol);
            buf.push(',');
            buf.push_str(&r.time);
            buf.push(',');
            write_price(&mut buf, r.open, d);
            buf.push(',');
            write_price(&mut buf, r.high, d);
            buf.push(',');
            write_price(&mut buf, r.low, d);
            buf.push(',');
            write_price(&mut buf, r.close, d);
            buf.push(',');
            let _ = write!(buf, "{}", r.volume);
            buf.push(',');
            write_opt_price(&mut buf, r.ma10, d);
            buf.push(',');
            write_opt_price(&mut buf, r.ma20, d);
            buf.push(',');
            write_opt_price(&mut buf, r.ma50, d);
            buf.push(',');
            write_opt_price(&mut buf, r.ma100, d);
            buf.push(',');
            write_opt_price(&mut buf, r.ma200, d);
            buf.push(',');
            write_opt_pct(&mut buf, r.ma10_score);
            buf.push(',');
            write_opt_pct(&mut buf, r.ma20_score);
            buf.push(',');
            write_opt_pct(&mut buf, r.ma50_score);
            buf.push(',');
            write_opt_pct(&mut buf, r.ma100_score);
            buf.push(',');
            write_opt_pct(&mut buf, r.ma200_score);
            buf.push(',');
            write_opt_pct(&mut buf, r.close_changed);
            buf.push(',');
            write_opt_pct(&mut buf, r.volume_changed);
            buf.push(',');
            write_opt_pct(&mut buf, r.total_money_changed);
            buf.push('\n');
        }
    }

    (StatusCode::OK, [("content-type", "text/csv")], buf).into_response()
}
