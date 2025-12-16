use crate::constants::BATCH_FAILURE_THRESHOLD_MINUTES;
use crate::error::Error;
use crate::models::{Interval, SyncConfig, FetchProgress, SyncStats, TickerGroups};
use crate::services::ticker_fetcher::TickerFetcher;
use crate::services::vci::OhlcvData;
use crate::services::csv_enhancer::{enhance_data, save_enhanced_csv_with_changes};
use crate::services::mpsc::TickerUpdate;
use crate::utils::{get_market_data_dir, parse_timestamp, format_date};
use chrono::{DateTime, Utc};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::mpsc::SyncSender;
use std::time::Instant;

const TICKER_GROUP_FILE: &str = "ticker_group.json";

/// High-level data synchronization orchestrator
pub struct DataSync {
    config: SyncConfig,
    fetcher: TickerFetcher,
    stats: SyncStats,
    /// Tracks first failure time for each interval to implement smart fallback
    batch_failure_times: HashMap<Interval, Option<DateTime<Utc>>>,
    /// Optional MPSC channel sender for real-time ticker updates
    channel_sender: Option<SyncSender<TickerUpdate>>,
}

impl DataSync {
    /// Create new data sync orchestrator
    pub fn new(config: SyncConfig) -> Result<Self, Error> {
        let fetcher = TickerFetcher::new()?;

        Ok(Self {
            config,
            fetcher,
            stats: SyncStats::new(),
            batch_failure_times: HashMap::new(),
            channel_sender: None,
        })
    }

    /// Create new data sync orchestrator with MPSC channel support
    pub fn new_with_channel(
        config: SyncConfig,
        channel_sender: Option<SyncSender<TickerUpdate>>,
    ) -> Result<Self, Error> {
        let fetcher = TickerFetcher::new()?;

        Ok(Self {
            config,
            fetcher,
            stats: SyncStats::new(),
            batch_failure_times: HashMap::new(),
            channel_sender,
        })
    }

    /// Check if we should fallback to individual fetches based on failure duration
    /// Returns true if batch has been failing for >= BATCH_FAILURE_THRESHOLD_MINUTES
    fn should_fallback_to_individual(&self, interval: Interval) -> bool {
        if let Some(Some(first_failure)) = self.batch_failure_times.get(&interval) {
            let duration_minutes = (Utc::now() - *first_failure).num_minutes();
            duration_minutes >= BATCH_FAILURE_THRESHOLD_MINUTES
        } else {
            // No failure recorded yet, don't fallback
            false
        }
    }

    /// Record batch failure time for an interval (only if not already recorded)
    fn record_batch_failure(&mut self, interval: Interval) {
        self.batch_failure_times.entry(interval).or_insert(Some(Utc::now()));
    }

    /// Clear batch failure time for an interval (call when batch succeeds)
    fn clear_batch_failure(&mut self, interval: Interval) {
        self.batch_failure_times.insert(interval, None);
    }

    /// Synchronize all intervals for all tickers
    pub async fn sync_all_intervals(&mut self, debug: bool) -> Result<(), Error> {
        let start_time = Instant::now();

        // Load tickers from ticker_group.json or use debug list
        let tickers = if debug {
            println!("SYNC::FAST::🐛 Using debug ticker list: VNINDEX, VIC, VCB");
            vec!["VNINDEX".to_string(), "VIC".to_string(), "VCB".to_string()]
        } else {
            self.load_tickers()?
        };

        // println!("\n🚀 Starting data sync: {} tickers, {} intervals",
        //     tickers.len(),
        //     self.config.intervals.len()
        // );

        // println!("📅 Date range: {} to {}", self.config.start_date, self.config.end_date);
        // println!("📊 Mode: {}", if self.config.force_full { "FULL DOWNLOAD" } else { "RESUME (incremental)" });

        // Process each interval
        for interval in &self.config.intervals.clone() {
            // println!("\n{}", "=".repeat(70));
            // println!("📊 Interval: {} ({})", interval.to_vci_format(), self.interval_name(*interval));
            // println!("{}", "=".repeat(70));

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
        let category = self.fetcher.categorize_tickers(tickers, interval)?;

        // Batch fetch resume tickers using adaptive date
        let resume_results = if !category.resume_tickers.is_empty() {
            let resume_ticker_names = category.get_resume_ticker_names();

            // Always use actual last date from CSV files for adaptive resume
            // Apply resume_days offset for dividend detection
            let fetch_start_date = match category.get_min_resume_date() {
                Some(date) => {
                    // Subtract resume_days to get proper fetch window
                    use chrono::{Duration, Utc};
                    let last_date = chrono::NaiveDate::parse_from_str(&date, "%Y-%m-%d")
                        .unwrap_or_else(|_| Utc::now().naive_utc().date());
                    let start_date = last_date - Duration::days(self.config.resume_days.unwrap_or(2) as i64);
                    start_date.format("%Y-%m-%d").to_string()
                }
                None => self.config.get_effective_start_date(interval),  // New ticker fallback
            };

            // Calculate actual date range for dynamic batch sizing
            let date_range_days = {
                use chrono::NaiveDate;
                let start = NaiveDate::parse_from_str(&fetch_start_date, "%Y-%m-%d")
                    .unwrap_or_else(|_| chrono::Utc::now().naive_utc().date());
                let end = NaiveDate::parse_from_str(&self.config.end_date, "%Y-%m-%d")
                    .unwrap_or_else(|_| chrono::Utc::now().naive_utc().date());
                (end - start).num_days().max(1)
            };

            // Get dynamic batch size based on date range
            let dynamic_batch_size = self.config.get_dynamic_batch_size(interval, date_range_days);

            self.fetcher
                .batch_fetch(
                    &resume_ticker_names,
                    &fetch_start_date,
                    &self.config.end_date,
                    interval,
                    dynamic_batch_size,
                    self.config.concurrent_batches,
                )
                .await?
        } else {
            HashMap::new()
        };
        // println!("⏱️  Batch fetching took: {:.2}s", batch_start.elapsed().as_secs_f64());

        // Track batch API success/failure for smart fallback
        if !category.resume_tickers.is_empty() {
            // Check if batch fetch was successful (has at least one ticker with data)
            let batch_has_data = resume_results.values()
                .any(|result| matches!(result, Some(data) if !data.is_empty()));

            if batch_has_data {
                // Batch succeeded, clear failure time
                self.clear_batch_failure(interval);
            } else {
                // Batch failed (empty or all None), record failure time
                self.record_batch_failure(interval);
            }
        }

        // Fetch full history tickers individually (single-ticker API for complete data)
        let mut full_history_results = HashMap::new();

        if !category.full_history_tickers.is_empty() {
            // For full history, use the interval's minimum start date (2023-09-01 for hourly/minute)
            let full_history_start_date = interval.min_start_date();

            // println!(
            //     "\n🚀 Processing {} tickers needing full history (single-ticker requests from {})...",
            //     category.full_history_tickers.len(),
            //     full_history_start_date
            // );

            for ticker in &category.full_history_tickers {
                // Yield control to allow other tasks to run
                tokio::task::yield_now().await;

                match self.fetcher
                    .fetch_full_history(
                        ticker,
                        full_history_start_date,
                        &self.config.end_date,
                        interval,
                    )
                    .await
                {
                    Ok(data) => {
                        // println!("   ✅ {}: {} records", ticker, data.len());
                        full_history_results.insert(ticker.clone(), Some(data));
                    }
                    Err(e) => {
                        println!("SYNC::FAST::   ❌ {}: Failed - {}", ticker, e);
                        full_history_results.insert(ticker.clone(), None);
                    }
                }
            }

            // println!("⏱️  Full history fetching took: {:.2}s", full_history_start.elapsed().as_secs_f64());
        }

        // Fetch partial history tickers individually (gap > 3 days, batch API won't work)
        let mut partial_history_results = HashMap::new();

        if !category.partial_history_tickers.is_empty() {
            let prefix = self.get_log_prefix(interval);
            println!(
                "\n{}📥 Processing {} tickers with partial history (gap > 3 days)...",
                prefix, category.partial_history_tickers.len()
            );

            for (ticker, start_date) in &category.partial_history_tickers {
                // Yield control to allow other tasks to run
                tokio::task::yield_now().await;

                println!("{}   📥 {} - Fetching from {}...", prefix, ticker, start_date);
                match self.fetcher
                    .fetch_full_history(
                        ticker,
                        start_date,  // Use CSV last date instead of min_start_date
                        &self.config.end_date,
                        interval,
                    )
                    .await
                {
                    Ok(data) => {
                        println!("{}   ✅ {}: {} records", prefix, ticker, data.len());
                        partial_history_results.insert(ticker.clone(), Some(data));
                    }
                    Err(e) => {
                        println!("{}   ❌ {}: Failed - {}", prefix, ticker, e);
                        partial_history_results.insert(ticker.clone(), None);
                    }
                }
            }
        }

        // Combine batch results
        let mut batch_results = resume_results;
        batch_results.extend(full_history_results);
        batch_results.extend(partial_history_results);

        // Process each ticker with fallback strategy
        let total_tickers = tickers.len();

        for (i, ticker) in tickers.iter().enumerate() {
            // Yield control every few tickers to allow other tasks to run
            if i % 5 == 0 {
                tokio::task::yield_now().await;
            }

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
                    // Enhance and save to CSV (single write)
                    self.enhance_and_save_ticker_data(ticker, &data, interval)?;

                    self.stats.successful += 1;
                    self.stats.files_written += 1;
                    self.stats.total_records += data.len();

                    // Compact success line with progress
                    // print!("\r✅ {} | {} records", progress.format_compact(), data.len());

                    // // Only show timing if slow (> 0.1s)
                    // if save_elapsed.as_secs_f64() > 0.1 {
                    //     print!(" | {:.2}s", save_elapsed.as_secs_f64());
                    // }
                    // println!(); // New line after progress
                }
                Err(e) => {
                    // Handle SkipTicker error differently - don't count as failure
                    if matches!(e, Error::SkipTicker(_)) {
                        // Skip silently, don't increment failed counter
                        // This is expected during batch API temporary unavailability
                    } else {
                        self.stats.failed += 1;
                        println!("\r❌ {} | FAILED: {}", progress.format_compact(), e);
                    }
                }
            }
        }

        // Show minimal summary - just one line per sync
        let interval_time = interval_start_time.elapsed();
        let now = format_date(&Utc::now());
        let prefix = self.get_log_prefix(interval);
        println!(
            "{}[{}] ✨ {} sync: {} tickers, {}s, ✅{} ❌{}",
            prefix,
            now,
            self.interval_name(interval),
            total_tickers,
            interval_time.as_secs(),
            self.stats.successful,
            self.stats.failed
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
        // eprintln!("DEBUG [{}:process_ticker]: Starting processing", ticker);

        // Check if ticker is in resume mode OR partial history mode (both need merging)
        let ticker_last_date = category.resume_tickers
            .iter()
            .find(|(t, _)| t == ticker)
            .map(|(_, date)| date.clone())
            .or_else(|| {
                // Also check partial_history_tickers - they have existing data and need merging too
                category.partial_history_tickers
                    .iter()
                    .find(|(t, _)| t == ticker)
                    .map(|(_, date)| date.clone())
            });

        let is_resume = ticker_last_date.is_some();
        // eprintln!("DEBUG [{}:process_ticker]: is_resume={}", ticker, is_resume);

        // Check if we have batch result
        if let Some(Some(batch_data)) = batch_results.get(ticker) {
            // eprintln!("DEBUG [{}:process_ticker]: Using batch result with {} rows", ticker, batch_data.len());
            // if !batch_data.is_empty() {
            //     eprintln!("DEBUG [{}:process_ticker]: Batch first row: {}", ticker, batch_data.first().unwrap().time);
            //     eprintln!("DEBUG [{}:process_ticker]: Batch last row: {}", ticker, batch_data.last().unwrap().time);
            // }

            // For resume tickers, check dividend and merge
            if is_resume {
                return self
                    .smart_dividend_check_and_merge(ticker, batch_data, interval)
                    .await;
            } else {
                // Full history ticker - return batch data directly
                // eprintln!("DEBUG [{}:process_ticker]: Full history ticker, returning batch data directly", ticker);
                return Ok(batch_data.clone());
            }
        }

        // No batch result - check if we should fallback to individual fetch
        // Only fallback after batch has been failing for BATCH_FAILURE_THRESHOLD_MINUTES
        if !self.should_fallback_to_individual(interval) {
            // Batch just started failing, skip this iteration
            return Err(Error::SkipTicker(format!(
                "Batch API unavailable for {}, waiting for {} minutes before fallback",
                ticker, BATCH_FAILURE_THRESHOLD_MINUTES
            )));
        }

        // Batch has been failing for >= 15 minutes, fallback to individual fetch
        let prefix = self.get_log_prefix(interval);
        println!("{}   🔄 Batch not available for {} ({}min), fetching individually...",
                 prefix, ticker, BATCH_FAILURE_THRESHOLD_MINUTES);

        if is_resume {
            // Resume mode: fetch from last date in file
            let fetch_start = ticker_last_date.unwrap();

            // For minute interval, use progressive resume with overlap detection
            let recent_data = if interval == Interval::Minute {
                // Read existing data for overlap verification
                let file_path = self.get_ticker_file_path(ticker, interval);
                let existing_data = if file_path.exists() {
                    self.read_existing_data(&file_path).unwrap_or_default()
                } else {
                    vec![]
                };

                // Use progressive fetch (2 days -> 4 days -> 7 days -> fallback)
                self.progressive_resume_fetch(ticker, &fetch_start, interval, &existing_data)
                    .await?
            } else {
                // For daily/hourly: standard resume
                self.fetcher
                    .fetch_full_history(ticker, &fetch_start, &self.config.end_date, interval)
                    .await?
            };

            self.smart_dividend_check_and_merge(ticker, &recent_data, interval)
                .await
        } else {
            // Full history mode: fetch complete data
            let start_date = self.config.get_effective_start_date(interval);
            // eprintln!("DEBUG [{}:process_ticker]: Full history mode, fetching from {}", ticker, start_date);
            let data = self.fetcher
                .fetch_full_history(ticker, &start_date, &self.config.end_date, interval)
                .await?;

            // eprintln!("DEBUG [{}:process_ticker]: Full history fetched {} rows", ticker, data.len());
            // if !data.is_empty() {
            //     eprintln!("DEBUG [{}:process_ticker]: Full history first row: {}", ticker, data.first().unwrap().time);
            //     eprintln!("DEBUG [{}:process_ticker]: Full history last row: {}", ticker, data.last().unwrap().time);
            // }

            Ok(data)
        }
    }

    /// Progressive resume with adaptive date range expansion for minute interval
    /// Starts with small window (2 days), expands if overlap not found
    async fn progressive_resume_fetch(
        &mut self,
        ticker: &str,
        ticker_last_date: &str,
        interval: Interval,
        existing_data: &[OhlcvData],
    ) -> Result<Vec<OhlcvData>, Error> {
        use crate::models::{MIN_MINUTE_RESUME_DAYS, MID_MINUTE_RESUME_DAYS, MAX_MINUTE_RESUME_DAYS};
        use chrono::{Duration, Utc};

        // Only use progressive resume for minute interval
        if interval != Interval::Minute {
            // For other intervals, use standard resume
            return self.fetcher
                .fetch_full_history(ticker, ticker_last_date, &self.config.end_date, interval)
                .await;
        }

        // Try progressive expansion: 2 days -> 4 days -> 7 days -> fallback
        let resume_attempts = vec![
            MIN_MINUTE_RESUME_DAYS,
            MID_MINUTE_RESUME_DAYS,
            MAX_MINUTE_RESUME_DAYS,
        ];

        let today = Utc::now().format("%Y-%m-%d").to_string();

        for (attempt_num, &days_back) in resume_attempts.iter().enumerate() {
            let fetch_start = (Utc::now() - Duration::days(days_back))
                .format("%Y-%m-%d")
                .to_string();

            // Fetch data for this window
            let new_data = self.fetcher
                .fetch_full_history(ticker, &fetch_start, &today, interval)
                .await?;

            // Check overlap
            if self.verify_overlap(existing_data, &new_data) {
                // Good overlap found!
                if attempt_num > 0 {
                    println!("   ✅ Overlap found with {}-day window", days_back);
                }
                return Ok(new_data);
            } else if attempt_num < resume_attempts.len() - 1 {
                // No overlap, try next expansion
                println!("   ⚠️  Gap detected with {}-day window, expanding to {} days...",
                    days_back, resume_attempts[attempt_num + 1]);
            }
        }

        // All attempts failed, use default fallback (fetch from ticker's last date)
        println!("   ⚠️  Using fallback: fetch from ticker last date ({})", ticker_last_date);
        self.fetcher
            .fetch_full_history(ticker, ticker_last_date, &today, interval)
            .await
    }

    /// Smart dividend detection and data merging (OPTIMIZED - no extra API call!)
    async fn smart_dividend_check_and_merge(
        &mut self,
        ticker: &str,
        recent_data: &[OhlcvData],
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        // IMPORTANT: Only check for dividends in DAILY interval
        // For hourly/minute: just merge data without dividend checking
        if interval != Interval::Daily {
            // For non-daily intervals: check if CSV exists, if not it means
            // this ticker was marked for re-download due to dividend in daily pull
            let file_path = self.get_ticker_file_path(ticker, interval);

            if !file_path.exists() {
                // No existing file - download full history
                println!("   📊 No existing {} file, downloading full history...", interval.to_filename());
                let start_date = interval.min_start_date();
                return self
                    .fetcher
                    .fetch_full_history(ticker, start_date, &self.config.end_date, interval)
                    .await;
            }

            // File exists - merge with existing data
            let existing_data = self.read_existing_data(&file_path)?;
            let merged_data = self.merge_data(existing_data, recent_data.to_vec());
            self.stats.updated += 1;
            return Ok(merged_data);
        }

        // DAILY interval: Check for dividend using the data we already have (NO API call!)
        let dividend_detected = self.fetcher.check_dividend_from_data(ticker, recent_data, interval)?;

        if dividend_detected {
            println!("   💰 Dividend detected! Deleting ALL CSV files for {} to force re-download...", ticker);
            self.stats.updated += 1;

            // Delete ALL CSV files for this ticker (1D.csv, 1H.csv, 1m.csv)
            let ticker_dir = get_market_data_dir().join(ticker);
            for interval_type in [Interval::Daily, Interval::Hourly, Interval::Minute] {
                let csv_path = ticker_dir.join(interval_type.to_filename());
                if csv_path.exists() {
                    if let Err(e) = std::fs::remove_file(&csv_path) {
                        eprintln!("   ⚠️  Failed to delete {}: {}", csv_path.display(), e);
                    } else {
                        println!("   🗑️  Deleted {}", interval_type.to_filename());
                    }
                }
            }

            // Re-download daily data with full history
            let start_date = interval.min_start_date();
            return self
                .fetcher
                .fetch_full_history(ticker, start_date, &self.config.end_date, interval)
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

    /// Verify overlap between existing and new data
    /// Returns true if data is continuous (no gap), false if gap detected
    fn verify_overlap(&self, existing: &[OhlcvData], new: &[OhlcvData]) -> bool {
        if existing.is_empty() || new.is_empty() {
            return true; // No gap possible
        }

        // Get latest time from existing data
        let latest_existing = existing.iter().map(|d| d.time).max().unwrap();

        // Get earliest time from new data
        let earliest_new = new.iter().map(|d| d.time).min().unwrap();

        // Check if there's overlap or continuity
        // New data should start at or before the latest existing timestamp
        earliest_new <= latest_existing
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

    /// Enhance and save ticker data to CSV in a single write operation
    fn enhance_and_save_ticker_data(
        &self,
        ticker: &str,
        data: &[OhlcvData],
        interval: Interval,
    ) -> Result<(), Error> {
        if data.is_empty() {
            return Err(Error::InvalidInput("No data to save".to_string()));
        }

        // Calculate cutoff date based on LAST CSV DATE minus resume_days
        // This ensures we save all gap-filling data, not just recent data
        let resume_days = self.config.resume_days.unwrap_or(2) as i64;
        let file_path = self.get_ticker_file_path(ticker, interval);
        let cutoff_date = if file_path.exists() {
            // Read last date from CSV
            if let Ok(existing_data) = self.read_existing_data(&file_path) {
                if let Some(last_record) = existing_data.last() {
                    last_record.time - chrono::Duration::days(resume_days)
                } else {
                    // Empty CSV - save everything
                    chrono::DateTime::from_timestamp(0, 0).unwrap_or_else(|| Utc::now())
                }
            } else {
                // Failed to read - save everything to be safe
                chrono::DateTime::from_timestamp(0, 0).unwrap_or_else(|| Utc::now())
            }
        } else {
            // No CSV file - save everything (new ticker)
            chrono::DateTime::from_timestamp(0, 0).unwrap_or_else(|| Utc::now())
        };

        // Find cutoff index: where data.time >= cutoff_date
        let cutoff_index = data.iter()
            .position(|d| d.time >= cutoff_date)
            .unwrap_or(data.len());

        // Optimization: Only enhance records near cutoff with MA buffer
        // Take 200 records before cutoff (for MA200 context) + all records after cutoff
        const MA_BUFFER: usize = 200;
        let start_index = cutoff_index.saturating_sub(MA_BUFFER);
        let sliced_data = &data[start_index..];

        // eprintln!("DEBUG [{}]: Total records: {}, Cutoff index: {}, Sliced: {} (from {})",
        //     ticker, data.len(), cutoff_index, sliced_data.len(), start_index);

        // Create HashMap for single ticker data (required by enhance_data)
        let mut ticker_data = HashMap::new();
        ticker_data.insert(ticker.to_string(), sliced_data.to_vec());

        // Enhance only the sliced portion (massive performance gain for minute data)
        let enhanced = enhance_data(ticker_data);

        // Save enhanced data to CSV with change detection and real-time updates
        // Only records >= cutoff_date will be written to CSV
        if let Some(stock_data) = enhanced.get(ticker) {
            let market_data_dir = get_market_data_dir();
            let (change_type, record_count) = save_enhanced_csv_with_changes(
                ticker,
                stock_data,
                interval,
                cutoff_date,
                false, // rewrite_all - set to false for incremental
                &market_data_dir,
                self.channel_sender.clone(),
            )?;

            // Log concise change type without full record details
            let change_summary = match &change_type {
                crate::services::mpsc::ChangeType::NoChange => "NoChange".to_string(),
                crate::services::mpsc::ChangeType::NewRecords { records } => format!("NewRecords({})", records.len()),
                crate::services::mpsc::ChangeType::Truncated { from_record, new_records } => format!("Truncated(from:{}, new:{})", from_record, new_records.len()),
                crate::services::mpsc::ChangeType::FullFile { records } => format!("FullFile({})", records.len()),
            };

            tracing::info!(
                ticker = ticker,
                interval = ?interval,
                record_count = record_count,
                change_type = %change_summary,
                "Enhanced and saved with change detection"
            );
        }

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
        parse_timestamp(time_str)
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
        get_market_data_dir()
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

    /// Get log prefix based on interval type
    fn get_log_prefix(&self, interval: Interval) -> &'static str {
        match interval {
            Interval::Daily => "SYNC::FAST::",
            Interval::Hourly | Interval::Minute => "SYNC::SLOW::",
        }
    }

    /// Print final summary
    fn print_final_summary(&self, _total_time: std::time::Duration) {
        // println!("\n{}", "=".repeat(70));
        // println!("🎉 SYNC COMPLETE!");
        // println!("{}", "=".repeat(70));
        // println!("⏰ Finished at: {}", Utc::now().format("%Y-%m-%d %H:%M:%S"));
        // println!(
        //     "⏱️  Total execution time: {:.2} minutes ({:.1} seconds)",
        //     total_time.as_secs_f64() / 60.0,
        //     total_time.as_secs_f64()
        // );
        // println!(
        //     "📊 Results: ✅{} successful, ❌{} failed, 📝{} updated, ⏭️ {} skipped",
        //     self.stats.successful, self.stats.failed, self.stats.updated, self.stats.skipped
        // );
        // println!("📁 Files written: {}", self.stats.files_written);
        // println!("📈 Total records: {}", self.stats.total_records);
    }

    /// Get current sync statistics
    pub fn get_stats(&self) -> &SyncStats {
        &self.stats
    }
}
