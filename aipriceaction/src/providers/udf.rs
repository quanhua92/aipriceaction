use chrono::{DateTime, Utc};
use isahc::{HttpClient, config::Configurable, prelude::*};
use rand::seq::SliceRandom;
use serde::Deserialize;
use std::time::Duration as StdDuration;

pub use super::ohlcv::OhlcvData;
pub use crate::providers::vci::RateLimiter;

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum UdfError {
    Http(isahc::Error),
    Serialization(serde_json::Error),
    InvalidInterval(String),
    InvalidResponse(String),
    RateLimit,
    NoData,
}

impl From<isahc::Error> for UdfError {
    fn from(error: isahc::Error) -> Self {
        UdfError::Http(error)
    }
}

impl From<serde_json::Error> for UdfError {
    fn from(error: serde_json::Error) -> Self {
        UdfError::Serialization(error)
    }
}

impl std::fmt::Display for UdfError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            UdfError::Http(e) => write!(f, "HTTP error: {}", e),
            UdfError::Serialization(e) => write!(f, "Serialization error: {}", e),
            UdfError::InvalidInterval(s) => write!(f, "Invalid interval: {}", s),
            UdfError::InvalidResponse(s) => write!(f, "Invalid response: {}", s),
            UdfError::RateLimit => write!(f, "Rate limit exceeded"),
            UdfError::NoData => write!(f, "No data available"),
        }
    }
}

impl std::error::Error for UdfError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            UdfError::Http(e) => Some(e),
            UdfError::Serialization(e) => Some(e),
            _ => None,
        }
    }
}

impl From<isahc::http::Error> for UdfError {
    fn from(e: isahc::http::Error) -> Self {
        UdfError::InvalidResponse(format!("HTTP request build error: {}", e))
    }
}

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

/// Deserialize `null` or missing as empty Vec
fn deserialize_null_vec<'de, D, T>(deserializer: D) -> Result<Vec<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: serde::Deserialize<'de>,
{
    Option::<Vec<T>>::deserialize(deserializer).map(|opt| opt.unwrap_or_default())
}

#[derive(Debug, Clone, Deserialize)]
pub struct UdfHistoryResponse {
    #[serde(deserialize_with = "deserialize_null_vec")]
    pub t: Vec<i64>,
    #[serde(deserialize_with = "deserialize_null_vec")]
    pub o: Vec<f64>,
    #[serde(deserialize_with = "deserialize_null_vec")]
    pub h: Vec<f64>,
    #[serde(deserialize_with = "deserialize_null_vec")]
    pub l: Vec<f64>,
    #[serde(deserialize_with = "deserialize_null_vec")]
    pub c: Vec<f64>,
    #[serde(deserialize_with = "deserialize_null_vec")]
    pub v: Vec<f64>,
    #[serde(default)]
    pub s: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
pub struct UdfConfigResponse {
    #[serde(default)]
    pub supports_search: Option<bool>,
    #[serde(default)]
    pub supports_group_request: Option<bool>,
    #[serde(default)]
    pub supports_marks: Option<bool>,
    #[serde(default)]
    pub supports_timescale_marks: Option<bool>,
    #[serde(default)]
    pub supports_time: Option<bool>,
    #[serde(default)]
    pub exchanges: Option<serde_json::Value>,
    #[serde(default)]
    pub symbols_types: Option<serde_json::Value>,
    #[serde(default)]
    pub supported_resolutions: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
pub struct UdfSymbolInfo {
    #[serde(default)]
    pub symbol: Option<String>,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub exchange: Option<String>,
    #[serde(default)]
    pub r#type: Option<String>,
    #[serde(default)]
    pub session: Option<String>,
    #[serde(default)]
    pub timezone: Option<String>,
    #[serde(default)]
    pub minmov: Option<f64>,
    #[serde(default)]
    pub pricescale: Option<f64>,
    #[serde(default)]
    pub has_intraday: Option<bool>,
    #[serde(default)]
    pub has_daily: Option<bool>,
    #[serde(default)]
    pub has_weekly_and_monthly: Option<bool>,
    #[serde(default)]
    pub supported_resolutions: Option<Vec<String>>,
}

#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
pub struct UdfSearchResult {
    #[serde(default)]
    pub symbol: Option<String>,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub exchange: Option<String>,
    #[serde(default)]
    pub r#type: Option<String>,
}

// ---------------------------------------------------------------------------
// All available sources
// ---------------------------------------------------------------------------

pub const ALL_SOURCES: &[&str] = &["vietstock", "vndirect", "dnse", "vps"];

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
// UdfClient — shared HTTP infrastructure for a single UDF source
// ---------------------------------------------------------------------------

pub struct UdfClient {
    clients: Vec<HttpClient>,
    rate_limiters: Vec<RateLimiter>,
    base_url: String,
    history_path: String,
    referer: String,
    direct_connection: bool,
    /// Multiply stock OHLCV prices by this to normalize to VCI raw convention.
    /// Index tickers are never scaled (checked separately via is_index_ticker).
    price_multiplier: f64,
}

impl UdfClient {
    pub fn new(
        requests_per_minute: u32,
        base_url: &str,
        history_path: &str,
        referer: &str,
        price_multiplier: f64,
    ) -> Result<Self, UdfError> {
        Self::with_options(requests_per_minute, true, base_url, history_path, referer, price_multiplier)
    }

    pub fn with_options(
        requests_per_minute: u32,
        direct_connection: bool,
        base_url: &str,
        history_path: &str,
        referer: &str,
        price_multiplier: f64,
    ) -> Result<Self, UdfError> {
        let mut clients = Vec::new();
        let mut rate_limiters = Vec::new();

        // 1. Direct client
        if direct_connection {
            let direct_client = HttpClient::builder()
                .timeout(StdDuration::from_secs(30))
                .build()?;
            rate_limiters.push(RateLimiter::new(requests_per_minute));
            clients.push(direct_client);
            eprintln!("✓ UDF direct connection enabled");
        } else {
            eprintln!("⚠️  UDF direct connection DISABLED (proxy-only mode)");
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
                                eprintln!("✅ Added UDF proxy: {}", sanitize_proxy_url(proxy_url));
                            }
                            Err(e) => {
                                eprintln!(
                                    "❌ Failed to create client for proxy {}: {}",
                                    sanitize_proxy_url(proxy_url),
                                    e
                                );
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!(
                            "❌ Invalid proxy URL {}: {}",
                            sanitize_proxy_url(proxy_url),
                            e
                        );
                    }
                }
            }
        }

        if clients.is_empty() {
            return Err(UdfError::InvalidResponse(
                "No HTTP clients available (direct disabled and no proxies configured)"
                    .to_string(),
            ));
        }

        Ok(Self {
            clients,
            rate_limiters,
            base_url: base_url.to_string(),
            history_path: history_path.to_string(),
            referer: referer.to_string(),
            direct_connection,
            price_multiplier,
        })
    }

    pub fn client_count(&self) -> usize {
        self.clients.len()
    }

    // -----------------------------------------------------------------------
    // Core GET request with multi-client rotation (max 5 total attempts)
    // -----------------------------------------------------------------------

    async fn make_request(&self, url: &str) -> Result<Option<String>, UdfError> {
        const MAX_TOTAL_ATTEMPTS: usize = 5;

        let mut indices: Vec<usize> = (0..self.clients.len()).collect();
        indices.shuffle(&mut rand::thread_rng());

        let user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        ];
        let user_agent = user_agents.choose(&mut rand::thread_rng()).unwrap_or(&user_agents[0]);

        let mut last_error: Option<String> = None;

        for attempt_idx in 0..MAX_TOTAL_ATTEMPTS {
            let client_index = indices[attempt_idx % indices.len()];
            let client = &self.clients[client_index];

            let label = if client_index == 0 && self.direct_connection {
                "direct".to_string()
            } else if client_index == 0 && !self.direct_connection {
                "proxy-1".to_string()
            } else {
                format!("proxy-{}", client_index)
            };

            // Per-client rate limit
            self.rate_limiters[client_index].acquire().await;

            let request = isahc::Request::builder()
                .uri(url)
                .method("GET")
                .header("Accept", "application/json, text/plain, */*")
                .header("Accept-Language", "en-US,en;q=0.9")
                .header("User-Agent", *user_agent)
                .header("Referer", &self.referer)
                .body(())
                .map_err(|e| UdfError::InvalidResponse(format!("Request build error: {}", e)))?;

            match client.send_async(request).await {
                Ok(mut resp) => {
                    let status = resp.status();
                    if status.is_success() {
                        match resp.text().await {
                            Ok(text) => {
                                tracing::info!(
                                    via = %label,
                                    attempt = attempt_idx + 1,
                                    "✅ UDF request succeeded via {} (attempt {}/{})",
                                    label,
                                    attempt_idx + 1,
                                    MAX_TOTAL_ATTEMPTS,
                                );
                                return Ok(Some(text));
                            }
                            Err(e) => {
                                last_error = Some(format!("Response body error: {}", e));
                                continue;
                            }
                        }
                    } else if status == 404 {
                        // Endpoint not supported — return Ok(None)
                        tracing::debug!("UDF endpoint returned 404: {}", url);
                        return Ok(None);
                    } else {
                        let status_text = status.canonical_reason().unwrap_or("Unknown");
                        if status == 403 || status == 429 {
                            last_error = Some(format!(
                                "HTTP {} — rate limit or auth issue",
                                status.as_u16()
                            ));
                            tokio::time::sleep(StdDuration::from_secs(2)).await;
                            continue;
                        } else if status.is_server_error() {
                            last_error = Some(format!(
                                "Server error ({}) — {}",
                                status.as_u16(),
                                status_text
                            ));
                            continue;
                        } else if status.is_client_error() {
                            // Other client errors (400, etc.) — return None for optional endpoints
                            tracing::debug!(
                                "UDF endpoint returned client error {}: {}",
                                status.as_u16(),
                                status_text
                            );
                            return Ok(None);
                        } else {
                            last_error = Some(format!(
                                "HTTP error ({}) — {}",
                                status.as_u16(),
                                status_text
                            ));
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
            return Err(UdfError::RateLimit);
        }
        Err(UdfError::InvalidResponse(format!(
            "Max attempts exceeded ({}): {}",
            MAX_TOTAL_ATTEMPTS, error_msg,
        )))
    }

    // -----------------------------------------------------------------------
    // History URL builder
    // -----------------------------------------------------------------------

    fn build_history_url(
        &self,
        symbol: &str,
        resolution: &str,
        count_back: u32,
        end_timestamp: i64,
        from_timestamp: i64,
    ) -> String {
        format!(
            "{}{}?symbol={}&resolution={}&from={}&to={}&countback={}",
            self.base_url, self.history_path, symbol, resolution, from_timestamp, end_timestamp, count_back
        )
    }

    // -----------------------------------------------------------------------
    // get_history — main entry point
    // -----------------------------------------------------------------------

    pub async fn get_history(
        &self,
        symbol: &str,
        interval: &str,
        count_back: u32,
        end_timestamp: Option<i64>,
    ) -> Result<Vec<OhlcvData>, UdfError> {
        let resolution = resolve_resolution(interval)?;
        let end_timestamp =
            end_timestamp.unwrap_or_else(|| Utc::now().timestamp());

        // Compute a reasonable `from` timestamp. UDF providers ignore `countback` and return
        // all data within the from-to range, so we must size the window to cover the requested
        // number of bars accounting for non-trading time (weekends, lunch break, off-hours).
        //
        // VN trading hours: 9:00-11:30, 13:00-15:00 ICT = 4.5h/day, ~5/7 days tradeable.
        // Effective trading fractions:
        //   daily: 5/7 calendar days ≈ 0.71 → multiplier 3x, with 2x safety = 6x
        //   intraday (1H, 1m, etc.): 4.5/24h * 5/7 days ≈ 0.13 → multiplier 12x, with 2x safety = 24x
        let interval_secs = match interval {
            "1m" => 60,
            "5m" => 300,
            "15m" => 900,
            "30m" => 1800,
            "1H" => 3600,
            "1D" => 86400,
            "1W" => 604800,
            "1M" => 2592000,
            _ => 86400,
        };
        let calendar_multiplier: i64 = match interval {
            "1D" | "1W" | "1M" => 6,
            _ => 24,
        };
        let computed_from = end_timestamp - (count_back as i64 * interval_secs * calendar_multiplier);
        let min_from = end_timestamp - (5 * 86400); // at least 5 days back
        let from_timestamp = computed_from.min(min_from);

        let url = self.build_history_url(symbol, &resolution, count_back, end_timestamp, from_timestamp);

        let text = self
            .make_request(&url)
            .await?
            .ok_or(UdfError::InvalidResponse(
                "History endpoint returned no data".to_string(),
            ))?;

        let response: UdfHistoryResponse = serde_json::from_str(&text)?;

        // Check status — some providers omit `s` entirely (DNSE), treat missing as ok
        if let Some(ref status) = response.s {
            if status != "ok" {
                return Err(UdfError::InvalidResponse(format!(
                    "UDF response status: {}",
                    status
                )));
            }
        }

        let length = response.t.len();
        if length == 0 {
            return Err(UdfError::NoData);
        }

        if [response.o.len(), response.h.len(), response.l.len(), response.c.len(), response.v.len()]
            .iter()
            .any(|&len| len != length)
        {
            return Err(UdfError::InvalidResponse(
                "Inconsistent array lengths".to_string(),
            ));
        }

        // Normalize prices to VCI raw convention.
        // Some UDF providers (VNDirect, DNSE, VPS) return face-value prices
        // (VCB: 59.7) while VCI/Vietstock return raw (VCB: 59700).
        // Index tickers (VNINDEX etc.) are never scaled.
        let is_index = crate::server::types::is_index_ticker(symbol);
        let price_multiplier = if is_index { 1.0 } else { self.price_multiplier };

        let mut result = Vec::with_capacity(length);
        for i in 0..length {
            let time = DateTime::<Utc>::from_timestamp(response.t[i], 0).ok_or_else(|| {
                UdfError::InvalidResponse(format!(
                    "Cannot convert timestamp {} to DateTime at index {}",
                    response.t[i], i
                ))
            })?;

            result.push(OhlcvData {
                time,
                open: response.o[i] * price_multiplier,
                high: response.h[i] * price_multiplier,
                low: response.l[i] * price_multiplier,
                close: response.c[i] * price_multiplier,
                volume: response.v[i] as u64,
                symbol: Some(symbol.to_string()),
            });
        }

        result.sort_by(|a, b| a.time.cmp(&b.time));
        Ok(result)
    }

    /// GET /config — server capabilities, supported resolutions
    pub async fn get_config(&self) -> Result<Option<UdfConfigResponse>, UdfError> {
        let url = format!("{}/config", self.base_url);
        let text = self.make_request(&url).await?;
        match text {
            Some(t) => {
                let config: UdfConfigResponse = serde_json::from_str(&t)?;
                Ok(Some(config))
            }
            None => Ok(None),
        }
    }

    /// GET /symbols?symbol={symbol} — ticker metadata
    pub async fn get_symbol_info(
        &self,
        symbol: &str,
    ) -> Result<Option<UdfSymbolInfo>, UdfError> {
        let url = format!(
            "{}/symbols?symbol={}",
            self.base_url,
            symbol
        );
        let text = self.make_request(&url).await?;
        match text {
            Some(t) => {
                let info: UdfSymbolInfo = serde_json::from_str(&t)?;
                Ok(Some(info))
            }
            None => Ok(None),
        }
    }

    /// GET /search?query={query}&limit={limit} — ticker discovery
    pub async fn search(
        &self,
        query: &str,
        limit: u32,
    ) -> Result<Option<Vec<UdfSearchResult>>, UdfError> {
        let url = format!(
            "{}/search?query={}&limit={}",
            self.base_url,
            query,
            limit
        );
        let text = self.make_request(&url).await?;
        match text {
            Some(t) => {
                let results: Vec<UdfSearchResult> = serde_json::from_str(&t)?;
                Ok(Some(results))
            }
            None => Ok(None),
        }
    }

    /// GET /time — server clock sync
    pub async fn get_server_time(&self) -> Result<Option<i64>, UdfError> {
        let url = format!("{}/time", self.base_url);
        let text = self.make_request(&url).await?;
        match text {
            Some(t) => {
                let time: i64 = t.trim().parse().map_err(|_| {
                    UdfError::InvalidResponse(format!("Cannot parse server time: {}", t))
                })?;
                Ok(Some(time))
            }
            None => Ok(None),
        }
    }
}

// ---------------------------------------------------------------------------
// UdfProvider enum — per-provider dispatch
// ---------------------------------------------------------------------------

pub enum UdfProvider {
    Vietstock(UdfClient),
    Vndirect(UdfClient),
    Dnse {
        stock_client: UdfClient,
        index_client: UdfClient,
    },
    Vps(UdfClient),
}

impl UdfProvider {
    /// Create a provider by source name (e.g. "vietstock", "vndirect", "dnse", "vps")
    pub fn new(source: &str, requests_per_minute: u32) -> Result<Self, UdfError> {
        match source {
            "vietstock" => {
                let client = UdfClient::new(
                    requests_per_minute,
                    "https://api.vietstock.vn/tvnew",
                    "/history",
                    "https://finance.vietstock.vn/",
                    1.0, // Vietstock returns raw prices (same as VCI)
                )?;
                Ok(UdfProvider::Vietstock(client))
            }
            "vndirect" => {
                let client = UdfClient::new(
                    requests_per_minute,
                    "https://dchart-api.vndirect.com.vn/dchart",
                    "/history",
                    "https://dchart.vndirect.com.vn/",
                    1000.0, // VNDirect returns face-value prices, multiply by 1000
                )?;
                Ok(UdfProvider::Vndirect(client))
            }
            "dnse" => {
                let stock_client = UdfClient::new(
                    requests_per_minute,
                    "https://api.dnse.com.vn/chart-api/v2",
                    "/ohlcs/stock",
                    "https://www.dnse.com.vn/",
                    1000.0, // DNSE returns face-value prices
                )?;
                let index_client = UdfClient::new(
                    requests_per_minute,
                    "https://api.dnse.com.vn/chart-api/v2",
                    "/ohlcs/index",
                    "https://www.dnse.com.vn/",
                    1.0, // Index prices are already correct
                )?;
                Ok(UdfProvider::Dnse {
                    stock_client,
                    index_client,
                })
            }
            "vps" => {
                let client = UdfClient::new(
                    requests_per_minute,
                    "https://histdatafeed.vps.com.vn/tradingview",
                    "/history",
                    "https://www.vps.com.vn/",
                    1000.0, // VPS returns face-value prices, multiply by 1000
                )?;
                Ok(UdfProvider::Vps(client))
            }
            _ => Err(UdfError::InvalidResponse(format!(
                "Unknown UDF source: {}. Available: {:?}",
                source, ALL_SOURCES
            ))),
        }
    }

    /// Return the source name string
    #[allow(dead_code)]
    pub fn source_name(&self) -> &'static str {
        match self {
            UdfProvider::Vietstock(_) => "vietstock",
            UdfProvider::Vndirect(_) => "vndirect",
            UdfProvider::Dnse { .. } => "dnse",
            UdfProvider::Vps(_) => "vps",
        }
    }

    fn client(&self) -> &UdfClient {
        match self {
            UdfProvider::Vietstock(c) => c,
            UdfProvider::Vndirect(c) => c,
            UdfProvider::Dnse { stock_client, .. } => stock_client,
            UdfProvider::Vps(c) => c,
        }
    }

    pub fn client_count(&self) -> usize {
        match self {
            UdfProvider::Dnse {
                stock_client,
                index_client,
            } => stock_client.client_count() + index_client.client_count(),
            _ => self.client().client_count(),
        }
    }

    // -----------------------------------------------------------------------
    // get_history — dispatches to inner UdfClient
    // For DNSE: tries stock endpoint first, falls back to index
    // -----------------------------------------------------------------------

    pub async fn get_history(
        &self,
        symbol: &str,
        interval: &str,
        count_back: u32,
        end_timestamp: Option<i64>,
    ) -> Result<Vec<OhlcvData>, UdfError> {
        match self {
            UdfProvider::Dnse {
                stock_client,
                index_client,
            } => {
                // Try stock endpoint first, fall back to index if it returns no data
                match stock_client
                    .get_history(symbol, interval, count_back, end_timestamp)
                    .await
                {
                    Ok(data) if !data.is_empty() => Ok(data),
                    Ok(_) => {
                        tracing::debug!("DNSE stock endpoint returned empty, trying index");
                        index_client
                            .get_history(symbol, interval, count_back, end_timestamp)
                            .await
                    }
                    Err(UdfError::NoData) | Err(UdfError::InvalidResponse(_)) => {
                        tracing::debug!("DNSE stock endpoint failed, trying index");
                        index_client
                            .get_history(symbol, interval, count_back, end_timestamp)
                            .await
                    }
                    Err(e) => Err(e),
                }
            }
            _ => {
                self.client()
                    .get_history(symbol, interval, count_back, end_timestamp)
                    .await
            }
        }
    }

    // -----------------------------------------------------------------------
    // Optional UDF protocol methods — delegate to inner client
    // -----------------------------------------------------------------------

    pub async fn get_config(&self) -> Result<Option<UdfConfigResponse>, UdfError> {
        self.client().get_config().await
    }

    pub async fn get_symbol_info(
        &self,
        symbol: &str,
    ) -> Result<Option<UdfSymbolInfo>, UdfError> {
        self.client().get_symbol_info(symbol).await
    }

    pub async fn search(
        &self,
        query: &str,
        limit: u32,
    ) -> Result<Option<Vec<UdfSearchResult>>, UdfError> {
        self.client().search(query, limit).await
    }

    pub async fn get_server_time(&self) -> Result<Option<i64>, UdfError> {
        self.client().get_server_time().await
    }
}

// ---------------------------------------------------------------------------
// Resolution mapping — internal interval → UDF resolution string
// ---------------------------------------------------------------------------

fn resolve_resolution(interval: &str) -> Result<String, UdfError> {
    match interval {
        "1m" => Ok("1".to_string()),
        "5m" => Ok("5".to_string()),
        "15m" => Ok("15".to_string()),
        "30m" => Ok("30".to_string()),
        "1H" => Ok("60".to_string()),
        "1D" => Ok("1D".to_string()),
        "1W" => Ok("1W".to_string()),
        "1M" => Ok("1M".to_string()),
        _ => Err(UdfError::InvalidInterval(interval.to_string())),
    }
}
