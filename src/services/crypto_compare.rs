//! CryptoCompare API Client
//!
//! This module provides a client for fetching cryptocurrency historical data
//! from the CryptoCompare API (https://min-api.cryptocompare.com).
//!
//! Features:
//! - Rate limiting (5 calls/second for free tier)
//! - Exponential backoff retry logic (max 5 attempts)
//! - Support for daily, hourly, and minute intervals
//! - Pagination with toTs parameter for full history
//! - allData=true optimization for daily endpoint
//!
//! # Example
//!
//! ```rust
//! use crypto_compare::CryptoCompareClient;
//!
//! let mut client = CryptoCompareClient::new(None)?;
//!
//! // Fetch BTC daily data with full history
//! let data = client.get_history(
//!     "BTC",
//!     "2010-01-01",
//!     None,
//!     Interval::Daily,
//!     None,
//!     true, // Use allData=true
//! ).await?;
//! ```

use crate::models::Interval;
use crate::services::vci::OhlcvData;
use chrono::{DateTime, NaiveDate};
use reqwest::{Client, Error as ReqwestError};
use serde::Deserialize;
use std::time::{Duration as StdDuration, SystemTime};
use tokio::time::sleep;
use tracing::{debug, info, warn};

/// Base URL for CryptoCompare API
const BASE_URL: &str = "https://min-api.cryptocompare.com";

/// Default rate limit (5 calls/second for free tier)
const DEFAULT_RATE_LIMIT_PER_SECOND: u32 = 5;

/// Maximum retries for failed requests
const MAX_RETRIES: u32 = 5;

/// CryptoCompare API error types
#[derive(Debug)]
pub enum CryptoError {
    Http(ReqwestError),
    Serialization(serde_json::Error),
    InvalidInterval(String),
    InvalidResponse(String),
    RateLimit,
    NoData,
    InvalidSymbol(String),
}

impl From<ReqwestError> for CryptoError {
    fn from(error: ReqwestError) -> Self {
        CryptoError::Http(error)
    }
}

impl From<serde_json::Error> for CryptoError {
    fn from(error: serde_json::Error) -> Self {
        CryptoError::Serialization(error)
    }
}

impl std::fmt::Display for CryptoError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CryptoError::Http(e) => write!(f, "HTTP error: {}", e),
            CryptoError::Serialization(e) => write!(f, "Serialization error: {}", e),
            CryptoError::InvalidInterval(s) => write!(f, "Invalid interval: {}", s),
            CryptoError::InvalidResponse(s) => write!(f, "Invalid response: {}", s),
            CryptoError::RateLimit => write!(f, "Rate limit exceeded"),
            CryptoError::NoData => write!(f, "No data returned"),
            CryptoError::InvalidSymbol(s) => write!(f, "Invalid symbol: {}", s),
        }
    }
}

impl std::error::Error for CryptoError {}

/// CryptoCompare API response structure (for errors and success)
#[derive(Debug, Deserialize)]
struct CryptoCompareResponse {
    #[serde(rename = "Response")]
    response: String,
    #[serde(rename = "Message")]
    message: String,
    #[serde(rename = "HasWarning")]
    #[allow(dead_code)]
    has_warning: bool,
    #[serde(rename = "Type")]
    #[allow(dead_code)]
    response_type: i32,
    #[serde(rename = "Data")]
    #[serde(default)]
    data: Option<serde_json::Value>,  // Use Value to handle both CryptoCompareData and {}
    #[serde(rename = "RateLimit")]
    #[serde(default)]
    rate_limit: Option<RateLimitInfo>,
}

/// Rate limit information from CryptoCompare API
#[derive(Debug, Deserialize)]
struct RateLimitInfo {
    #[serde(rename = "calls_made")]
    calls_made: CallStats,
    #[serde(rename = "max_calls")]
    #[allow(dead_code)]
    max_calls: Option<CallStats>,
}

#[derive(Debug, Deserialize)]
struct CallStats {
    #[serde(default)]
    second: i32,
    #[serde(default)]
    minute: i32,
    #[serde(default)]
    hour: i32,
    #[serde(default)]
    day: i32,
    #[serde(default)]
    month: i32,
    #[serde(default)]
    total_calls: i32,
}

#[derive(Debug, Deserialize)]
struct CryptoCompareData {
    #[serde(rename = "Aggregated")]
    #[allow(dead_code)]
    aggregated: bool,
    #[serde(rename = "TimeFrom")]
    #[allow(dead_code)]
    time_from: i64,
    #[serde(rename = "TimeTo")]
    #[allow(dead_code)]
    time_to: i64,
    #[serde(rename = "Data")]
    data: Vec<CryptoCompareCandle>,
}

#[derive(Debug, Deserialize)]
struct CryptoCompareCandle {
    time: i64,
    high: f64,
    low: f64,
    open: f64,
    #[serde(rename = "volumefrom")]
    volume_from: f64,
    #[serde(rename = "volumeto")]
    volume_to: f64,
    close: f64,
    #[serde(rename = "conversionType")]
    #[allow(dead_code)]
    conversion_type: String,
    #[serde(rename = "conversionSymbol")]
    #[allow(dead_code)]
    conversion_symbol: String,
}

/// CryptoCompare API client with rate limiting and retry logic
#[derive(Clone)]
pub struct CryptoCompareClient {
    client: Client,
    api_key: Option<String>,
    rate_limit_per_second: u32,
    request_timestamps: Vec<SystemTime>,
}

impl CryptoCompareClient {
    /// Create a new CryptoCompare API client
    ///
    /// # Arguments
    ///
    /// * `api_key` - Optional API key for authenticated requests (higher rate limits)
    ///
    /// # Returns
    ///
    /// A new CryptoCompareClient instance
    pub fn new(api_key: Option<String>) -> Result<Self, CryptoError> {
        let client = Client::builder()
            .timeout(StdDuration::from_secs(30))
            .build()
            .map_err(CryptoError::Http)?;

        Ok(Self {
            client,
            api_key,
            rate_limit_per_second: DEFAULT_RATE_LIMIT_PER_SECOND,
            request_timestamps: Vec::new(),
        })
    }

    /// Set custom rate limit (useful for paid API tiers)
    pub fn with_rate_limit(mut self, rate_limit: u32) -> Self {
        self.rate_limit_per_second = rate_limit;
        self
    }

    /// Enforce rate limiting using sliding window algorithm
    ///
    /// This method ensures we don't exceed the rate limit by:
    /// 1. Removing timestamps older than 1 second
    /// 2. Waiting if we've hit the rate limit
    /// 3. Recording the current request timestamp
    async fn enforce_rate_limit(&mut self) {
        let current_time = SystemTime::now();

        // Remove timestamps older than 1 second (sliding window)
        self.request_timestamps.retain(|&timestamp| {
            current_time
                .duration_since(timestamp)
                .unwrap_or(StdDuration::from_secs(0))
                < StdDuration::from_secs(1)
        });

        // Wait if at rate limit
        if self.request_timestamps.len() >= self.rate_limit_per_second as usize {
            if let Some(&oldest_request) = self.request_timestamps.first() {
                let elapsed = current_time
                    .duration_since(oldest_request)
                    .unwrap_or(StdDuration::from_secs(0));

                if elapsed < StdDuration::from_secs(1) {
                    let wait_time = StdDuration::from_secs(1) - elapsed + StdDuration::from_millis(100);
                    debug!("Rate limit reached, waiting {:?}", wait_time);
                    sleep(wait_time).await;
                }
            }
        }

        // Record current request
        self.request_timestamps.push(current_time);
    }

    /// Make HTTP request with retry logic and exponential backoff
    async fn make_request(&mut self, url: &str) -> Result<CryptoCompareResponse, CryptoError> {
        for attempt in 0..MAX_RETRIES {
            // Always enforce rate limit before request
            self.enforce_rate_limit().await;

            // Exponential backoff for retries (after first attempt)
            if attempt > 0 {
                let delay = StdDuration::from_secs_f64(
                    2.0_f64.powi(attempt as i32 - 1) + rand::random::<f64>(),
                );
                let delay = delay.min(StdDuration::from_secs(60)); // Cap at 60 seconds
                info!(
                    "CryptoCompare API retry backoff: attempt {}/{}, waiting {:.1}s",
                    attempt + 1,
                    MAX_RETRIES,
                    delay.as_secs_f64()
                );
                sleep(delay).await;
            }

            // Build request
            let mut request = self.client.get(url);

            // Add API key if provided
            if let Some(ref api_key) = self.api_key {
                request = request.header("Authorization", format!("Apikey {}", api_key));
            }

            // Send request
            let response = match request.send().await {
                Ok(resp) => resp,
                Err(e) => {
                    warn!("Request failed (attempt {}): {}", attempt + 1, e);
                    continue; // Retry on network errors
                }
            };

            // Check HTTP status
            let status = response.status();

            if status.is_success() {
                // Get response body as text first (for better error logging)
                let body = match response.text().await {
                    Ok(text) => text,
                    Err(e) => {
                        warn!("Failed to read response body (attempt {}): {}", attempt + 1, e);
                        continue;
                    }
                };

                // Parse JSON response
                match serde_json::from_str::<CryptoCompareResponse>(&body) {
                    Ok(data) => {
                        // Check API response status
                        if data.response == "Success" {
                            return Ok(data);
                        } else if data.response == "Error" {
                            // Check for rate limit error (Type 99)
                            if data.response_type == 99 || data.message.contains("rate limit") {
                                // Log rate limit info if available
                                if let Some(ref rate_limit) = data.rate_limit {
                                    warn!(
                                        "Rate limit exceeded! Daily calls: {}/7500",
                                        rate_limit.calls_made.day
                                    );
                                } else {
                                    warn!("Rate limit exceeded: {}", data.message);
                                }
                                // Return immediately - do NOT retry on rate limit
                                return Err(CryptoError::RateLimit);
                            }

                            // Other API errors
                            if data.message.contains("invalid symbol") || data.message.contains("market does not exist") {
                                return Err(CryptoError::InvalidSymbol(data.message));
                            }

                            warn!("API error: {}", data.message);
                            if attempt < MAX_RETRIES - 1 {
                                continue; // Retry
                            } else {
                                return Err(CryptoError::InvalidResponse(data.message));
                            }
                        } else {
                            // Unknown response type
                            warn!("Unknown API response: {}", data.response);
                            if attempt < MAX_RETRIES - 1 {
                                continue;
                            } else {
                                return Err(CryptoError::InvalidResponse(data.response));
                            }
                        }
                    }
                    Err(e) => {
                        // Log the first 500 chars of response body for debugging
                        let body_preview = if body.len() > 500 {
                            format!("{}... (truncated)", &body[..500])
                        } else {
                            body.clone()
                        };
                        warn!("JSON parse error (attempt {}): {}", attempt + 1, e);
                        warn!("Response body: {}", body_preview);
                        continue; // Retry
                    }
                }
            } else if status == 429 || status == 403 {
                // Rate limit or forbidden - retry with backoff
                warn!("Rate limit or forbidden ({}), retrying...", status);
                continue;
            } else if status.is_server_error() {
                // 5xx errors - retry
                warn!("Server error ({}), retrying...", status);
                continue;
            } else {
                // Other client errors - don't retry
                return Err(CryptoError::InvalidResponse(format!(
                    "HTTP error: {}",
                    status
                )));
            }
        }

        Err(CryptoError::InvalidResponse(
            "Max retries exceeded".to_string(),
        ))
    }

    /// Get endpoint path for interval
    fn get_endpoint_path(&self, interval: Interval) -> &'static str {
        match interval {
            Interval::Daily => "/data/v2/histoday",
            Interval::Hourly => "/data/v2/histohour",
            Interval::Minute => "/data/v2/histominute",
        }
    }

    /// Fetch cryptocurrency historical data
    ///
    /// # Arguments
    ///
    /// * `symbol` - Cryptocurrency symbol (e.g., "BTC", "ETH")
    /// * `start_date` - Start date in YYYY-MM-DD format
    /// * `to_ts` - Optional Unix timestamp to fetch data before (for pagination)
    /// * `interval` - Data interval (Daily, Hourly, Minute)
    /// * `limit` - Optional limit on number of records (default: 2000, max: 2000)
    /// * `use_all_data` - Use allData=true for daily endpoint (fetches full history)
    ///
    /// # Returns
    ///
    /// Vector of OHLCV data sorted by time (oldest first)
    pub async fn get_history(
        &mut self,
        symbol: &str,
        start_date: &str,
        to_ts: Option<i64>,
        interval: Interval,
        limit: Option<usize>,
        use_all_data: bool,
    ) -> Result<Vec<OhlcvData>, CryptoError> {
        let endpoint = self.get_endpoint_path(interval);
        let limit = limit.unwrap_or(2000).min(2000); // Max 2000 per request

        // Build URL
        let mut url = format!(
            "{}{}?fsym={}&tsym=USD&limit={}",
            BASE_URL, endpoint, symbol, limit
        );

        // Add allData parameter for daily endpoint
        if use_all_data && interval == Interval::Daily {
            url.push_str("&allData=true");
        }

        // Add toTs for pagination
        if let Some(ts) = to_ts {
            url.push_str(&format!("&toTs={}", ts));
        }

        debug!("Fetching crypto data: {}", url);

        // Make request with retry logic
        let response = self.make_request(&url).await?;

        // Parse data
        let data_value = response.data.ok_or(CryptoError::NoData)?;
        let data: CryptoCompareData = serde_json::from_value(data_value)
            .map_err(|e| CryptoError::Serialization(e))?;

        // Convert to OhlcvData
        let mut ohlcv_data: Vec<OhlcvData> = data
            .data
            .into_iter()
            .filter_map(|candle| {
                // Skip zero-volume candles
                if candle.volume_from == 0.0 && candle.volume_to == 0.0 {
                    return None;
                }

                // Convert Unix timestamp to DateTime
                let time = match DateTime::from_timestamp(candle.time, 0) {
                    Some(dt) => dt,
                    None => {
                        warn!("Invalid timestamp: {}", candle.time);
                        return None;
                    }
                };

                Some(OhlcvData {
                    time,
                    open: candle.open,
                    high: candle.high,
                    low: candle.low,
                    close: candle.close,
                    volume: candle.volume_from as u64, // Use volumefrom (crypto volume)
                    symbol: Some(symbol.to_string()),
                })
            })
            .collect();

        // Filter by start_date if provided
        if let Ok(start_naive) = NaiveDate::parse_from_str(start_date, "%Y-%m-%d") {
            let start_dt = start_naive
                .and_hms_opt(0, 0, 0)
                .unwrap()
                .and_utc();
            ohlcv_data.retain(|d| d.time >= start_dt);
        }

        // Sort by time (oldest first)
        ohlcv_data.sort_by_key(|d| d.time);

        info!(
            "Fetched {} records for {} ({})",
            ohlcv_data.len(),
            symbol,
            interval.to_filename()
        );

        Ok(ohlcv_data)
    }

    /// Fetch full history for a cryptocurrency using pagination
    ///
    /// This method handles pagination automatically for hourly and minute data
    /// by making multiple requests with toTs parameter.
    ///
    /// For daily data, it uses allData=true to fetch everything in one request.
    ///
    /// # Arguments
    ///
    /// * `symbol` - Cryptocurrency symbol (e.g., "BTC", "ETH")
    /// * `start_date` - Start date in YYYY-MM-DD format
    /// * `interval` - Data interval (Daily, Hourly, Minute)
    ///
    /// # Returns
    ///
    /// Vector of OHLCV data sorted by time (oldest first)
    pub async fn get_full_history(
        &mut self,
        symbol: &str,
        start_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, CryptoError> {
        match interval {
            Interval::Daily => {
                // Use allData=true for daily (no pagination needed)
                self.get_history(symbol, start_date, None, interval, None, true)
                    .await
            }
            Interval::Hourly | Interval::Minute => {
                // Paginate for hourly/minute
                let mut all_data = Vec::new();
                let mut to_ts: Option<i64> = None;
                let limit = 2000;

                loop {
                    let batch = self
                        .get_history(symbol, start_date, to_ts, interval, Some(limit), false)
                        .await?;

                    if batch.is_empty() {
                        break; // No more data
                    }

                    let batch_len = batch.len();

                    // Get oldest timestamp for next batch
                    if let Some(first) = batch.first() {
                        to_ts = Some(first.time.timestamp());
                    }

                    all_data.extend(batch);

                    info!(
                        "Pagination: fetched {} records, total: {}, oldest: {}",
                        batch_len,
                        all_data.len(),
                        to_ts.map(|ts| DateTime::from_timestamp(ts, 0)
                            .unwrap()
                            .to_rfc3339())
                            .unwrap_or_default()
                    );

                    // Stop if we got less than limit (no more data available)
                    if batch_len < limit {
                        break;
                    }
                }

                // Sort by time (oldest first)
                all_data.sort_by_key(|d| d.time);

                // Remove duplicates (pagination might overlap)
                all_data.dedup_by_key(|d| d.time);

                info!(
                    "Full history: {} total records for {} ({})",
                    all_data.len(),
                    symbol,
                    interval.to_filename()
                );

                Ok(all_data)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Datelike;

    #[tokio::test]
    async fn test_rate_limiting() {
        let mut client = CryptoCompareClient::new(None).unwrap();

        let start = SystemTime::now();

        // Make 6 requests (should trigger rate limit)
        for i in 0..6 {
            client.enforce_rate_limit().await;
            println!("Request {} at {:?}", i + 1, start.elapsed().unwrap());
        }

        let elapsed = start.elapsed().unwrap();

        // Should take at least 1 second (5 requests per second, 6th request waits)
        assert!(
            elapsed >= StdDuration::from_millis(900),
            "Rate limiting not working: elapsed {:?}",
            elapsed
        );
    }

    #[tokio::test]
    #[ignore] // Requires network access
    async fn test_fetch_btc_daily() {
        let mut client = CryptoCompareClient::new(None).unwrap();

        let data = client
            .get_history("BTC", "2010-01-01", None, Interval::Daily, Some(10), false)
            .await
            .unwrap();

        assert!(!data.is_empty(), "Should return some data");
        assert!(data.len() <= 10, "Should respect limit");

        // Verify data structure
        for candle in &data {
            assert!(candle.open > 0.0);
            assert!(candle.high >= candle.open);
            assert!(candle.low <= candle.open);
            assert!(candle.close > 0.0);
        }
    }

    #[tokio::test]
    #[ignore] // Requires network access
    async fn test_fetch_btc_all_data() {
        let mut client = CryptoCompareClient::new(None).unwrap();

        let data = client
            .get_history("BTC", "2010-01-01", None, Interval::Daily, None, true)
            .await
            .unwrap();

        println!("Fetched {} daily records", data.len());

        assert!(data.len() > 5000, "Should have years of data");

        // Verify first record is from 2010
        if let Some(first) = data.first() {
            assert!(
                first.time.year() == 2010,
                "First record should be from 2010"
            );
        }
    }

    #[tokio::test]
    #[ignore] // Requires network access
    async fn test_pagination() {
        let mut client = CryptoCompareClient::new(None).unwrap();

        // Fetch first batch
        let batch1 = client
            .get_history("BTC", "2010-01-01", None, Interval::Hourly, Some(100), false)
            .await
            .unwrap();

        assert!(!batch1.is_empty());

        // Get oldest timestamp
        let to_ts = batch1.first().unwrap().time.timestamp();

        // Fetch second batch using toTs
        let batch2 = client
            .get_history(
                "BTC",
                "2010-01-01",
                Some(to_ts),
                Interval::Hourly,
                Some(100),
                false,
            )
            .await
            .unwrap();

        assert!(!batch2.is_empty());

        // Verify batch2 is older than batch1
        if let (Some(last1), Some(first2)) = (batch1.last(), batch2.first()) {
            assert!(
                first2.time < last1.time,
                "Pagination should fetch older data"
            );
        }
    }

    #[tokio::test]
    #[ignore] // Requires network access
    async fn test_invalid_symbol() {
        let mut client = CryptoCompareClient::new(None).unwrap();

        let result = client
            .get_history(
                "INVALID123",
                "2010-01-01",
                None,
                Interval::Daily,
                Some(10),
                false,
            )
            .await;

        assert!(result.is_err(), "Should return error for invalid symbol");
    }
}
