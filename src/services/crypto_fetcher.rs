use crate::error::Error;
use crate::models::{Interval, StockData};
use crate::services::crypto_compare::CryptoCompareClient;
use crate::services::crypto_api_client::AiPriceActionClient;
use crate::services::vci::OhlcvData;
use chrono::{DateTime, Utc, NaiveDate};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use tracing::{info, warn, debug};

/// Category result for crypto pre-scan
#[derive(Debug)]
pub struct CryptoCategory {
    pub resume_cryptos: Vec<(String, String)>,      // (symbol, last_date)
    pub full_history_cryptos: Vec<String>,
    pub partial_history_cryptos: Vec<(String, String)>, // (symbol, start_date)
}

impl CryptoCategory {
    pub fn new() -> Self {
        Self {
            resume_cryptos: Vec::new(),
            full_history_cryptos: Vec::new(),
            partial_history_cryptos: Vec::new(),
        }
    }

    pub fn total_cryptos(&self) -> usize {
        self.resume_cryptos.len() + self.full_history_cryptos.len() + self.partial_history_cryptos.len()
    }
}

/// Crypto data source - either CryptoCompare API or alternative aipriceaction API
enum CryptoDataSource {
    /// Direct CryptoCompare API access
    CryptoCompare(CryptoCompareClient),
    /// Alternative API (another aipriceaction instance)
    ApiProxy(AiPriceActionClient),
}

/// Cryptocurrency data fetcher with fallback support
pub struct CryptoFetcher {
    primary_source: CryptoDataSource,
    fallback_source: Option<CryptoCompareClient>,
}

impl CryptoFetcher {
    /// Create new crypto fetcher with automatic source selection
    ///
    /// Checks environment variables:
    /// - CRYPTO_WORKER_TARGET_URL: If set, use alternative API as primary source
    /// - CRYPTO_WORKER_TARGET_HOST: Optional Host header for CDN/proxy bypass
    ///
    /// If alternative API is used, CryptoCompare is kept as fallback
    pub fn new(api_key: Option<String>) -> Result<Self, Error> {
        // Check for alternative API configuration
        let target_url = std::env::var("CRYPTO_WORKER_TARGET_URL").ok();
        let target_host = std::env::var("CRYPTO_WORKER_TARGET_HOST").ok();

        let (primary_source, fallback_source) = if let Some(url) = target_url {
            // Use alternative API as primary source
            info!(
                "Using alternative API for crypto data: url={}, host={:?}",
                url, target_host
            );

            let api_client = AiPriceActionClient::new(url, target_host)
                .map_err(|e| Error::Config(format!("Failed to create API client: {}", e)))?;

            // Using API proxy mode for crypto data
            debug!("API proxy mode enabled for crypto data fetching");

            (CryptoDataSource::ApiProxy(api_client), None)
        } else {
            // Use CryptoCompare as primary (default behavior)
            info!("Using CryptoCompare API for crypto data (default)");

            let crypto_client = CryptoCompareClient::new(api_key)
                .map_err(|e| Error::Config(format!("Failed to create CryptoCompare client: {:?}", e)))?;

            (CryptoDataSource::CryptoCompare(crypto_client), None)
        };

        Ok(Self {
            primary_source,
            fallback_source,
        })
    }

    /// Read the last date from a CSV file
    fn read_last_date(&self, file_path: &Path) -> Result<Option<String>, Error> {
        use std::fs::File;
        use std::io::{BufRead, BufReader};

        let file = File::open(file_path)
            .map_err(|e| Error::Io(format!("Failed to open CSV: {}", e)))?;

        let reader = BufReader::new(file);
        let mut last_valid_date: Option<String> = None;

        // Read all lines to find the last valid data line
        for line in reader.lines() {
            let line = line.map_err(|e| Error::Io(format!("Failed to read line: {}", e)))?;

            // Skip header and empty lines
            if line.starts_with("ticker") || line.trim().is_empty() {
                continue;
            }

            // Parse CSV line (format: ticker,time,open,high,low,close,volume,...)
            let parts: Vec<&str> = line.split(',').collect();
            if parts.len() >= 2 {
                let date_str = parts[1].trim();
                // Extract just the date part (YYYY-MM-DD) from datetime strings
                let date = if date_str.contains(' ') {
                    date_str.split(' ').next().unwrap_or(date_str)
                } else if date_str.contains('T') {
                    date_str.split('T').next().unwrap_or(date_str)
                } else {
                    date_str
                };
                last_valid_date = Some(date.to_string());
            }
        }

        Ok(last_valid_date)
    }

    /// Read the last enhanced record from a CSV file (with MA indicators)
    /// Returns StockData with full 20-column CSV data
    fn read_last_enhanced_record(&self, file_path: &Path) -> Result<Option<StockData>, Error> {
        use std::fs::File;
        use std::io::{BufRead, BufReader};

        let file = File::open(file_path)
            .map_err(|e| Error::Io(format!("Failed to open CSV: {}", e)))?;

        let reader = BufReader::new(file);
        let mut last_valid_line: Option<String> = None;

        // Read all lines to find the last valid data line
        for line in reader.lines() {
            let line = line.map_err(|e| Error::Io(format!("Failed to read line: {}", e)))?;

            // Skip header and empty lines
            if line.starts_with("ticker") || line.trim().is_empty() {
                continue;
            }

            last_valid_line = Some(line);
        }

        // Parse the last valid line into StockData
        if let Some(line) = last_valid_line {
            Self::parse_enhanced_csv_line(&line)
        } else {
            Ok(None)
        }
    }

    /// Parse a CSV line into StockData (20 columns)
    /// Format: ticker,time,open,high,low,close,volume,ma10,ma20,ma50,ma100,ma200,
    ///         ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,
    ///         close_changed,volume_changed,total_money_changed
    fn parse_enhanced_csv_line(line: &str) -> Result<Option<StockData>, Error> {
        let parts: Vec<&str> = line.split(',').collect();
        if parts.len() < 20 {
            return Ok(None); // Not an enhanced CSV line
        }

        // Helper to parse optional f64
        let parse_opt_f64 = |s: &str| -> Option<f64> {
            s.trim().parse::<f64>().ok()
        };

        // Parse ticker (column 0)
        let ticker = parts[0].trim().to_string();

        // Parse time (column 1)
        let time_str = parts[1].trim();
        let time: DateTime<Utc> = if time_str.contains(' ') || time_str.contains('T') {
            // Hourly/Minute format: "2024-11-20 09:30:00" or "2024-11-20T09:30:00"
            // Normalize to ISO format and parse
            let normalized = time_str.replace(' ', "T");
            chrono::NaiveDateTime::parse_from_str(&normalized, "%Y-%m-%dT%H:%M:%S")
                .map(|ndt| ndt.and_utc())
                .map_err(|e| Error::Parse(format!("Failed to parse hourly/minute time '{}': {}", time_str, e)))?
        } else {
            // Daily format: "2024-11-20"
            chrono::NaiveDate::parse_from_str(time_str, "%Y-%m-%d")
                .map(|nd| nd.and_hms_opt(0, 0, 0).unwrap().and_utc())
                .map_err(|e| Error::Parse(format!("Failed to parse daily time '{}': {}", time_str, e)))?
        };

        // Parse OHLCV (columns 2-6)
        let open = parts[2].trim().parse::<f64>()
            .map_err(|e| Error::Parse(format!("Failed to parse open: {}", e)))?;
        let high = parts[3].trim().parse::<f64>()
            .map_err(|e| Error::Parse(format!("Failed to parse high: {}", e)))?;
        let low = parts[4].trim().parse::<f64>()
            .map_err(|e| Error::Parse(format!("Failed to parse low: {}", e)))?;
        let close = parts[5].trim().parse::<f64>()
            .map_err(|e| Error::Parse(format!("Failed to parse close: {}", e)))?;
        let volume = parts[6].trim().parse::<u64>()
            .map_err(|e| Error::Parse(format!("Failed to parse volume: {}", e)))?;

        // Parse MAs (columns 7-11)
        let ma10 = parse_opt_f64(parts[7]);
        let ma20 = parse_opt_f64(parts[8]);
        let ma50 = parse_opt_f64(parts[9]);
        let ma100 = parse_opt_f64(parts[10]);
        let ma200 = parse_opt_f64(parts[11]);

        // Parse MA scores (columns 12-16)
        let ma10_score = parse_opt_f64(parts[12]);
        let ma20_score = parse_opt_f64(parts[13]);
        let ma50_score = parse_opt_f64(parts[14]);
        let ma100_score = parse_opt_f64(parts[15]);
        let ma200_score = parse_opt_f64(parts[16]);

        // Parse change indicators (columns 17-19)
        let close_changed = parse_opt_f64(parts[17]);
        let volume_changed = parse_opt_f64(parts[18]);
        let total_money_changed = parse_opt_f64(parts[19]);

        Ok(Some(StockData {
            time,
            ticker,
            open,
            high,
            low,
            close,
            volume,
            ma10,
            ma20,
            ma50,
            ma100,
            ma200,
            ma10_score,
            ma20_score,
            ma50_score,
            ma100_score,
            ma200_score,
            close_changed,
            volume_changed,
            total_money_changed,
        }))
    }

    /// Get the file path for a crypto's CSV file
    fn get_crypto_file_path(&self, symbol: &str, interval: Interval) -> PathBuf {
        let crypto_data_dir = crate::utils::get_crypto_data_dir();
        crypto_data_dir.join(symbol).join(interval.to_filename())
    }

    /// Categorize cryptos into resume vs full history based on existing data
    /// Includes gap detection similar to stock system
    pub fn categorize_cryptos(
        &self,
        symbols: &[String],
        interval: Interval,
        disable_partial: bool,
    ) -> Result<CryptoCategory, Error> {
        println!(
            "\n🔍 Pre-scanning {} cryptos to categorize data needs for {}...",
            symbols.len(),
            interval.to_filename()
        );

        let mut category = CryptoCategory::new();
        let total = symbols.len();
        let show_first = 3;
        let show_last = 2;
        let today = Utc::now().date_naive();
        // Interval-specific gap thresholds (same as stock system):
        // - Daily: 14 days (2 weeks) - handles weekends + holidays efficiently
        // - Hourly: 7 days (1 week) - reasonable gap for hourly data
        // - Minute: 3 days - conservative approach for high-frequency data
        let gap_threshold_days = match interval {
            crate::models::Interval::Daily => 14,
            crate::models::Interval::Hourly => 7,
            crate::models::Interval::Minute => 3,
        };

        for (idx, symbol) in symbols.iter().enumerate() {
            let file_path = self.get_crypto_file_path(symbol, interval);

            // Only print first few and last few
            let should_print = idx < show_first || idx >= total - show_last;

            if idx == show_first && total > show_first + show_last {
                println!("   ... ({} more cryptos) ...", total - show_first - show_last);
            }

            if !file_path.exists() {
                info!(
                    "📄 {} [{}] file_path={} exists=false → full_history",
                    symbol, interval.to_filename(), file_path.display()
                );
                if should_print {
                    println!("   📄 {} - No existing data (full history needed)", symbol);
                }
                category.full_history_cryptos.push(symbol.clone());
            } else {
                // File exists - read last date and check gap
                match self.read_last_date(&file_path) {
                    Ok(Some(last_date)) => {
                        // Parse last date and calculate gap
                        match NaiveDate::parse_from_str(&last_date, "%Y-%m-%d") {
                            Ok(last_date_parsed) => {
                                let days_gap = (today - last_date_parsed).num_days();

                                // Gap detection logic - same as stock system
                                if days_gap > gap_threshold_days && !disable_partial {
                                    info!(
                                        "⏰ {} [{}] file_path={} last_date={} gap={}days → partial_history",
                                        symbol, interval.to_filename(), file_path.display(), last_date, days_gap
                                    );
                                    if should_print {
                                        println!("   ⏰ {} - Gap {} days > {} threshold (partial history from {})",
                                                symbol, days_gap, gap_threshold_days, last_date);
                                    }
                                    category.partial_history_cryptos.push((symbol.clone(), last_date));
                                } else {
                                    // Gap small enough for batch resume
                                    info!(
                                        "✅ {} [{}] file_path={} last_date={} gap={}days → resume",
                                        symbol, interval.to_filename(), file_path.display(), last_date, days_gap
                                    );
                                    if should_print {
                                        println!("   ✅ {} - Resume from {} ({} day gap)", symbol, last_date, days_gap);
                                    }
                                    category.resume_cryptos.push((symbol.clone(), last_date));
                                }
                            }
                            Err(e) => {
                                warn!(
                                    "⚠️ {} [{}] file_path={} last_date={} parse_error={} → full_history",
                                    symbol, interval.to_filename(), file_path.display(), last_date, e
                                );
                                if should_print {
                                    println!("   ⚠️  {} - Failed to parse last date (full history needed)", symbol);
                                }
                                category.full_history_cryptos.push(symbol.clone());
                            }
                        }
                    }
                    Ok(None) => {
                        info!(
                            "⚠️ {} [{}] file_path={} last_date=None → full_history",
                            symbol, interval.to_filename(), file_path.display()
                        );
                        if should_print {
                            println!("   ⚠️  {} - CSV exists but empty (full history needed)", symbol);
                        }
                        category.full_history_cryptos.push(symbol.clone());
                    }
                    Err(e) => {
                        info!(
                            "⚠️ {} [{}] file_path={} error={} → full_history",
                            symbol, interval.to_filename(), file_path.display(), e
                        );
                        if should_print {
                            println!("   ⚠️  {} - Failed to read CSV (full history needed)", symbol);
                        }
                        category.full_history_cryptos.push(symbol.clone());
                    }
                }
            }
        }

        println!(
            "📊 Categorization: {} resume, {} full history, {} partial",
            category.resume_cryptos.len(),
            category.full_history_cryptos.len(),
            category.partial_history_cryptos.len()
        );

        Ok(category)
    }

    /// Fetch full history for a single crypto with pagination
    pub async fn fetch_full_history(
        &mut self,
        symbol: &str,
        start_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        // Try primary source first
        let result = match &mut self.primary_source {
            CryptoDataSource::ApiProxy(client) => {
                // Fetch single ticker to save bandwidth
                client.fetch_single_crypto(symbol, start_date, interval).await
            }
            CryptoDataSource::CryptoCompare(client) => {
                match interval {
                    Interval::Daily => {
                        // Use allData=true for daily (no pagination needed)
                        client
                            .get_history(symbol, start_date, None, interval, None, true)
                            .await
                            .map_err(|e| {
                                if e.to_string().contains("Rate limit exceeded") {
                                    Error::RateLimit
                                } else {
                                    Error::Network(format!("Failed to fetch daily data: {}", e))
                                }
                            })
                    }
                    Interval::Hourly | Interval::Minute => {
                        // Use pagination for hourly and minute
                        Self::fetch_paginated_history_from_cryptocompare(client, symbol, start_date, interval).await
                    }
                }
            }
        };

        // If primary source failed and we have a fallback, try it
        match result {
            Ok(data) => Ok(data),
            Err(e) => {
                if let Some(fallback) = &mut self.fallback_source {
                    warn!(
                        "Primary source failed for {} ({}), trying fallback CryptoCompare: {}",
                        symbol, interval.to_filename(), e
                    );

                    match interval {
                        Interval::Daily => {
                            fallback
                                .get_history(symbol, start_date, None, interval, None, true)
                                .await
                                .map_err(|e| {
                                    if e.to_string().contains("Rate limit exceeded") {
                                        Error::RateLimit
                                    } else {
                                        Error::Network(format!("Fallback also failed: {}", e))
                                    }
                                })
                        }
                        Interval::Hourly | Interval::Minute => {
                            Self::fetch_paginated_history_from_cryptocompare(fallback, symbol, start_date, interval).await
                        }
                    }
                } else {
                    Err(e)
                }
            }
        }
    }

    /// Fetch recent data for resume mode
    pub async fn fetch_recent(
        &mut self,
        symbol: &str,
        last_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        // Try primary source first
        let result = match &mut self.primary_source {
            CryptoDataSource::ApiProxy(client) => {
                // Fetch single ticker to save bandwidth
                client.fetch_single_crypto(symbol, last_date, interval).await
            }
            CryptoDataSource::CryptoCompare(client) => {
                Self::fetch_paginated_history_from_cryptocompare(client, symbol, last_date, interval).await
            }
        };

        // If primary source failed and we have a fallback, try it
        match result {
            Ok(data) => Ok(data),
            Err(e) => {
                if let Some(fallback) = &mut self.fallback_source {
                    warn!(
                        "Primary source failed for {} resume ({}), trying fallback CryptoCompare: {}",
                        symbol, interval.to_filename(), e
                    );

                    Self::fetch_paginated_history_from_cryptocompare(fallback, symbol, last_date, interval).await
                } else {
                    Err(e)
                }
            }
        }
    }

    /// Fetch paginated history from CryptoCompare (for hourly and minute intervals)
    async fn fetch_paginated_history_from_cryptocompare(
        client: &mut CryptoCompareClient,
        symbol: &str,
        start_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        let mut all_data = Vec::new();
        let mut to_ts: Option<i64> = None;
        let limit = 2000; // CryptoCompare max

        loop {
            let batch = client
                .get_history(symbol, start_date, to_ts, interval, Some(limit), false)
                .await
                .map_err(|e| {
                    if e.to_string().contains("Rate limit exceeded") {
                        Error::RateLimit
                    } else {
                        Error::Network(format!("Pagination request failed: {}", e))
                    }
                })?;

            if batch.is_empty() {
                break;
            }

            let batch_len = batch.len();
            let oldest = batch.first().unwrap().time;

            // Set toTs to the oldest timestamp for next batch
            to_ts = Some(oldest.timestamp());

            all_data.extend(batch);

            // If we got less than limit, we've reached the end
            if batch_len < limit {
                break;
            }

            // Rate limit: wait 200ms between requests (5 calls/sec)
            tokio::time::sleep(tokio::time::Duration::from_millis(200)).await;
        }

        // Sort by time (oldest first)
        all_data.sort_by_key(|d| d.time);

        Ok(all_data)
    }

    /// Sequential fetch for multiple cryptos (no batch API for CryptoCompare)
    pub async fn sequential_fetch(
        &mut self,
        symbols: &[String],
        start_date: &str,
        interval: Interval,
        full: bool,
    ) -> Result<HashMap<String, Option<Vec<OhlcvData>>>, Error> {
        let mut results: HashMap<String, Option<Vec<OhlcvData>>> = HashMap::new();

        for (idx, symbol) in symbols.iter().enumerate() {
            println!("[{}/{}] Fetching {} {}...", idx + 1, symbols.len(), symbol, interval.to_filename());

            let data = if full {
                self.fetch_full_history(symbol, start_date, interval).await
            } else {
                // Resume mode - get last date from CSV
                let file_path = self.get_crypto_file_path(symbol, interval);
                match self.read_last_date(&file_path) {
                    Ok(Some(last_date)) => {
                        self.fetch_recent(symbol, &last_date, interval).await
                    }
                    _ => {
                        // No last date available, fetch full history
                        self.fetch_full_history(symbol, start_date, interval).await
                    }
                }
            };

            match data {
                Ok(ohlcv_data) => {
                    println!("   ✅ {} records fetched", ohlcv_data.len());
                    results.insert(symbol.clone(), Some(ohlcv_data));
                }
                Err(e) => {
                    eprintln!("   ❌ Failed to fetch {}: {}", symbol, e);
                    results.insert(symbol.clone(), None);
                    // Continue with next crypto instead of failing entire batch
                }
            }

            // Rate limit delay between cryptos (200ms)
            tokio::time::sleep(tokio::time::Duration::from_millis(200)).await;
        }

        Ok(results)
    }

    /// Fetch data for multiple cryptos using batch API (ApiProxy mode only)
    /// Returns HashMap of symbol -> Vec<OhlcvData>
    /// In CryptoCompare mode, falls back to sequential fetching
    pub async fn fetch_batch(
        &mut self,
        symbols: &[String],
        start_date: &str,
        interval: Interval,
    ) -> Result<HashMap<String, Vec<OhlcvData>>, Error> {
        match &mut self.primary_source {
            CryptoDataSource::ApiProxy(client) => {
                info!(
                    "Batch fetch: {} cryptos via API proxy (start_date={}, interval={})",
                    symbols.len(),
                    start_date,
                    interval.to_filename()
                );

                // Make single batch API call for specific cryptos in this chunk
                let all_data = client.fetch_all_cryptos(Some(symbols), Some(start_date), interval, None).await?;

                // Group data by symbol
                let mut result: HashMap<String, Vec<OhlcvData>> = HashMap::new();
                for record in all_data {
                    if let Some(ref symbol) = record.symbol {
                        result.entry(symbol.clone()).or_insert_with(Vec::new).push(record);
                    }
                }

                // Ensure all requested symbols have an entry (even if empty)
                for symbol in symbols {
                    result.entry(symbol.clone()).or_insert_with(Vec::new);
                }

                info!(
                    "Batch fetch: received data for {} cryptos",
                    result.iter().filter(|(_, v)| !v.is_empty()).count()
                );

                Ok(result)
            }
            CryptoDataSource::CryptoCompare(_) => {
                // CryptoCompare doesn't have batch API, fall back to sequential
                info!(
                    "Batch fetch: CryptoCompare mode, falling back to sequential fetch for {} cryptos",
                    symbols.len()
                );
                self.sequential_fetch(symbols, start_date, interval, false).await.map(|results| {
                    results.into_iter()
                        .filter_map(|(k, v)| v.map(|data| (k, data)))
                        .collect()
                })
            }
        }
    }

    /// Check if using API proxy mode
    pub fn is_proxy_mode(&self) -> bool {
        matches!(self.primary_source, CryptoDataSource::ApiProxy(_))
    }

    /// Pre-check if interval data has changed for any crypto
    /// Returns true if ALL cryptos unchanged (skip sync), false if ANY changed (proceed with sync)
    /// Only works in ApiProxy mode - returns false for CryptoCompare mode
    pub async fn pre_check_interval_unchanged(
        &mut self,
        symbols: &[String],
        interval: Interval,
    ) -> Result<bool, Error> {
        // Only works in ApiProxy mode
        if !matches!(self.primary_source, CryptoDataSource::ApiProxy(_)) {
            debug!("Pre-check: Not in ApiProxy mode, skipping pre-check");
            return Ok(false); // Skip pre-check in CryptoCompare mode
        }

        info!(
            "Pre-check: Fetching last 1 record for {} cryptos via batch API (interval={})",
            symbols.len(),
            interval.to_filename()
        );

        // Fetch last 1 record for specific cryptos (no start_date, just limit=1)
        let api_data = match &mut self.primary_source {
            CryptoDataSource::ApiProxy(client) => {
                client.fetch_all_cryptos(Some(symbols), None, interval, Some(1)).await?
            }
            _ => return Ok(false), // Should never reach here due to check above
        };

        // Group API data by symbol
        let mut api_map: HashMap<String, &OhlcvData> = HashMap::new();
        for record in &api_data {
            if let Some(ref symbol) = record.symbol {
                api_map.insert(symbol.clone(), record);
            }
        }

        info!(
            "Pre-check: Received {} records from API, comparing with CSV files",
            api_map.len()
        );

        // Compare each crypto's last record
        for symbol in symbols {
            let csv_path = self.get_crypto_file_path(symbol, interval);

            // Read last record from CSV
            let csv_record = match self.read_last_enhanced_record(&csv_path) {
                Ok(Some(record)) => record,
                Ok(None) => {
                    info!("Pre-check: {} - CSV missing or empty, needs sync", symbol);
                    return Ok(false); // CSV missing, need sync
                }
                Err(e) => {
                    warn!("Pre-check: {} - Failed to read CSV: {}, needs sync", symbol, e);
                    return Ok(false); // CSV read error, need sync
                }
            };

            // Get API record
            let api_record = match api_map.get(symbol) {
                Some(record) => record,
                None => {
                    info!("Pre-check: {} - Not in API response, needs sync", symbol);
                    return Ok(false); // API missing data, need sync
                }
            };

            // Compare OHLC values (tolerance: 0.01)
            let tolerance = 0.01;
            if (csv_record.open - api_record.open).abs() >= tolerance
                || (csv_record.high - api_record.high).abs() >= tolerance
                || (csv_record.low - api_record.low).abs() >= tolerance
                || (csv_record.close - api_record.close).abs() >= tolerance
            {
                info!(
                    "Pre-check: {} - OHLC changed (CSV close={}, API close={}), needs sync",
                    symbol, csv_record.close, api_record.close
                );
                return Ok(false); // Price changed, need sync
            }

            // Note: We can't compare MA20/MA50 directly from OhlcvData
            // The API response needs to be enhanced to include MA values
            // For now, we'll just compare OHLC which is sufficient for detecting new data

            debug!(
                "Pre-check: {} - OHLC matches (close={})",
                symbol, csv_record.close
            );
        }

        info!(
            "Pre-check: All {} cryptos unchanged, sync can be skipped",
            symbols.len()
        );
        Ok(true) // All cryptos unchanged
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_crypto_category_new() {
        let category = CryptoCategory::new();
        assert_eq!(category.total_cryptos(), 0);
        assert_eq!(category.resume_cryptos.len(), 0);
        assert_eq!(category.full_history_cryptos.len(), 0);
    }
}
