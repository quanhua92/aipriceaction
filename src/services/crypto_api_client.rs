use crate::error::Error;
use crate::models::Interval;
use crate::services::vci::OhlcvData;
use chrono::{DateTime, Utc};
use reqwest;
use serde_json::Value;
use std::collections::HashMap;
use tracing::{info, debug, warn, error};

/// Client for fetching crypto data from another aipriceaction API instance
/// Used when CryptoCompare API is blocked but we have access to another server
pub struct AiPriceActionClient {
    base_url: String,
    host_header: Option<String>,
    client: reqwest::Client,
}

impl AiPriceActionClient {
    /// Create a new API client
    ///
    /// # Arguments
    /// * `base_url` - Base URL of the aipriceaction API (e.g., "https://api.aipriceaction.com")
    /// * `host_header` - Optional Host header value for CDN/proxy bypass
    pub fn new(base_url: String, host_header: Option<String>) -> Result<Self, Error> {
        // Trim whitespace and remove trailing slashes from base_url
        let base_url = base_url.trim().trim_end_matches('/').to_string();

        debug!("After trimming, base_url: '{}'", base_url);

        // Validate URL format
        if !base_url.starts_with("http://") && !base_url.starts_with("https://") {
            return Err(Error::Config(format!(
                "Invalid base_url: must start with http:// or https://, got: '{}'",
                base_url
            )));
        }

        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(120))
            .build()
            .map_err(|e| Error::Network(format!("Failed to create HTTP client: {}", e)))?;

        info!(
            "Created AiPriceActionClient: base_url='{}', host_header={:?}",
            base_url, host_header
        );

        Ok(Self {
            base_url,
            host_header,
            client,
        })
    }

    /// Fetch all crypto data for a given interval and start date
    /// Makes a single API call: GET /tickers?mode=crypto&interval={interval}&start_date={start_date}
    ///
    /// # Arguments
    /// * `start_date` - Start date in YYYY-MM-DD format (e.g., "2024-11-01")
    /// * `interval` - Time interval (Daily, Hourly, Minute)
    ///
    /// # Returns
    /// Vector of OHLCV data for all cryptocurrencies
    pub async fn fetch_all_cryptos(
        &self,
        start_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        let interval_str = match interval {
            Interval::Daily => "1D",
            Interval::Hourly => "1H",
            Interval::Minute => "1m",
        };

        // Build URL: /tickers?mode=crypto&interval={interval}&start_date={start_date}
        let url = format!(
            "{}/tickers?mode=crypto&interval={}&start_date={}",
            self.base_url, interval_str, start_date
        );

        debug!(
            "Fetching crypto data from API: url={}, interval={}, start_date={}",
            url, interval_str, start_date
        );

        // Build request
        let mut request_builder = self.client.get(&url);

        // Add Host header if provided (for CDN/proxy bypass)
        // Note: reqwest handles Host header specially, so we use header() carefully
        if let Some(ref host) = self.host_header {
            debug!("Setting Host header: {}", host);
            // Use HeaderValue to ensure proper encoding
            use reqwest::header::{HeaderValue, HOST};
            match HeaderValue::from_str(host) {
                Ok(header_value) => {
                    request_builder = request_builder.header(HOST, header_value);
                    debug!("Host header set successfully");
                }
                Err(e) => {
                    warn!("Failed to set Host header '{}': {}. Proceeding without custom Host header.", host, e);
                }
            }
        }

        // Execute request
        debug!("Sending request to: {}", url);
        let response = request_builder
            .send()
            .await
            .map_err(|e| {
                let error_msg = format!("API request failed: {} (url: {}, host: {:?})", e, url, self.host_header);
                error!("{}", error_msg);
                Error::Network(error_msg)
            })?;

        // Check status
        if !response.status().is_success() {
            let status = response.status();
            let body = response
                .text()
                .await
                .unwrap_or_else(|_| "Unable to read response body".to_string());
            return Err(Error::Network(format!(
                "API returned error status {}: {}",
                status, body
            )));
        }

        // Parse JSON response
        let body = response
            .text()
            .await
            .map_err(|e| Error::Network(format!("Failed to read response body: {}", e)))?;

        let json: HashMap<String, Vec<Value>> = serde_json::from_str(&body)
            .map_err(|e| Error::Parse(format!("Failed to parse JSON response: {}", e)))?;

        // Convert to Vec<OhlcvData>
        let mut all_data = Vec::new();
        for (symbol, records) in json {
            for record in records {
                let ohlcv = self.parse_ohlcv_record(&symbol, &record, interval)?;
                all_data.push(ohlcv);
            }
        }

        info!(
            "Fetched {} records for interval {} from API",
            all_data.len(),
            interval_str
        );

        Ok(all_data)
    }

    /// Parse a single JSON record into OhlcvData
    fn parse_ohlcv_record(
        &self,
        symbol: &str,
        record: &Value,
        interval: Interval,
    ) -> Result<OhlcvData, Error> {
        // Extract time field
        let time_str = record["time"]
            .as_str()
            .ok_or_else(|| Error::Parse("Missing 'time' field".to_string()))?;

        // Parse time based on interval
        let time = match interval {
            Interval::Daily => {
                // Daily format: "2024-11-20"
                DateTime::parse_from_rfc3339(&format!("{}T00:00:00Z", time_str))
                    .map_err(|e| Error::Parse(format!("Failed to parse daily time: {}", e)))?
                    .with_timezone(&Utc)
            }
            Interval::Hourly | Interval::Minute => {
                // Hourly/Minute format: "2024-11-20 09:30:00"
                DateTime::parse_from_rfc3339(&format!("{}Z", time_str.replace(" ", "T")))
                    .map_err(|e| Error::Parse(format!("Failed to parse time: {}", e)))?
                    .with_timezone(&Utc)
            }
        };

        // Extract OHLCV fields
        let open = record["open"]
            .as_f64()
            .ok_or_else(|| Error::Parse("Missing or invalid 'open' field".to_string()))?;

        let high = record["high"]
            .as_f64()
            .ok_or_else(|| Error::Parse("Missing or invalid 'high' field".to_string()))?;

        let low = record["low"]
            .as_f64()
            .ok_or_else(|| Error::Parse("Missing or invalid 'low' field".to_string()))?;

        let close = record["close"]
            .as_f64()
            .ok_or_else(|| Error::Parse("Missing or invalid 'close' field".to_string()))?;

        let volume = record["volume"]
            .as_u64()
            .or_else(|| record["volume"].as_f64().map(|v| v as u64))
            .ok_or_else(|| Error::Parse("Missing or invalid 'volume' field".to_string()))?;

        Ok(OhlcvData {
            time,
            open,
            high,
            low,
            close,
            volume,
            symbol: Some(symbol.to_string()),
        })
    }

    /// Fetch full history for all cryptos
    /// Uses hardcoded BTC inception date (2010-07-17)
    pub async fn fetch_full_history(
        &self,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        const BTC_INCEPTION: &str = "2010-07-17";

        info!(
            "Fetching full history from API (start_date={})",
            BTC_INCEPTION
        );

        self.fetch_all_cryptos(BTC_INCEPTION, interval).await
    }

    /// Fetch recent data since a specific date (all cryptos)
    pub async fn fetch_recent(
        &self,
        last_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        info!(
            "Fetching recent data from API (start_date={})",
            last_date
        );

        self.fetch_all_cryptos(last_date, interval).await
    }

    /// Fetch data for a single crypto symbol (saves bandwidth)
    pub async fn fetch_single_crypto(
        &self,
        symbol: &str,
        start_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        let interval_str = match interval {
            Interval::Daily => "1D",
            Interval::Hourly => "1H",
            Interval::Minute => "1m",
        };

        // Build URL with symbol parameter
        let url = format!(
            "{}/tickers?mode=crypto&symbol={}&interval={}&start_date={}",
            self.base_url, symbol, interval_str, start_date
        );

        info!(
            "Fetching {} data from API proxy (start_date={})",
            symbol, start_date
        );

        // Build request
        let mut request_builder = self.client.get(&url);

        // Add Host header if provided
        if let Some(ref host) = self.host_header {
            use reqwest::header::{HeaderValue, HOST};
            if let Ok(header_value) = HeaderValue::from_str(host) {
                request_builder = request_builder.header(HOST, header_value);
            }
        }

        // Execute request
        let response = request_builder
            .send()
            .await
            .map_err(|e| Error::Network(format!("API request failed: {}", e)))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(Error::Network(format!("API error {}: {}", status, body)));
        }

        // Parse JSON response
        let body = response
            .text()
            .await
            .map_err(|e| Error::Network(format!("Failed to read response: {}", e)))?;

        let json: HashMap<String, Vec<Value>> = serde_json::from_str(&body)
            .map_err(|e| Error::Parse(format!("Failed to parse JSON: {}", e)))?;

        // Convert to Vec<OhlcvData>
        let mut all_data = Vec::new();
        for (sym, records) in json {
            for record in records {
                let ohlcv = self.parse_ohlcv_record(&sym, &record, interval)?;
                all_data.push(ohlcv);
            }
        }

        info!("Fetched {} records for {}", all_data.len(), symbol);
        Ok(all_data)
    }
}
