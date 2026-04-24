//! Raw HTTP handling for Yahoo tickers whose metadata causes serde deserialization
//! failures in the `yahoo-finance-api` crate (e.g. `.PVT` suffix tickers that
//! return `null` for non-optional `i32` fields like `price_hint`).
//!
//! Strategy: make raw `reqwest` calls, patch the null fields in the JSON, then
//! hand the fixed JSON to `YResponse::from_json()` so we reuse all quote
//! extraction logic from the crate.

use super::yahoo::{OhlcvData, RateLimiter, YahooError};
use chrono::{DateTime, Utc};
use yahoo_finance_api::YResponse;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const YAHOO_CHART_URL: &str = "https://query1.finance.yahoo.com/v8/finance/chart";
const MAX_TOTAL_ATTEMPTS: usize = 5;

// ---------------------------------------------------------------------------
// Ticker classification
// ---------------------------------------------------------------------------

/// Returns `true` for tickers that need the raw-HTTP + JSON-patch path.
pub fn needs_raw_path(ticker: &str) -> bool {
    ticker.to_uppercase().ends_with(".PVT")
}

/// Returns `true` for `.PVT` tickers that only support daily intervals.
pub fn is_pvt_ticker(ticker: &str) -> bool {
    ticker.to_uppercase().ends_with(".PVT")
}

// ---------------------------------------------------------------------------
// JSON patching
// ---------------------------------------------------------------------------

/// Replace `null` values in `chart.result[0].meta` with `0` so that serde
/// doesn't fail on non-optional `i32` fields like `price_hint`.
fn patch_null_meta(json: &mut serde_json::Value) {
    if let Some(meta) = json.pointer_mut("/chart/result/0/meta") {
        if let Some(obj) = meta.as_object_mut() {
            for (_, value) in obj.iter_mut() {
                if value.is_null() {
                    *value = serde_json::json!(0);
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// URL builders
// ---------------------------------------------------------------------------

fn chart_range_url(symbol: &str, interval: &str, range: &str) -> String {
    format!(
        "{}/{}?symbol={}&interval={}&range={}&events=div|split|capitalGains",
        YAHOO_CHART_URL, symbol, symbol, interval, range
    )
}

fn chart_period_url(symbol: &str, interval: &str, start: i64, end: i64) -> String {
    format!(
        "{}/{}?symbol={}&period1={}&period2={}&interval={}&events=div|split|capitalGains",
        YAHOO_CHART_URL, symbol, symbol, start, end, interval
    )
}

// ---------------------------------------------------------------------------
// Connector label (mirrors YahooProvider::connector_label)
// ---------------------------------------------------------------------------

fn connector_label(idx: usize, direct_connection: bool) -> String {
    if idx == 0 && direct_connection {
        "direct".to_string()
    } else if idx == 0 && !direct_connection {
        "proxy-1".to_string()
    } else {
        format!("proxy-{}", idx)
    }
}

// ---------------------------------------------------------------------------
// Fetch JSON via raw reqwest
// ---------------------------------------------------------------------------

const USER_AGENT: &str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";

async fn fetch_json(client: &reqwest::Client, url: &str) -> Result<serde_json::Value, YahooError> {
    let resp = client
        .get(url)
        .header("User-Agent", USER_AGENT)
        .send()
        .await
        .map_err(|e| YahooError::InvalidResponse(format!("HTTP error: {}", e)))?;

    if !resp.status().is_success() {
        return Err(YahooError::InvalidResponse(format!(
            "HTTP {}: {}",
            resp.status(),
            resp.status().canonical_reason().unwrap_or("unknown")
        )));
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| YahooError::InvalidResponse(format!("JSON parse error: {}", e)))?;

    // Check for Yahoo API-level errors
    if let Some(error) = json.pointer("/chart/error/code") {
        return Err(YahooError::InvalidResponse(format!(
            "Yahoo API error: {:?}",
            error
        )));
    }

    Ok(json)
}

// ---------------------------------------------------------------------------
// Raw retry with connector rotation
// ---------------------------------------------------------------------------

/// Retry loop using raw `reqwest::Client`s. Fetches JSON, patches nulls in
/// metadata, deserializes through `YResponse::from_json()`.
async fn retry_raw(
    context: &str,
    url: &str,
    raw_clients: &[reqwest::Client],
    rate_limiters: &[RateLimiter],
    direct_connection: bool,
) -> Result<YResponse, YahooError> {
    let mut indices: Vec<usize> = (0..raw_clients.len()).collect();
    use rand::seq::SliceRandom;
    indices.shuffle(&mut rand::thread_rng());

    let mut last_error: Option<YahooError> = None;

    for attempt_idx in 0..MAX_TOTAL_ATTEMPTS {
        let client_idx = indices[attempt_idx % indices.len()];
        let client = &raw_clients[client_idx];
        let label = connector_label(client_idx, direct_connection);

        rate_limiters[client_idx].acquire().await;

        match fetch_json(client, url).await {
            Ok(mut json) => {
                patch_null_meta(&mut json);
                match YResponse::from_json(json) {
                    Ok(yresponse) => {
                        tracing::info!(
                            context = %context,
                            via = %label,
                            attempt = attempt_idx + 1,
                            "Raw request succeeded via {} (attempt {}/{})",
                            label,
                            attempt_idx + 1,
                            MAX_TOTAL_ATTEMPTS,
                        );
                        return Ok(yresponse);
                    }
                    Err(e) => {
                        last_error = Some(YahooError::InvalidResponse(format!(
                            "JSON patch + deserialize failed: {}",
                            e
                        )));
                        tracing::warn!(
                            context = %context,
                            via = %label,
                            attempt = attempt_idx + 1,
                            "Deserialization after patch failed: {}",
                            e,
                        );
                        continue;
                    }
                }
            }
            Err(e) => {
                let err_str = e.to_string();
                last_error = Some(e);

                if err_str.contains("Too many requests")
                    || err_str.contains("429")
                    || err_str.contains("Unauthorized")
                {
                    tracing::warn!(
                        context = %context,
                        via = %label,
                        attempt = attempt_idx + 1,
                        error_kind = "rate_limit",
                        "Raw request failed via {label}: {err_str}",
                    );
                    tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
                    continue;
                }

                let kind = if err_str.contains("connection")
                    || err_str.contains("connect error")
                    || err_str.contains("timeout")
                    || err_str.contains("Could not resolve")
                {
                    "connection_error"
                } else {
                    "other_error"
                };

                tracing::warn!(
                    context = %context,
                    via = %label,
                    attempt = attempt_idx + 1,
                    error_kind = kind,
                    "Raw request failed via {label}: {err_str}",
                );
                continue;
            }
        }
    }

    Err(last_error.unwrap_or_else(|| {
        YahooError::InvalidResponse(format!(
            "Max attempts exceeded ({})",
            MAX_TOTAL_ATTEMPTS
        ))
    }))
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Fetch OHLCV for special tickers via raw HTTP (range-based query).
pub async fn get_history_raw(
    ticker: &str,
    interval: &str,
    range: &str,
    raw_clients: &[reqwest::Client],
    rate_limiters: &[RateLimiter],
    direct_connection: bool,
) -> Result<Vec<OhlcvData>, YahooError> {
    let yahoo_interval = super::yahoo::YahooProvider::map_interval(interval)?;
    let url = chart_range_url(ticker, yahoo_interval, range);

    let response = retry_raw(
        &format!("raw_get_quote_range {}", ticker),
        &url,
        raw_clients,
        rate_limiters,
        direct_connection,
    )
    .await?;

    extract_quotes(response, ticker)
}

/// Fetch OHLCV for special tickers via raw HTTP (period-based query).
pub async fn get_history_interval_raw(
    ticker: &str,
    interval: &str,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
    raw_clients: &[reqwest::Client],
    rate_limiters: &[RateLimiter],
    direct_connection: bool,
) -> Result<Vec<OhlcvData>, YahooError> {
    let yahoo_interval = super::yahoo::YahooProvider::map_interval(interval)?;
    let url = chart_period_url(ticker, yahoo_interval, start.timestamp(), end.timestamp());

    let response = retry_raw(
        &format!("raw_get_quote_history_interval {}", ticker),
        &url,
        raw_clients,
        rate_limiters,
        direct_connection,
    )
    .await?;

    extract_quotes(response, ticker)
}

// ---------------------------------------------------------------------------
// Quote extraction
// ---------------------------------------------------------------------------

fn extract_quotes(response: YResponse, ticker: &str) -> Result<Vec<OhlcvData>, YahooError> {
    let quotes = response.quotes().map_err(|e| {
        YahooError::InvalidResponse(format!("Failed to extract quotes: {}", e))
    })?;

    if quotes.is_empty() {
        return Err(YahooError::NoData);
    }

    let mut result: Vec<OhlcvData> = Vec::with_capacity(quotes.len());
    for quote in &quotes {
        let time = DateTime::<Utc>::from_timestamp(quote.timestamp, 0);
        if let Some(time) = time {
            result.push(OhlcvData {
                time,
                open: quote.open,
                high: quote.high,
                low: quote.low,
                close: quote.close,
                volume: quote.volume,
                symbol: Some(ticker.to_string()),
            });
        }
    }

    result.sort_by(|a, b| a.time.cmp(&b.time));
    Ok(result)
}
