use isahc::{HttpClient, config::Configurable, prelude::*};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration as StdDuration;
use tokio::sync::Semaphore;
use tokio::time::sleep;
use chrono::{DateTime, NaiveDate, Utc};

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
    refill_interval_ms: u64,
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
                sem_clone.add_permits(1);
            }
        });

        Self {
            semaphore,
            refill_interval_ms,
            refill_handle: Arc::new(std::sync::Mutex::new(Some(handle))),
        }
    }

    /// Acquire a permit (blocks until one is available, then consumes it).
    pub async fn acquire(&self) {
        let permit = self.semaphore.acquire().await.expect("rate limiter closed");
        permit.forget();
    }

    pub fn requests_per_minute(&self) -> u32 {
        (60_000 / self.refill_interval_ms) as u32
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
        let user_agent = user_agents.choose(&mut rand::thread_rng()).unwrap();
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

        Err(VciError::InvalidResponse(format!(
            "Max attempts exceeded ({}): {}",
            MAX_TOTAL_ATTEMPTS,
            last_error.unwrap_or_else(|| "all clients failed".to_string()),
        )))
    }

    // -----------------------------------------------------------------------
    // Interval mapping
    // -----------------------------------------------------------------------

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
    // company_info — Company data via GraphQL
    // -----------------------------------------------------------------------

    pub async fn company_info(&self, symbol: &str) -> Result<CompanyInfo, VciError> {
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

        // CompanyListingInfo
        if let Some(listing) = data.get("CompanyListingInfo") {
            if let Some(profile) = listing.get("companyProfile").and_then(|v| v.as_str()) {
                company_info.company_profile = Some(profile.to_string());
            }
            if let Some(industry) = listing.get("icbName3").and_then(|v| v.as_str()) {
                company_info.industry = Some(industry.to_string());
            }
            if let Some(shares) = listing.get("issueShare").and_then(|v| v.as_u64()) {
                company_info.outstanding_shares = Some(shares);
            }
        }

        // TickerPriceInfo
        if let Some(ticker_info) = data.get("TickerPriceInfo") {
            if let Some(exchange) = ticker_info.get("exchange").and_then(|v| v.as_str()) {
                company_info.exchange = Some(exchange.to_string());
            }
            if let Some(price) = ticker_info.get("matchPrice").and_then(|v| v.as_f64()) {
                company_info.current_price = Some(price);
            }
            if let (Some(price), Some(shares)) =
                (company_info.current_price, company_info.outstanding_shares)
            {
                company_info.market_cap = Some(price * shares as f64);
            }
        }

        // Shareholders
        if let Some(arr) = data.get("OrganizationShareHolders").and_then(|v| v.as_array()) {
            for sh in arr {
                if let (Some(name), Some(pct)) = (
                    sh.get("ownerFullName").and_then(|v| v.as_str()),
                    sh.get("percentage").and_then(|v| v.as_f64()),
                ) {
                    company_info.shareholders.push(ShareholderInfo {
                        name: name.to_string(),
                        percentage: pct,
                    });
                }
            }
        }

        // Officers
        if let Some(arr) = data.get("OrganizationManagers").and_then(|v| v.as_array()) {
            for mgr in arr {
                if let (Some(name), Some(pos)) = (
                    mgr.get("fullName").and_then(|v| v.as_str()),
                    mgr.get("positionName").and_then(|v| v.as_str()),
                ) {
                    let pct = mgr.get("percentage").and_then(|v| v.as_f64());
                    company_info.officers.push(OfficerInfo {
                        name: name.to_string(),
                        position: pos.to_string(),
                        percentage: pct,
                    });
                }
            }
        }

        Ok(company_info)
    }

    // -----------------------------------------------------------------------
    // financial_ratios — Financial data via GraphQL
    // -----------------------------------------------------------------------

    pub async fn financial_ratios(
        &self,
        symbol: &str,
        period: &str,
    ) -> Result<Vec<HashMap<String, Value>>, VciError> {
        let url = self.base_url.replace("/api/", "/data-mt/") + "graphql";

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
        let ratios = financial_ratio
            .get("ratio")
            .and_then(|v| v.as_array())
            .ok_or(VciError::NoData)?;

        let mut result = Vec::with_capacity(ratios.len());
        for ratio in ratios {
            if let Some(obj) = ratio.as_object() {
                let map: HashMap<String, Value> = obj
                    .iter()
                    .filter_map(|(k, v)| {
                        if k == "__typename" {
                            None
                        } else {
                            Some((k.clone(), v.clone()))
                        }
                    })
                    .collect();
                result.push(map);
            }
        }

        Ok(result)
    }
}
