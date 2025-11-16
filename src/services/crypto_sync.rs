use crate::error::Error;
use crate::models::{Interval, SyncConfig, SyncStats};
use crate::services::crypto_fetcher::{CryptoCategory, CryptoFetcher};
use crate::services::vci::OhlcvData;
use crate::services::csv_enhancer::{enhance_data, save_enhanced_csv_to_dir};
use chrono::{DateTime, Utc};
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::time::Instant;

/// High-level cryptocurrency data synchronization orchestrator
pub struct CryptoSync {
    config: SyncConfig,
    fetcher: CryptoFetcher,
    stats: SyncStats,
}

impl CryptoSync {
    /// Create new crypto sync orchestrator
    pub fn new(config: SyncConfig, api_key: Option<String>) -> Result<Self, Error> {
        let fetcher = CryptoFetcher::new(api_key)?;

        Ok(Self {
            config,
            fetcher,
            stats: SyncStats::new(),
        })
    }

    /// Synchronize all intervals for all crypto symbols
    pub async fn sync_all_intervals(&mut self, symbols: &[String]) -> Result<(), Error> {
        let start_time = Instant::now();

        println!("\n🚀 Starting crypto data sync: {} cryptos, {} intervals",
            symbols.len(),
            self.config.intervals.len()
        );

        println!("📅 Date range: {} to {}", self.config.start_date, self.config.end_date);
        println!("📊 Mode: {}", if self.config.force_full { "FULL DOWNLOAD" } else { "RESUME (incremental)" });

        // Process each interval
        for interval in &self.config.intervals.clone() {
            println!("\n{}", "=".repeat(70));
            println!("📊 Interval: {}", interval.to_filename());
            println!("{}", "=".repeat(70));

            self.sync_interval(symbols, *interval).await?;
        }

        let total_time = start_time.elapsed();

        // Print final summary
        self.print_final_summary(total_time);

        Ok(())
    }

    /// Synchronize a single interval for all crypto symbols
    async fn sync_interval(&mut self, symbols: &[String], interval: Interval) -> Result<(), Error> {
        let interval_start_time = Instant::now();

        // Categorize cryptos (resume vs full history)
        let category = self.fetcher.categorize_cryptos(symbols, interval)?;

        // Process resume cryptos
        let mut success_count = 0;
        let mut failed_cryptos: Vec<String> = Vec::new();

        // If force_full, treat resume cryptos as full history cryptos
        let (resume_list, full_list) = if self.config.force_full {
            // force_full=true: all cryptos get full history download
            let mut all_full = category.full_history_cryptos.clone();
            all_full.extend(category.resume_cryptos.iter().map(|(s, _)| s.clone()));
            (Vec::new(), all_full)
        } else {
            // Normal mode: respect categorization
            (category.resume_cryptos.clone(), category.full_history_cryptos.clone())
        };

        if !resume_list.is_empty() {
            println!("\n🔄 Processing {} resume cryptos...", resume_list.len());

            for (idx, (symbol, last_date)) in resume_list.iter().enumerate() {
                print!("   [{}/{}] {} (from {})... ",
                    idx + 1,
                    resume_list.len(),
                    symbol,
                    last_date
                );

                match self.fetch_recent(symbol, last_date, interval).await {
                    Ok(data) => {
                        match self.process_crypto(symbol, Some(data), interval, last_date).await {
                            Ok(_) => {
                                success_count += 1;
                                println!("✅");
                            }
                            Err(e) => {
                                eprintln!("❌ Save failed: {}", e);
                                failed_cryptos.push(symbol.clone());
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("❌ Fetch failed: {}", e);
                        failed_cryptos.push(symbol.clone());
                    }
                }

                // Rate limit delay
                tokio::time::sleep(tokio::time::Duration::from_millis(200)).await;
            }
        }

        // Process full history cryptos
        if !full_list.is_empty() {
            println!("\n📥 Processing {} full history cryptos...", full_list.len());

            let start_date = self.get_start_date_for_interval(interval);

            for (idx, symbol) in full_list.iter().enumerate() {
                print!("   [{}/{}] {} (full)... ",
                    idx + 1,
                    full_list.len(),
                    symbol
                );

                match self.fetch_full_history(symbol, &start_date, interval).await {
                    Ok(data) => {
                        match self.process_crypto(symbol, Some(data), interval, "").await {
                            Ok(_) => {
                                success_count += 1;
                                println!("✅");
                            }
                            Err(e) => {
                                eprintln!("❌ Save failed: {}", e);
                                failed_cryptos.push(symbol.clone());
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("❌ Fetch failed: {}", e);
                        failed_cryptos.push(symbol.clone());
                    }
                }

                // Rate limit delay
                tokio::time::sleep(tokio::time::Duration::from_millis(200)).await;
            }
        }

        let interval_time = interval_start_time.elapsed();

        // Print interval summary
        println!("\n📊 {} Summary:", interval.to_filename());
        println!("   ✅ Successful: {}/{}", success_count, symbols.len());
        if !failed_cryptos.is_empty() {
            println!("   ❌ Failed: {} ({:?})", failed_cryptos.len(), failed_cryptos);
        }
        println!("   ⏱️  Duration: {:.1}s", interval_time.as_secs_f64());

        Ok(())
    }

    /// Fetch full history for a crypto
    async fn fetch_full_history(
        &mut self,
        symbol: &str,
        start_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        self.fetcher.fetch_full_history(symbol, start_date, interval).await
    }

    /// Fetch recent data for a crypto (resume mode)
    async fn fetch_recent(
        &mut self,
        symbol: &str,
        last_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        self.fetcher.fetch_recent(symbol, last_date, interval).await
    }

    /// Process a single crypto (enhance and save)
    async fn process_crypto(
        &self,
        symbol: &str,
        data: Option<Vec<OhlcvData>>,
        interval: Interval,
        last_date: &str,
    ) -> Result<(), Error> {
        if let Some(ohlcv_data) = data {
            if ohlcv_data.is_empty() {
                return Ok(()); // Skip empty data
            }

            self.enhance_and_save_crypto_data(symbol, &ohlcv_data, interval, last_date)?;
        }

        Ok(())
    }

    /// Enhance and save crypto data
    fn enhance_and_save_crypto_data(
        &self,
        symbol: &str,
        data: &[OhlcvData],
        interval: Interval,
        last_date: &str,
    ) -> Result<(), Error> {
        // Convert to HashMap for enhance_data()
        let mut data_map: HashMap<String, Vec<OhlcvData>> = HashMap::new();
        data_map.insert(symbol.to_string(), data.to_vec());

        // Enhance data (calculates all MAs and scores)
        let enhanced_data = enhance_data(data_map);

        // Get the enhanced data for this symbol
        let stock_data = enhanced_data.get(symbol)
            .ok_or_else(|| Error::Other("Failed to enhance data".to_string()))?;

        let crypto_data_dir = PathBuf::from("crypto_data");

        // Determine if this is resume mode or full mode
        let is_resume = !last_date.is_empty() && !self.config.force_full;

        if is_resume {
            // Resume mode: append new data
            let cutoff_date = chrono::NaiveDate::parse_from_str(last_date, "%Y-%m-%d")
                .map_err(|e| Error::InvalidInput(format!("Invalid date format: {}", e)))?;
            let cutoff_datetime = chrono::DateTime::<Utc>::from_naive_utc_and_offset(
                cutoff_date.and_hms_opt(0, 0, 0).unwrap(),
                Utc
            );

            save_enhanced_csv_to_dir(
                symbol,
                stock_data,
                interval,
                cutoff_datetime,
                false, // Don't rewrite all
                &crypto_data_dir
            )?;
        } else {
            // Full mode: rewrite entire file
            let cutoff_date = chrono::Utc::now() - chrono::Duration::days(365 * 20); // Far in past

            save_enhanced_csv_to_dir(
                symbol,
                stock_data,
                interval,
                cutoff_date,
                true, // rewrite_all
                &crypto_data_dir
            )?;
        }

        Ok(())
    }

    /// Get start date for interval (accounts for data retention limits)
    fn get_start_date_for_interval(&self, interval: Interval) -> String {
        match interval {
            Interval::Daily | Interval::Hourly => {
                // Daily and hourly go back to BTC inception
                "2010-07-17".to_string()
            }
            Interval::Minute => {
                // CryptoCompare only keeps 7 days of minute data
                let seven_days_ago = chrono::Utc::now() - chrono::Duration::days(7);
                seven_days_ago.format("%Y-%m-%d").to_string()
            }
        }
    }

    /// Print final summary of sync operation
    fn print_final_summary(&self, total_time: std::time::Duration) {
        println!("\n{}", "=".repeat(70));
        println!("📊 CRYPTO SYNC SUMMARY");
        println!("{}", "=".repeat(70));
        println!("⏱️  Total time: {:.1}s", total_time.as_secs_f64());
        println!("{}", "=".repeat(70));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_start_date_for_interval() {
        let config = SyncConfig::default();
        let sync = CryptoSync::new(config, None).unwrap();

        assert_eq!(sync.get_start_date_for_interval(Interval::Daily), "2010-07-17");
        assert_eq!(sync.get_start_date_for_interval(Interval::Hourly), "2010-07-17");

        let minute_date = sync.get_start_date_for_interval(Interval::Minute);
        // Should be a recent date (within last 7 days)
        assert!(minute_date.starts_with("20")); // Starts with year 20XX
    }
}
