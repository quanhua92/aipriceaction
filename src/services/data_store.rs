use crate::constants::csv_column;
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

/// Cache size limits (configurable via environment variables)
pub const DEFAULT_MAX_CACHE_SIZE_MB: usize = 500; // 500MB default cache size
pub const MAX_ITEM_CACHE_SIZE_MB: usize = 100; // Don't cache individual items larger than 100MB

/// In-memory data store: HashMap<Ticker, HashMap<Interval, Vec<StockData>>>
pub type IntervalData = HashMap<Interval, Vec<StockData>>;
pub type InMemoryData = HashMap<String, IntervalData>;

/// Cache entry for tracking individual cached items
#[derive(Clone, Debug)]
struct CacheEntry {
    data: Vec<StockData>,
    size_bytes: usize,
    cached_at: DateTime<Utc>,
}

/// Cache for hourly/minute data with size tracking
/// Key: (ticker, interval)
type HourlyMinuteCache = HashMap<(String, Interval), CacheEntry>;

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

    // Disk cache statistics
    pub disk_cache_entries: usize,
    pub disk_cache_size_bytes: usize,
    pub disk_cache_size_mb: f64,
    pub disk_cache_limit_mb: usize,
    pub disk_cache_usage_percent: f64,

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
            disk_cache_entries: 0,
            disk_cache_size_bytes: 0,
            disk_cache_size_mb: 0.0,
            disk_cache_limit_mb: DEFAULT_MAX_CACHE_SIZE_MB,
            disk_cache_usage_percent: 0.0,
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
    /// Cache for disk-read data (hourly, minute, and cache=false queries)
    disk_cache: Mutex<HourlyMinuteCache>,
    /// Total size of disk cache in bytes
    disk_cache_size: Mutex<usize>,
    /// Maximum cache size in bytes (configurable via env)
    max_cache_size_bytes: usize,
}

impl DataStore {
    /// Create a new data store
    pub fn new(market_data_dir: PathBuf) -> Self {
        // Read MAX_CACHE_SIZE_MB from environment variable, default to 500MB
        let max_cache_size_mb = std::env::var("MAX_CACHE_SIZE_MB")
            .ok()
            .and_then(|s| s.parse::<usize>().ok())
            .unwrap_or(DEFAULT_MAX_CACHE_SIZE_MB);

        let max_cache_size_bytes = max_cache_size_mb * 1024 * 1024;

        tracing::info!(
            "Initializing DataStore with max_cache_size={}MB ({}bytes)",
            max_cache_size_mb,
            max_cache_size_bytes
        );

        Self {
            data: Mutex::new(HashMap::new()),
            market_data_dir,
            cache_last_updated: Mutex::new(Utc::now()),
            disk_cache: Mutex::new(HashMap::new()),
            disk_cache_size: Mutex::new(0),
            max_cache_size_bytes,
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

            // Parse technical indicators if present (enhanced CSV format)
            let mut stock_data = StockData::new(time, ticker.to_string(), open, high, low, close, volume);

            if record.len() >= csv_column::VOLUME_CHANGED + 1 {
                // Parse MAs
                stock_data.ma10 = record.get(csv_column::MA10).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma20 = record.get(csv_column::MA20).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma50 = record.get(csv_column::MA50).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });

                // Parse MA scores
                stock_data.ma10_score = record.get(csv_column::MA10_SCORE).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma20_score = record.get(csv_column::MA20_SCORE).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma50_score = record.get(csv_column::MA50_SCORE).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });

                // Parse change indicators (percentage change from previous row)
                stock_data.close_changed = record.get(csv_column::CLOSE_CHANGED).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.volume_changed = record.get(csv_column::VOLUME_CHANGED).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
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
    /// Smart caching: if cache doesn't have requested range or is expired, automatically read from disk
    pub async fn get_data_with_cache(
        &self,
        tickers: Vec<String>,
        interval: Interval,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
        use_cache: bool,
    ) -> HashMap<String, Vec<StockData>> {
        // For hourly/minute data or when cache=false explicitly requested, always use disk
        if interval == Interval::Hourly || interval == Interval::Minute || !use_cache {
            return self.get_data_from_disk_with_cache(tickers, interval, start_date, end_date).await;
        }

        // For daily data with cache=true: check if cache is expired (TTL)
        let cache_expired = self.is_cache_expired().await;
        if cache_expired {
            tracing::info!("In-memory cache expired (TTL: {}s), reading from disk", CACHE_TTL_SECONDS);
            // Cache expired - read from disk for all tickers
            return self.get_data_from_disk_with_cache(tickers, interval, start_date, end_date).await;
        }

        // Cache is fresh - try cache first, fall back to disk if insufficient
        let store = self.data.lock().await;
        let mut result = HashMap::new();
        let mut need_disk_read: Vec<String> = Vec::new();

        for ticker in &tickers {
            if let Some(ticker_data) = store.get(ticker) {
                if let Some(interval_data) = ticker_data.get(&interval) {
                    // Check if cache has the requested date range
                    let has_required_range = if let Some(start) = start_date {
                        // Check if cache has data going back to requested start date
                        interval_data.first().map(|d| d.time <= start).unwrap_or(false)
                    } else {
                        true // No specific start requested, cache is fine
                    };

                    if has_required_range {
                        // Cache has sufficient data
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
                    } else {
                        // Cache doesn't have full range - need disk read
                        tracing::debug!(
                            "Cache insufficient for {} (requested start: {:?}, cache starts: {:?})",
                            ticker,
                            start_date,
                            interval_data.first().map(|d| d.time)
                        );
                        need_disk_read.push(ticker.clone());
                    }
                } else {
                    // No data in cache for this ticker/interval
                    need_disk_read.push(ticker.clone());
                }
            } else {
                // Ticker not in cache
                need_disk_read.push(ticker.clone());
            }
        }

        drop(store); // Release lock before disk read

        // Read missing tickers from disk
        if !need_disk_read.is_empty() {
            tracing::info!(
                "Reading {} tickers from disk (cache insufficient): {:?}",
                need_disk_read.len(),
                need_disk_read
            );
            let disk_data = self.get_data_from_disk_with_cache(
                need_disk_read,
                interval,
                start_date,
                end_date,
            ).await;

            // Merge disk data into result
            result.extend(disk_data);
        }

        result
    }

    /// Read data from disk with caching (for hourly/minute intervals and cache=false queries)
    /// Also updates in-memory cache timestamp when reading daily data
    async fn get_data_from_disk_with_cache(
        &self,
        tickers: Vec<String>,
        interval: Interval,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
    ) -> HashMap<String, Vec<StockData>> {
        let mut result = HashMap::new();

        for ticker in tickers {
            let cache_key = (ticker.clone(), interval);

            // Check disk cache first
            let cache_hit = {
                let disk_cache = self.disk_cache.lock().await;
                if let Some(entry) = disk_cache.get(&cache_key) {
                    // Check if cache entry is still valid (TTL)
                    let now = Utc::now();
                    let age = now.signed_duration_since(entry.cached_at);
                    if age.num_seconds() < CACHE_TTL_SECONDS {
                        Some(entry.data.clone())
                    } else {
                        None
                    }
                } else {
                    None
                }
            };

            let data = if let Some(cached_data) = cache_hit {
                tracing::debug!("Disk cache hit for {}/{}", ticker, interval.to_filename());
                cached_data
            } else {
                // Cache miss, read from disk
                let csv_path = self.market_data_dir.join(&ticker).join(interval.to_filename());

                if !csv_path.exists() {
                    continue;
                }

                match self.read_csv_file(&csv_path, &ticker, interval, None) {
                    Ok(data) => {
                        if data.is_empty() {
                            continue;
                        }

                        // Calculate size of this data
                        let item_size = self.estimate_data_size(&data);

                        // Only cache if item size is under 100MB
                        if item_size <= MAX_ITEM_CACHE_SIZE_MB * 1024 * 1024 {
                            // Try to add to cache
                            self.try_add_to_cache(cache_key.clone(), data.clone(), item_size).await;
                        } else {
                            tracing::debug!(
                                "Skipping cache for {}/{} (size {}MB > {}MB limit)",
                                ticker,
                                interval.to_filename(),
                                item_size / (1024 * 1024),
                                MAX_ITEM_CACHE_SIZE_MB
                            );
                        }

                        data
                    }
                    Err(e) => {
                        tracing::warn!("Failed to read {} data for {}: {}", interval.to_filename(), ticker, e);
                        continue;
                    }
                }
            };

            // Filter by date range
            let filtered = if start_date.is_some() || end_date.is_some() {
                data.into_iter()
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
                    .collect()
            } else {
                data
            };

            if !filtered.is_empty() {
                result.insert(ticker.clone(), filtered);
            }
        }

        // Update cache timestamp for daily data (to reset TTL)
        if interval == Interval::Daily && !result.is_empty() {
            self.update_cache_timestamp().await;
            tracing::debug!("Updated in-memory cache timestamp after disk read");
        }

        result
    }

    /// Try to add data to cache, evicting old entries if necessary
    async fn try_add_to_cache(&self, key: (String, Interval), data: Vec<StockData>, size: usize) {
        let mut disk_cache = self.disk_cache.lock().await;
        let mut disk_cache_size = self.disk_cache_size.lock().await;

        // Check if we need to evict entries to make room
        while *disk_cache_size + size > self.max_cache_size_bytes && !disk_cache.is_empty() {
            // Evict the oldest entry (LRU-like)
            if let Some(oldest_key) = disk_cache
                .iter()
                .min_by_key(|(_, entry)| entry.cached_at)
                .map(|(k, _)| k.clone())
            {
                if let Some(evicted) = disk_cache.remove(&oldest_key) {
                    *disk_cache_size -= evicted.size_bytes;
                    tracing::debug!(
                        "Evicted cache entry for {}/{} (size {}MB)",
                        oldest_key.0,
                        oldest_key.1.to_filename(),
                        evicted.size_bytes / (1024 * 1024)
                    );
                }
            } else {
                break;
            }
        }

        // Add new entry if there's room
        if *disk_cache_size + size <= self.max_cache_size_bytes {
            let entry = CacheEntry {
                data,
                size_bytes: size,
                cached_at: Utc::now(),
            };

            disk_cache.insert(key.clone(), entry);
            *disk_cache_size += size;

            tracing::debug!(
                "Cached {}/{} (size {}MB, total cache {}MB/{}MB)",
                key.0,
                key.1.to_filename(),
                size / (1024 * 1024),
                *disk_cache_size / (1024 * 1024),
                self.max_cache_size_bytes / (1024 * 1024)
            );
        } else {
            tracing::warn!(
                "Cannot cache {}/{} (size {}MB) - would exceed cache limit",
                key.0,
                key.1.to_filename(),
                size / (1024 * 1024)
            );
        }
    }

    /// Estimate size of StockData vector
    fn estimate_data_size(&self, data: &[StockData]) -> usize {
        let mut size = std::mem::size_of::<Vec<StockData>>();
        size += data.len() * std::mem::size_of::<StockData>();
        for item in data {
            size += item.ticker.len();
        }
        size
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

    /// Get disk cache statistics (entries count, size, limit)
    pub async fn get_disk_cache_stats(&self) -> (usize, usize, usize) {
        let disk_cache = self.disk_cache.lock().await;
        let disk_cache_size = self.disk_cache_size.lock().await;
        (disk_cache.len(), *disk_cache_size, self.max_cache_size_bytes)
    }

    /// Check if in-memory cache has expired based on TTL
    async fn is_cache_expired(&self) -> bool {
        let cache_last_updated = self.cache_last_updated.lock().await;
        let now = Utc::now();
        let cache_age = now.signed_duration_since(*cache_last_updated);
        cache_age.num_seconds() >= CACHE_TTL_SECONDS
    }

    /// Update cache timestamp (called after reading fresh data from disk)
    async fn update_cache_timestamp(&self) {
        let mut cache_last_updated = self.cache_last_updated.lock().await;
        *cache_last_updated = Utc::now();
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
