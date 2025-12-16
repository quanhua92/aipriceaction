//! CSV Enhancement Service
//!
//! Enhances raw OHLCV data with technical indicators in-memory,
//! producing enhanced CSV files with 11 columns including:
//! - Moving averages (MA10, MA20, MA50)
//! - MA scores (percentage deviation from MA)
//! - Close changed and volume changed (percentage change from previous row)

use crate::error::Error;
use crate::models::{Interval, StockData};
use crate::models::indicators::{calculate_sma, calculate_ma_score};
use crate::services::vci::OhlcvData;
use crate::services::mpsc::{TickerUpdate, ChangeType};
use crate::utils::{get_market_data_dir, parse_timestamp, format_date, format_timestamp, deduplicate_ohlcv_by_time, deduplicate_stock_data_by_time};
use chrono::DateTime;
use csv::Writer;
use std::collections::HashMap;
use std::path::Path;
use std::time::{Duration, Instant};
use std::io::{BufRead, BufReader, Seek, SeekFrom};

/// Format price with adaptive precision based on magnitude
/// This is crucial for small-priced cryptocurrencies like BONK (~0.000012)
fn format_adaptive_price(value: f64) -> String {
    if value >= 1.0 {
        format!("{:.2}", value)        // 1.2345 -> 1.23
    } else if value >= 0.01 {
        format!("{:.4}", value)        // 0.1234 -> 0.1234
    } else if value >= 0.0001 {
        format!("{:.6}", value)        // 0.000123 -> 0.000123
    } else {
        format!("{:.8}", value)        // 0.00000123 -> 0.00000123
    }
}

/// Statistics for enhancement operation
#[derive(Debug)]
pub struct EnhancementStats {
    pub tickers: usize,
    pub records: usize,
    pub duration: Duration,
    pub ma_time: Duration,
    pub write_time: Duration,
    pub total_bytes_written: u64,
}

/// Enhance OHLCV data with technical indicators (in-memory)
/// Returns HashMap of enhanced StockData ready to be saved
pub fn enhance_data(
    data: HashMap<String, Vec<OhlcvData>>,
) -> HashMap<String, Vec<StockData>> {
    let mut enhanced: HashMap<String, Vec<StockData>> = HashMap::new();

    for (ticker, ohlcv_vec) in data {
        if ohlcv_vec.is_empty() {
            continue;
        }

        // Convert OhlcvData to StockData
        let mut stock_data: Vec<StockData> = ohlcv_vec
            .iter()
            .map(|d| StockData::new(d.time, ticker.clone(), d.open, d.high, d.low, d.close, d.volume))
            .collect();

        // Calculate moving averages
        let closes: Vec<f64> = stock_data.iter().map(|d| d.close).collect();
        let ma10_values = calculate_sma(&closes, 10);
        let ma20_values = calculate_sma(&closes, 20);
        let ma50_values = calculate_sma(&closes, 50);
        let ma100_values = calculate_sma(&closes, 100);
        let ma200_values = calculate_sma(&closes, 200);

        // Update StockData with MA values and scores
        for (i, stock) in stock_data.iter_mut().enumerate() {
            // Set MA values
            if ma10_values[i] > 0.0 {
                stock.ma10 = Some(ma10_values[i]);
                stock.ma10_score = Some(calculate_ma_score(stock.close, ma10_values[i]));
            }
            if ma20_values[i] > 0.0 {
                stock.ma20 = Some(ma20_values[i]);
                stock.ma20_score = Some(calculate_ma_score(stock.close, ma20_values[i]));
            }
            if ma50_values[i] > 0.0 {
                stock.ma50 = Some(ma50_values[i]);
                stock.ma50_score = Some(calculate_ma_score(stock.close, ma50_values[i]));
            }
            if ma100_values[i] > 0.0 {
                stock.ma100 = Some(ma100_values[i]);
                stock.ma100_score = Some(calculate_ma_score(stock.close, ma100_values[i]));
            }
            if ma200_values[i] > 0.0 {
                stock.ma200 = Some(ma200_values[i]);
                stock.ma200_score = Some(calculate_ma_score(stock.close, ma200_values[i]));
            }
        }

        // Calculate close_changed and volume_changed in a second pass to avoid borrow checker issues
        for i in 1..stock_data.len() {
            let prev_close = stock_data[i - 1].close;
            let prev_volume = stock_data[i - 1].volume;
            let curr = &mut stock_data[i];

            // Close changed: ((curr - prev) / prev) * 100
            if prev_close > 0.0 {
                curr.close_changed = Some(((curr.close - prev_close) / prev_close) * 100.0);
            }

            // Volume changed: ((curr - prev) / prev) * 100
            if prev_volume > 0 {
                curr.volume_changed = Some(((curr.volume as f64 - prev_volume as f64) / prev_volume as f64) * 100.0);
            }

            // Total money changed: (price_change × volume) in VND
            // This represents the absolute money flow in Vietnamese Dong
            let price_change = curr.close - prev_close;  // Absolute price change in VND
            curr.total_money_changed = Some(price_change * curr.volume as f64);  // Total money in VND
        }

        enhanced.insert(ticker, stock_data);
    }

    enhanced
}

/// Save enhanced stock data to CSV with smart cutoff strategy and file locking
///
/// This function uses the efficient cutoff strategy from data_sync.rs:
/// - For existing files: truncate to cutoff_date, then append new data
/// - For new files: create with all data
/// - Uses file locking to prevent race conditions
pub fn save_enhanced_csv(
    ticker: &str,
    data: &[StockData],
    interval: Interval,
    cutoff_date: DateTime<chrono::Utc>,
    rewrite_all: bool,
) -> Result<(), Error> {
    save_enhanced_csv_to_dir(ticker, data, interval, cutoff_date, rewrite_all, &get_market_data_dir())
}

/// Save enhanced CSV to a specific directory (for crypto_data support)
pub fn save_enhanced_csv_to_dir(
    ticker: &str,
    data: &[StockData],
    interval: Interval,
    cutoff_date: DateTime<chrono::Utc>,
    rewrite_all: bool,
    base_dir: &Path,
) -> Result<(), Error> {
    if data.is_empty() {
        return Err(Error::InvalidInput("No data to save".to_string()));
    }

    // Deduplicate data before writing (favor last occurrence)
    // Create mutable copy, sort, and deduplicate
    let mut data_vec: Vec<StockData> = data.to_vec();
    data_vec.sort_by_key(|d| d.time);
    let duplicates_removed = deduplicate_stock_data_by_time(&mut data_vec);

    // Check if we should do full rewrite due to duplicates
    let has_excessive_duplicates = duplicates_removed > 0;

    if duplicates_removed > 0 {
        tracing::info!(
            ticker = ticker,
            interval = ?interval,
            duplicates_removed = duplicates_removed,
            records_remaining = data_vec.len(),
            "Deduplicated StockData before writing CSV"
        );
    }

    // Use deduplicated data for all writes below
    let data = &data_vec;

    // Create ticker directory
    let ticker_dir = base_dir.join(ticker);
    std::fs::create_dir_all(&ticker_dir)
        .map_err(|e| Error::Io(format!("Failed to create directory: {}", e)))?;

    // Get file path
    let file_path = ticker_dir.join(interval.to_filename());
    let file_exists = file_path.exists();

    // Force full rewrite if duplicates detected, regardless of rewrite_all parameter
    let should_rewrite_all = !file_exists || rewrite_all || has_excessive_duplicates;

    if should_rewrite_all {
        // Log if we're fixing duplicates
        if has_excessive_duplicates && file_exists {
            tracing::info!(
                ticker = ticker,
                interval = ?interval,
                duplicates_found = duplicates_removed,
                records_after_dedup = data.len(),
                "[FIX-DUPLICATION] Duplicates detected - performing full rewrite"
            );
        }

        // New file or rewrite - write directly (no locking needed for new files)
        let file = std::fs::OpenOptions::new()
            .create(true)
            .truncate(true)  // Always truncate when we decide to rewrite
            .write(true)
            .open(&file_path)
            .map_err(|e| Error::Io(format!("Failed to create file: {}", e)))?;

        let mut wtr = csv::Writer::from_writer(file);

        // Write 20-column header
        wtr.write_record(&[
            "ticker", "time", "open", "high", "low", "close", "volume",
            "ma10", "ma20", "ma50", "ma100", "ma200",
            "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
            "close_changed", "volume_changed", "total_money_changed"
        ])
        .map_err(|e| Error::Io(format!("Failed to write header: {}", e)))?;

        for row in data {
            write_stock_data_row(&mut wtr, row, ticker, interval)?;
        }

        wtr.flush()
            .map_err(|e| Error::Io(format!("Failed to flush CSV: {}", e)))?;
    } else if has_excessive_duplicates {
        // File exists AND has duplicates - perform safe full rewrite
        // This is safer than truncation when we know there are data issues

        // Generate unique processing filename with timestamp
        use std::time::SystemTime;
        let timestamp = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .map_err(|e| Error::Io(format!("Failed to get timestamp: {}", e)))?
            .as_secs();

        let processing_path = file_path.with_extension(format!("{}.processing.{}",
            file_path.extension().and_then(|s| s.to_str()).unwrap_or("csv"), timestamp));

        // Log the safe full rewrite due to duplicates
        tracing::info!(
            ticker = ticker,
            interval = ?interval,
            duplicates_found = duplicates_removed,
            records_after_dedup = data.len(),
            processing_path = ?processing_path,
            "[SAFE-FULL-REWRITE] Duplicates detected - skipping truncation, rewriting entire file"
        );

        // Write header and all deduplicated data directly to processing file
        let enhancement_result = (|| -> Result<(), Error> {
            let file = std::fs::OpenOptions::new()
                .create(true)
                .write(true)
                .open(&processing_path)
                .map_err(|e| Error::Io(format!("Failed to create processing file: {}", e)))?;

            let mut wtr = csv::Writer::from_writer(file);

            // Write 20-column header
            wtr.write_record(&[
                "ticker", "time", "open", "high", "low", "close", "volume",
                "ma10", "ma20", "ma50", "ma100", "ma200",
                "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
                "close_changed", "volume_changed", "total_money_changed"
            ])
            .map_err(|e| Error::Io(format!("Failed to write header: {}", e)))?;

            // Write ALL deduplicated data (no cutoff filtering)
            for row in data {
                write_stock_data_row(&mut wtr, row, ticker, interval)?;
            }

            wtr.flush()
                .map_err(|e| Error::Io(format!("Failed to flush processing CSV: {}", e)))?;

            Ok(())
        })();

        // Handle result - atomic rename on success, cleanup on failure
        match enhancement_result {
            Ok(()) => {
                // Atomic rename: processing file becomes the new original
                std::fs::rename(&processing_path, &file_path)
                    .map_err(|e| Error::Io(format!("Failed to atomically rename processing file: {}", e)))?;

                tracing::debug!(
                    ticker = ticker,
                    interval = ?interval,
                    "Successfully performed safe full rewrite to eliminate duplicates"
                );
            }
            Err(e) => {
                // Enhancement failed - keep original, remove processing file
                let _ = std::fs::remove_file(&processing_path);
                return Err(e);
            }
        }
    } else {
        // File exists, no duplicates - use copy-processing-rename strategy for incremental updates

        // Generate unique processing filename with timestamp
        use std::time::SystemTime;
        let timestamp = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .map_err(|e| Error::Io(format!("Failed to get timestamp: {}", e)))?
            .as_secs();

        let processing_path = file_path.with_extension(format!("{}.processing.{}",
            file_path.extension().and_then(|s| s.to_str()).unwrap_or("csv"), timestamp));

        // Step 1: Copy original file to processing file
        std::fs::copy(&file_path, &processing_path)
            .map_err(|e| Error::Io(format!("Failed to copy file to processing: {}", e)))?;

        // Step 2: Find truncation point by reading original file (for validation)
        let truncate_pos: Option<u64> = {
            let file = std::fs::File::open(&processing_path)
                .map_err(|e| Error::Io(format!("Failed to open processing file for reading: {}", e)))?;
            let reader = BufReader::new(file);
            let mut pos: Option<u64> = None;
            let mut current_pos = 0u64;

            for line_result in reader.lines() {
                let line = line_result.map_err(|e| Error::Io(format!("Failed to read line: {}", e)))?;
                let line_len = (line.len() + 1) as u64; // +1 for newline

                // Parse timestamp from line (skip header)
                if current_pos > 0 {
                    let parts: Vec<&str> = line.split(',').collect();
                    if parts.len() >= 2 {
                        let time_str = parts[1];
                        let time = parse_time(time_str)?;

                        if time >= cutoff_date {
                            // Found cutoff - truncate here
                            break;
                        }
                        // This line is before cutoff, update truncate position
                        pos = Some(current_pos + line_len);
                    }
                }

                current_pos += line_len;
            }

            pos
        };

        // Step 3: Perform enhancement on processing file (truncate + append)
        let enhancement_result = (|| -> Result<(), Error> {
            let mut file = std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .open(&processing_path)
                .map_err(|e| Error::Io(format!("Failed to open processing file for writing: {}", e)))?;

            // Truncate file at cutoff point (or keep all if no cutoff found)
            if let Some(pos) = truncate_pos {
                file.set_len(pos)
                    .map_err(|e| Error::Io(format!("Failed to truncate processing file: {}", e)))?;
            }

            // Seek to end and append new data (only rows >= cutoff_date)
            file.seek(SeekFrom::End(0))
                .map_err(|e| Error::Io(format!("Failed to seek to end of processing file: {}", e)))?;

            let mut wtr = csv::Writer::from_writer(file);
            for row in data.iter().filter(|r| r.time >= cutoff_date) {
                write_stock_data_row(&mut wtr, row, ticker, interval)?;
            }

            wtr.flush()
                .map_err(|e| Error::Io(format!("Failed to flush processing CSV: {}", e)))?;

            Ok(())
        })();

        // Step 4: Handle result - atomic rename on success, cleanup on failure
        match enhancement_result {
            Ok(()) => {
                // Validation: Basic check that processing file is valid
                let validation_result = (|| -> Result<(), Error> {
                    // Quick validation: check if file exists and has content
                    let metadata = std::fs::metadata(&processing_path)
                        .map_err(|e| Error::Io(format!("Failed to read processing file metadata: {}", e)))?;
                    if metadata.len() == 0 {
                        return Err(Error::Io("Processing file is empty after enhancement".to_string()));
                    }
                    Ok(())
                })();

                match validation_result {
                    Ok(()) => {
                        // Atomic rename: processing file becomes the new original
                        std::fs::rename(&processing_path, &file_path)
                            .map_err(|e| Error::Io(format!("Failed to atomically rename processing file: {}", e)))?;

                        tracing::debug!(
                            ticker = ticker,
                            interval = ?interval,
                            processing_path = ?processing_path,
                            target_path = ?file_path,
                            "Successfully enhanced CSV with copy-processing-rename strategy"
                        );
                    }
                    Err(e) => {
                        // Validation failed - keep original, remove processing file
                        let _ = std::fs::remove_file(&processing_path);
                        return Err(Error::Io(format!("Enhancement validation failed: {}. Original file preserved.", e)));
                    }
                }
            }
            Err(e) => {
                // Enhancement failed - keep original, remove processing file
                let _ = std::fs::remove_file(&processing_path);
                return Err(e);
            }
        }
    }

    Ok(())
}

/// Save enhanced CSV to a specific directory with MPSC change detection and notification
/// This function detects changes and sends real-time updates through the MPSC channel
pub async fn save_enhanced_csv_to_dir_with_changes(
    ticker: &str,
    data: &[StockData],
    interval: Interval,
    cutoff_date: DateTime<chrono::Utc>,
    rewrite_all: bool,
    base_dir: &Path,
    channel_sender: Option<std::sync::mpsc::SyncSender<TickerUpdate>>,
) -> Result<(ChangeType, usize), Error> {
    if data.is_empty() {
        return Ok((ChangeType::NoChange, 0));
    }

    // Deduplicate data before writing (favor last occurrence)
    // Create mutable copy, sort, and deduplicate
    let mut data_vec: Vec<StockData> = data.to_vec();
    data_vec.sort_by_key(|d| d.time);
    let duplicates_removed = deduplicate_stock_data_by_time(&mut data_vec);

    // Check if we should do full rewrite due to duplicates
    let has_excessive_duplicates = duplicates_removed > 0;

    if duplicates_removed > 0 {
        tracing::info!(
            ticker = ticker,
            interval = ?interval,
            duplicates_removed = duplicates_removed,
            records_remaining = data_vec.len(),
            "Deduplicated StockData before writing CSV with MPSC"
        );
    }

    // Use deduplicated data for all writes below
    let data = &data_vec;

    // Create ticker directory
    let ticker_dir = base_dir.join(ticker);
    std::fs::create_dir_all(&ticker_dir)
        .map_err(|e| Error::Io(format!("Failed to create directory: {}", e)))?;

    // Get file path
    let file_path = ticker_dir.join(interval.to_filename());
    let file_exists = file_path.exists();

    // Force full rewrite if duplicates detected, regardless of rewrite_all parameter
    let should_rewrite_all = !file_exists || rewrite_all || has_excessive_duplicates;

    let change_type = if should_rewrite_all {
        // Log if we're fixing duplicates
        if has_excessive_duplicates && file_exists {
            tracing::info!(
                ticker = ticker,
                interval = ?interval,
                duplicates_found = duplicates_removed,
                records_after_dedup = data.len(),
                "[FIX-DUPLICATION] Duplicates detected - performing full rewrite with MPSC"
            );
        }

        // New file or rewrite - write directly (no locking needed for new files)
        let file = std::fs::OpenOptions::new()
            .create(true)
            .truncate(true)  // Always truncate when we decide to rewrite
            .write(true)
            .open(&file_path)
            .map_err(|e| Error::Io(format!("Failed to create file: {}", e)))?;

        let mut wtr = csv::Writer::from_writer(file);

        // Write header
        wtr.write_record(&[
            "ticker", "time", "open", "high", "low", "close", "volume",
            "ma10", "ma20", "ma50", "ma100", "ma200",
            "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
            "close_changed", "volume_changed", "total_money_changed"
        ])
        .map_err(|e| Error::Io(format!("Failed to write header: {}", e)))?;

        // Write all data
        for stock_data in data {
            write_stock_data_row(&mut wtr, stock_data, ticker, interval)?;
        }

        wtr.flush()
            .map_err(|e| Error::Io(format!("Failed to flush writer: {}", e)))?;

        tracing::debug!(
            ticker = ticker,
            interval = ?interval,
            records = data.len(),
            "Created new CSV file with {} records",
            data.len()
        );

        if rewrite_all {
            ChangeType::FullFile { records: data.to_vec() }
        } else {
            ChangeType::NewRecords { records: data.to_vec() }
        }
    } else if has_excessive_duplicates {
        // File exists AND has duplicates - perform safe full rewrite with MPSC
        // This is safer than truncation when we know there are data issues

        // Generate unique processing filename with timestamp
        use std::time::SystemTime;
        let timestamp = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .map_err(|e| Error::Io(format!("Failed to get timestamp: {}", e)))?
            .as_secs();

        let processing_path = file_path.with_extension(format!("{}.processing.{}",
            file_path.extension().and_then(|s| s.to_str()).unwrap_or("csv"), timestamp));

        // Log the safe full rewrite due to duplicates
        tracing::info!(
            ticker = ticker,
            interval = ?interval,
            duplicates_found = duplicates_removed,
            records_after_dedup = data.len(),
            processing_path = ?processing_path,
            "[SAFE-FULL-REWRITE] Duplicates detected - skipping truncation, rewriting entire file with MPSC"
        );

        // Write header and all deduplicated data directly to processing file
        let enhancement_result = (|| -> Result<(), Error> {
            let file = std::fs::OpenOptions::new()
                .create(true)
                .write(true)
                .open(&processing_path)
                .map_err(|e| Error::Io(format!("Failed to create processing file: {}", e)))?;

            let mut wtr = csv::Writer::from_writer(file);

            // Write header
            wtr.write_record(&[
                "ticker", "time", "open", "high", "low", "close", "volume",
                "ma10", "ma20", "ma50", "ma100", "ma200",
                "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
                "close_changed", "volume_changed", "total_money_changed"
            ])
            .map_err(|e| Error::Io(format!("Failed to write header: {}", e)))?;

            // Write ALL deduplicated data (no cutoff filtering)
            for row in data {
                write_stock_data_row(&mut wtr, row, ticker, interval)?;
            }

            wtr.flush()
                .map_err(|e| Error::Io(format!("Failed to flush processing CSV: {}", e)))?;

            Ok(())
        })();

        // Handle result - atomic rename on success, cleanup on failure
        match enhancement_result {
            Ok(()) => {
                // Atomic rename: processing file becomes the new original
                std::fs::rename(&processing_path, &file_path)
                    .map_err(|e| Error::Io(format!("Failed to atomically rename processing file: {}", e)))?;

                tracing::debug!(
                    ticker = ticker,
                    interval = ?interval,
                    "Successfully performed safe full rewrite with MPSC to eliminate duplicates"
                );
            }
            Err(e) => {
                // Enhancement failed - keep original, remove processing file
                let _ = std::fs::remove_file(&processing_path);
                return Err(e);
            }
        }

        // All deduplicated data was written, so it's a full file change
        ChangeType::FullFile { records: data.to_vec() }
    } else {
        // Existing file, no duplicates - implement smart cutoff strategy without locking
        // Read existing data to find cutoff point
        let mut existing_data: Vec<StockData> = Vec::new();
        {
            let mut reader = csv::Reader::from_path(&file_path)
                .map_err(|e| Error::Io(format!("Failed to open file for reading: {}", e)))?;

            for result in reader.records() {
                let record = result.map_err(|e| Error::Parse(format!("Failed to parse CSV: {}", e)))?;
                if record.len() < 7 {
                    continue; // Skip incomplete records
                }

                // Parse time
                let time_str = &record[1];
                let time = parse_time(time_str)?;

                // Parse OHLCV (last 5 fields)
                let open: f64 = record[2].parse().map_err(|_| Error::Parse("Invalid open price".to_string()))?;
                let high: f64 = record[3].parse().map_err(|_| Error::Parse("Invalid high price".to_string()))?;
                let low: f64 = record[4].parse().map_err(|_| Error::Parse("Invalid low price".to_string()))?;
                let close: f64 = record[5].parse().map_err(|_| Error::Parse("Invalid close price".to_string()))?;
                let volume: u64 = record[6].parse().map_err(|_| Error::Parse("Invalid volume".to_string()))?;

                existing_data.push(StockData::new(time, ticker.to_string(), open, high, low, close, volume));
            }
        }

        // Find cutoff point in existing data
        let cutoff_index = existing_data
            .iter()
            .position(|d| d.time >= cutoff_date)
            .unwrap_or(existing_data.len());

        // CRITICAL FIX: Smart deduplication during cutoff
        // Handle cutoff edge case: if cutoff is at index 0, we need full deduplication
        let (mut final_data, mut existing_timestamps) = if cutoff_index == 0 && !existing_data.is_empty() {
            // Edge case: cutoff at first record, need to deduplicate ALL existing data
            let mut timestamps: std::collections::HashSet<DateTime<chrono::Utc>> = existing_data.iter().map(|d| d.time).collect();
            let mut deduped: Vec<StockData> = Vec::new();
            let mut skipped = 0;

            // Process existing data in reverse, keep only latest per timestamp
            for existing_record in existing_data.iter().rev() {
                if !timestamps.contains(&existing_record.time) {
                    deduped.push(existing_record.clone());
                    timestamps.insert(existing_record.time);
                } else {
                    skipped += 1;
                }
            }

            deduped.reverse(); // Restore chronological order
            (deduped, timestamps)
        } else {
            // Normal case: use existing data up to cutoff point
            let existing_prefix = existing_data[..cutoff_index].to_vec();
            let timestamps: std::collections::HashSet<DateTime<chrono::Utc>> = existing_prefix.iter().map(|d| d.time).collect();
            (existing_prefix, timestamps)
        };

        // Helper function to check if a record is enhanced (has MA values)
        let is_enhanced = |record: &StockData| -> bool {
            record.ma10.unwrap_or(0.0) > 0.0 || record.ma20.unwrap_or(0.0) > 0.0 || record.ma50.unwrap_or(0.0) > 0.0
        };

        // Add new records only if timestamp doesn't already exist (favor latest)
        let mut duplicates_skipped = 0;
        let mut enhanced_replacements = 0;
        for new_record in data.iter().rev() {  // Process in reverse to favor latest
            if !existing_timestamps.contains(&new_record.time) {
                final_data.push(new_record.clone());
                existing_timestamps.insert(new_record.time);
            } else {
                // Timestamp exists - check if we should replace with enhanced version
                if let Some(existing_pos) = final_data.iter().position(|r| r.time == new_record.time) {
                    if is_enhanced(new_record) && !is_enhanced(&final_data[existing_pos]) {
                        // Replace unenhanced existing record with enhanced new record
                        final_data[existing_pos] = new_record.clone();
                        enhanced_replacements += 1;
                    } else {
                        duplicates_skipped += 1;
                    }
                } else {
                    duplicates_skipped += 1;
                }
            }
        }

        // Sort final data by time (required for CSV writing)
        final_data.sort_by_key(|d| d.time);

        if duplicates_skipped > 0 || enhanced_replacements > 0 {
            tracing::info!(
                ticker = ticker,
                interval = ?interval,
                duplicates_skipped = duplicates_skipped,
                enhanced_replacements = enhanced_replacements,
                final_records = final_data.len(),
                "[SMART-DEDUPLICATION] Processed duplicates: skipped={}, enhanced_replacements={}",
                duplicates_skipped, enhanced_replacements
            );
        }

        // Write to temporary file first, then atomically rename
        let temp_path = file_path.with_extension("tmp");
        {
            use std::io::Write;
            let mut file = std::fs::OpenOptions::new()
                .create(true)
                .write(true)
                .truncate(true)
                .open(&temp_path)
                .map_err(|e| Error::Io(format!("Failed to create temp file: {}", e)))?;

            let mut wtr = csv::Writer::from_writer(file);
            wtr.write_record(&[
                "ticker", "time", "open", "high", "low", "close", "volume",
                "ma10", "ma20", "ma50", "ma100", "ma200",
                "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
                "close_changed", "volume_changed", "total_money_changed"
            ])
            .map_err(|e| Error::Io(format!("Failed to write header: {}", e)))?;

            // Write final deduplicated data
            for stock_data in &final_data {
                write_stock_data_row(&mut wtr, stock_data, ticker, interval)?;
            }

            wtr.flush()
                .map_err(|e| Error::Io(format!("Failed to flush writer: {}", e)))?;
        }

        // Atomically replace the old file
        std::fs::rename(&temp_path, &file_path)
            .map_err(|e| Error::Io(format!("Failed to atomically replace file: {}", e)))?;

        tracing::debug!(
            ticker = ticker,
            interval = ?interval,
            existing_before_cutoff = cutoff_index,
            new_records = data.len(),
            duplicates_skipped = duplicates_skipped,
            final_records = final_data.len(),
            "Updated CSV with smart cutoff strategy (atomic rename)"
        );

        if cutoff_index == 0 {
            ChangeType::NewRecords { records: final_data }
        } else {
            ChangeType::Truncated {
                from_record: cutoff_index,
                new_records: final_data,
            }
        }
    };

    // Send update through channel if provided
    if let Some(sender) = channel_sender {
        let update = TickerUpdate::new(
            ticker.to_string(),
            interval,
            change_type.clone(),
        );

        // Non-blocking send - don't fail if channel is full
        match sender.send(update) {
            Ok(()) => {
                tracing::info!(
                    ticker = ticker,
                    interval = ?interval,
                    record_count = change_type.record_count(),
                    change_type = %change_type,
                    "Sent real-time update via MPSC"
                );
            }
            Err(_) => {
                // Channel is full or disconnected - log but don't fail
                tracing::warn!(
                    ticker = ticker,
                    interval = ?interval,
                    record_count = change_type.record_count(),
                    "MPSC channel full - skipping real-time update"
                );
            }
        }
    }

    Ok((change_type, data.len()))
}

/// Save enhanced CSV to a specific directory with MPSC change detection and notification
/// This function detects changes and sends real-time updates through the MPSC channel
pub async fn save_enhanced_csv_with_changes(
    ticker: &str,
    data: &[StockData],
    interval: Interval,
    cutoff_date: DateTime<chrono::Utc>,
    rewrite_all: bool,
    base_dir: &Path,
    channel_sender: Option<std::sync::mpsc::SyncSender<TickerUpdate>>,
) -> Result<(ChangeType, usize), Error> {
    if data.is_empty() {
        return Ok((ChangeType::NoChange, 0));
    }

    // Deduplicate data before writing (favor last occurrence)
    // Create mutable copy, sort, and deduplicate
    let mut data_vec: Vec<StockData> = data.to_vec();
    data_vec.sort_by_key(|d| d.time);
    let duplicates_removed = deduplicate_stock_data_by_time(&mut data_vec);

    // Check if we should do full rewrite due to duplicates
    let has_excessive_duplicates = duplicates_removed > 0;

    if duplicates_removed > 0 {
        tracing::info!(
            ticker = ticker,
            interval = ?interval,
            duplicates_removed = duplicates_removed,
            records_remaining = data_vec.len(),
            "Deduplicated StockData before writing CSV with MPSC"
        );
    }

    // Use deduplicated data for all writes below
    let data = &data_vec;

    // Create ticker directory
    let ticker_dir = base_dir.join(ticker);
    std::fs::create_dir_all(&ticker_dir)
        .map_err(|e| Error::Io(format!("Failed to create directory: {}", e)))?;

    // Get file path
    let file_path = ticker_dir.join(interval.to_filename());
    let file_exists = file_path.exists();

    // Force full rewrite if duplicates detected, regardless of rewrite_all parameter
    let should_rewrite_all = !file_exists || rewrite_all || has_excessive_duplicates;

    let change_type = if should_rewrite_all {
        // Log if we're fixing duplicates
        if has_excessive_duplicates && file_exists {
            tracing::info!(
                ticker = ticker,
                interval = ?interval,
                duplicates_found = duplicates_removed,
                records_after_dedup = data.len(),
                "[MPSC-FIX-DUPLICATION] Duplicates detected - performing full rewrite"
            );
        }

        // New file or rewrite - write directly (no locking needed for new files)
        let file = std::fs::OpenOptions::new()
            .create(true)
            .truncate(true)  // Always truncate when we decide to rewrite
            .write(true)
            .open(&file_path)
            .map_err(|e| Error::Io(format!("Failed to create file: {}", e)))?;

        let mut wtr = csv::Writer::from_writer(file);

        // Write 20-column header
        wtr.write_record(&[
            "ticker", "time", "open", "high", "low", "close", "volume",
            "ma10", "ma20", "ma50", "ma100", "ma200",
            "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
            "close_changed", "volume_changed", "total_money_changed"
        ])
        .map_err(|e| Error::Io(format!("Failed to write header: {}", e)))?;

        for row in data {
            write_stock_data_row(&mut wtr, row, ticker, interval)?;
        }

        wtr.flush()
            .map_err(|e| Error::Io(format!("Failed to flush CSV: {}", e)))?;

        ChangeType::FullFile { records: data.to_vec() }
    } else {
        // For incremental updates, detect actual new records
        let new_records: Vec<StockData> = data
            .iter()
            .filter(|r| r.time >= cutoff_date)
            .cloned()
            .collect();

        // File exists and no duplicates - use copy-processing-rename strategy for incremental updates
        // Generate unique processing filename with timestamp
        use std::time::SystemTime;
        let timestamp = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .map_err(|e| Error::Io(format!("Failed to get timestamp: {}", e)))?
            .as_secs();

        let processing_path = file_path.with_extension(format!("{}.processing.{}",
            file_path.extension().and_then(|s| s.to_str()).unwrap_or("csv"), timestamp));

        // Step 1: Copy original file to processing file
        std::fs::copy(&file_path, &processing_path)
            .map_err(|e| Error::Io(format!("Failed to copy file to processing: {}", e)))?;

        // Step 2: Perform enhancement on processing file (truncate + append)
        let enhancement_result = (|| -> Result<(), Error> {
            let mut file = std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .open(&processing_path)
                .map_err(|e| Error::Io(format!("Failed to open processing file for writing: {}", e)))?;

            // Truncate file at cutoff point (or keep all if no cutoff found)
            let truncate_pos = {
                let file = std::fs::File::open(&processing_path)
                    .map_err(|e| Error::Io(format!("Failed to open processing file for reading: {}", e)))?;
                let reader = BufReader::new(file);
                let mut pos: Option<u64> = None;
                let mut current_pos = 0u64;

                for line_result in reader.lines() {
                    let line = line_result.map_err(|e| Error::Io(format!("Failed to read line: {}", e)))?;
                    let line_len = (line.len() + 1) as u64;

                    // Parse timestamp from line (skip header)
                    if current_pos > 0 {
                        let parts: Vec<&str> = line.split(',').collect();
                        if parts.len() >= 2 {
                            let time_str = parts[1];
                            let time = parse_time(time_str)?;

                            if time >= cutoff_date {
                                // Found cutoff - truncate here
                                break;
                            }
                            // This line is before cutoff, update truncate position
                            pos = Some(current_pos + line_len);
                        }
                    }

                    current_pos += line_len;
                }

                pos
            };

            // Truncate file at cutoff point (or keep all if no cutoff found)
            if let Some(pos) = truncate_pos {
                file.set_len(pos)
                    .map_err(|e| Error::Io(format!("Failed to truncate processing file: {}", e)))?;
            }

            // Seek to end and append new data (only rows >= cutoff_date)
            file.seek(SeekFrom::End(0))
                .map_err(|e| Error::Io(format!("Failed to seek to end of processing file: {}", e)))?;

            let mut wtr = csv::Writer::from_writer(file);
            for row in data.iter().filter(|r| r.time >= cutoff_date) {
                write_stock_data_row(&mut wtr, row, ticker, interval)?;
            }

            wtr.flush()
                .map_err(|e| Error::Io(format!("Failed to flush processing CSV: {}", e)))?;

            Ok(())
        })();

        // Step 4: Handle result - atomic rename on success, cleanup on failure
        match enhancement_result {
            Ok(()) => {
                // Validation: Basic check that processing file is valid
                let validation_result = (|| -> Result<(), Error> {
                    // Quick validation: check if file exists and has content
                    let metadata = std::fs::metadata(&processing_path)
                        .map_err(|e| Error::Io(format!("Failed to read processing file metadata: {}", e)))?;
                    if metadata.len() == 0 {
                        return Err(Error::Io("Processing file is empty after enhancement".to_string()));
                    }
                    Ok(())
                })();

                match validation_result {
                    Ok(()) => {
                        // Atomic rename: processing file becomes the new original
                        std::fs::rename(&processing_path, &file_path)
                            .map_err(|e| Error::Io(format!("Failed to atomically rename processing file: {}", e)))?;

                        tracing::info!(
                            ticker = ticker,
                            interval = ?interval,
                            records = data.len(),
                            file_path = ?file_path,
                            "[MPSC] Enhanced CSV with incremental updates"
                        );
                    }
                    Err(e) => {
                        // Validation failed - keep original, remove processing file
                        let _ = std::fs::remove_file(&processing_path);
                        return Err(Error::Io(format!("Enhancement validation failed: {}. Original file preserved.", e)));
                    }
                }
            }
            Err(e) => {
                // Enhancement failed - keep original, remove processing file
                let _ = std::fs::remove_file(&processing_path);
                return Err(e);
            }
        }

        ChangeType::NewRecords {
            records: new_records
        }
    };

    // Send update through channel for real-time memory cache update (if channel provided)
    if let Some(sender) = channel_sender {
        println!("[CSV_ENHANCER] About to send MPSC update for ticker={}, interval={:?}, change_type={}",
                 ticker, interval, change_type);
        let update = TickerUpdate::new(
            ticker.to_string(),
            interval,
            change_type.clone(),
        );

        // Send with retry mechanism - wait for channel to be available instead of skipping
        println!("[CSV_ENHANCER] Calling send_with_retry_async for ticker={}", ticker);
        match crate::services::mpsc::send_with_retry_async(&sender, update, 50).await {
            Ok(()) => {
                println!("[CSV_ENHANCER] ✅ Successfully sent MPSC update for ticker={}", ticker);
                tracing::info!(
                    ticker = ticker,
                    interval = ?interval,
                    record_count = change_type.record_count(),
                    "[MPSC] Sent real-time update via channel"
                );
            }
            Err(e) => {
                // Failed after retries - log but don't fail the CSV write
                println!("[CSV_ENHANCER] ❌ ERROR: Failed to send MPSC update for ticker={}, error={}", ticker, e);
                tracing::warn!(
                    ticker = ticker,
                    interval = ?interval,
                    error = e,
                    "[MPSC] Failed to send data update via channel after retries"
                );
            }
        }
    }

    Ok((change_type, data.len()))
}

/// Write a single StockData row to CSV (20 columns)
fn write_stock_data_row(
    wtr: &mut Writer<std::fs::File>,
    stock_data: &StockData,
    ticker: &str,
    interval: Interval,
) -> Result<(), Error> {
    let time_str = match interval {
        Interval::Daily => format_date(&stock_data.time),
        Interval::Hourly | Interval::Minute => format_timestamp(&stock_data.time),
    };

    wtr.write_record(&[
        ticker,
        &time_str,
        &format_adaptive_price(stock_data.open),
        &format_adaptive_price(stock_data.high),
        &format_adaptive_price(stock_data.low),
        &format_adaptive_price(stock_data.close),
        &stock_data.volume.to_string(),
        &stock_data.ma10.map_or(String::new(), format_adaptive_price),
        &stock_data.ma20.map_or(String::new(), format_adaptive_price),
        &stock_data.ma50.map_or(String::new(), format_adaptive_price),
        &stock_data.ma100.map_or(String::new(), format_adaptive_price),
        &stock_data.ma200.map_or(String::new(), format_adaptive_price),
        &stock_data.ma10_score.map_or(String::new(), |v| format!("{:.4}", v)),
        &stock_data.ma20_score.map_or(String::new(), |v| format!("{:.4}", v)),
        &stock_data.ma50_score.map_or(String::new(), |v| format!("{:.4}", v)),
        &stock_data.ma100_score.map_or(String::new(), |v| format!("{:.4}", v)),
        &stock_data.ma200_score.map_or(String::new(), |v| format!("{:.4}", v)),
        &stock_data.close_changed.map_or(String::new(), |v| format!("{:.4}", v)),
        &stock_data.volume_changed.map_or(String::new(), |v| format!("{:.4}", v)),
        &stock_data.total_money_changed.map_or(String::new(), |v| format!("{:.0}", v)),
    ])
    .map_err(|e| Error::Io(format!("Failed to write row: {}", e)))?;

    Ok(())
}

/// Parse time from string (delegates to centralized utility)
fn parse_time(time_str: &str) -> Result<DateTime<chrono::Utc>, Error> {
    parse_timestamp(time_str)
}

/// Legacy function for backward compatibility: reads CSV files and enhances them
/// This is used by workers that don't have direct access to OhlcvData
/// Uses streaming approach to process one ticker at a time (memory-efficient)
///
/// # Arguments
/// * `interval` - The interval to enhance (Daily, Hourly, Minute)
/// * `market_data_dir` - The directory containing ticker subdirectories
/// * `tickers_filter` - Optional list of tickers to process. If None, processes all tickers in directory.
pub fn enhance_interval_filtered(
    interval: Interval,
    market_data_dir: &Path,
    tickers_filter: Option<&[String]>,
) -> Result<EnhancementStats, Error> {
    let start_time = Instant::now();

    // Calculate cutoff date (2 days ago) for smart saving
    let cutoff_date = chrono::Utc::now() - chrono::Duration::days(2);

    // Scan ticker subdirectories (optionally filtered)
    let entries: Vec<_> = std::fs::read_dir(market_data_dir)
        .map_err(|e| Error::Io(format!("Failed to read market_data directory: {}", e)))?
        .filter_map(|e| e.ok())
        .filter(|e| {
            if !e.path().is_dir() {
                return false;
            }
            // If filter is specified, only process tickers in the filter list
            if let Some(filter) = tickers_filter {
                if let Some(ticker_name) = e.path().file_name().and_then(|n| n.to_str()) {
                    return filter.contains(&ticker_name.to_string());
                }
                return false;
            }
            true
        })
        .collect();

    if entries.is_empty() {
        return Ok(EnhancementStats {
            tickers: 0,
            records: 0,
            duration: start_time.elapsed(),
            ma_time: Duration::ZERO,
            write_time: Duration::ZERO,
            total_bytes_written: 0,
        });
    }

    let total_tickers = entries.len();
    let mut ticker_count = 0;
    let mut total_records = 0;
    let mut total_bytes_written = 0u64;
    let mut total_ma_time = Duration::ZERO;
    let mut total_write_time = Duration::ZERO;

    // Process each ticker sequentially (streaming - one at a time)
    for (idx, entry) in entries.iter().enumerate() {
        let ticker_dir = entry.path();
        let ticker = match ticker_dir.file_name().and_then(|n| n.to_str()) {
            Some(t) => t,
            None => continue,
        };

        // Check if CSV exists for this interval
        let csv_path = ticker_dir.join(interval.to_filename());
        if !csv_path.exists() {
            continue;
        }

        // Process single ticker: Load → Enhance → Write → Free memory
        match process_single_ticker(ticker, interval, market_data_dir, cutoff_date) {
            Ok(stats) => {
                ticker_count += 1;
                total_records += stats.records;
                total_bytes_written += stats.bytes_written;
                total_ma_time += stats.ma_time;
                total_write_time += stats.write_time;

                // Log progress (every 10 tickers or last ticker)
                if (idx + 1) % 10 == 0 || idx == total_tickers - 1 {
                    tracing::debug!(
                        "[{}/{}] Processed {} ({} records, {:.1} KB)",
                        idx + 1,
                        total_tickers,
                        ticker,
                        stats.records,
                        stats.bytes_written as f64 / 1024.0
                    );
                }
            }
            Err(e) => {
                tracing::warn!("Failed to process {}: {}", ticker, e);
            }
        }
        // Ticker data is now dropped, memory freed
    }

    Ok(EnhancementStats {
        tickers: ticker_count,
        records: total_records,
        duration: start_time.elapsed(),
        ma_time: total_ma_time,
        write_time: total_write_time,
        total_bytes_written,
    })
}

/// Backward-compatible wrapper that processes all tickers in directory
pub fn enhance_interval(
    interval: Interval,
    market_data_dir: &Path,
) -> Result<EnhancementStats, Error> {
    enhance_interval_filtered(interval, market_data_dir, None)
}

/// Statistics for single ticker processing
struct TickerStats {
    records: usize,
    bytes_written: u64,
    ma_time: Duration,
    write_time: Duration,
}

/// Process a single ticker: Load → Enhance → Write → Free memory
/// This is the streaming version that processes one ticker at a time
fn process_single_ticker(
    ticker: &str,
    interval: Interval,
    market_data_dir: &Path,
    _cutoff_date: DateTime<chrono::Utc>, // Unused - we calculate per-ticker cutoff
) -> Result<TickerStats, Error> {
    let ticker_dir = market_data_dir.join(ticker);
    let csv_path = ticker_dir.join(interval.to_filename());

    // Step 1: Read CSV file for this ticker (OHLCV only)
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true) // Allow varying number of fields per record
        .from_path(&csv_path)
        .map_err(|e| Error::Io(format!("Failed to read {}: {}", csv_path.display(), e)))?;

    let mut ticker_data = Vec::new();

    for result in reader.records() {
        let record = result.map_err(|e| Error::Io(format!("CSV parse error in {}: {}", csv_path.display(), e)))?;

        // Read basic OHLCV data (first 7 columns)
        if record.len() < 7 {
            continue;
        }

        let time_str = record.get(1).ok_or_else(|| Error::Io("Missing time".to_string()))?;
        let open: f64 = record
            .get(2)
            .ok_or_else(|| Error::Io("Missing open".to_string()))?
            .parse()
            .map_err(|e| Error::Io(format!("Invalid open: {}", e)))?;
        let high: f64 = record
            .get(3)
            .ok_or_else(|| Error::Io("Missing high".to_string()))?
            .parse()
            .map_err(|e| Error::Io(format!("Invalid high: {}", e)))?;
        let low: f64 = record
            .get(4)
            .ok_or_else(|| Error::Io("Missing low".to_string()))?
            .parse()
            .map_err(|e| Error::Io(format!("Invalid low: {}", e)))?;
        let close: f64 = record
            .get(5)
            .ok_or_else(|| Error::Io("Missing close".to_string()))?
            .parse()
            .map_err(|e| Error::Io(format!("Invalid close: {}", e)))?;
        let volume: u64 = record
            .get(6)
            .ok_or_else(|| Error::Io("Missing volume".to_string()))?
            .parse()
            .map_err(|e| Error::Io(format!("Invalid volume: {}", e)))?;

        // Parse datetime
        let time = parse_time(time_str)?;

        let ohlcv = OhlcvData {
            time,
            open,
            high,
            low,
            close,
            volume,
            symbol: Some(ticker.to_string()),
        };
        ticker_data.push(ohlcv);
    }

    if ticker_data.is_empty() {
        return Ok(TickerStats {
            records: 0,
            bytes_written: 0,
            ma_time: Duration::ZERO,
            write_time: Duration::ZERO,
        });
    }

    // Sort by time (oldest first)
    ticker_data.sort_by_key(|d| d.time);

    // Deduplicate by timestamp (favor last duplicate)
    let duplicates_removed = deduplicate_ohlcv_by_time(&mut ticker_data);
    if duplicates_removed > 0 {
        tracing::info!(
            ticker = ticker,
            interval = ?interval,
            duplicates_removed = duplicates_removed,
            records_remaining = ticker_data.len(),
            "Deduplicated CSV data"
        );
    }

    // Step 2: Calculate cutoff date based on existing CSV data (before moving ticker_data)
    let resume_days = 2i64;
    let proper_cutoff_date = if !ticker_data.is_empty() {
        // Use existing CSV's last record time - resume_days
        if let Some(last_record) = ticker_data.last() {
            last_record.time - chrono::Duration::days(resume_days)
        } else {
            chrono::DateTime::from_timestamp(0, 0).unwrap_or_else(|| chrono::Utc::now())
        }
    } else {
        chrono::DateTime::from_timestamp(0, 0).unwrap_or_else(|| chrono::Utc::now())
    };

    // Step 3: Enhance data (calculate MAs and scores) - in-memory
    let ma_start = Instant::now();
    let mut data_map: HashMap<String, Vec<OhlcvData>> = HashMap::new();
    data_map.insert(ticker.to_string(), ticker_data);
    let enhanced_map = enhance_data(data_map);
    let ma_time = ma_start.elapsed();

    // Get enhanced data for this ticker
    let enhanced_data = enhanced_map.get(ticker)
        .ok_or_else(|| Error::Io(format!("Failed to enhance data for {}", ticker)))?;

    let record_count = enhanced_data.len();

    // Step 4: Write enhanced CSV with proper cutoff
    let write_start = Instant::now();

    save_enhanced_csv_to_dir(ticker, enhanced_data, interval, proper_cutoff_date, false, market_data_dir)?;
    let write_time = write_start.elapsed();

    // Step 4: Get file size for stats
    let bytes_written = std::fs::metadata(&csv_path)
        .map(|m| m.len())
        .unwrap_or(0);

    Ok(TickerStats {
        records: record_count,
        bytes_written,
        ma_time,
        write_time,
    })
    // ticker_data and enhanced_map are dropped here, freeing memory
}

/// Clean up existing duplicates in a CSV file (one-time cleanup function)
pub fn cleanup_existing_duplicates(file_path: &Path, ticker: &str, interval: Interval) -> Result<usize, Error> {
    tracing::info!(
        ticker = ticker,
        interval = ?interval,
        file_path = ?file_path,
        "[CLEANUP] Starting one-time duplicate cleanup"
    );

    // Read full CSV file using regular CSV parser
    let mut data = Vec::new();
    if file_path.exists() {
        let file = std::fs::File::open(file_path)
            .map_err(|e| Error::Io(format!("Failed to open CSV file: {}", e)))?;

        let mut rdr = csv::ReaderBuilder::new()
            .has_headers(true)
            .from_reader(file);

        for result in rdr.records() {
            let record = result
                .map_err(|e| Error::Io(format!("Failed to read CSV record: {}", e)))?;

            if record.len() >= 7 {
                let time_str = record.get(1).unwrap_or_default();
                if let Ok(time) = chrono::DateTime::parse_from_rfc3339(format!("{}T00:00:00Z", time_str).as_str()) {
                    let stock_data = crate::models::StockData {
                        ticker: ticker.to_string(),
                        time: time.with_timezone(&chrono::Utc),
                        open: record.get(2).unwrap_or("0").parse().unwrap_or(0.0),
                        high: record.get(3).unwrap_or("0").parse().unwrap_or(0.0),
                        low: record.get(4).unwrap_or("0").parse().unwrap_or(0.0),
                        close: record.get(5).unwrap_or("0").parse().unwrap_or(0.0),
                        volume: record.get(6).unwrap_or("0").parse().unwrap_or(0),
                        ma10: Some(record.get(7).unwrap_or("0").parse().unwrap_or(0.0)),
                        ma20: Some(record.get(8).unwrap_or("0").parse().unwrap_or(0.0)),
                        ma50: Some(record.get(9).unwrap_or("0").parse().unwrap_or(0.0)),
                        ma100: Some(record.get(10).unwrap_or("0").parse().unwrap_or(0.0)),
                        ma200: Some(record.get(11).unwrap_or("0").parse().unwrap_or(0.0)),
                        ma10_score: Some(record.get(12).unwrap_or("0").parse().unwrap_or(0.0)),
                        ma20_score: Some(record.get(13).unwrap_or("0").parse().unwrap_or(0.0)),
                        ma50_score: Some(record.get(14).unwrap_or("0").parse().unwrap_or(0.0)),
                        ma100_score: Some(record.get(15).unwrap_or("0").parse().unwrap_or(0.0)),
                        ma200_score: Some(record.get(16).unwrap_or("0").parse().unwrap_or(0.0)),
                        close_changed: Some(record.get(17).unwrap_or("0").parse().unwrap_or(0.0)),
                        volume_changed: Some(record.get(18).unwrap_or("0").parse().unwrap_or(0.0)),
                        total_money_changed: Some(record.get(19).unwrap_or("0").parse().unwrap_or(0.0)),
                    };
                    data.push(stock_data);
                }
            }
        }
    }
    let original_count = data.len();

    if data.is_empty() {
        tracing::warn!(
            ticker = ticker,
            interval = ?interval,
            "[CLEANUP] CSV file is empty, nothing to cleanup"
        );
        return Ok(0);
    }

    // Sort by time to group duplicates together
    data.sort_by_key(|d| d.time);

    // Deduplicate by keeping only the latest record for each timestamp
    let mut deduplicated_data = Vec::new();
    let mut seen_timestamps = std::collections::HashSet::new();
    let mut duplicates_removed = 0;

    // Process in reverse to favor the latest records
    for record in data.iter().rev() {
        if seen_timestamps.contains(&record.time) {
            duplicates_removed += 1;
        } else {
            seen_timestamps.insert(record.time);
            deduplicated_data.push(record.clone());
        }
    }

    // Reverse back to chronological order
    deduplicated_data.reverse();

    if duplicates_removed == 0 {
        tracing::info!(
            ticker = ticker,
            interval = ?interval,
            total_records = original_count,
            "[CLEANUP] No duplicates found, file is already clean"
        );
        return Ok(0);
    }

    // Write cleaned data to temporary file first, then atomically rename
    let temp_path = file_path.with_extension("tmp");
    {
        use std::io::Write;
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&temp_path)
            .map_err(|e| Error::Io(format!("Failed to create temp file: {}", e)))?;

        let mut wtr = csv::Writer::from_writer(file);
        wtr.write_record(&[
            "ticker", "time", "open", "high", "low", "close", "volume",
            "ma10", "ma20", "ma50", "ma100", "ma200",
            "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
            "close_changed", "volume_changed", "total_money_changed"
        ])
        .map_err(|e| Error::Io(format!("Failed to write header: {}", e)))?;

        // Write deduplicated data
        for record in &deduplicated_data {
            wtr.write_record(&[
                &record.ticker,
                &record.time.format("%Y-%m-%d").to_string(),
                &format!("{:.2}", record.open),
                &format!("{:.2}", record.high),
                &format!("{:.2}", record.low),
                &format!("{:.2}", record.close),
                &record.volume.to_string(),
                &format!("{:.2}", record.ma10.unwrap_or(0.0)),
                &format!("{:.2}", record.ma20.unwrap_or(0.0)),
                &format!("{:.2}", record.ma50.unwrap_or(0.0)),
                &format!("{:.2}", record.ma100.unwrap_or(0.0)),
                &format!("{:.2}", record.ma200.unwrap_or(0.0)),
                &format!("{:.2}", record.ma10_score.unwrap_or(0.0)),
                &format!("{:.2}", record.ma20_score.unwrap_or(0.0)),
                &format!("{:.2}", record.ma50_score.unwrap_or(0.0)),
                &format!("{:.2}", record.ma100_score.unwrap_or(0.0)),
                &format!("{:.2}", record.ma200_score.unwrap_or(0.0)),
                &format!("{:.2}", record.close_changed.unwrap_or(0.0)),
                &format!("{:.2}", record.volume_changed.unwrap_or(0.0)),
                &format!("{:.2}", record.total_money_changed.unwrap_or(0.0)),
            ])
            .map_err(|e| Error::Io(format!("Failed to write record: {}", e)))?;
        }

        wtr.flush().map_err(|e| Error::Io(format!("Failed to flush CSV: {}", e)))?;
    }

    // Replace original with cleaned file
    std::fs::rename(&temp_path, file_path)
        .map_err(|e| Error::Io(format!("Failed to rename cleaned file: {}", e)))?;

    tracing::info!(
        ticker = ticker,
        interval = ?interval,
        original_records = original_count,
        duplicates_removed = duplicates_removed,
        final_records = deduplicated_data.len(),
        "[CLEANUP] Successfully removed {} duplicate timestamps",
        duplicates_removed
    );

    Ok(duplicates_removed)
}

