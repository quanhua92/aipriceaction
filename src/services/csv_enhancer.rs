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
use crate::services::data_store::{DataUpdateMessage, DataMode};
use crate::utils::{get_market_data_dir, parse_timestamp, format_date, format_timestamp, deduplicate_ohlcv_by_time, deduplicate_stock_data_by_time};
use chrono::{DateTime, Utc};
use csv::Writer;
use std::collections::HashMap;
use std::path::Path;
use std::sync::mpsc;
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
/// - Optionally sends data through MPSC channel for real-time cache updates
pub fn save_enhanced_csv(
    ticker: &str,
    data: &[StockData],
    interval: Interval,
    cutoff_date: DateTime<chrono::Utc>,
    rewrite_all: bool,
    channel_sender: Option<&mpsc::Sender<DataUpdateMessage>>,
    mode: DataMode,
) -> Result<(), Error> {
    save_enhanced_csv_to_dir(ticker, data, interval, cutoff_date, rewrite_all, &get_market_data_dir(), channel_sender, mode)
}

/// Backward-compatible wrapper for save_enhanced_csv (no channel support)
pub fn save_enhanced_csv_legacy(
    ticker: &str,
    data: &[StockData],
    interval: Interval,
    cutoff_date: DateTime<chrono::Utc>,
    rewrite_all: bool,
) -> Result<(), Error> {
    let mode = if get_market_data_dir().to_string_lossy().contains("crypto_data") {
        DataMode::Crypto
    } else {
        DataMode::VN
    };
    save_enhanced_csv(ticker, data, interval, cutoff_date, rewrite_all, None, mode)
}

/// Save enhanced CSV to a specific directory (for crypto_data support)
pub fn save_enhanced_csv_to_dir(
    ticker: &str,
    data: &[StockData],
    interval: Interval,
    cutoff_date: DateTime<chrono::Utc>,
    rewrite_all: bool,
    base_dir: &Path,
    channel_sender: Option<&mpsc::Sender<DataUpdateMessage>>,
    mode: DataMode,
) -> Result<(), Error> {
    if data.is_empty() {
        return Err(Error::InvalidInput("No data to save".to_string()));
    }

    // Deduplicate data before writing (favor last occurrence)
    // Create mutable copy, sort, and deduplicate
    let mut data_vec: Vec<StockData> = data.to_vec();
    data_vec.sort_by_key(|d| d.time);
    let duplicates_removed = deduplicate_stock_data_by_time(&mut data_vec);
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

    if !file_exists || rewrite_all {
        // New file or rewrite - write directly (no locking needed for new files)
        let file = std::fs::OpenOptions::new()
            .create(true)
            .truncate(rewrite_all)
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
    } else {
        // File exists - use copy-processing-rename strategy for safe updates

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

      // Send data through channel for real-time memory cache update (if channel provided)
    if let Some(sender) = channel_sender {
        let message = DataUpdateMessage::Single {
            ticker: ticker.to_string(),
            data: data.to_vec(), // Send ALL data including buffer
            interval,
            mode,
            timestamp: Utc::now(),
        };

        // Non-blocking send - if channel is full or disconnected, just log and continue
        match sender.send(message) {
            Ok(()) => {
                tracing::debug!(
                    ticker = ticker,
                    interval = ?interval,
                    records = data.len(),
                    "[MPSC] Sent data update via channel"
                );
            }
            Err(e) => {
                // Channel is disconnected or full - log but don't fail the CSV write
                tracing::warn!(
                    ticker = ticker,
                    interval = ?interval,
                    error = %e,
                    "[MPSC] Failed to send data update via channel (channel disconnected or full)"
                );
            }
        }
    }

    Ok(())
}

/// Backward-compatible wrapper for save_enhanced_csv_to_dir (no channel support)
pub fn save_enhanced_csv_to_dir_legacy(
    ticker: &str,
    data: &[StockData],
    interval: Interval,
    cutoff_date: DateTime<chrono::Utc>,
    rewrite_all: bool,
    base_dir: &Path,
) -> Result<(), Error> {
    let mode = if base_dir.to_string_lossy().contains("crypto_data") {
        DataMode::Crypto
    } else {
        DataMode::VN
    };
    save_enhanced_csv_to_dir(ticker, data, interval, cutoff_date, rewrite_all, base_dir, None, mode)
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
/// * `channel_sender` - Optional MPSC channel sender for real-time cache updates
/// * `mode` - Data mode (VN or Crypto)
pub fn enhance_interval_filtered(
    interval: Interval,
    market_data_dir: &Path,
    tickers_filter: Option<&[String]>,
    channel_sender: Option<&mpsc::Sender<DataUpdateMessage>>,
    mode: DataMode,
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
        match process_single_ticker(ticker, interval, market_data_dir, cutoff_date, channel_sender, mode) {
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
    let mode = if market_data_dir.to_string_lossy().contains("crypto_data") {
        DataMode::Crypto
    } else {
        DataMode::VN
    };
    enhance_interval_filtered(interval, market_data_dir, None, None, mode)
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
    channel_sender: Option<&mpsc::Sender<DataUpdateMessage>>,
    mode: DataMode,
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

    save_enhanced_csv_to_dir(ticker, enhanced_data, interval, proper_cutoff_date, false, market_data_dir, channel_sender, mode)?;
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

