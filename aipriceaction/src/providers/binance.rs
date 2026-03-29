use chrono::{DateTime, Duration, Utc};
use isahc::{HttpClient, config::Configurable, prelude::*};
use rand::seq::SliceRandom;
use serde_json::Value;
use std::collections::HashMap;
use std::io::Cursor;
use std::time::Duration as StdDuration;
use zip::ZipArchive;

use super::ohlcv::OhlcvData;
pub use crate::providers::vci::RateLimiter;

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum BinanceError {
    Http(isahc::Error),
    HttpReq(reqwest::Error),
    Io(std::io::Error),
    Zip(zip::result::ZipError),
    InvalidInterval(String),
    InvalidResponse(String),
    RateLimit,
    NoData,
}

impl std::fmt::Display for BinanceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BinanceError::Http(e) => write!(f, "HTTP error: {e}"),
            BinanceError::HttpReq(e) => write!(f, "Reqwest error: {e}"),
            BinanceError::Io(e) => write!(f, "IO error: {e}"),
            BinanceError::Zip(e) => write!(f, "ZIP error: {e}"),
            BinanceError::InvalidInterval(s) => write!(f, "Invalid interval: {s}"),
            BinanceError::InvalidResponse(s) => write!(f, "Invalid response: {s}"),
            BinanceError::RateLimit => write!(f, "Rate limit exceeded"),
            BinanceError::NoData => write!(f, "No data available"),
        }
    }
}

impl std::error::Error for BinanceError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            BinanceError::Http(e) => Some(e),
            BinanceError::HttpReq(e) => Some(e),
            BinanceError::Io(e) => Some(e),
            BinanceError::Zip(e) => Some(e),
            _ => None,
        }
    }
}

impl From<isahc::Error> for BinanceError {
    fn from(e: isahc::Error) -> Self {
        BinanceError::Http(e)
    }
}

impl From<reqwest::Error> for BinanceError {
    fn from(e: reqwest::Error) -> Self {
        BinanceError::HttpReq(e)
    }
}

impl From<isahc::http::Error> for BinanceError {
    fn from(e: isahc::http::Error) -> Self {
        BinanceError::InvalidResponse(format!("HTTP request build error: {e}"))
    }
}

impl From<std::io::Error> for BinanceError {
    fn from(e: std::io::Error) -> Self {
        BinanceError::Io(e)
    }
}

impl From<zip::result::ZipError> for BinanceError {
    fn from(e: zip::result::ZipError) -> Self {
        BinanceError::Zip(e)
    }
}

// ---------------------------------------------------------------------------
// Historical scope constants
// ---------------------------------------------------------------------------

const HIST_MONTHLY_MONTHS: u32 = 12; // for 1d, 1h
const HIST_DAILY_DAYS: u32 = 3; // for 1m

// ---------------------------------------------------------------------------
// BinanceProvider
//   - reqwest (direct) for Binance Vision public ZIP downloads
//   - isahc (multi-client proxy rotation) for live Binance API
// ---------------------------------------------------------------------------

pub struct BinanceProvider {
    vision_client: reqwest::Client,
    api_clients: Vec<HttpClient>,
    api_rate_limiters: Vec<RateLimiter>,
    base_url_api: String,
    base_url_vision: String,
    direct_connection: bool,
}

impl BinanceProvider {
    pub fn new(requests_per_minute: u32) -> Result<Self, BinanceError> {
        Self::with_options(requests_per_minute, true)
    }

    pub fn with_options(
        requests_per_minute: u32,
        direct_connection: bool,
    ) -> Result<Self, BinanceError> {
        // 1. Vision client — single reqwest, direct only (public data, no auth needed)
        let vision_client = reqwest::Client::builder()
            .timeout(StdDuration::from_secs(60))
            .build()?;
        eprintln!("✓ Vision client (reqwest direct) ready");

        // 2. API clients — isahc with proxy rotation (same pattern as VciProvider)
        let mut api_clients = Vec::new();
        let mut api_rate_limiters = Vec::new();

        if direct_connection {
            let direct_client = HttpClient::builder()
                .timeout(StdDuration::from_secs(30))
                .build()?;
            api_rate_limiters.push(RateLimiter::new(requests_per_minute));
            api_clients.push(direct_client);
            eprintln!("✓ Direct connection enabled");
        } else {
            eprintln!("⚠️  Direct connection DISABLED (proxy-only mode)");
        }

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
                                api_rate_limiters.push(RateLimiter::new(requests_per_minute));
                                api_clients.push(client);
                                eprintln!("✅ Added proxy: {}", sanitize_proxy_url(proxy_url));
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
                        eprintln!("❌ Invalid proxy URL {}: {}", sanitize_proxy_url(proxy_url), e);
                    }
                }
            }
        }

        // Summary
        if !direct_connection {
            eprintln!(
                "📊 BinanceProvider initialized — Vision: 1 direct, API: {} proxy-only client(s), {}/min each",
                api_clients.len(), requests_per_minute,
            );
        } else {
            eprintln!(
                "📊 BinanceProvider initialized — Vision: 1 direct, API: {} client(s) (1 direct + {} proxies, {}/min each)",
                api_clients.len(),
                api_clients.len().saturating_sub(1),
                requests_per_minute,
            );
        }

        if api_clients.is_empty() {
            return Err(BinanceError::InvalidResponse(
                "No API clients available (direct disabled and no proxies configured)".to_string(),
            ));
        }

        Ok(Self {
            vision_client,
            api_clients,
            api_rate_limiters,
            base_url_api: "https://api.binance.com".to_string(),
            base_url_vision: "https://data.binance.vision".to_string(),
            direct_connection,
        })
    }

    pub fn client_count(&self) -> usize {
        1 + self.api_clients.len() // vision + api clients
    }

    // -----------------------------------------------------------------------
    // Vision request — single reqwest client, direct only
    // -----------------------------------------------------------------------

    async fn make_vision_request(&self, url: &str) -> Result<Vec<u8>, BinanceError> {
        let resp = self.vision_client.get(url).send().await?;
        let status = resp.status();

        if !status.is_success() {
            let status_text = status.canonical_reason().unwrap_or("Unknown");
            return Err(BinanceError::InvalidResponse(format!(
                "Vision HTTP {} — {}",
                status.as_u16(),
                status_text,
            )));
        }

        Ok(resp.bytes().await?.to_vec())
    }

    // -----------------------------------------------------------------------
    // API request — multi-client rotation with retry (isahc, same as VciProvider)
    // -----------------------------------------------------------------------

    async fn make_api_request(&self, url: &str) -> Result<String, BinanceError> {
        const MAX_TOTAL_ATTEMPTS: usize = 5;

        let mut indices: Vec<usize> = (0..self.api_clients.len()).collect();
        indices.shuffle(&mut rand::thread_rng());

        let mut last_error: Option<String> = None;

        for attempt_idx in 0..MAX_TOTAL_ATTEMPTS {
            let client_index = indices[attempt_idx % indices.len()];
            let client = &self.api_clients[client_index];

            let label = if client_index == 0 && self.direct_connection {
                "direct".to_string()
            } else if client_index == 0 && !self.direct_connection {
                "proxy-1".to_string()
            } else {
                format!("proxy-{client_index}")
            };

            // Per-client rate limit
            self.api_rate_limiters[client_index].acquire().await;

            let request = isahc::Request::builder()
                .uri(url)
                .method("GET")
                .header("Accept", "application/json")
                .header("User-Agent", "Mozilla/5.0 (compatible; aipriceaction/1.0)")
                .body(())?;

            match client.send_async(request).await {
                Ok(mut resp) => {
                    let status = resp.status();
                    if status.is_success() {
                        match resp.text().await {
                            Ok(text) => {
                                tracing::info!(
                                    via = %label,
                                    attempt = attempt_idx + 1,
                                    "✅ API request succeeded via {} (attempt {}/{})",
                                    label,
                                    attempt_idx + 1,
                                    MAX_TOTAL_ATTEMPTS,
                                );
                                return Ok(text);
                            }
                            Err(e) => {
                                last_error = Some(format!("Response body error: {e}"));
                                continue;
                            }
                        }
                    } else {
                        let status_text = status.canonical_reason().unwrap_or("Unknown");
                        if status == 403 || status == 429 || status == 451 {
                            last_error = Some(format!(
                                "HTTP {} — {}",
                                status.as_u16(),
                                status_text,
                            ));
                            continue;
                        } else if status.is_server_error() {
                            last_error = Some(format!(
                                "Server error ({}) — {}",
                                status.as_u16(),
                                status_text
                            ));
                            continue;
                        } else {
                            return Err(BinanceError::InvalidResponse(format!(
                                "Client error ({}) — {} — not retryable",
                                status.as_u16(),
                                status_text,
                            )));
                        }
                    }
                }
                Err(e) => {
                    last_error = Some(format!("Network error: {e}"));
                    continue;
                }
            }
        }

        Err(BinanceError::InvalidResponse(format!(
            "Max attempts exceeded ({}): {}",
            MAX_TOTAL_ATTEMPTS,
            last_error.unwrap_or_else(|| "all clients failed".to_string()),
        )))
    }

    // -----------------------------------------------------------------------
    // Interval mapping — Binance uses lowercase: 1d, 1h, 1m
    // -----------------------------------------------------------------------

    fn normalize_interval(interval: &str) -> Result<String, BinanceError> {
        match interval.to_lowercase().as_str() {
            "1d" | "1h" | "1m" => Ok(interval.to_lowercase()),
            _ => Err(BinanceError::InvalidInterval(interval.to_string())),
        }
    }

    // -----------------------------------------------------------------------
    // get_history — main entry point: historical + live merge
    // -----------------------------------------------------------------------

    pub async fn get_history(
        &self,
        symbol: &str,
        interval: &str,
        limit: u32,
    ) -> Result<Vec<OhlcvData>, BinanceError> {
        let interval = Self::normalize_interval(interval)?;

        // 1. Fetch historical data from Binance Vision (up to yesterday)
        let mut all_data = self.get_historical_vision(symbol, &interval).await?;

        tracing::info!(
            "Historical data: {} records for {} {}",
            all_data.len(),
            symbol,
            interval,
        );

        // 2. Fetch live data from Binance REST API (via proxy rotation)
        // Non-fatal: if live API fails (e.g. geo-blocked 451), still return Vision data
        let live_data = match self.get_live_klines(symbol, &interval, limit).await {
            Ok(data) => data,
            Err(e) => {
                tracing::warn!(
                    symbol,
                    "live klines failed, using Vision data only: {e}"
                );
                Vec::new()
            }
        };

        tracing::info!(
            "Live data: {} records for {} {}",
            live_data.len(),
            symbol,
            interval,
        );

        // 3. Merge: live overwrites historical by timestamp (dedup)
        if !live_data.is_empty() {
            let mut by_time: HashMap<i64, OhlcvData> = all_data
                .into_iter()
                .map(|d| (d.time.timestamp(), d))
                .collect();

            for row in live_data {
                by_time.insert(row.time.timestamp(), row);
            }

            let mut merged: Vec<OhlcvData> = by_time.into_values().collect();
            merged.sort_by(|a, b| a.time.cmp(&b.time));
            all_data = merged;
        }

        // 4. Apply limit (take the last N records)
        if all_data.len() > limit as usize {
            let start = all_data.len() - limit as usize;
            all_data = all_data[start..].to_vec();
        }

        Ok(all_data)
    }

    // -----------------------------------------------------------------------
    // Historical data from Binance Vision (public ZIP files, reqwest direct)
    // -----------------------------------------------------------------------

    /// Download a single monthly Vision ZIP file for any interval.
    /// URL: /data/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month}.zip
    /// Returns parsed data or empty Vec if 404 (month doesn't exist yet).
    pub async fn download_vision_month(
        &self,
        symbol: &str,
        interval: &str,
        year: &str,
        month: &str,
    ) -> Result<Vec<OhlcvData>, BinanceError> {
        let url = format!(
            "{}/data/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month}.zip",
            self.base_url_vision,
        );

        match self.fetch_and_parse_vision_zip(&url).await {
            Ok(data) => Ok(data),
            Err(e) => {
                if matches!(
                    e,
                    BinanceError::InvalidResponse(ref msg) if msg.contains("404")
                ) {
                    Ok(Vec::new())
                } else {
                    Err(e)
                }
            }
        }
    }

    /// Download a single daily Vision ZIP file for any interval.
    /// URL: /data/spot/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month}-{day}.zip
    /// Returns parsed data or empty Vec if 404 (day doesn't exist yet).
    pub async fn download_vision_day(
        &self,
        symbol: &str,
        interval: &str,
        year: &str,
        month: &str,
        day: &str,
    ) -> Result<Vec<OhlcvData>, BinanceError> {
        let url = format!(
            "{}/data/spot/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month}-{day}.zip",
            self.base_url_vision,
        );

        match self.fetch_and_parse_vision_zip(&url).await {
            Ok(data) => Ok(data),
            Err(e) => {
                if matches!(
                    e,
                    BinanceError::InvalidResponse(ref msg) if msg.contains("404")
                ) {
                    Ok(Vec::new())
                } else {
                    Err(e)
                }
            }
        }
    }

    async fn get_historical_vision(
        &self,
        symbol: &str,
        interval: &str,
    ) -> Result<Vec<OhlcvData>, BinanceError> {
        let mut all_data = Vec::new();

        match interval {
            "1d" | "1h" => {
                let now = Utc::now();
                let mut seen = std::collections::HashSet::new();
                for i in 0..HIST_MONTHLY_MONTHS {
                    let date = now - Duration::days((i as i64 + 1) * 30);
                    let year = date.format("%Y").to_string();
                    let month = date.format("%m").to_string();
                    let key = format!("{year}-{month}");
                    if !seen.insert(key.clone()) {
                        continue; // skip duplicates from 30-day stepping
                    }

                    let url = format!(
                        "{}/data/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month}.zip",
                        self.base_url_vision,
                    );

                    tracing::info!("Fetching historical: {url}");

                    match self.fetch_and_parse_vision_zip(&url).await {
                        Ok(data) => {
                            tracing::info!("  → {} records from {year}-{month}", data.len());
                            all_data.extend(data);
                        }
                        Err(e) => {
                            if matches!(
                                e,
                                BinanceError::InvalidResponse(ref msg) if msg.contains("404")
                            ) {
                                tracing::info!("  → No data for {year}-{month} (404)");
                            } else {
                                tracing::warn!("  → Error fetching {year}-{month}: {e}");
                            }
                        }
                    }
                }
            }
            "1m" => {
                let now = Utc::now();
                for i in (0..HIST_DAILY_DAYS).rev() {
                    let date = now - Duration::days(i as i64 + 1);
                    let year = date.format("%Y").to_string();
                    let month = date.format("%m").to_string();
                    let day = date.format("%d").to_string();

                    let url = format!(
                        "{}/data/spot/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month}-{day}.zip",
                        self.base_url_vision,
                    );

                    tracing::info!("Fetching historical: {url}");

                    match self.fetch_and_parse_vision_zip(&url).await {
                        Ok(data) => {
                            tracing::info!("  → {} records from {year}-{month}-{day}", data.len());
                            all_data.extend(data);
                        }
                        Err(e) => {
                            if matches!(
                                e,
                                BinanceError::InvalidResponse(ref msg) if msg.contains("404")
                            ) {
                                tracing::info!("  → No data for {year}-{month}-{day} (404)");
                            } else {
                                tracing::warn!("  → Error fetching {year}-{month}-{day}: {e}");
                            }
                        }
                    }
                }
            }
            _ => return Err(BinanceError::InvalidInterval(interval.to_string())),
        }

        all_data.sort_by(|a, b| a.time.cmp(&b.time));
        Ok(all_data)
    }

    /// Download a ZIP from Binance Vision, decompress in memory, parse CSV.
    async fn fetch_and_parse_vision_zip(
        &self,
        url: &str,
    ) -> Result<Vec<OhlcvData>, BinanceError> {
        let raw = self.make_vision_request(url).await?;

        let cursor = Cursor::new(raw.as_slice());
        let mut archive = ZipArchive::new(cursor)?;

        if archive.len() == 0 {
            return Ok(Vec::new());
        }

        let mut file = archive.by_index(0)?;
        let mut csv_text = String::new();
        std::io::Read::read_to_string(&mut file, &mut csv_text)?;

        parse_vision_csv(&csv_text)
    }

    // -----------------------------------------------------------------------
    // Live klines from Binance REST API (isahc, proxy rotation)
    // -----------------------------------------------------------------------

    async fn get_live_klines(
        &self,
        symbol: &str,
        interval: &str,
        limit: u32,
    ) -> Result<Vec<OhlcvData>, BinanceError> {
        let url = format!(
            "{}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}",
            self.base_url_api,
        );

        tracing::info!("Fetching live klines: {url}");

        let text = self.make_api_request(&url).await?;
        let value: Value = serde_json::from_str(&text)
            .map_err(|e| BinanceError::InvalidResponse(format!("JSON parse error: {e}")))?;

        let arr = value
            .as_array()
            .ok_or_else(|| BinanceError::InvalidResponse("Expected JSON array".to_string()))?;

        let mut data = Vec::with_capacity(arr.len());
        for row in arr {
            let open_time_ms = row
                .get(0)
                .and_then(|v| v.as_i64())
                .ok_or_else(|| BinanceError::InvalidResponse("Missing open_time".to_string()))?;

            let time = DateTime::<Utc>::from_timestamp_millis(open_time_ms)
                .ok_or_else(|| {
                    BinanceError::InvalidResponse(format!("Invalid open_time: {open_time_ms}"))
                })?;

            // Binance klines return OHLCV as strings, not numbers
            let parse_str_f64 = |idx: usize| -> f64 {
                row.get(idx)
                    .and_then(|v| v.as_str())
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(0.0)
            };
            // Volume comes as "11462.39847000" — parse as f64 then truncate
            let volume = parse_str_f64(5) as u64;

            data.push(OhlcvData {
                time,
                open: parse_str_f64(1),
                high: parse_str_f64(2),
                low: parse_str_f64(3),
                close: parse_str_f64(4),
                volume,
                symbol: Some(symbol.to_string()),
            });
        }

        Ok(data)
    }

    // -----------------------------------------------------------------------
    // Klines with startTime — forward pagination for bootstrap full download
    // -----------------------------------------------------------------------

    /// Fetch klines starting from `start_ts` (millisecond timestamp).
    /// Used by the bootstrap worker to page forward from 2010 to now.
    /// Returns data in chronological order.
    pub async fn get_klines_after(
        &self,
        symbol: &str,
        interval: &str,
        limit: u32,
        start_ts: i64,
    ) -> Result<Vec<OhlcvData>, BinanceError> {
        let url = format!(
            "{}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}&startTime={start_ts}",
            self.base_url_api,
        );

        let text = self.make_api_request(&url).await?;
        let value: Value = serde_json::from_str(&text)
            .map_err(|e| BinanceError::InvalidResponse(format!("JSON parse error: {e}")))?;

        let arr = value
            .as_array()
            .ok_or_else(|| BinanceError::InvalidResponse("Expected JSON array".to_string()))?;

        let mut data = Vec::with_capacity(arr.len());
        for row in arr {
            let open_time_ms = row
                .get(0)
                .and_then(|v| v.as_i64())
                .ok_or_else(|| BinanceError::InvalidResponse("Missing open_time".to_string()))?;

            let time = DateTime::<Utc>::from_timestamp_millis(open_time_ms)
                .ok_or_else(|| {
                    BinanceError::InvalidResponse(format!("Invalid open_time: {open_time_ms}"))
                })?;

            let parse_str_f64 = |idx: usize| -> f64 {
                row.get(idx)
                    .and_then(|v| v.as_str())
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(0.0)
            };
            let volume = parse_str_f64(5) as u64;

            data.push(OhlcvData {
                time,
                open: parse_str_f64(1),
                high: parse_str_f64(2),
                low: parse_str_f64(3),
                close: parse_str_f64(4),
                volume,
                symbol: Some(symbol.to_string()),
            });
        }

        Ok(data)
    }
}

// ---------------------------------------------------------------------------
// Parse Binance Vision CSV (12 columns, no header)
// ---------------------------------------------------------------------------

fn parse_vision_csv(csv_text: &str) -> Result<Vec<OhlcvData>, BinanceError> {
    let mut data = Vec::new();

    for line in csv_text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() < 6 {
            continue;
        }

        let open_time_us: i64 = cols[0]
            .parse()
            .map_err(|_| BinanceError::InvalidResponse(format!("Invalid open_time: {}", cols[0])))?;

        // Binance Vision CSV uses microsecond timestamps; convert to DateTime
        let time = DateTime::<Utc>::from_timestamp_micros(open_time_us)
            .ok_or_else(|| {
                BinanceError::InvalidResponse(format!("Invalid open_time us: {open_time_us}"))
            })?;

        data.push(OhlcvData {
            time,
            open: cols[1].parse().unwrap_or(0.0),
            high: cols[2].parse().unwrap_or(0.0),
            low: cols[3].parse().unwrap_or(0.0),
            close: cols[4].parse().unwrap_or(0.0),
            volume: cols[5].parse().unwrap_or(0.0) as u64,
            symbol: None,
        });
    }

    Ok(data)
}

// ---------------------------------------------------------------------------
// Sanitize proxy URL for logging
// ---------------------------------------------------------------------------

fn sanitize_proxy_url(proxy_url: &str) -> String {
    match proxy_url.parse::<isahc::http::Uri>() {
        Ok(uri) => {
            let scheme = uri.scheme_str().unwrap_or("unknown");
            let host = uri.host().unwrap_or("unknown");
            let port = uri.port_u16().map_or(String::new(), |p| format!(":{p}"));
            format!("{scheme}://{host}{port}")
        }
        Err(_) => {
            if let Some(at_pos) = proxy_url.find('@') {
                if let Some(scheme_end) = proxy_url.find("://") {
                    let scheme = &proxy_url[..scheme_end + 3];
                    let after_at = &proxy_url[at_pos + 1..];
                    format!("{scheme}:***@{after_at}")
                } else {
                    "***@***".to_string()
                }
            } else {
                proxy_url.to_string()
            }
        }
    }
}
