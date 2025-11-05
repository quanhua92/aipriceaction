use crate::error::Error;
use crate::models::{Interval, StockData};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::Mutex;

/// Memory management constants
pub const MAX_MEMORY_MB: usize = 4096; // 4GB limit for 1 year of data
pub const MAX_MEMORY_BYTES: usize = MAX_MEMORY_MB * 1024 * 1024;
pub const DATA_RETENTION_DAYS: i64 = 365; // Keep 1 year of data

/// In-memory data store: HashMap<Ticker, HashMap<Interval, Vec<StockData>>>
pub type IntervalData = HashMap<Interval, Vec<StockData>>;
pub type InMemoryData = HashMap<String, IntervalData>;
pub type SharedDataStore = Arc<Mutex<InMemoryData>>;

/// Health statistics for the data store
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HealthStats {
    // Worker statistics
    pub daily_last_sync: Option<String>,
    pub hourly_last_sync: Option<String>,
    pub minute_last_sync: Option<String>,
    pub daily_iteration_count: u64,
    pub slow_iteration_count: u64,

    // Memory statistics
    pub memory_usage_bytes: usize,
    pub memory_usage_mb: f64,
    pub memory_limit_mb: usize,
    pub memory_usage_percent: f64,

    // Ticker statistics
    pub total_tickers_count: usize,
    pub active_tickers_count: usize,
    pub daily_records_count: usize,
    pub hourly_records_count: usize,
    pub minute_records_count: usize,

    // System info
    pub uptime_secs: u64,
    pub current_system_time: String,
}

impl Default for HealthStats {
    fn default() -> Self {
        Self {
            daily_last_sync: None,
            hourly_last_sync: None,
            minute_last_sync: None,
            daily_iteration_count: 0,
            slow_iteration_count: 0,
            memory_usage_bytes: 0,
            memory_usage_mb: 0.0,
            memory_limit_mb: MAX_MEMORY_MB,
            memory_usage_percent: 0.0,
            total_tickers_count: 0,
            active_tickers_count: 0,
            daily_records_count: 0,
            hourly_records_count: 0,
            minute_records_count: 0,
            uptime_secs: 0,
            current_system_time: Utc::now().to_rfc3339(),
        }
    }
}

pub type SharedHealthStats = Arc<Mutex<HealthStats>>;

/// Data store for managing in-memory stock data
pub struct DataStore {
    data: SharedDataStore,
    market_data_dir: PathBuf,
}

impl DataStore {
    /// Create a new data store
    pub fn new(market_data_dir: PathBuf) -> Self {
        Self {
            data: Arc::new(Mutex::new(HashMap::new())),
            market_data_dir,
        }
    }

    /// Get shared reference to the data store
    pub fn shared(&self) -> SharedDataStore {
        self.data.clone()
    }

    /// Load last 1 year of data from CSV files for specified intervals
    pub async fn load_last_year(&self, intervals: Vec<Interval>) -> Result<(), Error> {
        let cutoff_date = Utc::now() - Duration::days(DATA_RETENTION_DAYS);

        for interval in intervals {
            self.load_interval(interval, Some(cutoff_date)).await?;
        }

        Ok(())
    }

    /// Load data for a specific interval from CSV files
    async fn load_interval(&self, interval: Interval, cutoff_date: Option<DateTime<Utc>>) -> Result<(), Error> {
        let mut data = HashMap::new();

        // Read all ticker directories
        let entries = std::fs::read_dir(&self.market_data_dir)
            .map_err(|e| Error::Io(format!("Failed to read market_data dir: {}", e)))?;

        for entry in entries {
            let entry = entry.map_err(|e| Error::Io(format!("Failed to read entry: {}", e)))?;
            let ticker_dir = entry.path();

            if !ticker_dir.is_dir() {
                continue;
            }

            let ticker = ticker_dir
                .file_name()
                .and_then(|n| n.to_str())
                .ok_or_else(|| Error::Io("Invalid ticker directory name".to_string()))?
                .to_string();

            let csv_path = ticker_dir.join(interval.to_filename());
            if !csv_path.exists() {
                continue;
            }

            // Read and parse CSV
            match self.read_csv_file(&csv_path, &ticker, cutoff_date) {
                Ok(ticker_data) => {
                    if !ticker_data.is_empty() {
                        data.insert(ticker.clone(), ticker_data);
                    }
                }
                Err(e) => {
                    tracing::warn!("Failed to read CSV for {}/{}: {}", ticker, interval.to_filename(), e);
                    continue;
                }
            }
        }

        // Update shared data store for this interval
        let mut store = self.data.lock().await;
        for (ticker, ticker_data) in data {
            store.entry(ticker)
                .or_insert_with(HashMap::new)
                .insert(interval, ticker_data);
        }

        Ok(())
    }

    /// Read a single CSV file and return StockData vector
    fn read_csv_file(&self, csv_path: &Path, ticker: &str, cutoff_date: Option<DateTime<Utc>>) -> Result<Vec<StockData>, Error> {
        let mut reader = csv::ReaderBuilder::new()
            .flexible(true) // Allow 7 or 16 columns
            .from_path(csv_path)?;

        let mut data = Vec::new();

        for result in reader.records() {
            let record = result?;

            // Parse time
            let time_str = record.get(1).ok_or_else(|| Error::Io("Missing time field".to_string()))?;
            let time = chrono::DateTime::parse_from_rfc3339(time_str)
                .map(|dt| dt.with_timezone(&Utc))
                .map_err(|e| Error::Io(format!("Invalid datetime: {}", e)))?;

            // Skip data older than cutoff
            if let Some(cutoff) = cutoff_date {
                if time < cutoff {
                    continue;
                }
            }

            // Parse OHLCV
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

            // Parse technical indicators if present (16 columns)
            let mut stock_data = StockData::new(time, ticker.to_string(), open, high, low, close, volume);

            if record.len() >= 16 {
                // Parse MAs
                stock_data.ma10 = record.get(7).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma20 = record.get(8).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma50 = record.get(9).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });

                // Parse MA scores
                stock_data.ma10_score = record.get(10).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma20_score = record.get(11).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma50_score = record.get(12).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });

                // Parse flow indicators
                stock_data.money_flow = record.get(13).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.dollar_flow = record.get(14).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.trend_score = record.get(15).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
            }

            data.push(stock_data);
        }

        // Sort by time
        data.sort_by(|a, b| a.time.cmp(&b.time));

        Ok(data)
    }

    /// Reload a specific interval from CSV files
    pub async fn reload_interval(&self, interval: Interval) -> Result<(), Error> {
        let cutoff_date = Utc::now() - Duration::days(DATA_RETENTION_DAYS);
        self.load_interval(interval, Some(cutoff_date)).await
    }

    /// Get data for specific tickers and interval
    pub async fn get_data(
        &self,
        tickers: Vec<String>,
        interval: Interval,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
    ) -> HashMap<String, Vec<StockData>> {
        let store = self.data.lock().await;
        let mut result = HashMap::new();

        for ticker in tickers {
            if let Some(ticker_data) = store.get(&ticker) {
                if let Some(interval_data) = ticker_data.get(&interval) {
                    let filtered: Vec<StockData> = interval_data
                        .iter()
                        .filter(|d| {
                            if let Some(start) = start_date {
                                if d.time < start {
                                    return false;
                                }
                            }
                            if let Some(end) = end_date {
                                if d.time > end {
                                    return false;
                                }
                            }
                            true
                        })
                        .cloned()
                        .collect();

                    if !filtered.is_empty() {
                        result.insert(ticker.clone(), filtered);
                    }
                }
            }
        }

        result
    }

    /// Estimate memory usage of the data store
    pub async fn estimate_memory_usage(&self) -> usize {
        let store = self.data.lock().await;
        estimate_memory_usage(&*store)
    }

    /// Get count of records per interval
    pub async fn get_record_counts(&self) -> (usize, usize, usize) {
        let store = self.data.lock().await;
        let mut daily_count = 0;
        let mut hourly_count = 0;
        let mut minute_count = 0;

        for (_ticker, intervals) in store.iter() {
            if let Some(data) = intervals.get(&Interval::Daily) {
                daily_count += data.len();
            }
            if let Some(data) = intervals.get(&Interval::Hourly) {
                hourly_count += data.len();
            }
            if let Some(data) = intervals.get(&Interval::Minute) {
                minute_count += data.len();
            }
        }

        (daily_count, hourly_count, minute_count)
    }

    /// Get active ticker count (tickers with data)
    pub async fn get_active_ticker_count(&self) -> usize {
        let store = self.data.lock().await;
        store.len()
    }
}

/// Estimate memory usage of in-memory data
pub fn estimate_memory_usage(data: &InMemoryData) -> usize {
    let mut total_size = std::mem::size_of::<InMemoryData>();

    for (ticker, intervals) in data {
        total_size += ticker.len(); // Ticker string
        total_size += std::mem::size_of::<IntervalData>(); // HashMap overhead

        for (_interval, stock_data_vec) in intervals {
            total_size += std::mem::size_of::<Vec<StockData>>(); // Vec overhead
            total_size += stock_data_vec.capacity() * std::mem::size_of::<StockData>();

            for stock_data in stock_data_vec {
                total_size += stock_data.ticker.len(); // Ticker string in StockData
            }
        }
    }

    total_size
}
