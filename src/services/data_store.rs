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

/// Cache TTL constants
pub const CACHE_TTL_SECONDS: i64 = 60; // 1 minute TTL for memory cache

/// In-memory data store: HashMap<Ticker, HashMap<Interval, Vec<StockData>>>
pub type IntervalData = HashMap<Interval, Vec<StockData>>;
pub type InMemoryData = HashMap<String, IntervalData>;

// Shared data store for passing between threads
pub type SharedDataStore = Arc<DataStore>;

/// Health statistics for the data store
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HealthStats {
    // Worker statistics
    pub daily_last_sync: Option<String>,
    pub hourly_last_sync: Option<String>,
    pub minute_last_sync: Option<String>,
    pub daily_iteration_count: u64,
    pub slow_iteration_count: u64,

    // Trading hours info
    pub is_trading_hours: bool,
    pub trading_hours_timezone: String,

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
            is_trading_hours: false,
            trading_hours_timezone: "Asia/Ho_Chi_Minh".to_string(),
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
    data: Mutex<InMemoryData>,
    market_data_dir: PathBuf,
    cache_last_updated: Mutex<DateTime<Utc>>,
}

impl DataStore {
    /// Create a new data store
    pub fn new(market_data_dir: PathBuf) -> Self {
        Self {
            data: Mutex::new(HashMap::new()),
            market_data_dir,
            cache_last_updated: Mutex::new(Utc::now()),
        }
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
            match self.read_csv_file(&csv_path, &ticker, interval, cutoff_date) {
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

        // Update cache timestamp to reflect fresh data
        {
            let mut cache_last_updated = self.cache_last_updated.lock().await;
            *cache_last_updated = Utc::now();
        }

        Ok(())
    }

    /// Read a single CSV file and return StockData vector
    fn read_csv_file(&self, csv_path: &Path, ticker: &str, interval: Interval, cutoff_date: Option<DateTime<Utc>>) -> Result<Vec<StockData>, Error> {
        let mut reader = csv::ReaderBuilder::new()
            .flexible(true) // Allow 7 or 16 columns
            .from_path(csv_path)?;

        let mut data = Vec::new();

        for result in reader.records() {
            let record = result?;

            // Parse time based on interval format
            let time_str = record.get(1).ok_or_else(|| Error::Io("Missing time field".to_string()))?;
            let time = match interval {
                Interval::Daily => {
                    // Daily format: "2025-01-05"
                    let naive_date = chrono::NaiveDate::parse_from_str(time_str, "%Y-%m-%d")
                        .map_err(|e| Error::Io(format!("Invalid date: {}", e)))?;
                    DateTime::<Utc>::from_naive_utc_and_offset(
                        naive_date.and_hms_opt(0, 0, 0).unwrap(),
                        Utc
                    )
                }
                Interval::Hourly | Interval::Minute => {
                    // Hourly/Minute format: "2023-09-10 13:00:00"
                    let naive_dt = chrono::NaiveDateTime::parse_from_str(time_str, "%Y-%m-%d %H:%M:%S")
                        .map_err(|e| Error::Io(format!("Invalid datetime: {}", e)))?;
                    DateTime::<Utc>::from_naive_utc_and_offset(naive_dt, Utc)
                }
            };

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
    /// - Daily: Read from memory
    /// - Hourly/Minute: Read from disk on-demand
    pub async fn get_data(
        &self,
        tickers: Vec<String>,
        interval: Interval,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
    ) -> HashMap<String, Vec<StockData>> {
        self.get_data_with_cache(tickers, interval, start_date, end_date, true).await
    }

    /// Get data with cache control option
    pub async fn get_data_with_cache(
        &self,
        tickers: Vec<String>,
        interval: Interval,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
        use_cache: bool,
    ) -> HashMap<String, Vec<StockData>> {
        // For hourly/minute data, read directly from disk
        if interval == Interval::Hourly || interval == Interval::Minute {
            return self.get_data_from_disk(tickers, interval, start_date, end_date);
        }

        // For daily data, check cache preference
        if !use_cache {
            return self.get_data_from_disk(tickers, interval, start_date, end_date);
        }

        // Check if cache is expired (TTL)
        if self.is_cache_expired().await {
            tracing::debug!("Cache expired (TTL: {}s), refreshing from disk", CACHE_TTL_SECONDS);
            // Refresh cache from disk and then serve fresh data
            if let Err(e) = self.refresh_cache(interval).await {
                tracing::warn!("Failed to refresh cache: {}, falling back to disk read", e);
                return self.get_data_from_disk(tickers, interval, start_date, end_date);
            }
        }

        // Use in-memory cache
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

    /// Read data from disk (for hourly/minute intervals)
    fn get_data_from_disk(
        &self,
        tickers: Vec<String>,
        interval: Interval,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
    ) -> HashMap<String, Vec<StockData>> {
        let mut result = HashMap::new();

        for ticker in tickers {
            let csv_path = self.market_data_dir.join(&ticker).join(interval.to_filename());
            if !csv_path.exists() {
                continue;
            }

            match self.read_csv_file(&csv_path, &ticker, interval, None) {
                Ok(mut data) => {
                    // Filter by date range
                    if start_date.is_some() || end_date.is_some() {
                        data.retain(|d| {
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
                        });
                    }

                    if !data.is_empty() {
                        result.insert(ticker.clone(), data);
                    }
                }
                Err(e) => {
                    tracing::warn!("Failed to read {} data for {}: {}", interval.to_filename(), ticker, e);
                    continue;
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

    /// Get all ticker names (from in-memory data)
    pub async fn get_all_ticker_names(&self) -> Vec<String> {
        let store = self.data.lock().await;
        store.keys().cloned().collect()
    }

    /// Check if cache has expired based on TTL
    async fn is_cache_expired(&self) -> bool {
        let cache_last_updated = self.cache_last_updated.lock().await;
        let now = Utc::now();
        let cache_age = now.signed_duration_since(*cache_last_updated);
        cache_age.num_seconds() >= CACHE_TTL_SECONDS
    }

    /// Refresh cache from disk for a specific interval
    async fn refresh_cache(&self, interval: Interval) -> Result<(), Error> {
        tracing::info!("Refreshing cache for interval: {}", interval.to_filename());

        // Load fresh data from disk
        let cutoff_date = Utc::now() - Duration::days(DATA_RETENTION_DAYS);
        self.load_interval(interval, Some(cutoff_date)).await?;

        // Update cache timestamp
        {
            let mut cache_last_updated = self.cache_last_updated.lock().await;
            *cache_last_updated = Utc::now();
        }

        tracing::info!("Cache refreshed successfully for interval: {}", interval.to_filename());
        Ok(())
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
