use chrono::{DateTime, Utc};
use std::sync::Arc;
use std::time::Duration as StdDuration;
use tokio::sync::Semaphore;
use tokio::time::{sleep, Duration};
use yahoo_finance_api::{Quote, YResponse, YSearchResult};

pub use super::ohlcv::OhlcvData;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum YahooError {
    Api(yahoo_finance_api::YahooError),
    InvalidInterval(String),
    InvalidResponse(String),
    NoData,
}

impl From<yahoo_finance_api::YahooError> for YahooError {
    fn from(error: yahoo_finance_api::YahooError) -> Self {
        YahooError::Api(error)
    }
}

impl std::fmt::Display for YahooError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            YahooError::Api(e) => write!(f, "Yahoo API error: {}", e),
            YahooError::InvalidInterval(s) => write!(f, "Invalid interval: {}", s),
            YahooError::InvalidResponse(s) => write!(f, "Invalid response: {}", s),
            YahooError::NoData => write!(f, "No data available"),
        }
    }
}

impl std::error::Error for YahooError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            YahooError::Api(e) => Some(e),
            _ => None,
        }
    }
}

// ---------------------------------------------------------------------------
// Per-client rate limiter (semaphore-based, Arc-sharable)
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct RateLimiter {
    semaphore: Arc<Semaphore>,
    refill_interval_ms: u64,
    refill_handle: Arc<std::sync::Mutex<Option<tokio::task::JoinHandle<()>>>>,
}

impl RateLimiter {
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
            refill_interval_ms,
            refill_handle: Arc::new(std::sync::Mutex::new(Some(handle))),
        }
    }

    pub async fn acquire(&self) {
        let permit = self.semaphore.acquire().await.expect("rate limiter closed");
        permit.forget();
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
    if let Some(at_pos) = proxy_url.find('@') {
        if let Some(scheme_end) = proxy_url.find("://") {
            let scheme = &proxy_url[..scheme_end + 3];
            let after_at = &proxy_url[at_pos + 1..];
            // Strip credentials from the host part too
            let host_part = if let Some(colon) = after_at.find(':') {
                &after_at[..colon]
            } else {
                after_at
            };
            let port_part = after_at.find(':').map_or(String::new(), |i| {
                // Extract port (up to any trailing slash or end)
                let port_str = &after_at[i..];
                let end = port_str.find('/').unwrap_or(port_str.len());
                format!(":{}", &port_str[..end])
            });
            format!("{}***@{}{}", scheme, host_part, port_part)
        } else {
            "***@***".to_string()
        }
    } else {
        proxy_url.to_string()
    }
}

// ---------------------------------------------------------------------------
// YahooProvider — multi-connector with random rotation + per-connector rate limit
// ---------------------------------------------------------------------------

pub struct YahooProvider {
    connectors: Vec<yahoo_finance_api::YahooConnector>,
    rate_limiters: Vec<RateLimiter>,
    direct_connection: bool,
}

impl YahooProvider {
    /// Build a new provider.
    ///
    /// - Always creates 1 direct `YahooConnector`.
    /// - Optionally adds proxy connectors from the `HTTP_PROXIES` env var (comma-separated).
    /// - Each connector gets its own `RateLimiter`.
    /// - `requests_per_minute` controls the per-connector rate limit.
    pub fn new(requests_per_minute: u32) -> Result<Self, YahooError> {
        Self::with_options(requests_per_minute, true, false)
    }

    pub fn with_options(requests_per_minute: u32, direct_connection: bool, skip_proxies: bool) -> Result<Self, YahooError> {
        let mut connectors = Vec::new();
        let mut rate_limiters = Vec::new();

        // 1. Direct connector
        if direct_connection {
            match yahoo_finance_api::YahooConnector::builder()
                .timeout(StdDuration::from_secs(30))
                .build()
            {
                Ok(connector) => {
                    rate_limiters.push(RateLimiter::new(requests_per_minute));
                    connectors.push(connector);
                    eprintln!("✓ Direct connection enabled");
                }
                Err(e) => {
                    eprintln!("❌ Failed to create direct connector: {}", e);
                }
            }
        } else {
            eprintln!("⚠️  Direct connection DISABLED (proxy-only mode)");
        }

        // 2. Proxy connectors from HTTP_PROXIES env var (using custom reqwest::Client for SOCKS5 support)
        if !skip_proxies {
            if let Ok(proxy_urls) = std::env::var("HTTP_PROXIES") {
                for proxy_url in proxy_urls.split(',') {
                    let proxy_url = proxy_url.trim();
                    if proxy_url.is_empty() {
                        continue;
                    }
                    match reqwest::Proxy::all(proxy_url) {
                        Ok(proxy) => {
                            match reqwest::Client::builder()
                                .proxy(proxy)
                                .timeout(StdDuration::from_secs(30))
                                .build()
                            {
                                Ok(client) => {
                                    match yahoo_finance_api::YahooConnectorBuilder::build_with_client(client) {
                                        Ok(connector) => {
                                            rate_limiters.push(RateLimiter::new(requests_per_minute));
                                            connectors.push(connector);
                                            eprintln!("✅ Added proxy (socks5): {}", sanitize_proxy_url(proxy_url));
                                        }
                                        Err(e) => {
                                            eprintln!("❌ Failed to create connector for proxy {}: {}", sanitize_proxy_url(proxy_url), e);
                                        }
                                    }
                                }
                                Err(e) => {
                                    eprintln!("❌ Failed to build reqwest client for proxy {}: {}", sanitize_proxy_url(proxy_url), e);
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

        // Summary
        if !direct_connection {
            eprintln!("📊 YahooProvider initialized with {} proxy-only connector(s)", connectors.len());
        } else {
            eprintln!(
                "📊 YahooProvider initialized with {} connector(s) (1 direct + {} proxies, {}/min each)",
                connectors.len(),
                connectors.len().saturating_sub(1),
                requests_per_minute,
            );
        }

        if connectors.is_empty() {
            return Err(YahooError::InvalidResponse(
                "No connectors available (direct disabled and no proxies configured)".to_string(),
            ));
        }

        Ok(Self {
            connectors,
            rate_limiters,
            direct_connection,
        })
    }

    pub fn client_count(&self) -> usize {
        self.connectors.len()
    }

    // -----------------------------------------------------------------------
    // Interval mapping
    // -----------------------------------------------------------------------

    fn map_interval(interval: &str) -> Result<&'static str, YahooError> {
        match interval {
            "1m" => Ok("1m"),
            "1h" => Ok("1h"),
            "1D" | "1d" => Ok("1d"),
            "1wk" | "1W" => Ok("1wk"),
            "1mo" | "1M" => Ok("1mo"),
            _ => Err(YahooError::InvalidInterval(interval.to_string())),
        }
    }

    // -----------------------------------------------------------------------
    // Quote -> OhlcvData conversion
    // -----------------------------------------------------------------------

    fn quote_to_ohlcv(quote: &Quote, symbol: &str) -> Option<OhlcvData> {
        let time = DateTime::<Utc>::from_timestamp(quote.timestamp, 0)?;
        Some(OhlcvData {
            time,
            open: quote.open,
            high: quote.high,
            low: quote.low,
            close: quote.close,
            volume: quote.volume,
            symbol: Some(symbol.to_string()),
        })
    }

    // -----------------------------------------------------------------------
    // get_history — OHLCV via get_quote_range with multi-connector rotation
    // -----------------------------------------------------------------------

    pub async fn get_history(
        &self,
        ticker: &str,
        interval: &str,
        range: &str,
    ) -> Result<Vec<OhlcvData>, YahooError> {
        let yahoo_interval = Self::map_interval(interval)?;
        let result = self
            .make_request(ticker, yahoo_interval, range)
            .await?;

        self.extract_quotes(result, ticker)
    }

    async fn make_request(
        &self,
        ticker: &str,
        interval: &str,
        range: &str,
    ) -> Result<YResponse, YahooError> {
        const MAX_TOTAL_ATTEMPTS: usize = 5;

        let mut indices: Vec<usize> = (0..self.connectors.len()).collect();
        use rand::seq::SliceRandom;
        indices.shuffle(&mut rand::thread_rng());

        let mut last_error: Option<YahooError> = None;

        for attempt_idx in 0..MAX_TOTAL_ATTEMPTS {
            let conn_index = indices[attempt_idx % indices.len()];
            let connector = &self.connectors[conn_index];

            let label = if conn_index == 0 && self.direct_connection {
                "direct".to_string()
            } else if conn_index == 0 && !self.direct_connection {
                "proxy-1".to_string()
            } else {
                format!("proxy-{}", conn_index)
            };

            // Per-connector rate limit
            self.rate_limiters[conn_index].acquire().await;

            match connector.get_quote_range(ticker, interval, range).await {
                Ok(response) => {
                    tracing::info!(
                        ticker = %ticker,
                        interval = %interval,
                        via = %label,
                        attempt = attempt_idx + 1,
                        "✅ Request succeeded via {} (attempt {}/{})",
                        label,
                        attempt_idx + 1,
                        MAX_TOTAL_ATTEMPTS,
                    );
                    return Ok(response);
                }
                Err(e) => {
                    let err_str = e.to_string();
                    last_error = Some(YahooError::Api(e));

                    if err_str.contains("Too many requests")
                        || err_str.contains("429")
                        || err_str.contains("Unauthorized")
                    {
                        tracing::warn!(
                            ticker = %ticker,
                            via = %label,
                            attempt = attempt_idx + 1,
                            error_kind = "rate_limit",
                            "Request failed via {label}: {err_str}",
                        );
                        sleep(Duration::from_secs(1)).await;
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
                        ticker = %ticker,
                        via = %label,
                        attempt = attempt_idx + 1,
                        error_kind = kind,
                        "Request failed via {label}: {err_str}",
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

    fn extract_quotes(
        &self,
        response: YResponse,
        ticker: &str,
    ) -> Result<Vec<OhlcvData>, YahooError> {
        let quotes = response.quotes().map_err(|e| {
            YahooError::InvalidResponse(format!("Failed to extract quotes: {}", e))
        })?;

        if quotes.is_empty() {
            return Err(YahooError::NoData);
        }

        let mut result: Vec<OhlcvData> = Vec::with_capacity(quotes.len());
        for quote in &quotes {
            if let Some(ohlcv) = Self::quote_to_ohlcv(quote, ticker) {
                result.push(ohlcv);
            }
        }

        result.sort_by(|a, b| a.time.cmp(&b.time));
        Ok(result)
    }

    // -----------------------------------------------------------------------
    // get_history_interval — OHLCV via get_quote_history_interval with time range
    // -----------------------------------------------------------------------

    pub async fn get_history_interval(
        &self,
        ticker: &str,
        interval: &str,
        start: chrono::DateTime<chrono::Utc>,
        end: chrono::DateTime<chrono::Utc>,
    ) -> Result<Vec<OhlcvData>, YahooError> {
        let yahoo_interval = Self::map_interval(interval)?;
        let result = self
            .make_request_interval(ticker, yahoo_interval, start, end)
            .await?;

        self.extract_quotes(result, ticker)
    }

    async fn make_request_interval(
        &self,
        ticker: &str,
        interval: &str,
        start: chrono::DateTime<chrono::Utc>,
        end: chrono::DateTime<chrono::Utc>,
    ) -> Result<YResponse, YahooError> {
        use yahoo_finance_api::time::{OffsetDateTime, UtcOffset};

        let start_odt = OffsetDateTime::from_unix_timestamp(start.timestamp())
            .unwrap_or(OffsetDateTime::UNIX_EPOCH)
            .to_offset(UtcOffset::UTC);
        let end_odt = OffsetDateTime::from_unix_timestamp(end.timestamp())
            .unwrap_or(OffsetDateTime::UNIX_EPOCH)
            .to_offset(UtcOffset::UTC);

        const MAX_TOTAL_ATTEMPTS: usize = 5;

        let mut indices: Vec<usize> = (0..self.connectors.len()).collect();
        use rand::seq::SliceRandom;
        indices.shuffle(&mut rand::thread_rng());

        let mut last_error: Option<YahooError> = None;

        for attempt_idx in 0..MAX_TOTAL_ATTEMPTS {
            let conn_index = indices[attempt_idx % indices.len()];
            let connector = &self.connectors[conn_index];

            let label = if conn_index == 0 && self.direct_connection {
                "direct".to_string()
            } else if conn_index == 0 && !self.direct_connection {
                "proxy-1".to_string()
            } else {
                format!("proxy-{}", conn_index)
            };

            self.rate_limiters[conn_index].acquire().await;

            match connector
                .get_quote_history_interval(ticker, start_odt, end_odt, interval)
                .await
            {
                Ok(response) => {
                    tracing::info!(
                        ticker = %ticker,
                        interval = %interval,
                        via = %label,
                        attempt = attempt_idx + 1,
                        start = %start.format("%Y-%m-%d"),
                        end = %end.format("%Y-%m-%d"),
                        "Request succeeded via {} (attempt {}/{})",
                        label,
                        attempt_idx + 1,
                        MAX_TOTAL_ATTEMPTS,
                    );
                    return Ok(response);
                }
                Err(e) => {
                    let err_str = e.to_string();
                    last_error = Some(YahooError::Api(e));

                    if err_str.contains("Too many requests")
                        || err_str.contains("429")
                        || err_str.contains("Unauthorized")
                    {
                        tracing::warn!(
                            ticker = %ticker,
                            via = %label,
                            attempt = attempt_idx + 1,
                            error_kind = "rate_limit",
                            "Request failed via {label}: {err_str}",
                        );
                        sleep(Duration::from_secs(1)).await;
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
                        ticker = %ticker,
                        via = %label,
                        attempt = attempt_idx + 1,
                        error_kind = kind,
                        "Request failed via {label}: {err_str}",
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

    // -----------------------------------------------------------------------
    // search_ticker — Search for tickers by name/symbol
    // -----------------------------------------------------------------------

    pub async fn search_ticker(&self, name: &str) -> Result<YSearchResult, YahooError> {
        const MAX_TOTAL_ATTEMPTS: usize = 5;

        let mut indices: Vec<usize> = (0..self.connectors.len()).collect();
        use rand::seq::SliceRandom;
        indices.shuffle(&mut rand::thread_rng());

        let mut last_error: Option<YahooError> = None;

        for attempt_idx in 0..MAX_TOTAL_ATTEMPTS {
            let conn_index = indices[attempt_idx % indices.len()];
            let connector = &self.connectors[conn_index];

            let label = if conn_index == 0 && self.direct_connection {
                "direct".to_string()
            } else if conn_index == 0 && !self.direct_connection {
                "proxy-1".to_string()
            } else {
                format!("proxy-{}", conn_index)
            };

            self.rate_limiters[conn_index].acquire().await;

            match connector.search_ticker(name).await {
                Ok(result) => {
                    tracing::info!(
                        query = %name,
                        via = %label,
                        attempt = attempt_idx + 1,
                        "✅ Search succeeded via {} — {} result(s)",
                        label,
                        result.count,
                    );
                    return Ok(result);
                }
                Err(e) => {
                    let err_str = e.to_string();
                    last_error = Some(YahooError::Api(e));

                    if err_str.contains("Too many requests")
                        || err_str.contains("429")
                        || err_str.contains("Unauthorized")
                    {
                        tracing::warn!(
                            query = %name,
                            via = %label,
                            attempt = attempt_idx + 1,
                            "Rate limit hit via {}, backing off 1s",
                            label,
                        );
                        sleep(Duration::from_secs(1)).await;
                        continue;
                    }

                    tracing::warn!(
                        query = %name,
                        via = %label,
                        attempt = attempt_idx + 1,
                        "Search failed via {}: {}",
                        label,
                        err_str,
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
}
