//! CSV Enhancement Service
//!
//! Enhances raw OHLCV CSV files (7 columns) with technical indicators,
//! producing enhanced CSV files with 16 columns including:
//! - Moving averages (MA10, MA20, MA50)
//! - MA scores (percentage deviation from MA)
//! - Money flow and dollar flow (market-normalized percentages)
//! - Trend scores (10-day rolling average)

use crate::error::Error;
use crate::models::{Interval, StockData};
use crate::models::indicators::{calculate_sma, calculate_ma_score, calculate_money_flow_multiplier};
use chrono::{DateTime, Utc, NaiveDate, Timelike};
use csv::{Reader, Writer};
use std::collections::HashMap;
use std::path::Path;
use std::time::{Duration, Instant};

/// Statistics for enhancement operation
#[derive(Debug)]
pub struct EnhancementStats {
    pub tickers: usize,
    pub records: usize,
    pub duration: Duration,
    pub read_time: Duration,
    pub ma_time: Duration,
    pub money_flow_time: Duration,
    pub trend_score_time: Duration,
    pub write_time: Duration,
    pub total_bytes_written: u64,
}

/// Read all OHLCV data for an interval from per-ticker CSV files
fn read_interval_data(interval: Interval, market_data_dir: &Path) -> Result<HashMap<String, Vec<StockData>>, Error> {
    let mut data: HashMap<String, Vec<StockData>> = HashMap::new();

    // Scan all ticker subdirectories
    let entries = std::fs::read_dir(market_data_dir)
        .map_err(|e| Error::Io(format!("Failed to read market_data directory: {}", e)))?;

    for entry in entries {
        let entry = entry.map_err(|e| Error::Io(format!("Failed to read directory entry: {}", e)))?;
        let ticker_dir = entry.path();

        if !ticker_dir.is_dir() {
            continue;
        }

        let ticker = ticker_dir.file_name()
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

            // Check field count to determine if this is raw (7) or enhanced (16) CSV
            // Header is skipped by reader, so we only see data rows
            let num_fields = record.len();
            if num_fields != 7 && num_fields != 16 {
                return Err(Error::Io(format!(
                    "Invalid CSV format in {}: expected 7 or 16 fields, got {}",
                    csv_path.display(), num_fields
                )));
            }

            // Fields 0-6 are the same for both formats (ticker, time, OHLCV)
            let time_str = record.get(1).ok_or_else(|| Error::Io("Missing time".to_string()))?;
            let open: f64 = record.get(2).ok_or_else(|| Error::Io("Missing open".to_string()))?.parse()
                .map_err(|e| Error::Io(format!("Invalid open: {}", e)))?;
            let high: f64 = record.get(3).ok_or_else(|| Error::Io("Missing high".to_string()))?.parse()
                .map_err(|e| Error::Io(format!("Invalid high: {}", e)))?;
            let low: f64 = record.get(4).ok_or_else(|| Error::Io("Missing low".to_string()))?.parse()
                .map_err(|e| Error::Io(format!("Invalid low: {}", e)))?;
            let close: f64 = record.get(5).ok_or_else(|| Error::Io("Missing close".to_string()))?.parse()
                .map_err(|e| Error::Io(format!("Invalid close: {}", e)))?;
            let volume: u64 = record.get(6).ok_or_else(|| Error::Io("Missing volume".to_string()))?.parse()
                .map_err(|e| Error::Io(format!("Invalid volume: {}", e)))?;

            // Parse datetime (handles both "YYYY-MM-DD" and "YYYY-MM-DD HH:MM:SS" formats)
            let time = if time_str.contains(' ') {
                DateTime::parse_from_str(&format!("{} +0700", time_str), "%Y-%m-%d %H:%M:%S %z")
                    .map_err(|e| Error::Io(format!("Invalid datetime: {}", e)))?
                    .with_timezone(&Utc)
            } else {
                let naive_date = NaiveDate::parse_from_str(time_str, "%Y-%m-%d")
                    .map_err(|e| Error::Io(format!("Invalid date: {}", e)))?;
                naive_date.and_hms_opt(0, 0, 0).unwrap().and_utc()
            };

            let stock_data = StockData::new(time, ticker.clone(), open, high, low, close, volume);
            ticker_data.push(stock_data);
        }

        // Sort by time (oldest first)
        ticker_data.sort_by_key(|d| d.time);

        if !ticker_data.is_empty() {
            data.insert(ticker, ticker_data);
        }
    }

    Ok(data)
}

/// Calculate moving averages and MA scores for all tickers
fn calculate_ticker_mas(data: &mut HashMap<String, Vec<StockData>>) {
    for ticker_data in data.values_mut() {
        if ticker_data.is_empty() {
            continue;
        }

        // Extract close prices
        let closes: Vec<f64> = ticker_data.iter().map(|d| d.close).collect();

        // Calculate MAs
        let ma10_values = calculate_sma(&closes, 10);
        let ma20_values = calculate_sma(&closes, 20);
        let ma50_values = calculate_sma(&closes, 50);

        // Update StockData with MA values and scores
        for (i, stock_data) in ticker_data.iter_mut().enumerate() {
            if ma10_values[i] > 0.0 {
                stock_data.ma10 = Some(ma10_values[i]);
                stock_data.ma10_score = Some(calculate_ma_score(stock_data.close, ma10_values[i]));
            }
            if ma20_values[i] > 0.0 {
                stock_data.ma20 = Some(ma20_values[i]);
                stock_data.ma20_score = Some(calculate_ma_score(stock_data.close, ma20_values[i]));
            }
            if ma50_values[i] > 0.0 {
                stock_data.ma50 = Some(ma50_values[i]);
                stock_data.ma50_score = Some(calculate_ma_score(stock_data.close, ma50_values[i]));
            }
        }
    }
}

/// Calculate money flow and dollar flow with market normalization
fn calculate_market_money_flows(
    data: &mut HashMap<String, Vec<StockData>>,
    vnindex_data: Option<&Vec<StockData>>,
) {
    // Step 1: Calculate raw money flow multipliers and flows for each ticker
    for (ticker, ticker_data) in data.iter_mut() {
        if ticker == "VNINDEX" || ticker == "VN30" {
            continue; // Skip indices
        }

        for i in 0..ticker_data.len() {
            let prev_close = if i > 0 {
                Some(ticker_data[i - 1].close)
            } else {
                None
            };

            let multiplier = calculate_money_flow_multiplier(
                ticker_data[i].open,
                ticker_data[i].high,
                ticker_data[i].low,
                ticker_data[i].close,
                prev_close,
            );

            // Calculate raw flows
            let activity_flow = multiplier * ticker_data[i].volume as f64;
            let dollar_flow = multiplier * ticker_data[i].close * ticker_data[i].volume as f64;

            // Store temporarily as raw values (will be converted to percentages below)
            ticker_data[i].money_flow = Some(activity_flow);
            ticker_data[i].dollar_flow = Some(dollar_flow);
        }
    }

    // Step 2: Calculate VNINDEX volume scaling (0.5 to 1.0 range)
    let vnindex_scaling = if let Some(vnindex) = vnindex_data {
        calculate_vnindex_scaling(vnindex)
    } else {
        HashMap::new()
    };

    // Step 3: Calculate daily totals across all tickers
    let mut daily_activity_totals: HashMap<String, f64> = HashMap::new();
    let mut daily_dollar_totals: HashMap<String, f64> = HashMap::new();

    for (ticker, ticker_data) in data.iter() {
        if ticker == "VNINDEX" || ticker == "VN30" {
            continue;
        }

        for stock_data in ticker_data {
            let date_key = stock_data.time.format("%Y-%m-%d").to_string();

            if let Some(mf) = stock_data.money_flow {
                *daily_activity_totals.entry(date_key.clone()).or_insert(0.0) += mf.abs();
            }
            if let Some(df) = stock_data.dollar_flow {
                *daily_dollar_totals.entry(date_key).or_insert(0.0) += df.abs();
            }
        }
    }

    // Step 4: Convert to percentages and apply VNINDEX scaling
    for ticker_data in data.values_mut() {
        for stock_data in ticker_data {
            let date_key = stock_data.time.format("%Y-%m-%d").to_string();
            let scaling = vnindex_scaling.get(&date_key).copied().unwrap_or(1.0);

            // Convert money flow to percentage
            if let Some(mf) = stock_data.money_flow {
                if let Some(total) = daily_activity_totals.get(&date_key) {
                    if *total > 0.0 {
                        let percentage = (mf.abs() / total) * 100.0;
                        let signed_percentage = if mf >= 0.0 { percentage } else { -percentage };
                        stock_data.money_flow = Some(signed_percentage * scaling);
                    }
                }
            }

            // Convert dollar flow to percentage
            if let Some(df) = stock_data.dollar_flow {
                if let Some(total) = daily_dollar_totals.get(&date_key) {
                    if *total > 0.0 {
                        let percentage = (df.abs() / total) * 100.0;
                        let signed_percentage = if df >= 0.0 { percentage } else { -percentage };
                        stock_data.dollar_flow = Some(signed_percentage * scaling);
                    }
                }
            }
        }
    }
}

/// Calculate VNINDEX volume scaling (0.5 to 1.0 range)
fn calculate_vnindex_scaling(vnindex_data: &[StockData]) -> HashMap<String, f64> {
    let mut scaling_map = HashMap::new();

    if vnindex_data.is_empty() {
        return scaling_map;
    }

    // Find min and max volumes
    let volumes: Vec<f64> = vnindex_data.iter().map(|d| d.volume as f64).collect();
    let min_volume = volumes.iter().copied().fold(f64::INFINITY, f64::min);
    let max_volume = volumes.iter().copied().fold(f64::NEG_INFINITY, f64::max);

    if (max_volume - min_volume).abs() < f64::EPSILON {
        // All volumes are the same, use 1.0 scaling
        for data in vnindex_data {
            let date_key = data.time.format("%Y-%m-%d").to_string();
            scaling_map.insert(date_key, 1.0);
        }
    } else {
        // Linear interpolation: 0.5 to 1.0 range
        for data in vnindex_data {
            let date_key = data.time.format("%Y-%m-%d").to_string();
            let normalized = (data.volume as f64 - min_volume) / (max_volume - min_volume);
            let scaling = 0.5 + normalized * 0.5;
            scaling_map.insert(date_key, scaling);
        }
    }

    scaling_map
}

/// Calculate trend scores (10-day rolling average of absolute money flow)
fn calculate_trend_scores(data: &mut HashMap<String, Vec<StockData>>) {
    for ticker_data in data.values_mut() {
        if ticker_data.len() < 10 {
            continue; // Not enough data for trend score
        }

        // Reverse order for rolling window (newest first matches Python)
        ticker_data.reverse();

        for i in 0..ticker_data.len() {
            let window_end = (i + 10).min(ticker_data.len());
            let window = &ticker_data[i..window_end];

            let sum: f64 = window.iter()
                .filter_map(|d| d.money_flow)
                .map(|mf| mf.abs())
                .sum();

            let count = window.len();
            ticker_data[i].trend_score = Some(sum / count as f64);
        }

        // Restore chronological order
        ticker_data.reverse();
    }
}

/// Write enhanced data back to per-ticker CSV files (16 columns)
fn write_enhanced_csv(
    data: &HashMap<String, Vec<StockData>>,
    interval: Interval,
    market_data_dir: &Path,
) -> Result<(usize, u64), Error> {
    let mut total_record_count = 0;
    let mut total_bytes_written = 0u64;

    for (ticker, ticker_data) in data {
        let ticker_dir = market_data_dir.join(ticker);
        let csv_path = ticker_dir.join(interval.to_filename());

        let mut writer = Writer::from_path(&csv_path)
            .map_err(|e| Error::Io(format!("Failed to create {}: {}", csv_path.display(), e)))?;

        // Write header
        writer.write_record(&[
            "ticker", "time", "open", "high", "low", "close", "volume",
            "ma10", "ma20", "ma50", "ma10_score", "ma20_score", "ma50_score",
            "money_flow", "dollar_flow", "trend_score"
        ]).map_err(|e| Error::Io(format!("Failed to write header to {}: {}", csv_path.display(), e)))?;

        for stock_data in ticker_data {
            // Format time based on whether it has hour/minute components
            let time_str = if stock_data.time.hour() == 0 && stock_data.time.minute() == 0 {
                stock_data.time.format("%Y-%m-%d").to_string()
            } else {
                stock_data.time.format("%Y-%m-%d %H:%M:%S").to_string()
            };

            writer.write_record(&[
                &stock_data.ticker,
                &time_str,
                &format!("{:.2}", stock_data.open),
                &format!("{:.2}", stock_data.high),
                &format!("{:.2}", stock_data.low),
                &format!("{:.2}", stock_data.close),
                &stock_data.volume.to_string(),
                &stock_data.ma10.map_or(String::new(), |v| format!("{:.2}", v)),
                &stock_data.ma20.map_or(String::new(), |v| format!("{:.2}", v)),
                &stock_data.ma50.map_or(String::new(), |v| format!("{:.2}", v)),
                &stock_data.ma10_score.map_or(String::new(), |v| format!("{:.4}", v)),
                &stock_data.ma20_score.map_or(String::new(), |v| format!("{:.4}", v)),
                &stock_data.ma50_score.map_or(String::new(), |v| format!("{:.4}", v)),
                &stock_data.money_flow.map_or(String::new(), |v| format!("{:.4}", v)),
                &stock_data.dollar_flow.map_or(String::new(), |v| format!("{:.4}", v)),
                &stock_data.trend_score.map_or(String::new(), |v| format!("{:.4}", v)),
            ]).map_err(|e| Error::Io(format!("Failed to write record to {}: {}", csv_path.display(), e)))?;

            total_record_count += 1;
        }

        writer.flush().map_err(|e| Error::Io(format!("Failed to flush {}: {}", csv_path.display(), e)))?;

        // Estimate bytes written (rough approximation: avg 65 bytes per 16-col record)
        let file_size = std::fs::metadata(&csv_path)
            .map(|m| m.len())
            .unwrap_or(0);
        total_bytes_written += file_size;
    }

    Ok((total_record_count, total_bytes_written))
}

/// Main entry point: Enhance CSV for a specific interval
pub fn enhance_interval(
    interval: Interval,
    market_data_dir: &Path,
) -> Result<EnhancementStats, Error> {
    let start_time = Instant::now();

    // Step 1: Read raw OHLCV data
    let read_start = Instant::now();
    let mut data = read_interval_data(interval, market_data_dir)?;
    let read_time = read_start.elapsed();

    if data.is_empty() {
        return Ok(EnhancementStats {
            tickers: 0,
            records: 0,
            duration: start_time.elapsed(),
            read_time: Duration::ZERO,
            ma_time: Duration::ZERO,
            money_flow_time: Duration::ZERO,
            trend_score_time: Duration::ZERO,
            write_time: Duration::ZERO,
            total_bytes_written: 0,
        });
    }

    let ticker_count = data.len();

    // Extract VNINDEX data for volume scaling
    let vnindex_data = data.get("VNINDEX").cloned();

    // Step 2: Calculate moving averages and scores
    let ma_start = Instant::now();
    calculate_ticker_mas(&mut data);
    let ma_time = ma_start.elapsed();

    // Step 3: Calculate money flows with market normalization
    let money_flow_start = Instant::now();
    calculate_market_money_flows(&mut data, vnindex_data.as_ref());
    let money_flow_time = money_flow_start.elapsed();

    // Step 4: Calculate trend scores
    let trend_score_start = Instant::now();
    calculate_trend_scores(&mut data);
    let trend_score_time = trend_score_start.elapsed();

    // Step 5: Write enhanced CSV back to per-ticker directories
    let write_start = Instant::now();
    let (record_count, total_bytes_written) = write_enhanced_csv(&data, interval, market_data_dir)?;
    let write_time = write_start.elapsed();

    Ok(EnhancementStats {
        tickers: ticker_count,
        records: record_count,
        duration: start_time.elapsed(),
        read_time,
        ma_time,
        money_flow_time,
        trend_score_time,
        write_time,
        total_bytes_written,
    })
}
