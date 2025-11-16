use crate::error::Error;
use crate::models::Interval;
use crate::services::crypto_compare::CryptoCompareClient;
use crate::services::vci::OhlcvData;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

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

/// Cryptocurrency data fetcher using CryptoCompare client
pub struct CryptoFetcher {
    crypto_client: CryptoCompareClient,
}

impl CryptoFetcher {
    /// Create new crypto fetcher with CryptoCompare client
    pub fn new(api_key: Option<String>) -> Result<Self, Error> {
        let crypto_client = CryptoCompareClient::new(api_key)
            .map_err(|e| Error::Config(format!("Failed to create CryptoCompare client: {:?}", e)))?;

        Ok(Self { crypto_client })
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

    /// Get the file path for a crypto's CSV file
    fn get_crypto_file_path(&self, symbol: &str, interval: Interval) -> PathBuf {
        let crypto_data_dir = PathBuf::from("crypto_data");
        crypto_data_dir.join(symbol).join(interval.to_filename())
    }

    /// Categorize cryptos into resume vs full history based on existing data
    pub fn categorize_cryptos(
        &self,
        symbols: &[String],
        interval: Interval,
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

        for (idx, symbol) in symbols.iter().enumerate() {
            let file_path = self.get_crypto_file_path(symbol, interval);

            // Only print first few and last few
            let should_print = idx < show_first || idx >= total - show_last;

            if idx == show_first && total > show_first + show_last {
                println!("   ... ({} more cryptos) ...", total - show_first - show_last);
            }

            if !file_path.exists() {
                if should_print {
                    println!("   📄 {} - No existing data (full history needed)", symbol);
                }
                category.full_history_cryptos.push(symbol.clone());
            } else {
                // File exists - read last date and use resume mode
                match self.read_last_date(&file_path) {
                    Ok(Some(last_date)) => {
                        if should_print {
                            println!("   ✅ {} - Resume from {}", symbol, last_date);
                        }
                        category.resume_cryptos.push((symbol.clone(), last_date));
                    }
                    Ok(None) => {
                        if should_print {
                            println!("   ⚠️  {} - CSV exists but empty (full history needed)", symbol);
                        }
                        category.full_history_cryptos.push(symbol.clone());
                    }
                    Err(_) => {
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
        match interval {
            Interval::Daily => {
                // Use allData=true for daily (no pagination needed)
                self.crypto_client
                    .get_history(symbol, start_date, None, interval, None, true)
                    .await
                    .map_err(|e| Error::Network(format!("Failed to fetch daily data: {}", e)))
            }
            Interval::Hourly | Interval::Minute => {
                // Use pagination for hourly and minute
                self.fetch_paginated_history(symbol, start_date, interval).await
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
        // For resume mode, use pagination from last_date
        self.fetch_paginated_history(symbol, last_date, interval).await
    }

    /// Fetch paginated history (for hourly and minute intervals)
    async fn fetch_paginated_history(
        &mut self,
        symbol: &str,
        start_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        let mut all_data = Vec::new();
        let mut to_ts: Option<i64> = None;
        let limit = 2000; // CryptoCompare max

        loop {
            let batch = self.crypto_client
                .get_history(symbol, start_date, to_ts, interval, Some(limit), false)
                .await
                .map_err(|e| Error::Network(format!("Pagination request failed: {}", e)))?;

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
