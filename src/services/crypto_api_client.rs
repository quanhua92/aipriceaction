use crate::error::Error;
use crate::models::Interval;
use crate::services::vci::OhlcvData;
use crate::constants::{
    CRYPTO_API_TARGET_RECORDS_DAILY,
    CRYPTO_API_TARGET_RECORDS_HOURLY,
    CRYPTO_API_TARGET_RECORDS_MINUTE,
    CRYPTO_API_BATCH_DELAY_MS,
    CRYPTO_API_MAX_RETRIES,
    CRYPTO_API_PRECHECK_MAX_SYMBOLS,
};
use chrono::{DateTime, Utc};
use reqwest;
use serde_json::Value;
use std::collections::HashMap;
use std::time::Duration;
use tokio::time::sleep;
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

    /// Fetch crypto data for a given interval and start date
    /// Automatically uses batching for large datasets to prevent timeouts
    ///
    /// # Arguments
    /// * `symbols` - Optional list of specific crypto symbols to fetch (e.g., ["BTC", "ETH", "XRP"])
    /// * `start_date` - Optional start date in YYYY-MM-DD format (e.g., "2024-11-01")
    /// * `interval` - Time interval (Daily, Hourly, Minute)
    /// * `limit` - Optional limit on number of records to return
    ///
    /// # Returns
    /// Vector of OHLCV data for requested cryptocurrencies (or all if symbols=None)
    pub async fn fetch_all_cryptos(
        &self,
        symbols: Option<&[String]>,
        start_date: Option<&str>,
        interval: Interval,
        limit: Option<usize>,
    ) -> Result<Vec<OhlcvData>, Error> {
        let interval_str = match interval {
            Interval::Daily => "1D",
            Interval::Hourly => "1H",
            Interval::Minute => "1m",
        };

        // Calculate effective limit
        let effective_limit = limit.unwrap_or_else(|| {
            if start_date.is_none() {
                1  // Pre-check mode
            } else {
                match interval {
                    Interval::Daily => 100,
                    Interval::Hourly => 200,
                    Interval::Minute => 500,
                }
            }
        });

        // Determine if we need batching
        let should_batch = match symbols {
            Some(syms) => {
                // Calculate if total records would exceed our batching threshold
                let total_symbols = syms.len();
                let estimated_total_records = total_symbols * effective_limit;

                // Use batching thresholds to decide
                let target_records = match interval {
                    Interval::Daily => CRYPTO_API_TARGET_RECORDS_DAILY,
                    Interval::Hourly => CRYPTO_API_TARGET_RECORDS_HOURLY,
                    Interval::Minute => CRYPTO_API_TARGET_RECORDS_MINUTE,
                };

                // Special case: pre-check mode uses larger chunks
                if effective_limit == 1 {
                    total_symbols > CRYPTO_API_PRECHECK_MAX_SYMBOLS
                } else {
                    estimated_total_records > target_records
                }
            }
            None => {
                // No symbols provided - let the API handle fetching all symbols without batching
                // This maintains backward compatibility for the "fetch all" case
                false
            }
        };

        if should_batch {
            debug!(
                "📦 Using batching for {} symbols (limit={}, interval={})",
                symbols.as_ref().unwrap().len(), effective_limit, interval_str
            );
            // Use the new batched implementation
            return self.fetch_all_cryptos_batched(symbols, start_date, interval, limit).await;
        }

        // Original single-call implementation for smaller datasets
        debug!("📦 Using single call (no batching needed)");

        // Build URL: /tickers?mode=crypto&interval={interval}[&symbol={symbol}...]&start_date={start_date}&limit={limit}
        let mut url = format!(
            "{}/tickers?mode=crypto&interval={}",
            self.base_url, interval_str
        );

        // Add symbol parameters if provided
        if let Some(symbols) = symbols {
            for symbol in symbols {
                url.push_str(&format!("&symbol={}", symbol));
            }
        }

        // Add start_date parameter if provided
        if let Some(date) = start_date {
            url.push_str(&format!("&start_date={}", date));
        }

        url.push_str(&format!("&limit={}", effective_limit));

        debug!(
            "Fetching crypto data from API: url={}, interval={}, start_date={:?}, limit={:?} (effective_limit={})",
            url, interval_str, start_date, limit, effective_limit
        );

        // Log the exact URL being called
        info!("🌐 CRYPTO API CALL: {} (Host: {:?})", url, self.host_header);

        // Execute request using our shared method
        let all_data = self.execute_api_call(&url, interval).await?;

        info!(
            "Fetched {} records for interval {} from API (single call)",
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

        self.fetch_all_cryptos(None, Some(BTC_INCEPTION), interval, None).await
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

        self.fetch_all_cryptos(None, Some(last_date), interval, None).await
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
            .map_err(|e| Error::Network(format!("API request failed: {} (url: {})", e, url)))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(Error::Network(format!("API error {} (url: {}): {}", status, url, body)));
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

    // ========== BATCHING IMPLEMENTATION ==========

    /// Calculate the optimal number of symbols per batch based on limit and interval
    ///
    /// This implements the core batching algorithm: (limit × symbols) ÷ target_records_per_batch
    fn calculate_symbols_per_batch(&self, limit: usize, total_symbols: usize, interval: Interval) -> usize {
        // Special case: pre-check mode (limit=1) uses larger chunks
        if limit == 1 {
            let result = CRYPTO_API_PRECHECK_MAX_SYMBOLS.min(total_symbols);
            debug!("Pre-check mode: using {} symbols per batch", result);
            return result;
        }

        let target_records = match interval {
            Interval::Daily => CRYPTO_API_TARGET_RECORDS_DAILY,
            Interval::Hourly => CRYPTO_API_TARGET_RECORDS_HOURLY,
            Interval::Minute => CRYPTO_API_TARGET_RECORDS_MINUTE,
        };

        // Calculate symbols per batch: target_records / limit, but ensure at least 1 and at most total_symbols
        let symbols_per_batch = (target_records / limit).max(1).min(total_symbols);

        info!(
            "📊 Batch calculation: limit={}, target_records={}, total_symbols={}, symbols_per_batch={}",
            limit, target_records, total_symbols, symbols_per_batch
        );

        symbols_per_batch
    }

    /// Split symbols array into batches of specified size
    fn split_symbols_into_batches(&self, symbols: &[String], symbols_per_batch: usize) -> Vec<Vec<String>> {
        symbols
            .chunks(symbols_per_batch)
            .map(|chunk| chunk.to_vec())
            .collect()
    }

    /// Fetch data for a specific batch of symbols with retry logic
    async fn fetch_symbols_batch(
        &self,
        symbols: &[String],
        start_date: Option<&str>,
        interval: Interval,
        limit: usize,
    ) -> Result<Vec<OhlcvData>, Error> {
        let interval_str = match interval {
            Interval::Daily => "1D",
            Interval::Hourly => "1H",
            Interval::Minute => "1m",
        };

        // Build URL for this batch
        let mut url = format!(
            "{}/tickers?mode=crypto&interval={}",
            self.base_url, interval_str
        );

        // Add symbols for this batch
        for symbol in symbols {
            url.push_str(&format!("&symbol={}", symbol));
        }

        // Add start_date if provided
        if let Some(date) = start_date {
            url.push_str(&format!("&start_date={}", date));
        }

        // Add limit
        url.push_str(&format!("&limit={}", limit));

        // Retry logic with exponential backoff
        let mut last_error_str: Option<String> = None;
        for attempt in 1..=CRYPTO_API_MAX_RETRIES {
            match self.execute_api_call(&url, interval).await {
                Ok(data) => {
                    debug!("✅ Batch fetch succeeded on attempt {} ({} symbols)", attempt, symbols.len());
                    return Ok(data);
                }
                Err(e) => {
                    last_error_str = Some(e.to_string());
                    warn!(
                        "⚠️ Batch fetch attempt {} failed ({} symbols): {}",
                        attempt, symbols.len(), e
                    );

                    if attempt < CRYPTO_API_MAX_RETRIES {
                        let delay_ms = CRYPTO_API_BATCH_DELAY_MS * (2_u64.pow(attempt - 1));
                        debug!("Retrying in {}ms...", delay_ms);
                        sleep(Duration::from_millis(delay_ms)).await;
                    }
                }
            }
        }

        error!(
            "❌ Batch fetch failed after {} attempts ({} symbols): {}",
            CRYPTO_API_MAX_RETRIES, symbols.len(),
            last_error_str.as_ref().unwrap_or(&"Unknown error".to_string())
        );

        Err(Error::Network(
            last_error_str.unwrap_or_else(|| "Unknown batch fetch error".to_string())
        ))
    }

    /// Execute a single API call and parse response
    async fn execute_api_call(&self, url: &str, interval: Interval) -> Result<Vec<OhlcvData>, Error> {
        // Build request
        let mut request_builder = self.client.get(url);

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
            .map_err(|e| Error::Network(format!("API request failed: {} (url: {})", e, url)))?;

        // Check status
        if !response.status().is_success() {
            let status = response.status();
            let body = response
                .text()
                .await
                .unwrap_or_else(|_| "Unable to read response body".to_string());
            return Err(Error::Network(format!(
                "API returned error status {} (url: {}): {}",
                status, url, body
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

        Ok(all_data)
    }

    /// Fetch all cryptos with batching logic to prevent timeouts
    /// This is the main batching entry point that should be used instead of fetch_all_cryptos for large datasets
    pub async fn fetch_all_cryptos_batched(
        &self,
        symbols: Option<&[String]>,
        start_date: Option<&str>,
        interval: Interval,
        limit: Option<usize>,
    ) -> Result<Vec<OhlcvData>, Error> {
        let interval_str = match interval {
            Interval::Daily => "1D",
            Interval::Hourly => "1H",
            Interval::Minute => "1m",
        };

        // Calculate effective limit
        let effective_limit = limit.unwrap_or_else(|| {
            if start_date.is_none() {
                1  // Pre-check mode
            } else {
                match interval {
                    Interval::Daily => 100,
                    Interval::Hourly => 200,
                    Interval::Minute => 500,
                }
            }
        });

        // Get symbols to fetch
        let all_symbols = match symbols {
            Some(syms) => syms.to_vec(),
            None => {
                // If no symbols provided, we need to fetch all cryptos
                // For now, we'll return an error since we don't have a way to get all symbols
                return Err(Error::Config(
                    "Batched fetch requires explicit symbols list. Use fetch_all_cryptos() for all symbols."
                        .to_string(),
                ));
            }
        };

        // Calculate batch size
        let symbols_per_batch = self.calculate_symbols_per_batch(effective_limit, all_symbols.len(), interval);

        // Check if batching is needed
        if all_symbols.len() <= symbols_per_batch {
            info!(
                "📦 No batching needed: {} symbols <= {} threshold, using single call",
                all_symbols.len(), symbols_per_batch
            );
            // Execute single API call directly instead of calling fetch_all_cryptos to avoid recursion
            return self.execute_single_api_call(&all_symbols, start_date, interval, effective_limit).await;
        }

        // Split into batches
        let symbol_batches = self.split_symbols_into_batches(&all_symbols, symbols_per_batch);
        let total_batches = symbol_batches.len();

        info!(
            "📦 Crypto API batching: {} symbols into {} batches (limit={}, interval={})",
            all_symbols.len(), total_batches, effective_limit, interval_str
        );

        // Process batches sequentially
        let mut all_data = Vec::new();
        let mut successful_batches = 0;
        let start_time = std::time::Instant::now();

        for (batch_index, batch_symbols) in symbol_batches.iter().enumerate() {
            info!(
                "📦 Processing batch {}/{}: {} symbols",
                batch_index + 1, total_batches, batch_symbols.len()
            );

            match self.fetch_symbols_batch(batch_symbols, start_date, interval, effective_limit).await {
                Ok(mut batch_data) => {
                    let record_count = batch_data.len();
                    all_data.append(&mut batch_data);
                    successful_batches += 1;

                    info!(
                        "✅ Batch {}/{} completed: {} records",
                        batch_index + 1, total_batches, record_count
                    );
                }
                Err(e) => {
                    error!(
                        "❌ Batch {}/{} failed ({} symbols): {}",
                        batch_index + 1, total_batches, batch_symbols.len(), e
                    );
                    // Continue with other batches instead of failing completely
                }
            }

            // Add delay between batches (except after the last batch)
            if batch_index < total_batches - 1 {
                debug!("Sleeping for {}ms between batches...", CRYPTO_API_BATCH_DELAY_MS);
                sleep(Duration::from_millis(CRYPTO_API_BATCH_DELAY_MS)).await;
            }
        }

        let total_duration = start_time.elapsed();

        if successful_batches == 0 {
            return Err(Error::Network("All batches failed".to_string()));
        }

        info!(
            "✅ Crypto API batching complete: {} total records from {}/{} batches in {:.2}s",
            all_data.len(), successful_batches, total_batches, total_duration.as_secs_f64()
        );

        if successful_batches < total_batches {
            warn!(
                "⚠️ Partial success: {}/{} batches completed successfully",
                successful_batches, total_batches
            );
        }

        Ok(all_data)
    }

    /// Execute a single API call for the given symbols (used to avoid recursion)
    async fn execute_single_api_call(
        &self,
        symbols: &[String],
        start_date: Option<&str>,
        interval: Interval,
        limit: usize,
    ) -> Result<Vec<OhlcvData>, Error> {
        let interval_str = match interval {
            Interval::Daily => "1D",
            Interval::Hourly => "1H",
            Interval::Minute => "1m",
        };

        // Build URL: /tickers?mode=crypto&interval={interval}[&symbol={symbol}...]&start_date={start_date}&limit={limit}
        let mut url = format!(
            "{}/tickers?mode=crypto&interval={}",
            self.base_url, interval_str
        );

        // Add symbol parameters
        for symbol in symbols {
            url.push_str(&format!("&symbol={}", symbol));
        }

        // Add start_date parameter if provided
        if let Some(date) = start_date {
            url.push_str(&format!("&start_date={}", date));
        }

        url.push_str(&format!("&limit={}", limit));

        debug!(
            "Single API call: url={}, interval={}, start_date={:?}, limit={}",
            url, interval_str, start_date, limit
        );

        // Log the exact URL being called
        info!("🌐 CRYPTO API CALL: {} (Host: {:?})", url, self.host_header);

        // Execute request using our shared method
        self.execute_api_call(&url, interval).await
    }
}
