use crate::error::Error;
use crate::models::{Interval, TickerCategory};
use crate::services::vci::{OhlcvData, VciClient};
use crate::utils::get_market_data_dir;
use chrono::{Datelike, NaiveDate};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Duration as StdDuration;

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

    /// Read the last date from a CSV file (efficiently reads only last few lines)
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

            // Parse CSV line (format: ticker,time,open,high,low,close,volume)
            let parts: Vec<&str> = line.split(',').collect();
            if parts.len() >= 2 {
                let date_str = parts[1].trim();
                // Extract just the date part (YYYY-MM-DD) from datetime strings
                let date = if date_str.contains(' ') {
                    date_str.split(' ').next().unwrap_or(date_str)
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
        println!(
            "\n🔍 Pre-scanning {} tickers to categorize data needs for {}...",
            tickers.len(),
            interval.to_vci_format()
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
                println!("   ... ({} more tickers) ...", total - show_first - show_last);
            }

            if !file_path.exists() {
                if should_print {
                    println!("   📄 {} - File does not exist: {:?}", ticker, file_path);
                }
                category.full_history_tickers.push(ticker.clone());
            } else {
                // File exists - read last date and use resume mode
                match self.read_last_date(&file_path) {
                    Ok(Some(last_date)) => {
                        // Skip stale tickers for minute interval (likely delisted/suspended)
                        if interval == Interval::Minute {
                            use chrono::NaiveDate;
                            use crate::models::STALE_TICKER_THRESHOLD_DAYS;

                            if let Ok(last_date_parsed) = NaiveDate::parse_from_str(&last_date, "%Y-%m-%d") {
                                let today = chrono::Utc::now().date_naive();
                                let days_old = (today - last_date_parsed).num_days();

                                if days_old > STALE_TICKER_THRESHOLD_DAYS {
                                    if should_print {
                                        println!("   ⏭️  {} - Skipping: last trade {} days ago ({})", ticker, days_old, last_date);
                                    }
                                    category.skipped_stale_tickers.push((ticker.clone(), last_date.clone(), days_old));
                                    continue; // Skip this ticker
                                }
                            }
                        }

                        // Check if gap is > 3 days - if so, use partial history instead of batch resume
                        // Can be disabled with DISABLE_PARTIAL_HISTORY=1 for debugging
                        use chrono::NaiveDate;
                        let gap_threshold_days = 3;
                        let disable_partial = std::env::var("DISABLE_PARTIAL_HISTORY").is_ok();

                        if let Ok(last_date_parsed) = NaiveDate::parse_from_str(&last_date, "%Y-%m-%d") {
                            let today = chrono::Utc::now().date_naive();
                            let days_gap = (today - last_date_parsed).num_days();

                            if days_gap > gap_threshold_days && !disable_partial {
                                // Gap too large - batch API won't work, use partial history
                                if should_print {
                                    println!("   📥 {} - Partial history from: {} ({} days gap)", ticker, last_date, days_gap);
                                }
                                category.partial_history_tickers.push((ticker.clone(), last_date));
                            } else {
                                // Gap small enough for batch resume (or partial history disabled)
                                if should_print {
                                    if disable_partial && days_gap > gap_threshold_days {
                                        println!("   📄 {} - Resume from: {} ({} days gap, PARTIAL_HISTORY_DISABLED)", ticker, last_date, days_gap);
                                    } else {
                                        println!("   📄 {} - Resume from: {}", ticker, last_date);
                                    }
                                }
                                category.resume_tickers.push((ticker.clone(), last_date));
                            }
                        } else {
                            // Can't parse date, default to resume
                            if should_print {
                                println!("   📄 {} - Resume from: {}", ticker, last_date);
                            }
                            category.resume_tickers.push((ticker.clone(), last_date));
                        }
                    }
                    Ok(None) => {
                        // File exists but no valid data - need full history
                        if should_print {
                            println!("   📄 {} - File exists but no valid data found: {:?}", ticker, file_path);
                        }
                        category.full_history_tickers.push(ticker.clone());
                    }
                    Err(e) => {
                        // Error reading file - need full history
                        if should_print {
                            println!("   📄 {} - Error reading file: {} - {:?}", ticker, e, file_path);
                        }
                        category.full_history_tickers.push(ticker.clone());
                    }
                }
            }
        }

        println!("\n📊 Categorization results:");
        println!(
            "   Resume mode tickers: {}",
            category.resume_tickers.len()
        );
        println!(
            "   Full history tickers: {}",
            category.full_history_tickers.len()
        );
        if !category.partial_history_tickers.is_empty() {
            println!(
                "   Partial history tickers: {} (gap > 3 days)",
                category.partial_history_tickers.len()
            );
        }
        if !category.skipped_stale_tickers.is_empty() {
            println!(
                "   Skipped stale tickers: {} (last trade >{} days ago)",
                category.skipped_stale_tickers.len(),
                crate::models::STALE_TICKER_THRESHOLD_DAYS
            );
        }

        // Show min/max dates for resume tickers
        if let Some(min_date) = category.get_min_resume_date() {
            println!("   Resume from: {} (earliest last date)", min_date);

            // Show which tickers have the earliest date (are behind)
            let behind_tickers: Vec<String> = category.resume_tickers
                .iter()
                .filter(|(_, date)| date == &min_date)
                .map(|(ticker, _)| ticker.clone())
                .take(5)
                .collect();

            if behind_tickers.len() > 0 {
                if behind_tickers.len() < category.resume_tickers.len() {
                    print!("   Behind tickers: ");
                    if behind_tickers.len() <= 5 {
                        println!("{}", behind_tickers.join(", "));
                    } else {
                        println!("{} and {} more", behind_tickers.join(", "),
                            category.resume_tickers.iter()
                                .filter(|(_, date)| date == &min_date)
                                .count() - 5);
                    }
                } else {
                    println!("   All tickers at same date ({})", min_date);
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
        if tickers.is_empty() {
            return Ok(HashMap::new());
        }

        let concurrent_batches = concurrent_batches.max(1); // At least 1

        println!(
            "\n-> Processing batch of {} tickers using VCI batch history [{}]",
            tickers.len(),
            interval.to_vci_format()
        );
        if concurrent_batches > 1 {
            println!("   🚀 Using {} concurrent batch requests", concurrent_batches);
        }

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
                            println!(
                                "\n--- Batch {}/{}: {} tickers | {:.2}s ---",
                                batch_idx + 1,
                                total_batches,
                                ticker_batch.len(),
                                api_elapsed.as_secs_f64()
                            );
                        } else if batch_idx == show_first {
                            println!("   ... ({} more batches) ...", total_batches - show_first - show_last);
                        }

                        match api_result {
                            Ok(batch_data) => {
                                // Process successful batch results
                                for ticker in ticker_batch.iter() {
                                    if let Some(data) = batch_data.get(ticker) {
                                        if let Some(ohlcv_vec) = data {
                                            if !ohlcv_vec.is_empty() {
                                                // Success - store result silently
                                                all_results.insert(ticker.clone(), Some(ohlcv_vec.clone()));
                                            } else {
                                                println!("   ❌ Batch failed: {} (empty data)", ticker);
                                                all_results.insert(ticker.clone(), None);
                                            }
                                        } else {
                                            println!("   ❌ Batch failed: {} (no data)", ticker);
                                            all_results.insert(ticker.clone(), None);
                                        }
                                    } else {
                                        println!("   ❌ Batch failed: {} (not in response)", ticker);
                                        all_results.insert(ticker.clone(), None);
                                    }
                                }
                            }
                            Err(e) => {
                                println!("   ❌ Batch request error: {:?}", e);
                                for ticker in ticker_batch.iter() {
                                    all_results.insert(ticker.clone(), None);
                                }
                            }
                        }
                    }
                    Err(e) => {
                        println!("   ❌ Task join error: {:?}", e);
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
        println!(
            "   - Downloading full history from {} to {} using VCI [{}]...",
            start_date,
            end_date,
            interval.to_vci_format()
        );

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

        println!("   - Downloaded {} records for full history", data.len());
        Ok(data)
    }

    /// Fetch hourly data in yearly chunks
    async fn fetch_hourly_chunks(
        &mut self,
        ticker: &str,
        start_date: &str,
        end_date: &str,
    ) -> Result<Vec<OhlcvData>, Error> {
        println!("   - Starting chunked hourly download for {}", ticker);

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

            println!(
                "   - Downloading {} chunk: {} to {}",
                year, year_start, year_end
            );

            match self
                .vci_client
                .get_history(ticker, &year_start, Some(&year_end), "1H")
                .await
            {
                Ok(chunk_data) if !chunk_data.is_empty() => {
                    println!("     - Downloaded {} records for {}", chunk_data.len(), year);
                    all_chunks.extend(chunk_data);
                }
                Ok(_) => {
                    println!("     - No data for {}", year);
                }
                Err(e) => {
                    println!("     - ERROR downloading {} chunk: {:?}", year, e);
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

        println!(
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
        println!("   - Starting chunked minute download for {}", ticker);

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

            println!(
                "   - Downloading {}-{:02} chunk: {} to {}",
                year, month, month_start, month_end
            );

            match self
                .vci_client
                .get_history(ticker, &month_start, Some(&month_end), "1m")
                .await
            {
                Ok(chunk_data) if !chunk_data.is_empty() => {
                    println!(
                        "     - Downloaded {} records for {}-{:02}",
                        chunk_data.len(),
                        year,
                        month
                    );
                    all_chunks.extend(chunk_data);
                }
                Ok(_) => {
                    println!("     - No data for {}-{:02}", year, month);
                }
                Err(e) => {
                    println!(
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

        println!(
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
                            println!(
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
            let time = chrono::DateTime::parse_from_rfc3339(time_str)
                .or_else(|_| {
                    // Try parsing as datetime "YYYY-MM-DD HH:MM:SS"
                    chrono::NaiveDateTime::parse_from_str(time_str, "%Y-%m-%d %H:%M:%S")
                        .map(|dt| dt.and_utc().into())
                        .map_err(|_| Error::Parse(format!("Invalid datetime: {}", time_str)))
                })
                .or_else(|_| {
                    // Try parsing as date only "YYYY-MM-DD"
                    let date = NaiveDate::parse_from_str(time_str, "%Y-%m-%d")
                        .map_err(|e| Error::Parse(format!("Invalid date: {}", e)))?;
                    Ok(date
                        .and_hms_opt(0, 0, 0)
                        .ok_or_else(|| Error::Parse("Failed to set time".to_string()))?
                        .and_utc()
                        .into())
                })
                .map_err(|e: Error| e)?;

            let ohlcv = OhlcvData {
                time: time.with_timezone(&chrono::Utc),
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
