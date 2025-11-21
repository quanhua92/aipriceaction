use crate::error::Error;
use crate::models::{Interval, SyncConfig, SyncStats};
use crate::services::crypto_fetcher::CryptoFetcher;
use crate::services::vci::OhlcvData;
use crate::services::csv_enhancer::{enhance_data, save_enhanced_csv_to_dir};
use crate::utils::get_crypto_data_dir;
use chrono::Utc;
use std::collections::HashMap;
use std::time::Instant;

/// High-level cryptocurrency data synchronization orchestrator
pub struct CryptoSync {
    config: SyncConfig,
    fetcher: CryptoFetcher,
    #[allow(dead_code)]
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

        // Check if proxy mode for faster delays
        let is_proxy_mode = std::env::var("CRYPTO_WORKER_TARGET_URL").is_ok();
        let delay_ms = if is_proxy_mode { 50 } else { 200 };

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

        // For log verbosity: show first 3 and last 2
        let show_first = 3;
        let show_last = 2;

        if !resume_list.is_empty() {
            println!("\n🔄 Processing {} resume cryptos...", resume_list.len());
            let total = resume_list.len();

            for (idx, (symbol, last_date)) in resume_list.iter().enumerate() {
                let should_print = idx < show_first || idx >= total.saturating_sub(show_last);
                if idx == show_first && total > show_first + show_last {
                    println!("   ... ({} more cryptos) ...", total - show_first - show_last);
                }

                match self.fetch_recent(symbol, last_date, interval).await {
                    Ok(data) => {
                        match self.process_crypto(symbol, Some(data), interval, last_date).await {
                            Ok(_) => {
                                success_count += 1;
                                if should_print {
                                    println!("   [{}/{}] {} ✅", idx + 1, total, symbol);
                                }
                            }
                            Err(e) => {
                                eprintln!("   [{}/{}] {} ❌ Save failed: {}", idx + 1, total, symbol, e);
                                failed_cryptos.push(symbol.clone());
                            }
                        }
                    }
                    Err(e) => {
                        if matches!(e, Error::RateLimit) {
                            eprintln!("❌ Rate limit hit - skipping remaining {} tickers", total - idx);
                            for (remaining_symbol, _) in resume_list.iter().skip(idx) {
                                failed_cryptos.push(remaining_symbol.clone());
                            }
                            break;
                        }
                        eprintln!("   [{}/{}] {} ❌ {}", idx + 1, total, symbol, e);
                        failed_cryptos.push(symbol.clone());
                    }
                }

                tokio::time::sleep(tokio::time::Duration::from_millis(delay_ms)).await;
            }
        }

        // Process full history cryptos
        if !full_list.is_empty() {
            println!("\n📥 Processing {} full history cryptos...", full_list.len());
            let total = full_list.len();
            let start_date = self.get_start_date_for_interval(interval);

            for (idx, symbol) in full_list.iter().enumerate() {
                let should_print = idx < show_first || idx >= total.saturating_sub(show_last);
                if idx == show_first && total > show_first + show_last {
                    println!("   ... ({} more cryptos) ...", total - show_first - show_last);
                }

                match self.fetch_full_history(symbol, &start_date, interval).await {
                    Ok(data) => {
                        match self.process_crypto(symbol, Some(data), interval, "").await {
                            Ok(_) => {
                                success_count += 1;
                                if should_print {
                                    println!("   [{}/{}] {} ✅", idx + 1, total, symbol);
                                }
                            }
                            Err(e) => {
                                eprintln!("   [{}/{}] {} ❌ Save failed: {}", idx + 1, total, symbol, e);
                                failed_cryptos.push(symbol.clone());
                            }
                        }
                    }
                    Err(e) => {
                        if matches!(e, Error::RateLimit) {
                            eprintln!("❌ Rate limit hit - skipping remaining {} tickers", total - idx);
                            for remaining_symbol in full_list.iter().skip(idx) {
                                failed_cryptos.push(remaining_symbol.clone());
                            }
                            break;
                        }
                        eprintln!("   [{}/{}] {} ❌ {}", idx + 1, total, symbol, e);
                        failed_cryptos.push(symbol.clone());
                    }
                }

                tokio::time::sleep(tokio::time::Duration::from_millis(delay_ms)).await;
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

    /// Enhance and save crypto data (mirrors stock sync optimization)
    fn enhance_and_save_crypto_data(
        &self,
        symbol: &str,
        new_data: &[OhlcvData],
        interval: Interval,
        last_date: &str,
    ) -> Result<(), Error> {
        if new_data.is_empty() {
            return Ok(());
        }

        let crypto_data_dir = get_crypto_data_dir();
        let file_path = crypto_data_dir.join(symbol).join(interval.to_filename());
        let is_resume = !last_date.is_empty() && !self.config.force_full;

        // Merge existing CSV data with new data (like stock sync does)
        let merged_data = if is_resume && file_path.exists() {
            let existing = self.read_existing_ohlcv(&file_path)?;
            self.merge_ohlcv_data(existing, new_data.to_vec())
        } else {
            new_data.to_vec()
        };

        if merged_data.is_empty() {
            return Ok(());
        }

        // Calculate cutoff date (like stock sync)
        let resume_days = 2i64;
        let cutoff_datetime = if is_resume && file_path.exists() {
            if let Some(last_record) = merged_data.last() {
                last_record.time - chrono::Duration::days(resume_days)
            } else {
                chrono::DateTime::from_timestamp(0, 0).unwrap_or_else(|| Utc::now())
            }
        } else {
            chrono::DateTime::from_timestamp(0, 0).unwrap_or_else(|| Utc::now())
        };

        // Find cutoff index
        let cutoff_index = merged_data.iter()
            .position(|d| d.time >= cutoff_datetime)
            .unwrap_or(merged_data.len());

        // Optimization: Take 200 records before cutoff for MA200 context
        const MA_BUFFER: usize = 200;
        let start_index = cutoff_index.saturating_sub(MA_BUFFER);
        let sliced_data = &merged_data[start_index..];

        // Enhance the sliced data (with proper MA context)
        let mut data_map: HashMap<String, Vec<OhlcvData>> = HashMap::new();
        data_map.insert(symbol.to_string(), sliced_data.to_vec());
        let enhanced_data = enhance_data(data_map);

        let stock_data = enhanced_data.get(symbol)
            .ok_or_else(|| Error::Other("Failed to enhance data".to_string()))?;

        // Save with cutoff strategy
        save_enhanced_csv_to_dir(
            symbol,
            stock_data,
            interval,
            cutoff_datetime,
            !is_resume, // rewrite_all for full mode
            &crypto_data_dir
        )?;

        Ok(())
    }

    /// Read existing OHLCV data from CSV file
    fn read_existing_ohlcv(&self, file_path: &std::path::Path) -> Result<Vec<OhlcvData>, Error> {
        if !file_path.exists() {
            return Ok(Vec::new());
        }

        let mut reader = csv::ReaderBuilder::new()
            .flexible(true)
            .from_path(file_path)
            .map_err(|e| Error::Io(format!("Failed to read CSV: {}", e)))?;

        let mut data = Vec::new();
        for result in reader.records() {
            let record = match result {
                Ok(r) => r,
                Err(_) => continue,
            };

            if record.len() < 7 {
                continue;
            }

            let symbol = record.get(0).unwrap_or("").to_string();
            let time_str = record.get(1).unwrap_or("");
            let open: f64 = record.get(2).and_then(|s| s.parse().ok()).unwrap_or(0.0);
            let high: f64 = record.get(3).and_then(|s| s.parse().ok()).unwrap_or(0.0);
            let low: f64 = record.get(4).and_then(|s| s.parse().ok()).unwrap_or(0.0);
            let close: f64 = record.get(5).and_then(|s| s.parse().ok()).unwrap_or(0.0);
            let volume: u64 = record.get(6).and_then(|s| s.parse().ok()).unwrap_or(0);

            // Parse time
            let time = crate::utils::parse_timestamp(time_str)
                .unwrap_or_else(|_| Utc::now());

            data.push(OhlcvData {
                time,
                open,
                high,
                low,
                close,
                volume,
                symbol: Some(symbol),
            });
        }

        data.sort_by_key(|d| d.time);
        Ok(data)
    }

    /// Merge existing and new OHLCV data (like stock sync)
    fn merge_ohlcv_data(&self, existing: Vec<OhlcvData>, new: Vec<OhlcvData>) -> Vec<OhlcvData> {
        if existing.is_empty() {
            return new;
        }
        if new.is_empty() {
            return existing;
        }

        // Find the latest timestamp in existing data
        let latest_existing_time = existing.iter()
            .map(|d| d.time)
            .max()
            .unwrap_or_else(|| Utc::now());

        // Keep existing data before latest time
        let mut merged: Vec<OhlcvData> = existing
            .into_iter()
            .filter(|d| d.time < latest_existing_time)
            .collect();

        // Add new data >= latest time (includes update to last row)
        let new_rows: Vec<OhlcvData> = new
            .into_iter()
            .filter(|d| d.time >= latest_existing_time)
            .collect();

        merged.extend(new_rows);
        merged.sort_by(|a, b| a.time.cmp(&b.time));
        merged
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
