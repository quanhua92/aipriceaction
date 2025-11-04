use crate::error::Error;
use crate::models::{Interval, SyncConfig, FetchProgress, SyncStats, TickerGroups};
use crate::services::ticker_fetcher::TickerFetcher;
use crate::services::vci::OhlcvData;
use chrono::{DateTime, NaiveDate, Utc};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

const MARKET_DATA_DIR: &str = "market_data";
const TICKER_GROUP_FILE: &str = "ticker_group.json";

/// High-level data synchronization orchestrator
pub struct DataSync {
    config: SyncConfig,
    fetcher: TickerFetcher,
    stats: SyncStats,
}

impl DataSync {
    /// Create new data sync orchestrator
    pub fn new(config: SyncConfig) -> Result<Self, Error> {
        let fetcher = TickerFetcher::new()?;

        Ok(Self {
            config,
            fetcher,
            stats: SyncStats::new(),
        })
    }

    /// Synchronize all intervals for all tickers
    pub async fn sync_all_intervals(&mut self, debug: bool) -> Result<(), Error> {
        let start_time = Instant::now();

        // Load tickers from ticker_group.json or use debug list
        let tickers = if debug {
            println!("🐛 Using debug ticker list: VNINDEX, VIC, VCB");
            vec!["VNINDEX".to_string(), "VIC".to_string(), "VCB".to_string()]
        } else {
            self.load_tickers()?
        };

        println!("\n🚀 Starting data sync: {} tickers, {} intervals",
            tickers.len(),
            self.config.intervals.len()
        );

        println!("📅 Date range: {} to {}", self.config.start_date, self.config.end_date);
        println!("📊 Mode: {}", if self.config.force_full { "FULL DOWNLOAD" } else { "RESUME (incremental)" });

        // Process each interval
        for interval in &self.config.intervals.clone() {
            println!("\n{}", "=".repeat(70));
            println!("📊 Interval: {} ({})", interval.to_vci_format(), self.interval_name(*interval));
            println!("{}", "=".repeat(70));

            self.sync_interval(&tickers, *interval).await?;
        }

        let total_time = start_time.elapsed();

        // Print final summary
        self.print_final_summary(total_time);

        Ok(())
    }

    /// Synchronize a single interval for all tickers
    async fn sync_interval(&mut self, tickers: &[String], interval: Interval) -> Result<(), Error> {
        let interval_start_time = Instant::now();

        // Categorize tickers (resume vs full history)
        let categorize_start = Instant::now();
        let category = self.fetcher.categorize_tickers(tickers, interval)?;
        println!("⏱️  Categorization took: {:.2}s", categorize_start.elapsed().as_secs_f64());

        let batch_size = self.config.get_batch_size(interval);

        // Batch fetch resume tickers using adaptive date (min of all last dates)
        let batch_start = Instant::now();
        let resume_results = if !category.resume_tickers.is_empty() {
            let resume_ticker_names = category.get_resume_ticker_names();

            // Try adaptive mode first (read from CSV dates)
            let (fetch_start_date, is_adaptive) = match category.get_min_resume_date() {
                Some(date) => {
                    (date, true) // ✅ Adaptive: using actual CSV dates
                }
                None => {
                    (self.config.get_fetch_start_date(interval), false) // ⚠️ Fallback: using fixed days
                }
            };

            if is_adaptive {
                println!("\n⚡ Batch processing {} tickers using ADAPTIVE resume mode...", resume_ticker_names.len());
                println!("   📅 Fetching from {} (earliest last date from CSV files)", fetch_start_date);
            } else {
                println!("\n⚠️  Batch processing {} tickers using FALLBACK mode (CSV read failed)...", resume_ticker_names.len());
                println!("   📅 Fetching from {} (using {} day fallback)", fetch_start_date, interval.default_resume_days());
            }

            self.fetcher
                .batch_fetch(
                    &resume_ticker_names,
                    &fetch_start_date,
                    &self.config.end_date,
                    interval,
                    batch_size,
                )
                .await?
        } else {
            HashMap::new()
        };
        println!("⏱️  Batch fetching took: {:.2}s", batch_start.elapsed().as_secs_f64());

        // Batch fetch full history tickers (all intervals support batch API, use smaller batch size)
        let full_history_start = Instant::now();
        let full_history_results = if !category.full_history_tickers.is_empty() {
            println!("\n🚀 Processing {} tickers needing full history...", category.full_history_tickers.len());
            self.fetcher
                .batch_fetch(
                    &category.full_history_tickers,
                    &self.config.start_date,
                    &self.config.end_date,
                    interval,
                    2, // Smaller batch size for full downloads
                )
                .await?
        } else {
            if !category.full_history_tickers.is_empty() {
                println!(
                    "\n🚀 Full history: {} tickers (will fetch individually for {} interval)",
                    category.full_history_tickers.len(),
                    interval.to_vci_format()
                );
            }
            HashMap::new()
        };
        if !category.full_history_tickers.is_empty() {
            println!("⏱️  Full history batch fetching took: {:.2}s", full_history_start.elapsed().as_secs_f64());
        }

        // Combine batch results
        let mut batch_results = resume_results;
        batch_results.extend(full_history_results);

        // Process each ticker with fallback strategy
        println!("\n🔄 Processing individual tickers with fallback strategy...");
        let processing_start = Instant::now();

        let total_tickers = tickers.len();

        for (i, ticker) in tickers.iter().enumerate() {
            let ticker_start_time = Instant::now();
            let current = i + 1;

            let result = self
                .process_ticker(ticker, interval, &batch_results, &category)
                .await;

            let ticker_elapsed = ticker_start_time.elapsed();

            // Build compact progress line
            let mut progress = FetchProgress::new(current, total_tickers, ticker.clone(), interval);
            progress.update_timing(ticker_elapsed, interval_start_time.elapsed());

            match result {
                Ok(data) => {
                    // Save to CSV
                    let save_start = Instant::now();
                    self.save_ticker_data(ticker, &data, interval)?;
                    let save_elapsed = save_start.elapsed();

                    self.stats.successful += 1;
                    self.stats.files_written += 1;
                    self.stats.total_records += data.len();

                    // Compact success line with progress
                    print!("\r✅ {} | {} records", progress.format_compact(), data.len());

                    // Only show timing if slow (> 0.1s)
                    if save_elapsed.as_secs_f64() > 0.1 {
                        print!(" | {:.2}s", save_elapsed.as_secs_f64());
                    }
                    println!(); // New line after progress
                }
                Err(e) => {
                    self.stats.failed += 1;
                    println!("\r❌ {} | FAILED: {}", progress.format_compact(), e);
                }
            }
        }

        println!("⏱️  Individual processing took: {:.2}s", processing_start.elapsed().as_secs_f64());

        let interval_time = interval_start_time.elapsed();
        println!(
            "\n✨ {} sync complete: {} tickers in {:.1}min",
            self.interval_name(interval),
            total_tickers,
            interval_time.as_secs_f64() / 60.0
        );

        Ok(())
    }

    /// Process a single ticker with fallback strategy
    async fn process_ticker(
        &mut self,
        ticker: &str,
        interval: Interval,
        batch_results: &HashMap<String, Option<Vec<OhlcvData>>>,
        category: &crate::models::TickerCategory,
    ) -> Result<Vec<OhlcvData>, Error> {
        // Check if ticker is in resume mode (has last date)
        let ticker_last_date = category.resume_tickers
            .iter()
            .find(|(t, _)| t == ticker)
            .map(|(_, date)| date.clone());

        let is_resume = ticker_last_date.is_some();

        // Check if we have batch result
        if let Some(Some(batch_data)) = batch_results.get(ticker) {
            // Using batch result for ticker

            // For resume tickers, check dividend and merge
            if is_resume {
                return self
                    .smart_dividend_check_and_merge(ticker, batch_data, interval)
                    .await;
            } else {
                // Full history ticker - return batch data directly
                return Ok(batch_data.clone());
            }
        }

        // No batch result - fetch individually
        println!("   🔄 Batch not available for {}, fetching individually...", ticker);

        if is_resume {
            // Resume mode: fetch from last date in file
            let fetch_start = ticker_last_date.unwrap();
            let recent_data = self
                .fetcher
                .fetch_full_history(ticker, &fetch_start, &self.config.end_date, interval)
                .await?;

            self.smart_dividend_check_and_merge(ticker, &recent_data, interval)
                .await
        } else {
            // Full history mode: fetch complete data
            self.fetcher
                .fetch_full_history(ticker, &self.config.start_date, &self.config.end_date, interval)
                .await
        }
    }

    /// Smart dividend detection and data merging (OPTIMIZED - no extra API call!)
    async fn smart_dividend_check_and_merge(
        &mut self,
        ticker: &str,
        recent_data: &[OhlcvData],
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        // Check for dividend using the data we already have (NO API call!)
        let dividend_detected = self.fetcher.check_dividend_from_data(ticker, recent_data, interval)?;

        if dividend_detected {
            println!("   💰 Dividend detected, re-downloading full history...");
            self.stats.updated += 1;

            return self
                .fetcher
                .fetch_full_history(ticker, &self.config.start_date, &self.config.end_date, interval)
                .await;
        }

        // No dividend - merge with existing data
        let file_path = self.get_ticker_file_path(ticker, interval);

        if !file_path.exists() {
            // No existing file - return recent data as-is
            return Ok(recent_data.to_vec());
        }

        // Read and merge existing data
        let existing_data = self.read_existing_data(&file_path)?;
        let merged_data = self.merge_data(existing_data, recent_data.to_vec());

        self.stats.updated += 1;
        Ok(merged_data)
    }

    /// Merge existing data with new data (update last row + append new)
    fn merge_data(&self, existing: Vec<OhlcvData>, new: Vec<OhlcvData>) -> Vec<OhlcvData> {
        if existing.is_empty() {
            return new;
        }

        if new.is_empty() {
            return existing;
        }

        // Find latest date in existing data
        let latest_existing_time = existing.iter().map(|d| d.time).max().unwrap();

        // Filter existing data to remove any dates >= latest_existing_time
        // (we'll replace with fresh data from API)
        let mut merged: Vec<OhlcvData> = existing
            .into_iter()
            .filter(|d| d.time < latest_existing_time)
            .collect();

        // Add all new data that is >= latest_existing_time (includes update to last row)
        let new_rows: Vec<OhlcvData> = new
            .into_iter()
            .filter(|d| d.time >= latest_existing_time)
            .collect();

        merged.extend(new_rows);

        // Sort by time
        merged.sort_by(|a, b| a.time.cmp(&b.time));

        merged
    }

    /// Save ticker data to CSV file
    fn save_ticker_data(
        &self,
        ticker: &str,
        data: &[OhlcvData],
        interval: Interval,
    ) -> Result<(), Error> {
        if data.is_empty() {
            return Err(Error::InvalidInput("No data to save".to_string()));
        }

        // Create ticker directory
        let ticker_dir = Path::new(MARKET_DATA_DIR).join(ticker);
        fs::create_dir_all(&ticker_dir)
            .map_err(|e| Error::Io(format!("Failed to create directory: {}", e)))?;

        // Get file path
        let file_path = ticker_dir.join(interval.to_filename());

        // Write CSV
        let mut wtr = csv::Writer::from_path(&file_path)
            .map_err(|e| Error::Io(format!("Failed to create CSV writer: {}", e)))?;

        // Write header
        wtr.write_record(&["ticker", "time", "open", "high", "low", "close", "volume"])
            .map_err(|e| Error::Io(format!("Failed to write header: {}", e)))?;

        // Write data rows (API values are already in correct format - store as-is)
        for row in data {
            // Format timestamp based on interval
            let time_str = match interval {
                Interval::Daily => row.time.format("%Y-%m-%d").to_string(),
                Interval::Hourly | Interval::Minute => row.time.format("%Y-%m-%d %H:%M:%S").to_string(),
            };

            wtr.write_record(&[
                ticker,
                &time_str,
                &format!("{:.2}", row.open),
                &format!("{:.2}", row.high),
                &format!("{:.2}", row.low),
                &format!("{:.2}", row.close),
                &row.volume.to_string(),
            ])
            .map_err(|e| Error::Io(format!("Failed to write row: {}", e)))?;
        }

        wtr.flush()
            .map_err(|e| Error::Io(format!("Failed to flush CSV: {}", e)))?;

        Ok(())
    }

    /// Read existing data from CSV file
    fn read_existing_data(&self, file_path: &Path) -> Result<Vec<OhlcvData>, Error> {
        let mut reader = csv::Reader::from_path(file_path)
            .map_err(|e| Error::Io(format!("Failed to open CSV: {}", e)))?;

        let mut data = Vec::new();

        for result in reader.records() {
            let record = result.map_err(|e| Error::Parse(format!("Failed to parse CSV: {}", e)))?;

            if record.len() < 7 {
                continue;
            }

            // Parse time
            let time_str = &record[1];
            let time = self.parse_time(time_str)?;

            let ohlcv = OhlcvData {
                time,
                open: record[2]
                    .parse()
                    .map_err(|e| Error::Parse(format!("Invalid open: {}", e)))?,
                high: record[3]
                    .parse()
                    .map_err(|e| Error::Parse(format!("Invalid high: {}", e)))?,
                low: record[4]
                    .parse()
                    .map_err(|e| Error::Parse(format!("Invalid low: {}", e)))?,
                close: record[5]
                    .parse()
                    .map_err(|e| Error::Parse(format!("Invalid close: {}", e)))?,
                volume: record[6]
                    .parse()
                    .map_err(|e| Error::Parse(format!("Invalid volume: {}", e)))?,
                symbol: None,
            };

            data.push(ohlcv);
        }

        Ok(data)
    }

    /// Parse time from string (supports multiple formats)
    fn parse_time(&self, time_str: &str) -> Result<DateTime<Utc>, Error> {
        // Try RFC3339 first
        if let Ok(dt) = DateTime::parse_from_rfc3339(time_str) {
            return Ok(dt.with_timezone(&Utc));
        }

        // Try datetime format "YYYY-MM-DD HH:MM:SS"
        if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(time_str, "%Y-%m-%d %H:%M:%S") {
            return Ok(dt.and_utc());
        }

        // Try date only format "YYYY-MM-DD"
        let date = NaiveDate::parse_from_str(time_str, "%Y-%m-%d")
            .map_err(|e| Error::Parse(format!("Invalid date format: {}", e)))?;

        Ok(date
            .and_hms_opt(0, 0, 0)
            .ok_or_else(|| Error::Parse("Failed to set time".to_string()))?
            .and_utc())
    }

    /// Load tickers from ticker_group.json
    fn load_tickers(&self) -> Result<Vec<String>, Error> {
        let content = fs::read_to_string(TICKER_GROUP_FILE)
            .map_err(|e| Error::Io(format!("Failed to read ticker_group.json: {}", e)))?;

        let ticker_groups: TickerGroups = serde_json::from_str(&content)
            .map_err(|e| Error::Parse(format!("Failed to parse ticker_group.json: {}", e)))?;

        let mut tickers: Vec<String> = ticker_groups
            .groups
            .values()
            .flat_map(|vec| vec.clone())
            .collect();

        // Add VNINDEX and VN30 if not already present
        if !tickers.contains(&"VNINDEX".to_string()) {
            tickers.insert(0, "VNINDEX".to_string());
        }
        if !tickers.contains(&"VN30".to_string()) {
            tickers.insert(1, "VN30".to_string());
        }

        // Remove duplicates and sort (keep VNINDEX and VN30 first)
        let vnindex = tickers.iter().position(|t| t == "VNINDEX");
        let vn30 = tickers.iter().position(|t| t == "VN30");

        let mut other_tickers: Vec<String> = tickers
            .iter()
            .filter(|t| *t != "VNINDEX" && *t != "VN30")
            .cloned()
            .collect();

        other_tickers.sort();
        other_tickers.dedup();

        let mut sorted_tickers = Vec::new();
        if vnindex.is_some() {
            sorted_tickers.push("VNINDEX".to_string());
        }
        if vn30.is_some() {
            sorted_tickers.push("VN30".to_string());
        }
        sorted_tickers.extend(other_tickers);

        Ok(sorted_tickers)
    }

    /// Get file path for ticker data
    fn get_ticker_file_path(&self, ticker: &str, interval: Interval) -> PathBuf {
        Path::new(MARKET_DATA_DIR)
            .join(ticker)
            .join(interval.to_filename())
    }

    /// Get interval display name
    fn interval_name(&self, interval: Interval) -> &'static str {
        match interval {
            Interval::Daily => "Daily",
            Interval::Hourly => "Hourly",
            Interval::Minute => "Minute",
        }
    }

    /// Print final summary
    fn print_final_summary(&self, total_time: std::time::Duration) {
        println!("\n{}", "=".repeat(70));
        println!("🎉 SYNC COMPLETE!");
        println!("{}", "=".repeat(70));
        println!("⏰ Finished at: {}", Utc::now().format("%Y-%m-%d %H:%M:%S"));
        println!(
            "⏱️  Total execution time: {:.2} minutes ({:.1} seconds)",
            total_time.as_secs_f64() / 60.0,
            total_time.as_secs_f64()
        );
        println!(
            "📊 Results: ✅{} successful, ❌{} failed, 📝{} updated, ⏭️ {} skipped",
            self.stats.successful, self.stats.failed, self.stats.updated, self.stats.skipped
        );
        println!("📁 Files written: {}", self.stats.files_written);
        println!("📈 Total records: {}", self.stats.total_records);
    }
}
