use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use std::collections::BTreeMap;

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

fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(n) => n.to_string(),
        None => String::new(),
    }
}

fn csv_response(data: &BTreeMap<String, Vec<StockDataResponse>>) -> Response {
    let mut buf = String::from(
        "symbol,time,open,high,low,close,volume,ma10,ma20,ma50,ma100,ma200,ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,close_changed,volume_changed,total_money_changed\n",
    );

    for (symbol, rows) in data {
        for r in rows {
            buf.push_str(symbol);
            buf.push(',');
            buf.push_str(&r.time);
            buf.push(',');
            buf.push_str(&r.open.to_string());
            buf.push(',');
            buf.push_str(&r.high.to_string());
            buf.push(',');
            buf.push_str(&r.low.to_string());
            buf.push(',');
            buf.push_str(&r.close.to_string());
            buf.push(',');
            buf.push_str(&r.volume.to_string());
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma10));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma20));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma50));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma100));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma200));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma10_score));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma20_score));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma50_score));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma100_score));
            buf.push(',');
            buf.push_str(&fmt_opt(r.ma200_score));
            buf.push(',');
            buf.push_str(&fmt_opt(r.close_changed));
            buf.push(',');
            buf.push_str(&fmt_opt(r.volume_changed));
            buf.push(',');
            buf.push_str(&fmt_opt(r.total_money_changed));
            buf.push('\n');
        }
    }

    (StatusCode::OK, [("content-type", "text/csv")], buf).into_response()
}
