use crate::error::Error;
use crate::models::{Interval, TickerCategory};
use crate::services::vci::{OhlcvData, VciClient};
use crate::utils::{get_market_data_dir, parse_timestamp};
use chrono::{Datelike, NaiveDate};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Duration as StdDuration;
use tracing::{info, warn, error, debug};

/// Ticker data fetcher using VCI client
pub struct TickerFetcher {
    vci_client: VciClient,
}

impl TickerFetcher {
    /// Create new ticker fetcher with VCI client
    pub fn new() -> Result<Self, Error> {
        // Use 60 calls/minute to match Python implementation
        let vci_client = VciClient::new(true, 60)
            .map_err(|e| Error::Config(format!("Failed to create VCI client: {:?}", e)))?;

        Ok(Self { vci_client })
    }

    /// Read the last date from a CSV file (fast - reads only last few KB from end)
    fn read_last_date(&self, file_path: &Path) -> Result<Option<String>, Error> {
        use std::fs::File;
        use std::io::{Seek, SeekFrom, Read};

        let file_size = match std::fs::metadata(file_path) {
            Ok(metadata) => metadata.len(),
            Err(_) => return Ok(None),
        };

        if file_size < 1024 {  // File too small, read normally
            return self.read_last_date_forward(file_path);
        }

        // Read only the last 8KB to find the last date quickly
        let read_size = std::cmp::min(8192, file_size as usize);
        let seek_pos = file_size - read_size as u64;

        let mut file = File::open(file_path)
            .map_err(|e| Error::Io(format!("Failed to open CSV: {}", e)))?;

        file.seek(SeekFrom::Start(seek_pos))
            .map_err(|e| Error::Io(format!("Failed to seek in CSV: {}", e)))?;

        let mut buffer = vec![0u8; read_size];
        file.read_exact(&mut buffer)
            .map_err(|e| Error::Io(format!("Failed to read CSV buffer: {}", e)))?;

        let data = String::from_utf8_lossy(&buffer);
        let mut last_valid_date: Option<String> = None;

        // Process lines backwards (more efficient - stop at first valid date)
        for line in data.lines().rev() {
            let line = line.trim();

            // Skip header and empty lines
            if line.starts_with("ticker") || line.is_empty() {
                continue;
            }

            // Parse CSV line (format: ticker,time,open,high,low,close,volume)
            let parts: Vec<&str> = line.split(',').collect();
            if parts.len() >= 2 {
                let date_str = parts[1].trim();
                // Extract just the date part (YYYY-MM-DD) from datetime strings
                // Handle both space-separated "YYYY-MM-DD HH:MM:SS" and ISO 8601 "YYYY-MM-DDTHH:MM:SS"
                let date = if date_str.contains(' ') {
                    date_str.split(' ').next().unwrap_or(date_str)
                } else if date_str.contains('T') {
                    date_str.split('T').next().unwrap_or(date_str)
                } else {
                    date_str
                };

                // Validate date format (should be YYYY-MM-DD)
                if date.len() == 10 && date.chars().filter(|&c| c == '-').count() == 2 {
                    last_valid_date = Some(date.to_string());
                    break; // Found a valid date, no need to continue
                }
            }
        }

        // If no valid date found in the tail, fall back to reading from start
        if last_valid_date.is_none() && file_size > read_size as u64 {
            return self.read_last_date_forward(file_path);
        }

        Ok(last_valid_date)
    }

    /// Fallback method: read from start (for very small files or malformed data)
    fn read_last_date_forward(&self, file_path: &Path) -> Result<Option<String>, Error> {
        use std::fs::File;
        use std::io::{BufRead, BufReader};

        let file = File::open(file_path)
            .map_err(|e| Error::Io(format!("Failed to open CSV: {}", e)))?;

        let reader = BufReader::new(file);
        let mut last_valid_date: Option<String> = None;

        for line in reader.lines() {
            let line = line.map_err(|e| Error::Io(format!("Failed to read line: {}", e)))?;

            if line.starts_with("ticker") || line.trim().is_empty() {
                continue;
            }

            let parts: Vec<&str> = line.split(',').collect();
            if parts.len() >= 2 {
                let date_str = parts[1].trim();
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

    /// Categorize tickers into resume vs full history based on existing data
    pub fn categorize_tickers(
        &self,
        tickers: &[String],
        interval: Interval,
    ) -> Result<TickerCategory, Error> {
        println!("[DEBUG] categorize_tickers: tickers_count={}, interval={}", tickers.len(), interval.to_vci_format());

        info!(
            tickers_count = tickers.len(),
            interval = interval.to_vci_format(),
            "Pre-scanning tickers to categorize data needs"
        );

        let mut category = TickerCategory::new();
        let total = tickers.len();
        let show_first = 1;
        let show_last = 1;

        for (idx, ticker) in tickers.iter().enumerate() {
            let file_path = self.get_ticker_file_path(ticker, interval);

            // Only print first few and last few
            let should_print = idx < show_first || idx >= total - show_last;

            if idx == show_first && total > show_first + show_last {
                debug!(remaining_tickers = total - show_first - show_last, "... more tickers");
            }

            if !file_path.exists() {
                if should_print {
                    println!("[DEBUG] ticker={}, file does not exist", ticker);
                    debug!(ticker = ticker, file_path = ?file_path, "File does not exist - full history needed");
                }
                category.full_history_tickers.push(ticker.clone());
            } else {
                // File exists - read last date and use resume mode
                match self.read_last_date(&file_path) {
                    Ok(Some(last_date)) => {
                        println!("[DEBUG] ticker={}, last_date={}", ticker, last_date);
                        // Skip stale tickers for minute interval (likely delisted/suspended)
                        if interval == Interval::Minute {
                            use chrono::NaiveDate;
                            use crate::models::STALE_TICKER_THRESHOLD_DAYS;

                            if let Ok(last_date_parsed) = NaiveDate::parse_from_str(&last_date, "%Y-%m-%d") {
                                let today = chrono::Utc::now().date_naive();
                                let days_old = (today - last_date_parsed).num_days();

                                if days_old > STALE_TICKER_THRESHOLD_DAYS {
                                    if should_print {
                                        info!(ticker = ticker, days_old = days_old, last_date = last_date, "Skipping stale ticker");
                                    }
                                    category.skipped_stale_tickers.push((ticker.clone(), last_date.clone(), days_old));
                                    continue; // Skip this ticker
                                }
                            }
                        }

                        // Check if gap exceeds interval-specific threshold - if so, use partial history instead of batch resume
                        // Different intervals have different gap tolerances:
                        // - Daily: 14 days (2 weeks) - handles weekends + holidays efficiently
                        // - Hourly: 7 days (1 week) - reasonable gap for hourly data
                        // - Minute: 3 days - conservative approach for high-frequency data
                        // Can be disabled with DISABLE_PARTIAL_HISTORY=1 for debugging
                        use chrono::NaiveDate;
                        let gap_threshold_days = match interval {
                            crate::models::Interval::Daily => 14,
                            crate::models::Interval::Hourly => 7,
                            crate::models::Interval::Minute => 3,
                        };
                        let disable_partial = std::env::var("DISABLE_PARTIAL_HISTORY").is_ok();

                        if let Ok(last_date_parsed) = NaiveDate::parse_from_str(&last_date, "%Y-%m-%d") {
                            let today = chrono::Utc::now().date_naive();
                            let days_gap = (today - last_date_parsed).num_days();

                            if days_gap > gap_threshold_days && !disable_partial {
                                // Gap too large - batch API won't work, use partial history
                                if should_print {
                                    info!(ticker = ticker, last_date = last_date, days_gap = days_gap, "Partial history from gap");
                                }
                                category.partial_history_tickers.push((ticker.clone(), last_date));
                            } else {
                                // Gap small enough for batch resume (or partial history disabled)
                                if should_print {
                                    if disable_partial && days_gap > gap_threshold_days {
                                        info!(ticker = ticker, last_date = last_date, days_gap = days_gap, "Resume from gap (PARTIAL_HISTORY_DISABLED)");
                                    } else {
                                        debug!(ticker = ticker, last_date = last_date, "Resume from date");
                                    }
                                }
                                category.resume_tickers.push((ticker.clone(), last_date));
                            }
                        } else {
                            // Can't parse date, default to resume
                            if should_print {
                                debug!(ticker = ticker, last_date = last_date, "Resume from unparsed date");
                            }
                            category.resume_tickers.push((ticker.clone(), last_date));
                        }
                    }
                    Ok(None) => {
                        // File exists but no valid data - need full history
                        if should_print {
                            warn!(ticker = ticker, file_path = ?file_path, "File exists but no valid data found - full history needed");
                        }
                        category.full_history_tickers.push(ticker.clone());
                    }
                    Err(e) => {
                        // Error reading file - need full history
                        if should_print {
                            warn!(ticker = ticker, error = %e, file_path = ?file_path, "Error reading file - full history needed");
                        }
                        category.full_history_tickers.push(ticker.clone());
                    }
                }
            }
        }

        info!(resume_tickers = category.resume_tickers.len(),
              full_history_tickers = category.full_history_tickers.len(),
              partial_history_tickers = category.partial_history_tickers.len(),
              skipped_stale_tickers = category.skipped_stale_tickers.len(),
              interval = interval.to_vci_format(),
              "Categorization results");

        println!("[DEBUG] Categorization summary: resume={}, full_history={}, partial={}, skipped={}",
            category.resume_tickers.len(),
            category.full_history_tickers.len(),
            category.partial_history_tickers.len(),
            category.skipped_stale_tickers.len()
        );

        // Log details for each category if not empty
        if !category.partial_history_tickers.is_empty() {
            let threshold = match interval {
                crate::models::Interval::Daily => 14,
                crate::models::Interval::Hourly => 7,
                crate::models::Interval::Minute => 3,
            };
            info!(count = category.partial_history_tickers.len(),
                  gap_threshold_days = threshold,
                  interval = interval.to_vci_format(),
                  "Partial history tickers");
        }

        if !category.skipped_stale_tickers.is_empty() {
            info!(count = category.skipped_stale_tickers.len(),
                  stale_threshold_days = crate::models::STALE_TICKER_THRESHOLD_DAYS,
                  "Skipped stale tickers");
        }

        // Show min/max dates for resume tickers
        if let Some(min_date) = category.get_min_resume_date() {
            info!(earliest_date = min_date, "Resume date for tickers");

            // Show which tickers have the earliest date (are behind)
            let behind_tickers: Vec<String> = category.resume_tickers
                .iter()
                .filter(|(_, date)| date == &min_date)
                .map(|(ticker, _)| ticker.clone())
                .take(5)
                .collect();
            let total_behind = category.resume_tickers
                .iter()
                .filter(|(_, date)| date == &min_date)
                .count();

            if !behind_tickers.is_empty() {
                if behind_tickers.len() < category.resume_tickers.len() {
                    if behind_tickers.len() <= 5 {
                        info!(tickers = ?behind_tickers, count = total_behind, "Behind tickers");
                    } else {
                        info!(tickers = ?behind_tickers[..5], count = total_behind, additional = total_behind - 5, "Behind tickers (some not shown)");
                    }
                } else {
                    info!(date = min_date, "All tickers at same date");
                }
            }
        }

        Ok(category)
    }

    /// Batch fetch data for multiple tickers with concurrent processing
    pub async fn batch_fetch(
        &mut self,
        tickers: &[String],
        start_date: &str,
        end_date: &str,
        interval: Interval,
        batch_size: usize,
        concurrent_batches: usize,
    ) -> Result<HashMap<String, Option<Vec<OhlcvData>>>, Error> {
        println!("[DEBUG] batch_fetch called: tickers_count={}, start_date={}, end_date={}, batch_size={}",
            tickers.len(), start_date, end_date, batch_size);

        if tickers.is_empty() {
            println!("[DEBUG] batch_fetch: tickers is empty, returning empty HashMap");
            return Ok(HashMap::new());
        }

        let concurrent_batches = concurrent_batches.max(1); // At least 1

        info!(ticker_count = tickers.len(),
              interval = interval.to_vci_format(),
              batch_size = batch_size,
              concurrent_batches = concurrent_batches,
              "Processing batch of tickers using VCI batch history");

        let mut all_results = HashMap::new();

        // Split into smaller batches
        let ticker_batches: Vec<Vec<String>> = tickers
            .chunks(batch_size)
            .map(|chunk| chunk.to_vec())
            .collect();

        // Process batches in groups of concurrent_batches
        for (group_idx, batch_group) in ticker_batches.chunks(concurrent_batches).enumerate() {
            let group_start = group_idx * concurrent_batches;

            // Process this group of batches concurrently
            let mut tasks = Vec::new();

            for (i, ticker_batch) in batch_group.iter().enumerate() {
                let batch_idx = group_start + i;
                let ticker_batch = ticker_batch.clone();
                let start_date = start_date.to_string();
                let end_date = end_date.to_string();
                let interval_str = interval.to_vci_format().to_string();

                // Clone VCI client for concurrent use
                let mut vci_client = self.vci_client.clone();

                let task = tokio::spawn(async move {
                    let api_start = std::time::Instant::now();
                    let result = vci_client
                        .get_batch_history(&ticker_batch, &start_date, Some(&end_date), &interval_str)
                        .await;
                    let api_elapsed = api_start.elapsed();

                    (batch_idx, ticker_batch, result, api_elapsed)
                });

                tasks.push(task);
            }

            // Wait for all concurrent batches to complete
            let results = futures::future::join_all(tasks).await;

            // Process results
            let total_batches = ticker_batches.len();
            for task_result in results {
                match task_result {
                    Ok((batch_idx, ticker_batch, api_result, api_elapsed)) => {
                        // Only show first and last batch
                        let show_first = 1;
                        let show_last = 1;
                        let should_print = batch_idx < show_first || batch_idx >= total_batches - show_last;

                        if should_print {
                            info!(batch_num = batch_idx + 1,
                                  total_batches = total_batches,
                                  tickers_count = ticker_batch.len(),
                                  duration_s = api_elapsed.as_secs_f64(),
                                  "Batch completed");
                        } else if batch_idx == show_first {
                            debug!(remaining_batches = total_batches - show_first - show_last, "... more batches");
                        }

                        match api_result {
                            Ok(batch_data) => {
                                // Debug: show what data the API returned
                                println!("[DEBUG] Batch API returned data for {} tickers out of {} requested",
                                    batch_data.len(), ticker_batch.len());

                                // Debug: show first ticker's data structure
                                if let Some(first_ticker) = ticker_batch.first() {
                                    if let Some(data) = batch_data.get(first_ticker) {
                                        println!("[DEBUG] First ticker ({}) data: {:?}", first_ticker,
                                            data.as_ref().map(|d| d.len()));
                                    }
                                }

                                // Process successful batch results
                                for ticker in ticker_batch.iter() {
                                    if let Some(data) = batch_data.get(ticker) {
                                        if let Some(ohlcv_vec) = data {
                                            if !ohlcv_vec.is_empty() {
                                                // Success - store result silently
                                                all_results.insert(ticker.clone(), Some(ohlcv_vec.clone()));
                                            } else {
                                                warn!(ticker = ticker, "Batch failed - empty data");
                                                all_results.insert(ticker.clone(), None);
                                            }
                                        } else {
                                            warn!(
                                                ticker = ticker,
                                                batch_num = batch_idx + 1,
                                                total_batches = total_batches,
                                                batch_size = ticker_batch.len(),
                                                start_date = start_date,
                                                end_date = end_date,
                                                other_tickers_in_batch = ?ticker_batch.iter().filter(|t| t != &ticker).take(5).collect::<Vec<_>>(),
                                                "Batch failed - no data"
                                            );
                                            all_results.insert(ticker.clone(), None);
                                        }
                                    } else {
                                        warn!(ticker = ticker, "Batch failed - ticker not in response");
                                        all_results.insert(ticker.clone(), None);
                                    }
                                }
                            }
                            Err(e) => {
                                warn!(error = ?e, "Batch request error");
                                for ticker in ticker_batch.iter() {
                                    all_results.insert(ticker.clone(), None);
                                }
                            }
                        }
                    }
                    Err(e) => {
                        error!(error = %e, "Task join error");
                    }
                }
            }

            // Small delay between groups (not needed between individual batches in a group)
            if group_idx < ticker_batches.chunks(concurrent_batches).len() - 1 {
                tokio::time::sleep(StdDuration::from_millis(50)).await;
            }
        }

        Ok(all_results)
    }

    /// Fetch full history for a single ticker with chunking for high-frequency data
    pub async fn fetch_full_history(
        &mut self,
        ticker: &str,
        start_date: &str,
        end_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        info!(ticker = ticker,
              start_date = start_date,
              end_date = end_date,
              interval = interval.to_vci_format(),
              "Downloading full history");

        match interval {
            Interval::Hourly => {
                self.fetch_hourly_chunks(ticker, start_date, end_date)
                    .await
            }
            Interval::Minute => {
                self.fetch_minute_chunks(ticker, start_date, end_date)
                    .await
            }
            Interval::Daily => {
                self.fetch_direct(ticker, start_date, end_date, interval)
                    .await
            }
        }
    }

    /// Direct fetch without chunking (for daily data)
    async fn fetch_direct(
        &mut self,
        ticker: &str,
        start_date: &str,
        end_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        let data = self
            .vci_client
            .get_history(ticker, start_date, Some(end_date), interval.to_vci_format())
            .await
            .map_err(|e| Error::Network(format!("VCI fetch failed: {:?}", e)))?;

        // Sleep after API call to respect rate limits
        tokio::time::sleep(StdDuration::from_millis(1500)).await;

        if data.is_empty() {
            return Err(Error::NotFound(format!(
                "No data returned for {}",
                ticker
            )));
        }

        info!(record_count = data.len(), "Downloaded records for full history");
        Ok(data)
    }

    /// Fetch hourly data in yearly chunks
    async fn fetch_hourly_chunks(
        &mut self,
        ticker: &str,
        start_date: &str,
        end_date: &str,
    ) -> Result<Vec<OhlcvData>, Error> {
        info!(ticker = ticker, "Starting chunked hourly download");

        let start_year = NaiveDate::parse_from_str(start_date, "%Y-%m-%d")
            .map_err(|e| Error::InvalidInput(format!("Invalid start date: {}", e)))?
            .year();

        let end_year = NaiveDate::parse_from_str(end_date, "%Y-%m-%d")
            .map_err(|e| Error::InvalidInput(format!("Invalid end date: {}", e)))?
            .year();

        let mut all_chunks = Vec::new();

        for year in start_year..=end_year {
            let year_start = if year == start_year {
                start_date.to_string()
            } else {
                format!("{}-01-01", year)
            };

            let year_end = if year == end_year {
                end_date.to_string()
            } else {
                format!("{}-12-31", year)
            };

            info!(
                "   - Downloading {} chunk: {} to {}",
                year, year_start, year_end
            );

            match self
                .vci_client
                .get_history(ticker, &year_start, Some(&year_end), "1H")
                .await
            {
                Ok(chunk_data) if !chunk_data.is_empty() => {
                    debug!(year = year, record_count = chunk_data.len(), "Downloaded hourly chunk");
                    all_chunks.extend(chunk_data);
                }
                Ok(_) => {
                    debug!(year = year, "No data for year");
                }
                Err(e) => {
                    error!(year = year, error = ?e, "ERROR downloading chunk");
                }
            }

            // Minimal sleep between chunks (VCI client has internal rate limiter)
            tokio::time::sleep(StdDuration::from_millis(50)).await;
        }

        if all_chunks.is_empty() {
            return Err(Error::NotFound(format!(
                "No hourly data downloaded for {}",
                ticker
            )));
        }

        // Sort by time
        all_chunks.sort_by(|a, b| a.time.cmp(&b.time));

        info!(
            "   - Combined {} total records from {} yearly chunks",
            all_chunks.len(),
            (end_year - start_year + 1)
        );

        Ok(all_chunks)
    }

    /// Fetch minute data in monthly chunks
    async fn fetch_minute_chunks(
        &mut self,
        ticker: &str,
        start_date: &str,
        end_date: &str,
    ) -> Result<Vec<OhlcvData>, Error> {
        info!(ticker = ticker, "Starting chunked minute download");

        let start_dt = NaiveDate::parse_from_str(start_date, "%Y-%m-%d")
            .map_err(|e| Error::InvalidInput(format!("Invalid start date: {}", e)))?;

        let end_dt = NaiveDate::parse_from_str(end_date, "%Y-%m-%d")
            .map_err(|e| Error::InvalidInput(format!("Invalid end date: {}", e)))?;

        let mut all_chunks = Vec::new();
        let mut current_dt = start_dt
            .with_day(1)
            .ok_or_else(|| Error::InvalidInput("Failed to set day to 1".to_string()))?;

        while current_dt <= end_dt {
            let year = current_dt.year();
            let month = current_dt.month();

            let month_start = if current_dt.year() == start_dt.year()
                && current_dt.month() == start_dt.month()
            {
                start_date.to_string()
            } else {
                current_dt.format("%Y-%m-%d").to_string()
            };

            // Calculate last day of month
            let next_month = if month == 12 {
                NaiveDate::from_ymd_opt(year + 1, 1, 1)
            } else {
                NaiveDate::from_ymd_opt(year, month + 1, 1)
            }
            .ok_or_else(|| Error::InvalidInput("Failed to calculate next month".to_string()))?;

            let last_day_of_month = next_month.pred_opt().ok_or_else(|| {
                Error::InvalidInput("Failed to calculate last day of month".to_string())
            })?;

            let month_end = if current_dt.year() == end_dt.year()
                && current_dt.month() == end_dt.month()
            {
                end_date.to_string()
            } else {
                last_day_of_month.format("%Y-%m-%d").to_string()
            };

            info!(
                "   - Downloading {}-{:02} chunk: {} to {}",
                year, month, month_start, month_end
            );

            match self
                .vci_client
                .get_history(ticker, &month_start, Some(&month_end), "1m")
                .await
            {
                Ok(chunk_data) if !chunk_data.is_empty() => {
                    info!(
                        "     - Downloaded {} records for {}-{:02}",
                        chunk_data.len(),
                        year,
                        month
                    );
                    all_chunks.extend(chunk_data);
                }
                Ok(_) => {
                    info!("     - No data for {}-{:02}", year, month);
                }
                Err(e) => {
                    info!(
                        "     - ERROR downloading {}-{:02} chunk: {:?}",
                        year, month, e
                    );
                }
            }

            // Minimal sleep between chunks (VCI client has internal rate limiter)
            tokio::time::sleep(StdDuration::from_millis(50)).await;

            // Move to next month
            current_dt = next_month;
        }

        if all_chunks.is_empty() {
            return Err(Error::NotFound(format!(
                "No minute data downloaded for {}",
                ticker
            )));
        }

        // Sort by time
        all_chunks.sort_by(|a, b| a.time.cmp(&b.time));

        let total_months = (end_dt.year() - start_dt.year()) * 12
            + (end_dt.month() as i32 - start_dt.month() as i32)
            + 1;

        info!(
            "   - Combined {} total records from {} monthly chunks",
            all_chunks.len(),
            total_months
        );

        Ok(all_chunks)
    }

    /// Check if dividend was issued using already-fetched data (no extra API call!)
    pub fn check_dividend_from_data(
        &self,
        ticker: &str,
        recent_data: &[OhlcvData],
        interval: Interval,
    ) -> Result<bool, Error> {
        // Skip dividend check for indices (they don't have dividends)
        if ticker == "VNINDEX" || ticker == "VN30" || ticker == "HNX" || ticker == "UPCOM" {
            return Ok(false);
        }

        let file_path = self.get_ticker_file_path(ticker, interval);

        if !file_path.exists() {
            return Ok(false); // No existing data to compare
        }

        // Checking for dividend adjustments (using fetched data)
        let div_start = std::time::Instant::now();

        // Load existing data from CSV
        let existing_data = self.read_ohlcv_from_csv(&file_path)?;

        // Find the most recent date in the new data (last trading day)
        let most_recent_date = recent_data
            .iter()
            .map(|d| d.time.date_naive())
            .max();

        if most_recent_date.is_none() || recent_data.len() < 2 {
            // Not enough data to check
            return Ok(false);
        }

        let last_day = most_recent_date.unwrap();

        // Get OLD days only (exclude the last/most recent day which can still be changing)
        let old_days_new: Vec<&OhlcvData> = recent_data
            .iter()
            .filter(|d| d.time.date_naive() < last_day)
            .collect();

        let old_days_existing: Vec<&OhlcvData> = existing_data
            .iter()
            .filter(|d| d.time.date_naive() < last_day)
            .collect();

        if old_days_new.is_empty() || old_days_existing.is_empty() {
            // No old days to compare (only have today's data)
            return Ok(false);
        }

        // Check price ratios for matching dates in OLD days only
        for new_row in &old_days_new {
            for existing_row in &old_days_existing {
                // Compare dates (same day)
                if new_row.time.date_naive() == existing_row.time.date_naive() {
                    if existing_row.close > 0.0 && new_row.close > 0.0 {
                        let ratio = existing_row.close / new_row.close;

                        // 2% threshold for dividend detection
                        if ratio > 1.02 {
                            let div_elapsed = div_start.elapsed();
                            info!(
                                "   - 💰 DIVIDEND DETECTED for {} on {} (old day): ratio={:.4} (check took {:.3}s)",
                                ticker,
                                new_row.time.format("%Y-%m-%d"),
                                ratio,
                                div_elapsed.as_secs_f64()
                            );
                            return Ok(true);
                        }
                    }
                }
            }
        }

        // No dividend detected
        Ok(false)
    }

    /// Get file path for ticker data
    fn get_ticker_file_path(&self, ticker: &str, interval: Interval) -> PathBuf {
        get_market_data_dir()
            .join(ticker)
            .join(interval.to_filename())
    }

    /// Read OHLCV data from CSV file
    fn read_ohlcv_from_csv(&self, file_path: &Path) -> Result<Vec<OhlcvData>, Error> {
        let mut reader = csv::Reader::from_path(file_path)
            .map_err(|e| Error::Io(format!("Failed to open CSV: {}", e)))?;

        let mut data = Vec::new();

        for result in reader.records() {
            let record = result.map_err(|e| Error::Parse(format!("Failed to parse CSV: {}", e)))?;

            // Assuming CSV format: ticker,time,open,high,low,close,volume,...
            if record.len() < 7 {
                continue;
            }

            let time_str = &record[1];
            let time = parse_timestamp(time_str)?;

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
}
