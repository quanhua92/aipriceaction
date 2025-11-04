use crate::error::Error;
use crate::models::{Interval, TickerCategory};
use crate::services::vci::{OhlcvData, VciClient};
use chrono::{Datelike, NaiveDate};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Duration as StdDuration;

const MARKET_DATA_DIR: &str = "market_data";

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

        for ticker in tickers {
            let file_path = self.get_ticker_file_path(ticker, interval);

            if !file_path.exists() {
                println!("   🆕 {}: No existing file - needs full history", ticker);
                category.full_history_tickers.push(ticker.clone());
            } else {
                // Check if existing data is sufficient
                match self.count_csv_rows(&file_path) {
                    Ok(row_count) if row_count <= 5 => {
                        println!(
                            "   📉 {}: Only {} rows - needs full history",
                            ticker, row_count
                        );
                        category.full_history_tickers.push(ticker.clone());
                    }
                    Ok(row_count) => {
                        println!("   ✅ {}: {} rows - can use resume mode", ticker, row_count);
                        category.resume_tickers.push(ticker.clone());
                    }
                    Err(e) => {
                        println!(
                            "   ❌ {}: Error reading file - needs full history ({})",
                            ticker, e
                        );
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

        Ok(category)
    }

    /// Batch fetch data for multiple tickers
    pub async fn batch_fetch(
        &mut self,
        tickers: &[String],
        start_date: &str,
        end_date: &str,
        interval: Interval,
        batch_size: usize,
    ) -> Result<HashMap<String, Option<Vec<OhlcvData>>>, Error> {
        if tickers.is_empty() {
            return Ok(HashMap::new());
        }

        println!(
            "\n-> Processing batch of {} tickers using VCI batch history [{}]",
            tickers.len(),
            interval.to_vci_format()
        );

        let mut all_results = HashMap::new();

        // Split into smaller batches
        let ticker_batches: Vec<&[String]> = tickers.chunks(batch_size).collect();

        for (batch_idx, ticker_batch) in ticker_batches.iter().enumerate() {
            println!(
                "\n--- Batch {}/{}: {} tickers ---",
                batch_idx + 1,
                ticker_batches.len(),
                ticker_batch.len()
            );
            println!("Tickers: {}", ticker_batch.join(", "));

            let api_start = std::time::Instant::now();
            match self
                .vci_client
                .get_batch_history(ticker_batch, start_date, Some(end_date), interval.to_vci_format())
                .await
            {
                Ok(batch_data) => {
                    // Process successful batch results
                    for ticker in ticker_batch.iter() {
                        if let Some(data) = batch_data.get(ticker) {
                            if let Some(ohlcv_vec) = data {
                                if !ohlcv_vec.is_empty() {
                                    println!("   ✅ Batch success: {} ({} records)", ticker, ohlcv_vec.len());
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
            let api_elapsed = api_start.elapsed();

            // Sleep 1-2 seconds between batches (matching proxy pattern)
            let sleep_ms = 1000 + (rand::random::<u64>() % 1000);
            println!("   ⏱️  API: {:.2}s | Sleep: {:.2}s", api_elapsed.as_secs_f64(), sleep_ms as f64 / 1000.0);
            tokio::time::sleep(StdDuration::from_millis(sleep_ms)).await;
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

            // Sleep between chunks to respect rate limits
            tokio::time::sleep(StdDuration::from_millis(2000)).await;
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

            // Sleep between chunks to respect rate limits (longer for minute data)
            tokio::time::sleep(StdDuration::from_millis(3000)).await;

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

    /// Check if dividend was issued (price adjustment detected)
    pub async fn check_dividend(
        &mut self,
        ticker: &str,
        interval: Interval,
    ) -> Result<bool, Error> {
        let file_path = self.get_ticker_file_path(ticker, interval);

        if !file_path.exists() {
            return Ok(false); // No existing data to compare
        }

        println!("   - Checking for dividend adjustments...");
        let div_start = std::time::Instant::now();

        // Download last 60 days
        let end_date = chrono::Utc::now().format("%Y-%m-%d").to_string();
        let start_date = (chrono::Utc::now() - chrono::Duration::days(60))
            .format("%Y-%m-%d")
            .to_string();

        let api_start = std::time::Instant::now();
        let recent_data = match self
            .vci_client
            .get_history(ticker, &start_date, Some(&end_date), interval.to_vci_format())
            .await
        {
            Ok(data) if !data.is_empty() => data,
            _ => {
                return Ok(false);
            }
        };
        let api_elapsed = api_start.elapsed();

        // Sleep after API call to respect rate limits
        tokio::time::sleep(StdDuration::from_millis(1500)).await;

        // Load existing data
        let existing_data = self.read_ohlcv_from_csv(&file_path)?;

        // Compare overlapping dates (3 weeks ago to 1 week ago window)
        let three_weeks_ago = chrono::Utc::now() - chrono::Duration::days(21);
        let one_week_ago = chrono::Utc::now() - chrono::Duration::days(7);

        let recent_window: Vec<&OhlcvData> = recent_data
            .iter()
            .filter(|d| d.time >= three_weeks_ago && d.time <= one_week_ago)
            .collect();

        let existing_window: Vec<&OhlcvData> = existing_data
            .iter()
            .filter(|d| d.time >= three_weeks_ago && d.time <= one_week_ago)
            .collect();

        if recent_window.len() < 2 || existing_window.len() < 2 {
            return Ok(false);
        }

        // Check price ratios for matching dates
        for recent_row in recent_window.iter().take(3) {
            for existing_row in &existing_window {
                // Compare dates (same day)
                if recent_row.time.date_naive() == existing_row.time.date_naive() {
                    if existing_row.close > 0.0 && recent_row.close > 0.0 {
                        let ratio = existing_row.close / recent_row.close;

                        // 2% threshold for dividend detection
                        if ratio > 1.02 {
                            println!(
                                "   - 💰 DIVIDEND DETECTED for {} on {}: ratio={:.4}",
                                ticker,
                                recent_row.time.format("%Y-%m-%d"),
                                ratio
                            );
                            return Ok(true);
                        }
                    }
                }
            }
        }

        let div_elapsed = div_start.elapsed();
        println!("   - No dividend detected (check took {:.2}s: API {:.2}s + sleep 1.5s)",
            div_elapsed.as_secs_f64(), api_elapsed.as_secs_f64());
        Ok(false)
    }

    /// Get file path for ticker data
    fn get_ticker_file_path(&self, ticker: &str, interval: Interval) -> PathBuf {
        Path::new(MARKET_DATA_DIR)
            .join(ticker)
            .join(interval.to_filename())
    }

    /// Count rows in CSV file (excluding header)
    fn count_csv_rows(&self, file_path: &Path) -> Result<usize, Error> {
        let content = std::fs::read_to_string(file_path)
            .map_err(|e| Error::Io(format!("Failed to read file: {}", e)))?;

        let count = content.lines().count().saturating_sub(1); // Exclude header
        Ok(count)
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
