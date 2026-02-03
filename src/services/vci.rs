use isahc::{HttpClient, config::Configurable, prelude::*};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::error::Error as StdError;
use std::sync::Arc;
use std::time::{Duration as StdDuration, SystemTime};
use tokio::sync::Mutex as TokioMutex;
use tokio::time::sleep;
use chrono::{DateTime, Duration as ChronoDuration, NaiveDate, Utc, Weekday, TimeZone, Datelike};

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
pub struct OhlcvData {
    #[serde(serialize_with = "serialize_time_as_date")]
    pub time: DateTime<Utc>,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: u64,
    pub symbol: Option<String>,
}

fn serialize_time_as_date<S>(time: &DateTime<Utc>, serializer: S) -> Result<S::Ok, S::Error>
where
    S: serde::Serializer,
{
    let date_string = time.format("%Y-%m-%d").to_string();
    serializer.serialize_str(&date_string)
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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FinancialRatio {
    pub pe: Option<f64>,
    pub pb: Option<f64>,
    pub roe: Option<f64>,
    pub roa: Option<f64>,
    pub revenue: Option<f64>,
    pub net_profit: Option<f64>,
    pub dividend: Option<f64>,
    pub eps: Option<f64>,
}

/// Shared rate limiter for VCI API requests across all concurrent tasks
#[derive(Debug)]
pub struct SharedRateLimiter {
    /// Timestamps of recent requests (sliding window)
    request_timestamps: TokioMutex<Vec<SystemTime>>,
    /// Maximum requests allowed per minute
    rate_limit_per_minute: u32,
}

impl SharedRateLimiter {
    /// Create a new shared rate limiter
    pub fn new(rate_limit_per_minute: u32) -> Self {
        Self {
            request_timestamps: TokioMutex::new(Vec::new()),
            rate_limit_per_minute,
        }
    }

    /// Enforce rate limiting using sliding window algorithm
    /// This method is async-safe and can be called from multiple concurrent tasks
    pub async fn enforce_rate_limit(&self) {
        let current_time = SystemTime::now();
        let mut timestamps = self.request_timestamps.lock().await;

        // Remove timestamps older than 1 minute
        timestamps.retain(|&timestamp| {
            current_time.duration_since(timestamp)
                .unwrap_or(StdDuration::from_secs(0))
                < StdDuration::from_secs(60)
        });

        // If at rate limit, wait until oldest request expires
        if timestamps.len() >= self.rate_limit_per_minute as usize {
            if let Some(&oldest_request) = timestamps.first() {
                let wait_time = StdDuration::from_secs(60)
                    - current_time.duration_since(oldest_request)
                        .unwrap_or(StdDuration::from_secs(0));

                if !wait_time.is_zero() {
                    // Drop lock before sleeping to allow other tasks to check rate limit
                    drop(timestamps);
                    sleep(wait_time + StdDuration::from_millis(100)).await;
                    // Re-acquire lock to add timestamp
                    let mut timestamps = self.request_timestamps.lock().await;
                    timestamps.push(current_time);
                } else {
                    timestamps.push(current_time);
                }
            }
        } else {
            timestamps.push(current_time);
        }
    }
}

/// Sanitize proxy URL for logging (removes credentials)
fn sanitize_proxy_url(proxy_url: &str) -> String {
    match proxy_url.parse::<isahc::http::Uri>() {
        Ok(uri) => {
            let scheme = uri.scheme_str().unwrap_or("unknown");
            let host = uri.host().unwrap_or("unknown");
            let port = uri.port_u16().map_or(String::new(), |p| format!(":{}", p));
            format!("{}://{}{}", scheme, host, port)
        }
        Err(_) => {
            // If parsing fails, return a masked version
            if let Some(at_pos) = proxy_url.find('@') {
                // Has credentials, mask them
                if let Some(scheme_end) = proxy_url.find("://") {
                    let scheme = &proxy_url[..scheme_end + 3];
                    let after_at = &proxy_url[at_pos + 1..];
                    format!("{}:***@{}", scheme, after_at)
                } else {
                    "***@***".to_string()
                }
            } else {
                // No credentials, return as-is (already safe)
                proxy_url.to_string()
            }
        }
    }
}

#[derive(Clone)]
pub struct VciClient {
    clients: Vec<HttpClient>,
    base_url: String,
    rate_limit_per_minute: u32,
    request_timestamps: Vec<SystemTime>,  // Keep for backward compatibility
    user_agents: Vec<String>,
    random_agent: bool,
    resample_map: HashMap<String, String>,
    /// Optional shared rate limiter (if None, uses per-instance rate limiting)
    shared_rate_limiter: Option<Arc<SharedRateLimiter>>,
    /// Whether direct connection (no proxy) is enabled
    direct_connection: bool,
}

impl VciClient {
    pub fn new(random_agent: bool, rate_limit_per_minute: u32) -> Result<Self, VciError> {
        Self::with_options(random_agent, rate_limit_per_minute, None, true)
    }

    /// Create VCI client with options
    pub fn with_options(
        random_agent: bool,
        rate_limit_per_minute: u32,
        shared_rate_limiter: Option<Arc<SharedRateLimiter>>,
        direct_connection: bool,
    ) -> Result<Self, VciError> {
        Self::with_shared_rate_limiter_internal(random_agent, rate_limit_per_minute, shared_rate_limiter, direct_connection)
    }

    /// Async builder that tests proxies with async connectivity checks
    pub async fn new_async(
        random_agent: bool,
        rate_limit_per_minute: u32,
        shared_rate_limiter: Option<Arc<SharedRateLimiter>>,
    ) -> Result<Self, VciError> {
        Self::new_async_with_options(random_agent, rate_limit_per_minute, shared_rate_limiter, true).await
    }

    /// Async builder with options
    pub async fn new_async_with_options(
        random_agent: bool,
        rate_limit_per_minute: u32,
        shared_rate_limiter: Option<Arc<SharedRateLimiter>>,
        direct_connection: bool,
    ) -> Result<Self, VciError> {
        let mut clients = Vec::new();

        // 1. Add direct client if enabled
        if direct_connection {
            let direct_client = HttpClient::builder()
                .timeout(StdDuration::from_secs(30))
                .build()?;
            clients.push(direct_client);
            eprintln!("✓ Direct connection enabled");
        } else {
            eprintln!("⚠️  Direct connection DISABLED (proxy-only mode)");
        }

        // 2. Read proxies from HTTP_PROXIES env, split by comma
        if let Ok(proxy_urls) = std::env::var("HTTP_PROXIES") {
            for proxy_url in proxy_urls.split(',') {
                let proxy_url = proxy_url.trim();
                if !proxy_url.is_empty() {
                    // Test connectivity asynchronously
                    let is_connected = Self::test_proxy_url(proxy_url).await;

                    if is_connected {
                        match proxy_url.parse::<isahc::http::Uri>() {
                            Ok(proxy_uri) => {
                                match HttpClient::builder()
                                    .proxy(Some(proxy_uri))
                                    .timeout(StdDuration::from_secs(30))
                                    .build()
                                {
                                    Ok(client) => {
                                        clients.push(client);
                                        eprintln!("✅ Added proxy: {}", sanitize_proxy_url(proxy_url));
                                    }
                                    Err(e) => {
                                        eprintln!("❌ Failed to create client for proxy {}: {}", sanitize_proxy_url(proxy_url), e);
                                    }
                                }
                            }
                            Err(e) => {
                                eprintln!("❌ Failed to parse proxy URL {}: {}", sanitize_proxy_url(proxy_url), e);
                            }
                        }
                    } else {
                        eprintln!("❌ Skipped proxy: {} (connectivity test failed)", sanitize_proxy_url(proxy_url));
                    }
                }
            }
        }

        // Log initialization summary
        if !direct_connection {
            eprintln!("📊 VciClient initialized with {} proxy-only client(s)",
                      clients.len());
        } else {
            eprintln!("📊 VciClient initialized with {} client(s) (1 direct + {} proxies)",
                      clients.len(), clients.len().saturating_sub(1));
        }

        let user_agents = vec![
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36".to_string(),
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36".to_string(),
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0".to_string(),
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15".to_string(),
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0".to_string(),
        ];

        let mut resample_map = HashMap::new();
        resample_map.insert("1W".to_string(), "1W".to_string());
        resample_map.insert("1M".to_string(), "1M".to_string());

        Ok(VciClient {
            clients,
            base_url: "https://trading.vietcap.com.vn/api/".to_string(),
            rate_limit_per_minute,
            request_timestamps: Vec::new(),
            user_agents,
            random_agent,
            resample_map,
            shared_rate_limiter,
            direct_connection,
        })
    }

    /// Test a proxy URL by connecting to httpbin.org (async)
    async fn test_proxy_url(proxy_url: &str) -> bool {
        eprintln!("🔍 Testing proxy: {}", sanitize_proxy_url(proxy_url));

        // Parse the proxy URL to isahc::http::Uri
        let proxy_uri = match proxy_url.parse() {
            Ok(uri) => uri,
            Err(e) => {
                eprintln!("  ✗ Proxy URL parse failed: {}", e);
                return false;
            }
        };

        // Build client with proxy
        let client = match HttpClient::builder()
            .proxy(Some(proxy_uri))
            .timeout(StdDuration::from_secs(10))
            .build()
        {
            Ok(c) => {
                eprintln!("  ✓ Client built successfully");
                c
            }
            Err(e) => {
                eprintln!("  ✗ Client build failed: {}", e);
                return false;
            }
        };

        // Test connectivity (use httpbin.org as example.com may be blocked)
        eprintln!("  → Connecting to http://httpbin.org/ip ...");
        match client.get_async("http://httpbin.org/ip").await {
            Ok(resp) => {
                let status = resp.status();
                if status.is_success() {
                    eprintln!("  ✓ Connectivity test passed (status: {})", status);
                    true
                } else {
                    eprintln!("  ✗ Connectivity test failed (status: {})", status);
                    false
                }
            }
            Err(e) => {
                eprintln!("  ✗ Connectivity test failed: {}", e);
                // Print error chain for more details
                let mut source_opt = e.source();
                while let Some(err) = source_opt {
                    eprintln!("    caused by: {}", err);
                    source_opt = err.source();
                }
                false
            }
        }
    }

    /// Create VCI client with optional shared rate limiter
    pub fn with_shared_rate_limiter(
        random_agent: bool,
        rate_limit_per_minute: u32,
        shared_rate_limiter: Option<Arc<SharedRateLimiter>>,
    ) -> Result<Self, VciError> {
        Self::with_shared_rate_limiter_internal(random_agent, rate_limit_per_minute, shared_rate_limiter, true)
    }

    /// Internal builder with direct connection option
    fn with_shared_rate_limiter_internal(
        random_agent: bool,
        rate_limit_per_minute: u32,
        shared_rate_limiter: Option<Arc<SharedRateLimiter>>,
        direct_connection: bool,
    ) -> Result<Self, VciError> {
        let mut clients = Vec::new();

        // 1. Add direct client if enabled
        if direct_connection {
            let direct_client = HttpClient::builder()
                .timeout(StdDuration::from_secs(30))
                .build()?;
            clients.push(direct_client);
            eprintln!("✓ Direct connection enabled");
        } else {
            eprintln!("⚠️  Direct connection DISABLED (proxy-only mode)");
        }

        // 2. Read proxies from HTTP_PROXIES env, split by comma
        // Note: For sync constructor, we skip connectivity tests and just add all configured proxies
        if let Ok(proxy_urls) = std::env::var("HTTP_PROXIES") {
            for proxy_url in proxy_urls.split(',') {
                let proxy_url = proxy_url.trim();
                if !proxy_url.is_empty() {
                    match proxy_url.parse::<isahc::http::Uri>() {
                        Ok(proxy_uri) => {
                            match HttpClient::builder()
                                .proxy(Some(proxy_uri))
                                .timeout(StdDuration::from_secs(30))
                                .build()
                            {
                                Ok(client) => {
                                    clients.push(client);
                                    eprintln!("✅ Added proxy: {} (connectivity: skip - sync mode)", sanitize_proxy_url(proxy_url));
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
        }

        // Log initialization summary
        if !direct_connection {
            eprintln!("📊 VciClient initialized with {} proxy-only client(s)",
                      clients.len());
        } else {
            eprintln!("📊 VciClient initialized with {} client(s) (1 direct + {} proxies)",
                      clients.len(), clients.len().saturating_sub(1));
        }

        let user_agents = vec![
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36".to_string(),
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36".to_string(),
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0".to_string(),
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15".to_string(),
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0".to_string(),
        ];

        let mut resample_map = HashMap::new();
        resample_map.insert("1W".to_string(), "1W".to_string());
        resample_map.insert("1M".to_string(), "1M".to_string());

        Ok(VciClient {
            clients,
            base_url: "https://trading.vietcap.com.vn/api/".to_string(),
            rate_limit_per_minute,
            request_timestamps: Vec::new(),
            user_agents,
            random_agent,
            resample_map,
            shared_rate_limiter,
            direct_connection,
        })
    }

    fn get_interval_value(&self, interval: &str) -> Result<String, VciError> {
        let interval_map = HashMap::from([
            ("1m", "ONE_MINUTE"),
            ("5m", "ONE_MINUTE"),
            ("15m", "ONE_MINUTE"),
            ("30m", "ONE_MINUTE"),
            ("1H", "ONE_HOUR"),
            ("1D", "ONE_DAY"),
            ("1W", "ONE_DAY"),
            ("1M", "ONE_DAY"),
        ]);

        interval_map.get(interval)
            .map(|s| s.to_string())
            .ok_or_else(|| VciError::InvalidInterval(interval.to_string()))
    }

    fn get_user_agent(&self) -> String {
        if self.random_agent {
            use rand::seq::SliceRandom;
            self.user_agents.choose(&mut rand::thread_rng())
                .unwrap_or(&self.user_agents[0])
                .clone()
        } else {
            self.user_agents[0].clone()
        }
    }

    async fn enforce_rate_limit(&mut self) {
        // Use shared rate limiter if available, otherwise use per-instance
        if let Some(ref limiter) = self.shared_rate_limiter {
            limiter.enforce_rate_limit().await;
        } else {
            // Original per-instance implementation (backward compatible)
            let current_time = SystemTime::now();

            // Remove timestamps older than 1 minute
            self.request_timestamps.retain(|&timestamp| {
                current_time.duration_since(timestamp).unwrap_or(StdDuration::from_secs(0)) < StdDuration::from_secs(60)
            });

            // If we're at the rate limit, wait
            if self.request_timestamps.len() >= self.rate_limit_per_minute as usize {
                if let Some(&oldest_request) = self.request_timestamps.first() {
                    let wait_time = StdDuration::from_secs(60) - current_time.duration_since(oldest_request).unwrap_or(StdDuration::from_secs(0));
                    if !wait_time.is_zero() {
                        sleep(wait_time + StdDuration::from_millis(100)).await;
                    }
                }
            }

            self.request_timestamps.push(current_time);
        }
    }

    async fn make_request(&mut self, url: &str, payload: &Value) -> Result<Value, VciError> {
        const MAX_RETRIES: u32 = 5;

        // 1. Get randomized indices for load balancing
        let mut indices: Vec<usize> = (0..self.clients.len()).collect();
        use rand::seq::SliceRandom;
        indices.shuffle(&mut rand::thread_rng());

        // 2. Try each client until one succeeds
        let mut last_error: Option<String> = None;

        for (client_idx, &client_index) in indices.iter().enumerate() {
            let client = self.clients[client_index].clone();

            // Determine label: check if this is direct connection or proxy
            let label = if client_index == 0 && self.direct_connection {
                "direct".to_string()
            } else if client_index == 0 && !self.direct_connection {
                "proxy-1".to_string()  // Only client is a proxy (proxy-only mode)
            } else {
                format!("proxy-{}", client_index)
            };

            // 3. Retry logic for this client (existing logic)
            for attempt in 0..MAX_RETRIES {
                self.enforce_rate_limit().await;

                if attempt > 0 {
                    let delay = StdDuration::from_secs_f64(2.0_f64.powi(attempt as i32 - 1) + rand::random::<f64>());
                    let delay = delay.min(StdDuration::from_secs(60));
                    let reason = last_error.as_deref().unwrap_or("unknown error");
                    tracing::info!("VCI API retry backoff via {}: attempt {}/{} - reason: {}, waiting {:.1}s before retry",
                        label, attempt + 1, MAX_RETRIES, reason, delay.as_secs_f64());
                    sleep(delay).await;
                }

                let user_agent = self.get_user_agent();
                let body = serde_json::to_string(payload)?;

                // Log the exact request details
                tracing::debug!(
                    "VCI_MAKE_REQUEST: attempt={}, url={}, payload_size={}, headers_count=12, client={}",
                    attempt + 1,
                    url,
                    body.len(),
                    label
                );
                tracing::debug!(
                    "VCI_MAKE_REQUEST_DEBUG: url={}, payload={}",
                    url,
                    serde_json::to_string_pretty(payload).unwrap_or_default()
                );

                // Build request with all headers using isahc Request builder
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
                    .header("User-Agent", &user_agent)
                    .header("Referer", "https://trading.vietcap.com.vn/")
                    .header("Origin", "https://trading.vietcap.com.vn")
                    .body(body)
                    .map_err(|e| VciError::InvalidResponse(format!("Request build error: {}", e)))?;

                let response = client.send_async(request).await;

                match response {
                    Ok(mut resp) => {
                        let status = resp.status();

                        if status.is_success() {
                            match resp.text().await {
                                Ok(text) => {
                                    match serde_json::from_str::<Value>(&text) {
                                        Ok(data) => {
                                            eprintln!("✅ Request succeeded via {} (attempt {}/{})", label, client_idx + 1, indices.len());
                                            return Ok(data);
                                        }
                                        Err(e) => {
                                            last_error = Some(format!("JSON parse error: {}", e));
                                            continue;
                                        }
                                    }
                                }
                                Err(e) => {
                                    last_error = Some(format!("Response body error: {}", e));
                                    continue;
                                }
                            }
                        } else {
                            let status_text = status.canonical_reason().unwrap_or("Unknown");
                            if status == 403 {
                                last_error = Some(format!("Forbidden (403) - rate limit or auth issue"));
                                continue;
                            } else if status == 429 {
                                last_error = Some(format!("Too Many Requests (429) - rate limited"));
                                continue;
                            } else if status.is_server_error() {
                                last_error = Some(format!("Server error ({}) - {}", status.as_u16(), status_text));
                                continue;
                            } else if status.is_client_error() {
                                // Don't try other clients for client errors (4xx) - these are request problems
                                return Err(VciError::InvalidResponse(format!("Client error ({}) - {} - not retryable", status.as_u16(), status_text)));
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

            // Log that we're trying next client
            if client_idx < indices.len() - 1 {
                eprintln!("⚠️ Client {} failed, trying next client...", label);
            }
        }

        // All clients failed
        Err(VciError::InvalidResponse("Max retries exceeded - all clients failed".to_string()))
    }

    pub fn calculate_timestamp(&self, date_str: Option<&str>) -> i64 {
        match date_str {
            Some(date) => {
                let naive_date = NaiveDate::parse_from_str(date, "%Y-%m-%d")
                    .expect("Invalid date format");
                // Use the actual date as end timestamp (not future date)
                let naive_datetime = naive_date.and_hms_opt(23, 59, 59).unwrap();
                let datetime = naive_datetime.and_utc();
                datetime.timestamp()
            }
            None => {
                // For current timestamp, use end of current day
                let now = Utc::now();
                let end_of_day = now.date_naive().and_hms_opt(23, 59, 59).unwrap().and_utc();
                end_of_day.timestamp()
            },
        }
    }

    fn calculate_count_back(&self, start: &str, end: Option<&str>, interval: &str) -> u32 {
        let start_date = NaiveDate::parse_from_str(start, "%Y-%m-%d").expect("Invalid start date");
        let end_date = match end {
            Some(date) => NaiveDate::parse_from_str(date, "%Y-%m-%d").expect("Invalid end date"),
            None => Utc::now().date_naive(),
        };

        // Calculate actual business days (exclude weekends) - matching Python pd.bdate_range
        let mut business_days = 0u32;
        let mut current_date = start_date;
        while current_date <= end_date {
            // Skip weekends (Saturday = 6, Sunday = 0)
            let weekday = current_date.weekday().num_days_from_sunday();
            if weekday != 0 && weekday != 6 {  // Not Sunday(0) or Saturday(6)
                business_days += 1;
            }
            current_date += ChronoDuration::days(1);
        }
        
        // VCI API needs much larger buffer to reliably return historical data
        let count_back = match interval {
            "1D" => business_days + 100, // Large buffer for reliable historical data
            "1W" | "1M" => business_days + 100,
            "1H" => ((business_days as f32 * 6.5) as u32) + 100,
            _ => ((business_days as f32 * 6.5 * 60.0) as u32) + 100,
        };

        tracing::debug!("Count back calculation: start={}, end={:?}, business_days={} (Python-style), count_back={}", 
            start, end, business_days, count_back);

        count_back
    }

    pub async fn get_history(
        &mut self,
        symbol: &str,
        start: &str,
        end: Option<&str>,
        interval: &str,
    ) -> Result<Vec<OhlcvData>, VciError> {
        let interval_value = self.get_interval_value(interval)?;
        let end_timestamp = self.calculate_timestamp(end);
        let count_back = self.calculate_count_back(start, end, interval);

        let url = format!("{}chart/OHLCChart/gap-chart", self.base_url);
        let payload = serde_json::json!({
            "timeFrame": interval_value,
            "symbols": [symbol],
            "to": end_timestamp,
            "countBack": count_back
        });

        // Extensive input logging for debugging
        tracing::debug!(
            "VCI_GET_HISTORY_INPUT: symbol={}, start={}, end={:?}, interval={}, interval_value={}, end_timestamp={}, count_back={}, url={}",
            symbol, start, end, interval, interval_value, end_timestamp, count_back, url
        );
        tracing::debug!("VCI_GET_HISTORY_PAYLOAD: {}", serde_json::to_string_pretty(&payload).unwrap_or_default());


        let response_data = self.make_request(&url, &payload).await?;

        // Log raw VCI response for debugging
        tracing::debug!("VCI API raw response for {}: {}", symbol, serde_json::to_string(&response_data).unwrap_or_else(|_| "invalid json".to_string()));

        if !response_data.is_array() || response_data.as_array().unwrap().is_empty() {
            return Err(VciError::NoData);
        }

        let data_item = &response_data[0];
        tracing::debug!("VCI data for {}: {} records in response arrays", symbol, data_item.get("t").and_then(|t| t.as_array()).map(|a| a.len()).unwrap_or(0));
        
        
        let required_keys = ["o", "h", "l", "c", "v", "t"];
        
        for key in &required_keys {
            if !data_item.get(key).is_some() {
                return Err(VciError::InvalidResponse(format!("Missing key: {}", key)));
            }
        }

        let opens = data_item["o"].as_array().ok_or(VciError::InvalidResponse("Invalid opens".to_string()))?;
        let highs = data_item["h"].as_array().ok_or(VciError::InvalidResponse("Invalid highs".to_string()))?;
        let lows = data_item["l"].as_array().ok_or(VciError::InvalidResponse("Invalid lows".to_string()))?;
        let closes = data_item["c"].as_array().ok_or(VciError::InvalidResponse("Invalid closes".to_string()))?;
        let volumes = data_item["v"].as_array().ok_or(VciError::InvalidResponse("Invalid volumes".to_string()))?;
        let times = data_item["t"].as_array().ok_or(VciError::InvalidResponse("Invalid times".to_string()))?;

        let length = times.len();
        if [opens.len(), highs.len(), lows.len(), closes.len(), volumes.len()].iter().any(|&len| len != length) {
            return Err(VciError::InvalidResponse("Inconsistent array lengths".to_string()));
        }

        let mut result = Vec::new();
        let start_date = NaiveDate::parse_from_str(start, "%Y-%m-%d").expect("Invalid start date");
        
        for i in 0..length {
            // Try to get timestamp as string first, then as i64
            let timestamp = if let Some(ts_str) = times[i].as_str() {
                ts_str.parse::<i64>().map_err(|_| {
                    VciError::InvalidResponse(format!("Cannot parse timestamp string '{}' to i64 at index {}", ts_str, i))
                })?
            } else if let Some(ts_int) = times[i].as_i64() {
                ts_int
            } else {
                return Err(VciError::InvalidResponse(format!("Invalid timestamp format at index {}: {:?}", i, &times[i])));
            };
            
            let time = DateTime::<Utc>::from_timestamp(timestamp, 0).ok_or_else(|| {
                VciError::InvalidResponse(format!("Cannot convert timestamp {} to DateTime at index {}", timestamp, i))
            })?;

            if time.date_naive() >= start_date {
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
        }

        result.sort_by(|a, b| a.time.cmp(&b.time));
        
        // Apply resampling if needed
        if self.resample_map.contains_key(interval) && !["1m", "1H", "1D"].contains(&interval) {
            result = self.resample_ohlcv(result, interval)?;
        }
        
        Ok(result)
    }

    pub async fn get_batch_history(
        &mut self,
        symbols: &[String],
        start: &str,
        end: Option<&str>,
        interval: &str,
    ) -> Result<HashMap<String, Option<Vec<OhlcvData>>>, VciError> {
        if symbols.is_empty() {
            return Err(VciError::InvalidResponse("Symbols list cannot be empty".to_string()));
        }

        let interval_value = self.get_interval_value(interval)?;
        let end_timestamp = self.calculate_timestamp(end);
        let original_count_back = self.calculate_count_back(start, end, interval);
        // Double countBack to fix VCI batch history bug (per legacy Python code)
        let count_back = original_count_back * 2;

        let url = format!("{}chart/OHLCChart/gap-chart", self.base_url);
        let payload = serde_json::json!({
            "timeFrame": interval_value,
            "symbols": symbols,
            "to": end_timestamp,
            "countBack": count_back
        });

        // Extensive input logging for debugging
        tracing::debug!(
            "VCI_GET_BATCH_HISTORY_INPUT: symbols_count={}, symbols[5..]={:?}, start={}, end={:?}, interval={}, interval_value={}, end_timestamp={}, original_count_back={}, count_back={}, url={}",
            symbols.len(),
            &symbols[..symbols.len().min(5)],
            start, end, interval, interval_value, end_timestamp, original_count_back, count_back, url
        );
        tracing::debug!("VCI_GET_BATCH_HISTORY_PAYLOAD: {}", serde_json::to_string_pretty(&payload).unwrap_or_default());

        tracing::debug!("VCI API request: interval={}, start={}, end={:?}, original_count_back={}, count_back={} (2x), to_timestamp={}",
            interval, start, end, original_count_back, count_back, end_timestamp);

        tracing::debug!("VCI API payload: {}", serde_json::to_string_pretty(&payload).unwrap_or_else(|_| "failed to serialize".to_string()));

        let response_data = self.make_request(&url, &payload).await?;

        if !response_data.is_array() {
            return Err(VciError::NoData);
        }

        let response_array = response_data.as_array().unwrap();

        let mut results = HashMap::new();
        let start_date = NaiveDate::parse_from_str(start, "%Y-%m-%d").expect("Invalid start date");

        tracing::debug!("VCI filtering with start_date: {}, end_date: {:?}", start_date, end);

        // Create a mapping from response data using symbol field
        let mut response_map = HashMap::new();
        for (_i, data_item) in response_array.iter().enumerate() {
            if let Some(obj) = data_item.as_object() {
                // Find symbol identifier in response
                let symbol_fields = ["symbol", "ticker", "Symbol", "Ticker", "s"];
                let mut response_symbol = None;
                for field in &symbol_fields {
                    if let Some(val) = obj.get(*field).and_then(|v| v.as_str()) {
                        response_symbol = Some(val.to_uppercase());
                        break;
                    }
                }
                
                if let Some(sym) = response_symbol {
                    response_map.insert(sym.clone(), data_item.clone());
                }
            }
        }

        // Process each requested symbol using correct mapping
        for symbol in symbols {
            let symbol_upper = symbol.to_uppercase();
            
            if !response_map.contains_key(&symbol_upper) {
                results.insert(symbol.clone(), None);
                continue;
            }
            
            let data_item = response_map.get(&symbol_upper).unwrap();
            let required_keys = ["o", "h", "l", "c", "v", "t"];
            
            let mut valid = true;
            for key in &required_keys {
                if !data_item.get(key).is_some() {
                    valid = false;
                    break;
                }
            }

            if !valid {
                results.insert(symbol.clone(), None);
                continue;
            }

            let opens = data_item["o"].as_array().unwrap();
            let highs = data_item["h"].as_array().unwrap();
            let lows = data_item["l"].as_array().unwrap();
            let closes = data_item["c"].as_array().unwrap();
            let volumes = data_item["v"].as_array().unwrap();
            let times = data_item["t"].as_array().unwrap();

            let length = times.len();
            if [opens.len(), highs.len(), lows.len(), closes.len(), volumes.len()].iter().any(|&len| len != length) {
                results.insert(symbol.clone(), None);
                continue;
            }

            if length == 0 {
                results.insert(symbol.clone(), None);
                continue;
            }

            let mut symbol_data = Vec::new();
            let mut total_data_points = 0;
            let mut filtered_data_points = 0;
            
            // Debug: Show all timestamps in raw VCI response
            tracing::debug!("Symbol {}: Raw VCI timestamps from API:", symbol);
            for j in 0..length.min(10) { // Show first 10 timestamps
                let timestamp = if let Some(ts_str) = times[j].as_str() {
                    ts_str.parse::<i64>().unwrap_or(0)
                } else {
                    times[j].as_i64().unwrap_or(0)
                };
                let time = DateTime::<Utc>::from_timestamp(timestamp, 0).unwrap_or_default();
                tracing::debug!("  Raw timestamp[{}]: {} -> {}", j, timestamp, time.format("%Y-%m-%d %H:%M:%S"));
            }
            
            for j in 0..length {
                total_data_points += 1;
                
                // Try to get timestamp as string first, then as i64 (same fix as above)
                let timestamp = if let Some(ts_str) = times[j].as_str() {
                    ts_str.parse::<i64>().unwrap_or(0)
                } else {
                    times[j].as_i64().unwrap_or(0)
                };
                let time = DateTime::<Utc>::from_timestamp(timestamp, 0).unwrap_or_default();

                if time.date_naive() >= start_date {
                    filtered_data_points += 1;
                    symbol_data.push(OhlcvData {
                        time,
                        open: opens[j].as_f64().unwrap_or(0.0),
                        high: highs[j].as_f64().unwrap_or(0.0),
                        low: lows[j].as_f64().unwrap_or(0.0),
                        close: closes[j].as_f64().unwrap_or(0.0),
                        volume: volumes[j].as_u64().unwrap_or(0),
                        symbol: Some(symbol.clone()),
                    });
                }
            }

            tracing::debug!("Symbol {}: VCI returned {} data points, filtered to {} (start_date: {})",
                symbol, total_data_points, filtered_data_points, start_date);

            symbol_data.sort_by(|a, b| a.time.cmp(&b.time));
            
            // Apply resampling if needed
            if self.resample_map.contains_key(interval) && !["1m", "1H", "1D"].contains(&interval) {
                if let Ok(resampled) = self.resample_ohlcv(symbol_data.clone(), interval) {
                    symbol_data = resampled;
                }
            }
            
            results.insert(symbol.clone(), Some(symbol_data));
        }

        Ok(results)
    }

    pub async fn company_info(&mut self, symbol: &str) -> Result<CompanyInfo, VciError> {
        let url = self.base_url.replace("/api/", "/data-mt/") + "graphql";
        
        let graphql_query = r#"query Query($ticker: String!, $lang: String!) {
            AnalysisReportFiles(ticker: $ticker, langCode: $lang) {
                date
                description
                link
                name
                __typename
            }
            News(ticker: $ticker, langCode: $lang) {
                id
                organCode
                ticker
                newsTitle
                newsSubTitle
                friendlySubTitle
                newsImageUrl
                newsSourceLink
                createdAt
                publicDate
                updatedAt
                langCode
                newsId
                newsShortContent
                newsFullContent
                closePrice
                referencePrice
                floorPrice
                ceilingPrice
                percentPriceChange
                __typename
            }
            CompanyListingInfo(ticker: $ticker) {
                id
                issueShare
                history
                companyProfile
                icbName3
                icbName2
                icbName4
                financialRatio {
                    id
                    ticker
                    issueShare
                    charterCapital
                    __typename
                }
                __typename
            }
            TickerPriceInfo(ticker: $ticker) {
                ticker
                exchange
                matchPrice
                priceChange
                percentPriceChange
                totalVolume
                highestPrice1Year
                lowestPrice1Year
                financialRatio {
                    pe
                    pb
                    roe
                    roa
                    eps
                    revenue
                    netProfit
                    dividend
                    __typename
                }
                __typename
            }
            OrganizationShareHolders(ticker: $ticker) {
                id
                ticker
                ownerFullName
                percentage
                updateDate
                __typename
            }
            OrganizationManagers(ticker: $ticker) {
                id
                ticker
                fullName
                positionName
                percentage
                __typename
            }
        }"#;

        let payload = serde_json::json!({
            "query": graphql_query,
            "variables": {
                "ticker": symbol.to_uppercase(),
                "lang": "vi"
            }
        });


        let response_data = self.make_request(&url, &payload).await?;

        let data = response_data.get("data").ok_or(VciError::NoData)?;

        let mut company_info = CompanyInfo {
            symbol: symbol.to_uppercase(),
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

        // Extract from CompanyListingInfo
        if let Some(company_listing) = data.get("CompanyListingInfo") {
            if let Some(profile) = company_listing.get("companyProfile").and_then(|v| v.as_str()) {
                company_info.company_profile = Some(profile.to_string());
            }
            if let Some(industry) = company_listing.get("icbName3").and_then(|v| v.as_str()) {
                company_info.industry = Some(industry.to_string());
            }
            if let Some(shares) = company_listing.get("issueShare").and_then(|v| v.as_u64()) {
                company_info.outstanding_shares = Some(shares);
            }
        }

        // Extract from TickerPriceInfo
        if let Some(ticker_info) = data.get("TickerPriceInfo") {
            if let Some(exchange) = ticker_info.get("exchange").and_then(|v| v.as_str()) {
                company_info.exchange = Some(exchange.to_string());
            }
            if let Some(price) = ticker_info.get("matchPrice").and_then(|v| v.as_f64()) {
                company_info.current_price = Some(price);
            }

            // Calculate market cap
            if let (Some(price), Some(shares)) = (company_info.current_price, company_info.outstanding_shares) {
                company_info.market_cap = Some(price * shares as f64);
            }
        }

        // Extract shareholders
        if let Some(shareholders_array) = data.get("OrganizationShareHolders").and_then(|v| v.as_array()) {
            for shareholder in shareholders_array {
                if let (Some(name), Some(percentage)) = (
                    shareholder.get("ownerFullName").and_then(|v| v.as_str()),
                    shareholder.get("percentage").and_then(|v| v.as_f64())
                ) {
                    company_info.shareholders.push(ShareholderInfo {
                        name: name.to_string(),
                        percentage,
                    });
                }
            }
        }

        // Extract officers
        if let Some(managers_array) = data.get("OrganizationManagers").and_then(|v| v.as_array()) {
            for manager in managers_array {
                if let (Some(name), Some(position)) = (
                    manager.get("fullName").and_then(|v| v.as_str()),
                    manager.get("positionName").and_then(|v| v.as_str())
                ) {
                    let percentage = manager.get("percentage").and_then(|v| v.as_f64());
                    company_info.officers.push(OfficerInfo {
                        name: name.to_string(),
                        position: position.to_string(),
                        percentage,
                    });
                }
            }
        }

        Ok(company_info)
    }

    pub async fn financial_ratios(&mut self, symbol: &str, period: &str) -> Result<Vec<HashMap<String, Value>>, VciError> {
        let url = self.base_url.replace("/api/", "/data-mt/") + "graphql";

        // Map period: "quarter" -> "Q", "year" -> "Y"
        let vci_period = match period {
            "quarter" => "Q",
            "year" => "Y",
            _ => "Q",
        };

        let graphql_query = r#"fragment Ratios on CompanyFinancialRatio {
  ticker
  yearReport
  lengthReport
  updateDate
  revenue
  revenueGrowth
  netProfit
  netProfitGrowth
  ebitMargin
  roe
  roic
  roa
  pe
  pb
  eps
  currentRatio
  cashRatio
  quickRatio
  interestCoverage
  ae
  netProfitMargin
  grossMargin
  ev
  issueShare
  ps
  pcf
  bvps
  evPerEbitda
  BSA1
  BSA2
  BSA5
  BSA8
  BSA10
  BSA159
  BSA16
  BSA22
  BSA23
  BSA24
  BSA162
  BSA27
  BSA29
  BSA43
  BSA46
  BSA50
  BSA209
  BSA53
  BSA54
  BSA55
  BSA56
  BSA58
  BSA67
  BSA71
  BSA173
  BSA78
  BSA79
  BSA80
  BSA175
  BSA86
  BSA90
  BSA96
  CFA21
  CFA22
  at
  fat
  acp
  dso
  dpo
  ccc
  de
  le
  ebitda
  ebit
  dividend
  RTQ10
  charterCapitalRatio
  RTQ4
  epsTTM
  charterCapital
  __typename
}

query Query($ticker: String!, $period: String!) {
  CompanyFinancialRatio(ticker: $ticker, period: $period) {
    ratio {
      ...Ratios
      __typename
    }
    period
    __typename
  }
}"#;

        let payload = serde_json::json!({
            "query": graphql_query,
            "variables": {
                "ticker": symbol.to_uppercase(),
                "period": vci_period
            }
        });

        let response_data = self.make_request(&url, &payload).await?;

        let data = response_data.get("data").ok_or(VciError::NoData)?;
        let financial_ratio = data.get("CompanyFinancialRatio").ok_or(VciError::NoData)?;
        let ratios = financial_ratio.get("ratio").and_then(|v| v.as_array()).ok_or(VciError::NoData)?;

        // Convert each ratio object to HashMap
        let mut result = Vec::new();
        for ratio in ratios {
            if let Some(obj) = ratio.as_object() {
                let mut map = HashMap::new();
                for (key, value) in obj {
                    map.insert(key.clone(), value.clone());
                }
                result.push(map);
            }
        }

        Ok(result)
    }

    fn resample_ohlcv(&self, data: Vec<OhlcvData>, interval: &str) -> Result<Vec<OhlcvData>, VciError> {
        if data.is_empty() {
            return Ok(data);
        }
        
        match interval {
            "1W" => self.resample_weekly(data),
            "1M" => self.resample_monthly(data),
            _ => Ok(data), // No resampling needed
        }
    }
    
    fn resample_weekly(&self, data: Vec<OhlcvData>) -> Result<Vec<OhlcvData>, VciError> {
        let mut weekly_data = HashMap::new();
        
        for item in data {
            // Get the Monday of the week for this date
            let week_start = self.get_week_start(item.time);
            
            weekly_data.entry(week_start)
                .and_modify(|week_item: &mut OhlcvData| {
                    // Update OHLC values
                    week_item.high = week_item.high.max(item.high);
                    week_item.low = week_item.low.min(item.low);
                    week_item.close = item.close; // Last close
                    week_item.volume += item.volume;
                })
                .or_insert(OhlcvData {
                    time: week_start,
                    open: item.open,
                    high: item.high,
                    low: item.low,
                    close: item.close,
                    volume: item.volume,
                    symbol: item.symbol,
                });
        }
        
        let mut result: Vec<OhlcvData> = weekly_data.into_values().collect();
        result.sort_by(|a, b| a.time.cmp(&b.time));
        Ok(result)
    }
    
    fn resample_monthly(&self, data: Vec<OhlcvData>) -> Result<Vec<OhlcvData>, VciError> {
        let mut monthly_data = HashMap::new();
        
        for item in data {
            // Get the first day of the month
            let month_start = self.get_month_start(item.time);
            
            monthly_data.entry(month_start)
                .and_modify(|month_item: &mut OhlcvData| {
                    // Update OHLC values
                    month_item.high = month_item.high.max(item.high);
                    month_item.low = month_item.low.min(item.low);
                    month_item.close = item.close; // Last close
                    month_item.volume += item.volume;
                })
                .or_insert(OhlcvData {
                    time: month_start,
                    open: item.open,
                    high: item.high,
                    low: item.low,
                    close: item.close,
                    volume: item.volume,
                    symbol: item.symbol,
                });
        }
        
        let mut result: Vec<OhlcvData> = monthly_data.into_values().collect();
        result.sort_by(|a, b| a.time.cmp(&b.time));
        Ok(result)
    }
    
    fn get_week_start(&self, date: DateTime<Utc>) -> DateTime<Utc> {
        let days_since_monday = match date.weekday() {
            Weekday::Mon => 0,
            Weekday::Tue => 1,
            Weekday::Wed => 2,
            Weekday::Thu => 3,
            Weekday::Fri => 4,
            Weekday::Sat => 5,
            Weekday::Sun => 6,
        };
        
        let week_start_date = date.date_naive() - ChronoDuration::days(days_since_monday);
        week_start_date.and_hms_opt(0, 0, 0)
            .and_then(|dt| Utc.from_local_datetime(&dt).single())
            .unwrap_or(date)
    }
    
    fn get_month_start(&self, date: DateTime<Utc>) -> DateTime<Utc> {
        let year = date.year();
        let month = date.month();
        
        Utc.with_ymd_and_hms(year, month, 1, 0, 0, 0)
            .single()
            .unwrap_or(date)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_vci_client_creation() {
        let client = VciClient::new_async(true, 6, None).await;
        assert!(client.is_ok());
    }

    #[tokio::test]
    async fn test_interval_mapping() {
        let client = VciClient::new_async(false, 6, None).await.unwrap();
        assert_eq!(client.get_interval_value("1D").unwrap(), "ONE_DAY");
        assert_eq!(client.get_interval_value("1H").unwrap(), "ONE_HOUR");
        assert!(client.get_interval_value("invalid").is_err());
    }
}