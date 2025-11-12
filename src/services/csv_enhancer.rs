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
use crate::utils::{get_market_data_dir, parse_timestamp, format_date, format_timestamp};
use chrono::DateTime;
use csv::Writer;
use fs2::FileExt;
use std::collections::HashMap;
use std::path::Path;
use std::time::{Duration, Instant};

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
    if data.is_empty() {
        return Err(Error::InvalidInput("No data to save".to_string()));
    }

    // Create ticker directory
    let ticker_dir = get_market_data_dir().join(ticker);
    std::fs::create_dir_all(&ticker_dir)
        .map_err(|e| Error::Io(format!("Failed to create directory: {}", e)))?;

    // Get file path
    let file_path = ticker_dir.join(interval.to_filename());
    let file_exists = file_path.exists();

    if !file_exists || rewrite_all {
        // New file or rewrite - create/truncate with exclusive lock
        let file = std::fs::OpenOptions::new()
            .create(true)
            .truncate(rewrite_all)
            .write(true)
            .open(&file_path)
            .map_err(|e| Error::Io(format!("Failed to create file: {}", e)))?;

        // Acquire exclusive lock before writing
        file.lock_exclusive()
            .map_err(|e| Error::Io(format!("Failed to acquire file lock: {}", e)))?;

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
        // Lock is automatically released when file (inside wtr) goes out of scope
    } else {
        // File exists - use smart cutoff strategy
        use std::io::{BufRead, BufReader, Seek, SeekFrom};

        // Step 1: Find truncation point by reading file backwards
        let truncate_pos: Option<u64> = {
            let file = std::fs::File::open(&file_path)
                .map_err(|e| Error::Io(format!("Failed to open file for reading: {}", e)))?;
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
        }; // Reader is now dropped, file is closed

        // Step 2: Open file for writing, truncate and append
        let mut file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(&file_path)
            .map_err(|e| Error::Io(format!("Failed to open file for writing: {}", e)))?;

        // CRITICAL SECTION: Acquire exclusive lock to prevent race conditions
        file.lock_exclusive()
            .map_err(|e| Error::Io(format!("Failed to acquire file lock: {}", e)))?;

        // Truncate file at cutoff point (or keep all if no cutoff found)
        if let Some(pos) = truncate_pos {
            file.set_len(pos)
                .map_err(|e| Error::Io(format!("Failed to truncate file: {}", e)))?;
        }

        // Seek to end and append new data (only rows >= cutoff_date)
        file.seek(SeekFrom::End(0))
            .map_err(|e| Error::Io(format!("Failed to seek to end: {}", e)))?;

        let mut wtr = csv::Writer::from_writer(file);
        for row in data.iter().filter(|r| r.time >= cutoff_date) {
            write_stock_data_row(&mut wtr, row, ticker, interval)?;
        }

        wtr.flush()
            .map_err(|e| Error::Io(format!("Failed to flush CSV: {}", e)))?;
        // Lock is automatically released when file (inside wtr) goes out of scope
    }

    Ok(())
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
        &format!("{:.2}", stock_data.open),
        &format!("{:.2}", stock_data.high),
        &format!("{:.2}", stock_data.low),
        &format!("{:.2}", stock_data.close),
        &stock_data.volume.to_string(),
        &stock_data.ma10.map_or(String::new(), |v| format!("{:.2}", v)),
        &stock_data.ma20.map_or(String::new(), |v| format!("{:.2}", v)),
        &stock_data.ma50.map_or(String::new(), |v| format!("{:.2}", v)),
        &stock_data.ma100.map_or(String::new(), |v| format!("{:.2}", v)),
        &stock_data.ma200.map_or(String::new(), |v| format!("{:.2}", v)),
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
pub fn enhance_interval(
    interval: Interval,
    market_data_dir: &Path,
) -> Result<EnhancementStats, Error> {
    let start_time = Instant::now();

    // Read all CSV files for this interval
    let ma_start = Instant::now();
    let data = read_and_enhance_interval(interval, market_data_dir)?;
    let ma_time = ma_start.elapsed();

    if data.is_empty() {
        return Ok(EnhancementStats {
            tickers: 0,
            records: 0,
            duration: start_time.elapsed(),
            ma_time: Duration::ZERO,
            write_time: Duration::ZERO,
            total_bytes_written: 0,
        });
    }

    let ticker_count = data.len();

    // Calculate cutoff date (2 days ago) for smart saving
    let cutoff_date = chrono::Utc::now() - chrono::Duration::days(2);

    // Write enhanced CSV back to per-ticker directories
    let write_start = Instant::now();
    let (record_count, total_bytes_written) = write_enhanced_csv(&data, interval, market_data_dir, cutoff_date)?;
    let write_time = write_start.elapsed();

    Ok(EnhancementStats {
        tickers: ticker_count,
        records: record_count,
        duration: start_time.elapsed(),
        ma_time,
        write_time,
        total_bytes_written,
    })
}

/// Read CSV files and enhance them (legacy function for workers)
fn read_and_enhance_interval(
    interval: Interval,
    market_data_dir: &Path,
) -> Result<HashMap<String, Vec<StockData>>, Error> {
    let mut data: HashMap<String, Vec<OhlcvData>> = HashMap::new();

    // Scan all ticker subdirectories
    let entries = std::fs::read_dir(market_data_dir)
        .map_err(|e| Error::Io(format!("Failed to read market_data directory: {}", e)))?;

    for entry in entries {
        let entry = entry.map_err(|e| Error::Io(format!("Failed to read directory entry: {}", e)))?;
        let ticker_dir = entry.path();

        if !ticker_dir.is_dir() {
            continue;
        }

        let ticker = ticker_dir
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| Error::Io("Invalid ticker directory name".to_string()))?
            .to_string();

        // Read CSV file for this ticker
        let csv_path = ticker_dir.join(interval.to_filename());
        if !csv_path.exists() {
            continue;
        }

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
                symbol: Some(ticker.clone()),
            };
            ticker_data.push(ohlcv);
        }

        // Sort by time (oldest first)
        ticker_data.sort_by_key(|d| d.time);

        if !ticker_data.is_empty() {
            data.insert(ticker, ticker_data);
        }
    }

    // Enhance the data in-memory
    Ok(enhance_data(data))
}

/// Write enhanced data back to per-ticker CSV files (11 columns)
fn write_enhanced_csv(
    data: &HashMap<String, Vec<StockData>>,
    interval: Interval,
    market_data_dir: &Path,
    cutoff_date: DateTime<chrono::Utc>,
) -> Result<(usize, u64), Error> {
    let mut total_record_count = 0;
    let mut total_bytes_written = 0u64;

    for (ticker, ticker_data) in data {
        save_enhanced_csv(ticker, ticker_data, interval, cutoff_date, false)?;

        total_record_count += ticker_data.len();

        // Estimate bytes written
        let ticker_dir = market_data_dir.join(ticker);
        let csv_path = ticker_dir.join(interval.to_filename());
        let file_size = std::fs::metadata(&csv_path)
            .map(|m| m.len())
            .unwrap_or(0);
        total_bytes_written += file_size;
    }

    Ok((total_record_count, total_bytes_written))
}
