use isahc::{HttpClient, config::Configurable, prelude::*};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration as StdDuration;
use tokio::sync::Semaphore;
use tokio::time::{sleep, Duration};
use chrono::{DateTime, NaiveDate, Utc};
use crate::constants::vci_worker;

pub use super::ohlcv::OhlcvData;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum VciError {
    Http(isahc::Error),
    Serialization(serde_json::Error),
    InvalidInterval(String),
    InvalidResponse(String),
    RateLimit,
    NoData,
}

impl From<isahc::Error> for VciError {
    fn from(error: isahc::Error) -> Self {
        VciError::Http(error)
    }
}

impl From<serde_json::Error> for VciError {
    fn from(error: serde_json::Error) -> Self {
        VciError::Serialization(error)
    }
}

impl std::fmt::Display for VciError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            VciError::Http(e) => write!(f, "HTTP error: {}", e),
            VciError::Serialization(e) => write!(f, "Serialization error: {}", e),
            VciError::InvalidInterval(s) => write!(f, "Invalid interval: {}", s),
            VciError::InvalidResponse(s) => write!(f, "Invalid response: {}", s),
            VciError::RateLimit => write!(f, "Rate limit exceeded"),
            VciError::NoData => write!(f, "No data available"),
        }
    }
}

impl std::error::Error for VciError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            VciError::Http(e) => Some(e),
            VciError::Serialization(e) => Some(e),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompanyInfo {
    pub symbol: String,
    pub exchange: Option<String>,
    pub industry: Option<String>,
    pub company_type: Option<String>,
    pub established_year: Option<u32>,
    pub employees: Option<u32>,
    pub market_cap: Option<f64>,
    pub current_price: Option<f64>,
    pub outstanding_shares: Option<u64>,
    pub company_profile: Option<String>,
    pub website: Option<String>,
    pub shareholders: Vec<ShareholderInfo>,
    pub officers: Vec<OfficerInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShareholderInfo {
    pub name: String,
    pub percentage: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OfficerInfo {
    pub name: String,
    pub position: String,
    pub percentage: Option<f64>,
}

// ---------------------------------------------------------------------------
// Per-client rate limiter (semaphore-based, Arc-sharable)
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub struct RateLimiter {
    semaphore: Arc<Semaphore>,
    refill_handle: Arc<std::sync::Mutex<Option<tokio::task::JoinHandle<()>>>>,
}

impl RateLimiter {
    /// Create a new per-client rate limiter.
    ///
    /// `requests_per_minute` controls how many requests a single client can send
    /// per 60-second window. Permits are refilled at a steady rate.
    pub fn new(requests_per_minute: u32) -> Self {
        let semaphore = Arc::new(Semaphore::new(requests_per_minute as usize));
        let refill_interval_ms = 60_000 / requests_per_minute as u64;

        let sem_clone = semaphore.clone();
        let handle = tokio::spawn(async move {
            loop {
                sleep(StdDuration::from_millis(refill_interval_ms)).await;
                if sem_clone.available_permits() < requests_per_minute as usize {
                    sem_clone.add_permits(1);
                }
            }
        });

        Self {
            semaphore,
            refill_handle: Arc::new(std::sync::Mutex::new(Some(handle))),
        }
    }

    /// Acquire a permit (blocks until one is available, then consumes it).
    pub async fn acquire(&self) {
        match self.semaphore.acquire().await {
            Ok(permit) => permit.forget(),
            Err(_) => {
                tracing::error!("rate limiter semaphore closed unexpectedly");
                // Sleep to avoid busy-looping if semaphore is permanently closed
                tokio::time::sleep(std::time::Duration::from_secs(1)).await;
            }
        }
    }

}

impl Drop for RateLimiter {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.refill_handle.lock() {
            if let Some(handle) = guard.take() {
                handle.abort();
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Sanitize proxy URL for logging
// ---------------------------------------------------------------------------

fn sanitize_proxy_url(proxy_url: &str) -> String {
    match proxy_url.parse::<isahc::http::Uri>() {
        Ok(uri) => {
            let scheme = uri.scheme_str().unwrap_or("unknown");
            let host = uri.host().unwrap_or("unknown");
            let port = uri.port_u16().map_or(String::new(), |p| format!(":{}", p));
            format!("{}://{}{}", scheme, host, port)
        }
        Err(_) => {
            if let Some(at_pos) = proxy_url.find('@') {
                if let Some(scheme_end) = proxy_url.find("://") {
                    let scheme = &proxy_url[..scheme_end + 3];
                    let after_at = &proxy_url[at_pos + 1..];
                    format!("{}:***@{}", scheme, after_at)
                } else {
                    "***@***".to_string()
                }
            } else {
                proxy_url.to_string()
            }
        }
    }
}

// ---------------------------------------------------------------------------
// VciProvider — multi-client with random rotation + per-client rate limit
// ---------------------------------------------------------------------------

pub struct VciProvider {
    clients: Vec<HttpClient>,
    rate_limiters: Vec<RateLimiter>,
    base_url: String,
    direct_connection: bool,
    handshake_cookies: tokio::sync::Mutex<Option<String>>,
}

impl VciProvider {
    /// Build a new provider.
    ///
    /// - Always creates 1 direct `HttpClient`.
    /// - Optionally adds proxy clients from the `HTTP_PROXIES` env var (comma-separated).
    /// - Each client gets its own `RateLimiter`.
    /// - `requests_per_minute` controls the per-client rate limit.
    pub fn new(requests_per_minute: u32) -> Result<Self, VciError> {
        Self::with_options(requests_per_minute, true)
    }

    pub fn with_options(requests_per_minute: u32, direct_connection: bool) -> Result<Self, VciError> {
        let mut clients = Vec::new();
        let mut rate_limiters = Vec::new();

        // 1. Direct client
        if direct_connection {
            let direct_client = HttpClient::builder()
                .timeout(StdDuration::from_secs(30))
                .build()?;
            rate_limiters.push(RateLimiter::new(requests_per_minute));
            clients.push(direct_client);
            eprintln!("✓ Direct connection enabled");
        } else {
            eprintln!("⚠️  Direct connection DISABLED (proxy-only mode)");
        }

        // 2. Proxy clients from HTTP_PROXIES env var
        if let Ok(proxy_urls) = std::env::var("HTTP_PROXIES") {
            for proxy_url in proxy_urls.split(',') {
                let proxy_url = proxy_url.trim();
                if proxy_url.is_empty() {
                    continue;
                }
                match proxy_url.parse::<isahc::http::Uri>() {
                    Ok(proxy_uri) => {
                        match HttpClient::builder()
                            .proxy(Some(proxy_uri))
                            .timeout(StdDuration::from_secs(30))
                            .build()
                        {
                            Ok(client) => {
                                rate_limiters.push(RateLimiter::new(requests_per_minute));
                                clients.push(client);
                                eprintln!("✅ Added proxy: {}", sanitize_proxy_url(proxy_url));
                            }
                            Err(e) => {
                                eprintln!("❌ Failed to create client for proxy {}: {}", sanitize_proxy_url(proxy_url), e);
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("❌ Invalid proxy URL {}: {}", sanitize_proxy_url(proxy_url), e);
                    }
                }
            }
        }

        // Summary
        if !direct_connection {
            eprintln!("📊 VciProvider initialized with {} proxy-only client(s)", clients.len());
        } else {
            eprintln!(
                "📊 VciProvider initialized with {} client(s) (1 direct + {} proxies, {}/min each)",
                clients.len(),
                clients.len().saturating_sub(1),
                requests_per_minute,
            );
        }

        if clients.is_empty() {
            return Err(VciError::InvalidResponse(
                "No HTTP clients available (direct disabled and no proxies configured)".to_string(),
            ));
        }

        Ok(Self {
            clients,
            rate_limiters,
            base_url: "https://trading.vietcap.com.vn/api/".to_string(),
            direct_connection,
            handshake_cookies: tokio::sync::Mutex::new(None),
        })
    }

    pub fn client_count(&self) -> usize {
        self.clients.len()
    }

    // -----------------------------------------------------------------------
    // Core request with multi-client rotation (max 5 total attempts)
    // -----------------------------------------------------------------------

    async fn make_request(&self, url: &str, payload: &Value) -> Result<Value, VciError> {
        const MAX_TOTAL_ATTEMPTS: usize = 5;

        // Extract ticker(s) from payload for logging
        let tickers: Vec<String> = payload
            .get("symbols")
            .and_then(|v| {
                if v.is_array() {
                    v.as_array()
                        .map(|arr| arr.iter().filter_map(|v| v.as_str()).map(String::from).collect())
                } else {
                    None
                }
            })
            .unwrap_or_default();

        let ticker_display = if tickers.is_empty() {
            "unknown".to_string()
        } else if tickers.len() == 1 {
            tickers[0].clone()
        } else {
            format!("{} tickers", tickers.len())
        };

        let interval = payload
            .get("timeFrame")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown");

        // Pre-shuffle client indices once
        let mut indices: Vec<usize> = (0..self.clients.len()).collect();
        use rand::seq::SliceRandom;
        indices.shuffle(&mut rand::thread_rng());

        let mut last_error: Option<String> = None;
        let user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        ];
        let user_agent = user_agents.choose(&mut rand::thread_rng()).unwrap_or(&"Mozilla/5.0");
        let body = serde_json::to_string(payload)?;

        for attempt_idx in 0..MAX_TOTAL_ATTEMPTS {
            let client_index = indices[attempt_idx % indices.len()];
            let client = &self.clients[client_index];

            // Label
            let label = if client_index == 0 && self.direct_connection {
                "direct".to_string()
            } else if client_index == 0 && !self.direct_connection {
                "proxy-1".to_string()
            } else {
                format!("proxy-{}", client_index)
            };

            // Per-client rate limit
            self.rate_limiters[client_index].acquire().await;

            // Build request with full browser headers
            let request = isahc::Request::builder()
                .uri(url)
                .method("POST")
                .header("Accept", "application/json, text/plain, */*")
                .header("Accept-Language", "en-US,en;q=0.9,vi-VN;q=0.8,vi;q=0.7")
                .header("Accept-Encoding", "gzip, deflate, br")
                .header("Connection", "keep-alive")
                .header("Content-Type", "application/json")
                .header("Cache-Control", "no-cache")
                .header("Pragma", "no-cache")
                .header("DNT", "1")
                .header("Sec-Fetch-Dest", "empty")
                .header("Sec-Fetch-Mode", "cors")
                .header("Sec-Fetch-Site", "same-site")
                .header("sec-ch-ua", "\"Not_A Brand\";v=\"8\", \"Chromium\";v=\"120\", \"Google Chrome\";v=\"120\"")
                .header("sec-ch-ua-mobile", "?0")
                .header("sec-ch-ua-platform", "\"Windows\"")
                .header("User-Agent", *user_agent)
                .header("Referer", "https://trading.vietcap.com.vn/")
                .header("Origin", "https://trading.vietcap.com.vn")
                .body(body.clone())
                .map_err(|e| VciError::InvalidResponse(format!("Request build error: {}", e)))?;

            match client.send_async(request).await {
                Ok(mut resp) => {
                    let status = resp.status();
                    if status.is_success() {
                        match resp.text().await {
                            Ok(text) => match serde_json::from_str::<Value>(&text) {
                                Ok(data) => {
                                    tracing::info!(
                                        ticker = %ticker_display,
                                        interval = %interval,
                                        via = %label,
                                        attempt = attempt_idx + 1,
                                        "✅ Request succeeded via {} (attempt {}/{})",
                                        label,
                                        attempt_idx + 1,
                                        MAX_TOTAL_ATTEMPTS,
                                    );
                                    return Ok(data);
                                }
                                Err(e) => {
                                    last_error = Some(format!("JSON parse error: {}", e));
                                    continue;
                                }
                            },
                            Err(e) => {
                                last_error = Some(format!("Response body error: {}", e));
                                continue;
                            }
                        }
                    } else {
                        let status_text = status.canonical_reason().unwrap_or("Unknown");
                        if status == 403 || status == 429 {
                            last_error = Some(format!("HTTP {} - rate limit or auth issue", status.as_u16()));
                            // Back off before trying next client to avoid hammering the API
                            sleep(Duration::from_secs(vci_worker::RATE_LIMIT_CLIENT_BACKOFF_SECS)).await;
                            continue;
                        } else if status.is_server_error() {
                            last_error = Some(format!("Server error ({}) - {}", status.as_u16(), status_text));
                            continue;
                        } else if status.is_client_error() {
                            return Err(VciError::InvalidResponse(format!(
                                "Client error ({}) - {} - not retryable",
                                status.as_u16(),
                                status_text,
                            )));
                        } else {
                            last_error = Some(format!("HTTP error ({}) - {}", status.as_u16(), status_text));
                            continue;
                        }
                    }
                }
                Err(e) => {
                    last_error = Some(format!("Network error: {}", e));
                    continue;
                }
            }
        }

        let error_msg = last_error.unwrap_or_else(|| "all clients failed".to_string());
        // Return RateLimit if all attempts failed with 429/403
        if error_msg.contains("429") {
            tracing::warn!("all clients rate limited, sleeping 60s");
            sleep(Duration::from_secs(60)).await;
            return Err(VciError::RateLimit);
        }
        Err(VciError::InvalidResponse(format!(
            "Max attempts exceeded ({}): {}",
            MAX_TOTAL_ATTEMPTS,
            error_msg,
        )))
    }

    // -----------------------------------------------------------------------
    // GET request for REST API endpoints (iq.vietcap.com.vn)
    // -----------------------------------------------------------------------

    async fn make_get_request(&self, url: &str, label_prefix: &str) -> Result<Value, VciError> {
        const MAX_TOTAL_ATTEMPTS: usize = 5;
        const REQUEST_TIMEOUT_SECS: u64 = 15;

        tracing::info!("[{}] → GET {}", label_prefix, &url[..url.len().min(120)]);

        let result = tokio::time::timeout(
            StdDuration::from_secs(REQUEST_TIMEOUT_SECS * MAX_TOTAL_ATTEMPTS as u64),
            self.make_get_request_inner(url, label_prefix),
        )
        .await;

        match result {
            Ok(r) => r,
            Err(_) => {
                tracing::error!("[{}] timed out after {}s", label_prefix, REQUEST_TIMEOUT_SECS * MAX_TOTAL_ATTEMPTS as u64);
                Err(VciError::InvalidResponse("request timed out".to_string()))
            }
        }
    }

    async fn make_get_request_inner(&self, url: &str, label_prefix: &str) -> Result<Value, VciError> {
        const MAX_TOTAL_ATTEMPTS: usize = 5;

        let mut indices: Vec<usize> = (0..self.clients.len()).collect();
        use rand::seq::SliceRandom;
        indices.shuffle(&mut rand::thread_rng());

        let user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        ];

        let mut last_error: Option<String> = None;
        let cookies = self.handshake_cookies.lock().await.clone();

        for attempt_idx in 0..MAX_TOTAL_ATTEMPTS {
            let client_index = indices[attempt_idx % indices.len()];
            let client = &self.clients[client_index];
            let user_agent = user_agents.choose(&mut rand::thread_rng()).unwrap_or(&"Mozilla/5.0");

            let label = if client_index == 0 && self.direct_connection {
                "direct".to_string()
            } else if client_index == 0 && !self.direct_connection {
                "proxy-1".to_string()
            } else {
                format!("proxy-{}", client_index)
            };

            self.rate_limiters[client_index].acquire().await;

            let mut builder = isahc::Request::builder()
                .uri(url)
                .method("GET")
                .header("Accept", "application/json, text/plain, */*")
                .header("Accept-Language", "en-US,en;q=0.9,vi-VN;q=0.8,vi;q=0.7")
                .header("Accept-Encoding", "gzip, deflate, br")
                .header("Connection", "keep-alive")
                .header("Cache-Control", "no-cache")
                .header("Pragma", "no-cache")
                .header("DNT", "1")
                .header("Sec-Fetch-Dest", "empty")
                .header("Sec-Fetch-Mode", "cors")
                .header("Sec-Fetch-Site", "same-site")
                .header("sec-ch-ua", "\"Not_A Brand\";v=\"8\", \"Chromium\";v=\"120\", \"Google Chrome\";v=\"120\"")
                .header("sec-ch-ua-mobile", "?0")
                .header("sec-ch-ua-platform", "\"Windows\"")
                .header("User-Agent", *user_agent)
                .header("Referer", "https://trading.vietcap.com.vn/")
                .header("Origin", "https://trading.vietcap.com.vn");

            if let Some(ref ck) = cookies {
                if !ck.is_empty() {
                    builder = builder.header("Cookie", ck.as_str());
                }
            }

            let request = builder
                .body(())
                .map_err(|e| VciError::InvalidResponse(format!("Request build error: {}", e)))?;

            match client.send_async(request).await {
                Ok(mut resp) => {
                    let status = resp.status();
                    if status.is_success() {
                        match resp.text().await {
                            Ok(text) => match serde_json::from_str::<Value>(&text) {
                                Ok(data) => {
                                    tracing::info!(
                                        via = %label,
                                        attempt = attempt_idx + 1,
                                        "✅ [{}] GET succeeded via {} (attempt {}/{})",
                                        label_prefix, label, attempt_idx + 1, MAX_TOTAL_ATTEMPTS,
                                    );
                                    return Ok(data);
                                }
                                Err(e) => {
                                    last_error = Some(format!("JSON parse error: {}", e));
                                    continue;
                                }
                            },
                            Err(e) => {
                                last_error = Some(format!("Response body error: {}", e));
                                continue;
                            }
                        }
                    } else {
                        let status_text = status.canonical_reason().unwrap_or("Unknown");
                        if status == 403 || status == 429 {
                            last_error = Some(format!("HTTP {} - rate limit or auth issue", status.as_u16()));
                            sleep(Duration::from_secs(vci_worker::RATE_LIMIT_CLIENT_BACKOFF_SECS)).await;
                            continue;
                        } else if status.is_server_error() {
                            last_error = Some(format!("Server error ({}) - {}", status.as_u16(), status_text));
                            continue;
                        } else if status.is_client_error() {
                            return Err(VciError::InvalidResponse(format!(
                                "Client error ({}) - {} - not retryable",
                                status.as_u16(), status_text,
                            )));
                        } else {
                            last_error = Some(format!("HTTP error ({}) - {}", status.as_u16(), status_text));
                            continue;
                        }
                    }
                }
                Err(e) => {
                    last_error = Some(format!("Network error: {}", e));
                    continue;
                }
            }
        }

        let error_msg = last_error.unwrap_or_else(|| "all clients failed".to_string());
        if error_msg.contains("429") {
            tracing::warn!("[{}] all clients rate limited, sleeping 60s", label_prefix);
            sleep(Duration::from_secs(60)).await;
            return Err(VciError::RateLimit);
        }
        Err(VciError::InvalidResponse(format!(
            "Max attempts exceeded ({}): {}",
            MAX_TOTAL_ATTEMPTS, error_msg,
        )))
    }

    // -----------------------------------------------------------------------
    // Handshake — fetch session cookies from priceboard (tries all clients)
    // -----------------------------------------------------------------------

    async fn ensure_handshake(&self) {
        let mut guard = self.handshake_cookies.lock().await;
        if guard.is_some() {
            return;
        }

        tracing::info!("[VCI-REST] handshake starting...");

        let url = "https://trading.vietcap.com.vn/priceboard";
        let user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ];

        let mut indices: Vec<usize> = (0..self.clients.len()).collect();
        use rand::seq::SliceRandom;
        indices.shuffle(&mut rand::thread_rng());

        for &client_index in &indices {
            let client = &self.clients[client_index];
            let user_agent = user_agents.choose(&mut rand::thread_rng()).unwrap_or(&"Mozilla/5.0");

            let label = if client_index == 0 && self.direct_connection {
                "direct".to_string()
            } else if client_index == 0 && !self.direct_connection {
                "proxy-1".to_string()
            } else {
                format!("proxy-{}", client_index)
            };

            self.rate_limiters[client_index].acquire().await;

            let request = isahc::Request::builder()
                .uri(url)
                .method("GET")
                .header("User-Agent", *user_agent)
                .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
                .header("Accept-Language", "en-US,en;q=0.9")
                .header("Connection", "keep-alive")
                .body(())
                .unwrap();

            match tokio::time::timeout(
                StdDuration::from_secs(10),
                client.send_async(request),
            )
            .await
            {
                Ok(Ok(resp)) => {
                    let cookie_header: Vec<String> = resp
                        .headers()
                        .get_all("set-cookie")
                        .iter()
                        .filter_map(|v| v.to_str().ok())
                        .map(|s| {
                            if let Some(idx) = s.find(';') {
                                s[..idx].to_string()
                            } else {
                                s.to_string()
                            }
                        })
                        .collect();

                    if !cookie_header.is_empty() {
                        let combined = cookie_header.join("; ");
                        tracing::info!("[VCI-REST] handshake got {} cookies via {}", cookie_header.len(), label);
                        *guard = Some(combined);
                        return;
                    } else {
                        tracing::debug!("[VCI-REST] handshake via {} returned no cookies, trying next client", label);
                    }
                }
                Ok(Err(e)) => {
                    tracing::warn!("[VCI-REST] handshake via {} failed: {e}", label);
                }
                Err(_) => {
                    tracing::warn!("[VCI-REST] handshake via {} timed out (10s)", label);
                }
            }
        }

        tracing::debug!("[VCI-REST] all handshake attempts returned no cookies (may still work)");
        *guard = Some(String::new());
    }

    fn get_interval_value(interval: &str) -> Result<String, VciError> {
        let interval_map: HashMap<&str, &str> = [
            ("1m", "ONE_MINUTE"),
            ("5m", "ONE_MINUTE"),
            ("15m", "ONE_MINUTE"),
            ("30m", "ONE_MINUTE"),
            ("1H", "ONE_HOUR"),
            ("1D", "ONE_DAY"),
            ("1W", "ONE_DAY"),
            ("1M", "ONE_DAY"),
        ]
        .into_iter()
        .collect();

        interval_map
            .get(interval)
            .map(|s| s.to_string())
            .ok_or_else(|| VciError::InvalidInterval(interval.to_string()))
    }

    fn calculate_timestamp(date_str: Option<&str>) -> i64 {
        match date_str {
            Some(date) => {
                let naive_date = NaiveDate::parse_from_str(date, "%Y-%m-%d")
                    .expect("Invalid date format");
                let naive_datetime = naive_date.and_hms_opt(23, 59, 59).unwrap();
                naive_datetime.and_utc().timestamp()
            }
            None => {
                let now = Utc::now();
                now.date_naive()
                    .and_hms_opt(23, 59, 59)
                    .unwrap()
                    .and_utc()
                    .timestamp()
            }
        }
    }

    // -----------------------------------------------------------------------
    // get_history — single-ticker OHLCV
    // -----------------------------------------------------------------------

    pub async fn get_history(
        &self,
        symbol: &str,
        interval: &str,
        count_back: u32,
        end_timestamp: Option<i64>,
    ) -> Result<Vec<OhlcvData>, VciError> {
        let interval_value = Self::get_interval_value(interval)?;
        let end_timestamp = end_timestamp.unwrap_or_else(|| Self::calculate_timestamp(None));

        let url = format!("{}chart/OHLCChart/gap-chart", self.base_url);
        let payload = serde_json::json!({
            "timeFrame": interval_value,
            "symbols": [symbol],
            "to": end_timestamp,
            "countBack": count_back,
        });

        let response_data = self.make_request(&url, &payload).await?;

        if !response_data.is_array() || response_data.as_array().unwrap().is_empty() {
            return Err(VciError::NoData);
        }

        let data_item = &response_data[0];
        let required_keys = ["o", "h", "l", "c", "v", "t"];
        for key in &required_keys {
            if data_item.get(key).is_none() {
                return Err(VciError::InvalidResponse(format!("Missing key: {}", key)));
            }
        }

        let opens = data_item["o"]
            .as_array()
            .ok_or(VciError::InvalidResponse("Invalid opens".to_string()))?;
        let highs = data_item["h"]
            .as_array()
            .ok_or(VciError::InvalidResponse("Invalid highs".to_string()))?;
        let lows = data_item["l"]
            .as_array()
            .ok_or(VciError::InvalidResponse("Invalid lows".to_string()))?;
        let closes = data_item["c"]
            .as_array()
            .ok_or(VciError::InvalidResponse("Invalid closes".to_string()))?;
        let volumes = data_item["v"]
            .as_array()
            .ok_or(VciError::InvalidResponse("Invalid volumes".to_string()))?;
        let times = data_item["t"]
            .as_array()
            .ok_or(VciError::InvalidResponse("Invalid times".to_string()))?;

        let length = times.len();
        if [opens.len(), highs.len(), lows.len(), closes.len(), volumes.len()]
            .iter()
            .any(|&len| len != length)
        {
            return Err(VciError::InvalidResponse(
                "Inconsistent array lengths".to_string(),
            ));
        }

        let mut result = Vec::with_capacity(length);
        for i in 0..length {
            let timestamp = if let Some(ts_str) = times[i].as_str() {
                ts_str.parse::<i64>().map_err(|_| {
                    VciError::InvalidResponse(format!(
                        "Cannot parse timestamp string '{}' at index {}",
                        ts_str, i
                    ))
                })?
            } else if let Some(ts_int) = times[i].as_i64() {
                ts_int
            } else {
                return Err(VciError::InvalidResponse(format!(
                    "Invalid timestamp format at index {}: {:?}",
                    i, &times[i]
                )));
            };

            let time = DateTime::<Utc>::from_timestamp(timestamp, 0).ok_or_else(|| {
                VciError::InvalidResponse(format!(
                    "Cannot convert timestamp {} to DateTime at index {}",
                    timestamp, i
                ))
            })?;

            result.push(OhlcvData {
                time,
                open: opens[i].as_f64().unwrap_or(0.0),
                high: highs[i].as_f64().unwrap_or(0.0),
                low: lows[i].as_f64().unwrap_or(0.0),
                close: closes[i].as_f64().unwrap_or(0.0),
                volume: volumes[i].as_u64().unwrap_or(0),
                symbol: Some(symbol.to_string()),
            });
        }

        result.sort_by(|a, b| a.time.cmp(&b.time));
        Ok(result)
    }

    // -----------------------------------------------------------------------
    // company_info — Company data via REST API (iq.vietcap.com.vn)
    // -----------------------------------------------------------------------

    pub async fn company_info(&self, symbol: &str) -> Result<CompanyInfo, VciError> {
        let symbol = symbol.to_uppercase();
        let vciq_base = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company";

        let details_url = format!("{}/details?ticker={}", vciq_base, symbol);
        let details_resp = self.make_get_request(&details_url, "company-details").await?;
        let details_data = details_resp.get("data").ok_or(VciError::NoData)?;

        tracing::info!(
            "[FUNDAMENTAL] [company-details] {} keys: {:?}",
            symbol,
            details_data.as_object().map(|o| o.keys().take(20).collect::<Vec<_>>()).unwrap_or_default()
        );

        let mut company_info = CompanyInfo {
            symbol: symbol.clone(),
            exchange: None,
            industry: None,
            company_type: None,
            established_year: None,
            employees: None,
            market_cap: None,
            current_price: None,
            outstanding_shares: None,
            company_profile: None,
            website: None,
            shareholders: Vec::new(),
            officers: Vec::new(),
        };

        if let Some(ticker) = details_data.get("ticker").and_then(|v| v.as_str()) {
            company_info.symbol = ticker.to_string();
        }
        if let Some(industry) = details_data.get("sectorVn").and_then(|v| v.as_str()) {
            company_info.industry = Some(industry.to_string());
        } else if let Some(industry) = details_data.get("icbName3").and_then(|v| v.as_str()) {
            company_info.industry = Some(industry.to_string());
        }
        if let Some(profile) = details_data.get("profile").and_then(|v| v.as_str()) {
            let cleaned = profile
                .replace("<br>", " ")
                .replace("<br/>", " ")
                .replace("<p>", " ")
                .replace("</p>", " ");
            company_info.company_profile = Some(cleaned);
        }
        if let Some(shares) = details_data.get("numberOfSharesMktCap").and_then(|v| v.as_f64()) {
            company_info.outstanding_shares = Some(shares as u64);
        } else if let Some(shares) = details_data.get("issueShare").and_then(|v| v.as_f64()) {
            company_info.outstanding_shares = Some(shares as u64);
        }
        if let Some(exchange) = details_data.get("exchange").and_then(|v| v.as_str()) {
            company_info.exchange = Some(exchange.to_string());
        }
        if let Some(price) = details_data.get("matchPrice").and_then(|v| v.as_f64()) {
            company_info.current_price = Some(price);
        }
        if let (Some(price), Some(shares)) =
            (company_info.current_price, company_info.outstanding_shares)
        {
            company_info.market_cap = Some(price * shares as f64);
        }

        let shareholder_url = format!("{}/{}/shareholder", vciq_base, symbol);
        match self.make_get_request(&shareholder_url, "company-shareholder").await {
            Ok(sh_resp) => {
                if let Some(arr) = sh_resp.get("data").and_then(|v| v.as_array()) {
                    for sh in arr {
                        let owner_type = sh.get("ownerType").and_then(|v| v.as_str()).unwrap_or("");
                        let name = sh
                            .get("ownerName")
                            .or_else(|| sh.get("ownerFullName"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        let pct = sh.get("percentage").and_then(|v| v.as_f64()).unwrap_or(0.0);
                        let position = sh.get("positionName").and_then(|v| v.as_str());

                        if owner_type == "INDIVIDUAL" && position.is_some() {
                            company_info.officers.push(OfficerInfo {
                                name: name.to_string(),
                                position: position.unwrap().to_string(),
                                percentage: if pct > 0.0 { Some(pct) } else { None },
                            });
                        } else if !name.is_empty() && pct > 0.0 {
                            company_info.shareholders.push(ShareholderInfo {
                                name: name.to_string(),
                                percentage: pct,
                            });
                        }
                    }
                }
            }
            Err(e) => {
                tracing::debug!("[VCI-REST] shareholder fetch failed for {}: {e}", symbol);
            }
        }

        tracing::info!(
            "[FUNDAMENTAL] [company-details] {} parsed: exchange={:?} industry={:?} shares={:?} price={:?} profile={} shareholders={} officers={}",
            symbol,
            company_info.exchange,
            company_info.industry,
            company_info.outstanding_shares,
            company_info.current_price,
            company_info.company_profile.as_ref().map(|p| if p.len() > 30 { &p[..30] } else { p }).unwrap_or("none"),
            company_info.shareholders.len(),
            company_info.officers.len(),
        );

        Ok(company_info)
    }

    // -----------------------------------------------------------------------
    // financial_ratios — Financial data via REST API (iq.vietcap.com.vn)
    // -----------------------------------------------------------------------

    pub async fn financial_ratios(
        &self,
        symbol: &str,
        _period: &str,
    ) -> Result<Vec<HashMap<String, Value>>, VciError> {
        self.ensure_handshake().await;

        let symbol = symbol.to_uppercase();
        let url = format!(
            "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{}/statistics-financial",
            symbol
        );

        let resp = self.make_get_request(&url, "financial-ratios").await?;
        let data = resp.get("data").ok_or(VciError::NoData)?;

        let items = if data.is_array() {
            data.as_array().unwrap()
        } else {
            tracing::info!("[FUNDAMENTAL] [financial-ratios] {} data is not array: {:?}", symbol, data);
            return Err(VciError::NoData);
        };

        if items.is_empty() {
            tracing::info!("[FUNDAMENTAL] [financial-ratios] {} returned 0 items", symbol);
            return Err(VciError::NoData);
        }

        tracing::info!(
            "[FUNDAMENTAL] [financial-ratios] {} returned {} items, first keys: {:?}",
            symbol,
            items.len(),
            items[0].as_object().map(|o| o.keys().take(15).collect::<Vec<_>>()).unwrap_or_default()
        );

        let mut result = Vec::with_capacity(items.len());
        for item in items {
            if let Some(obj) = item.as_object() {
                let mut map: HashMap<String, Value> = HashMap::new();

                map.insert("ticker".to_string(), Value::String(symbol.clone()));

                for (k, v) in obj {
                    match k.as_str() {
                        "__typename" => continue,
                        "year" => {
                            map.insert("yearReport".to_string(), v.clone());
                        }
                        "quarter" => {
                            map.insert("lengthReport".to_string(), v.clone());
                        }
                        _ => {
                            map.insert(k.clone(), v.clone());
                        }
                    }
                }

                result.push(map);
            }
        }

        Ok(result)
    }
}
