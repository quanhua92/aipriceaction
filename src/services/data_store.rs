use crate::constants::csv_column;
use crate::error::Error;
use crate::models::{Interval, StockData, AggregatedInterval};
use crate::utils::{parse_timestamp, deduplicate_stock_data_by_time};
use tracing::{debug, info};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::SystemTime;
use tokio::sync::RwLock;
use tokio::task::JoinHandle;

/// Memory management constants
pub const MAX_MEMORY_MB: usize = 4096; // 4GB limit
pub const MAX_MEMORY_BYTES: usize = MAX_MEMORY_MB * 1024 * 1024;
pub const DATA_RETENTION_RECORDS: usize = 730; // Keep last 730 records per ticker per interval

/// Cache TTL constants
pub const CACHE_TTL_SECONDS: i64 = 30; // 30 seconds TTL for memory cache (reduced from 15s for CPU efficiency)

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
    pub crypto_last_sync: Option<String>,
    pub daily_iteration_count: u64,
    pub slow_iteration_count: u64,
    pub crypto_iteration_count: u64,

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
            crypto_last_sync: None,
            daily_iteration_count: 0,
            slow_iteration_count: 0,
            crypto_iteration_count: 0,
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

pub type SharedHealthStats = Arc<RwLock<HealthStats>>;

/// Query parameters for the smart data store method
#[derive(Debug, Clone)]
pub struct QueryParameters {
    /// Ticker symbols to query
    pub tickers: Vec<String>,
    /// Interval (1D, 1H, 1m) - parsed from request
    pub interval: Interval,
    /// Aggregated interval if specified (5m, 15m, 30m, 1W, 2W, 1M)
    pub aggregated_interval: Option<AggregatedInterval>,
    /// Start date filter
    pub start_date: Option<DateTime<Utc>>,
    /// End date filter
    pub end_date: Option<DateTime<Utc>>,
    /// Limit number of records (defaults to 365 if None)
    pub limit: usize,
    /// Use memory cache (default: true)
    pub use_cache: bool,
    /// Apply legacy price scaling
    pub legacy_prices: bool,
}

impl QueryParameters {
    /// Create new query parameters with defaults
    pub fn new(
        tickers: Vec<String>,
        interval: Interval,
        aggregated_interval: Option<AggregatedInterval>,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
        limit: Option<usize>,
        use_cache: bool,
        legacy_prices: bool,
    ) -> Self {
        Self {
            tickers,
            interval,
            aggregated_interval,
            start_date,
            end_date,
            limit: limit.unwrap_or(252), // Default to 252 trading days per year
            use_cache,
            legacy_prices,
        }
    }

    /// Calculate the effective limit for base interval data fetching
    /// For aggregated intervals, we need extra records for MA200 calculation
    pub fn effective_limit(&self) -> usize {
        if let Some(agg_interval) = self.aggregated_interval {
            // For aggregated intervals, we need: (requested_records * multiplier) + MA200_buffer
            let multiplier = match agg_interval {
                AggregatedInterval::Minutes5 => 5,
                AggregatedInterval::Minutes15 => 15,
                AggregatedInterval::Minutes30 => 30,
                AggregatedInterval::Week => 7,
                AggregatedInterval::Week2 => 14,
                AggregatedInterval::Month => 30,
            };
            (self.limit * multiplier) + (200 * multiplier) // MA200 buffer on top of requested records
        } else {
            self.limit // No buffer needed for regular intervals
        }
    }

    /// Determine if we need to fetch more than the requested limit
    pub fn needs_extra_buffer(&self) -> bool {
        self.aggregated_interval.is_some()
    }
}

/// Data store for managing in-memory stock data
pub struct DataStore {
    data: RwLock<InMemoryData>,
    market_data_dir: PathBuf,
    cache_last_updated: RwLock<DateTime<Utc>>,
    /// Cache for disk-read data (hourly, minute, and cache=false queries)
    disk_cache: RwLock<HourlyMinuteCache>,
    /// Total size of disk cache in bytes
    disk_cache_size: RwLock<usize>,
    /// Maximum cache size in bytes (configurable via env)
    max_cache_size_bytes: usize,
    /// File modification time tracker for auto-reload optimization
    /// Key: (ticker, interval), Value: SystemTime from fs::metadata().modified()
    file_mtimes: RwLock<HashMap<(String, Interval), SystemTime>>,
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
            data: RwLock::new(HashMap::new()),
            market_data_dir,
            cache_last_updated: RwLock::new(Utc::now()),
            disk_cache: RwLock::new(HashMap::new()),
            disk_cache_size: RwLock::new(0),
            max_cache_size_bytes,
            file_mtimes: RwLock::new(HashMap::new()),
        }
    }

    /// Load data from CSV files for specified intervals (limited to last 730 records per ticker)
    pub async fn load_last_year(&self, intervals: Vec<Interval>) -> Result<(), Error> {
        for interval in intervals {
            self.load_interval(interval, None, Some(DATA_RETENTION_RECORDS)).await?;
        }

        Ok(())
    }

    /// Quick check data integrity after loading
    /// Verifies all tickers have data for the last 10 trading days (based on VNINDEX)
    pub async fn quick_check_data(&self) -> Result<(), Error> {
        let data = self.data.read().await;

        // Get VNINDEX daily data to determine reference trading days
        let vnindex_data = match data.get("VNINDEX") {
            Some(intervals) => match intervals.get(&Interval::Daily) {
                Some(records) => records,
                None => {
                    tracing::error!("QUICK_CHECK: VNINDEX has no daily data");
                    return Ok(());
                }
            },
            None => {
                tracing::error!("QUICK_CHECK: VNINDEX not found in memory");
                return Ok(());
            }
        };

        // Get last 10 trading dates from VNINDEX
        let reference_dates: Vec<chrono::NaiveDate> = vnindex_data
            .iter()
            .rev()
            .take(10)
            .map(|d| d.time.date_naive())
            .collect();

        if reference_dates.is_empty() {
            tracing::error!("QUICK_CHECK: VNINDEX has no records");
            return Ok(());
        }

        println!(
            "🔍 QUICK_CHECK: Checking {} tickers against {} reference dates ({} to {})",
            data.len(),
            reference_dates.len(),
            reference_dates.last().map(|d| d.to_string()).unwrap_or_default(),
            reference_dates.first().map(|d| d.to_string()).unwrap_or_default()
        );

        let mut missing_count = 0;

        for (ticker, intervals) in data.iter() {
            if ticker == "VNINDEX" {
                continue; // Skip reference ticker
            }

            if let Some(daily_data) = intervals.get(&Interval::Daily) {
                // Get dates this ticker has
                let ticker_dates: std::collections::HashSet<chrono::NaiveDate> =
                    daily_data.iter().map(|d| d.time.date_naive()).collect();

                // Check for missing dates
                let missing_dates: Vec<&chrono::NaiveDate> = reference_dates
                    .iter()
                    .filter(|d| !ticker_dates.contains(d))
                    .collect();

                if !missing_dates.is_empty() {
                    missing_count += 1;
                    eprintln!(
                        "❌ QUICK_CHECK: {} missing {} dates: {:?}",
                        ticker,
                        missing_dates.len(),
                        missing_dates
                    );
                }
            } else {
                missing_count += 1;
                eprintln!("❌ QUICK_CHECK: {} has no daily data", ticker);
            }
        }

        if missing_count == 0 {
            println!("✅ QUICK_CHECK: All {} tickers have complete data for last 10 trading days", data.len() - 1);
        } else {
            eprintln!("⚠️  QUICK_CHECK: {} tickers have missing data", missing_count);
        }

        Ok(())
    }

    /// Load data for a specific interval from CSV files with optional record limit
    async fn load_interval(&self, interval: Interval, cutoff_date: Option<DateTime<Utc>>, limit: Option<usize>) -> Result<(), Error> {
        // Step 1: Read all CSV files WITHOUT holding any locks
        // This prevents file I/O from blocking API requests
        let mut data = HashMap::new();
        let mut files_read = 0;
        let mut files_skipped = 0;

        // Read current mtimes snapshot (acquire lock briefly)
        let mtimes_snapshot = {
            let mtimes = self.file_mtimes.read().await;
            mtimes.clone()
        };

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

            // Check modification time before reading
            let mtime_key = (ticker.clone(), interval);
            let should_read = match std::fs::metadata(&csv_path) {
                Ok(metadata) => {
                    match metadata.modified() {
                        Ok(current_mtime) => {
                            // Compare with stored mtime
                            match mtimes_snapshot.get(&mtime_key) {
                                Some(stored_mtime) => {
                                    if current_mtime != *stored_mtime {
                                        // File modified, need to reload
                                        tracing::debug!("File modified: {}/{}", ticker, interval.to_filename());
                                        true
                                    } else {
                                        // File unchanged, skip reading
                                        tracing::debug!("File unchanged, skipping: {}/{}", ticker, interval.to_filename());
                                        files_skipped += 1;
                                        false
                                    }
                                }
                                None => {
                                    // New file, need to load
                                    tracing::debug!("New file detected: {}/{}", ticker, interval.to_filename());
                                    true
                                }
                            }
                        }
                        Err(e) => {
                            tracing::warn!("Failed to get mtime for {}/{}: {}", ticker, interval.to_filename(), e);
                            true // Read anyway if mtime check fails
                        }
                    }
                }
                Err(e) => {
                    tracing::warn!("Failed to get metadata for {}/{}: {}", ticker, interval.to_filename(), e);
                    true // Read anyway if metadata check fails
                }
            };

            // Only read file if it has changed or is new
            if should_read {
                // Read and parse CSV (blocking I/O happens here, NO locks held)
                match self.read_csv_file(&csv_path, &ticker, interval, cutoff_date) {
                    Ok(mut ticker_data) => {
                        if !ticker_data.is_empty() {
                            // Apply record limit if specified (keep last N records)
                            if let Some(max_records) = limit {
                                if ticker_data.len() > max_records {
                                    // Sort descending by time and take last N records
                                    ticker_data.sort_by(|a, b| b.time.cmp(&a.time));
                                    ticker_data.truncate(max_records);
                                    // Sort back to ascending order for consistency
                                    ticker_data.sort_by(|a, b| a.time.cmp(&b.time));
                                }
                            }
                            data.insert(ticker.clone(), ticker_data);
                            files_read += 1;
                        }
                    }
                    Err(e) => {
                        tracing::warn!("Failed to read CSV for {}/{}: {}", ticker, interval.to_filename(), e);
                        continue;
                    }
                }
            }
        }

        tracing::info!(
            "Auto-reload {} completed: {} files read, {} files skipped (unchanged)",
            interval.to_filename(),
            files_read,
            files_skipped
        );

        // Step 2: Acquire write lock ONLY to update in-memory cache (fast operation)
        // File I/O is complete, so this lock is held very briefly
        let loaded_tickers: Vec<String> = {
            let mut store = self.data.write().await;
            let mut loaded = Vec::new();
            for (ticker, ticker_data) in data {
                loaded.push(ticker.clone());
                store.entry(ticker)
                    .or_insert_with(HashMap::new)
                    .insert(interval, ticker_data);
            }
            loaded
        }; // Write lock released immediately

        // Step 3: Update modification times for loaded files
        {
            let mut mtimes = self.file_mtimes.write().await;
            for ticker in &loaded_tickers {
                let csv_path = self.market_data_dir.join(ticker).join(interval.to_filename());
                if let Ok(metadata) = std::fs::metadata(&csv_path) {
                    if let Ok(mtime) = metadata.modified() {
                        mtimes.insert((ticker.clone(), interval), mtime);
                    }
                }
            }
        }

        // Step 4: Update cache timestamp (separate lock, also fast)
        {
            let mut cache_last_updated = self.cache_last_updated.write().await;
            *cache_last_updated = Utc::now();
        }

        Ok(())
    }

    /// Read a single CSV file and return StockData vector
    fn read_csv_file(&self, csv_path: &Path, ticker: &str, _interval: Interval, cutoff_date: Option<DateTime<Utc>>) -> Result<Vec<StockData>, Error> {
        let mut reader = csv::ReaderBuilder::new()
            .flexible(true) // Allow 7 or 16 columns
            .from_path(csv_path)?;

        let mut data = Vec::new();

        for result in reader.records() {
            let record = result?;

            // Parse time based on interval format
            let time_str = record.get(1).ok_or_else(|| Error::Io("Missing time field".to_string()))?;
            let time = parse_timestamp(time_str)?;

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
                stock_data.ma100 = record.get(csv_column::MA100).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma200 = record.get(csv_column::MA200).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });

                // Parse MA scores
                stock_data.ma10_score = record.get(csv_column::MA10_SCORE).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma20_score = record.get(csv_column::MA20_SCORE).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma50_score = record.get(csv_column::MA50_SCORE).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma100_score = record.get(csv_column::MA100_SCORE).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.ma200_score = record.get(csv_column::MA200_SCORE).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });

                // Parse change indicators (percentage change from previous row)
                stock_data.close_changed = record.get(csv_column::CLOSE_CHANGED).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });
                stock_data.volume_changed = record.get(csv_column::VOLUME_CHANGED).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });

                // Parse total money changed if present (enhanced CSV format)
                stock_data.total_money_changed = record.get(csv_column::TOTAL_MONEY_CHANGED).and_then(|s| if s.is_empty() { None } else { s.parse().ok() });

                // Set total_money_changed to 0 for market indices (VNINDEX, VN30)
                // Market indices don't have money flow like individual stocks
                if crate::constants::INDEX_TICKERS.contains(&stock_data.ticker.as_str()) {
                    stock_data.total_money_changed = Some(0.0);
                }
            }

            data.push(stock_data);
        }

        // Sort by time
        data.sort_by(|a, b| a.time.cmp(&b.time));

        // Deduplicate by timestamp (favor last duplicate)
        let duplicates_removed = deduplicate_stock_data_by_time(&mut data);
        if duplicates_removed > 0 {
            debug!(
                path = ?csv_path,
                duplicates_removed = duplicates_removed,
                records_remaining = data.len(),
                "Deduplicated CSV data during read"
            );
        }

        Ok(data)
    }

    /// Reload a specific interval from CSV files (limited to last 730 records per ticker)
    pub async fn reload_interval(&self, interval: Interval) -> Result<(), Error> {
        self.load_interval(interval, None, Some(DATA_RETENTION_RECORDS)).await
    }

    /// Spawn a background task that auto-reloads the specified interval every CACHE_TTL_SECONDS
    /// Returns a JoinHandle that can be used for graceful shutdown
    pub fn spawn_auto_reload_task(
        self: Arc<Self>,
        interval: Interval,
    ) -> JoinHandle<()> {
        tokio::spawn(async move {
            tracing::info!(
                "Starting auto-reload task for {} interval (TTL: {}s, limit: {} records)",
                interval.to_filename(),
                CACHE_TTL_SECONDS,
                DATA_RETENTION_RECORDS
            );

            loop {
                // Sleep first (cache was just loaded during server startup)
                tokio::time::sleep(tokio::time::Duration::from_secs(CACHE_TTL_SECONDS as u64)).await;

                tracing::debug!(
                    "[DEBUG:PERF:BG_TASK] Auto-reload start for {} (TTL expired)",
                    interval.to_filename()
                );

                let reload_start = std::time::Instant::now();
                match self.reload_interval(interval).await {
                    Ok(_) => {
                        tracing::info!("[DEBUG:PERF:BG_TASK] ♻️  Reloaded {} cache: {:.2}ms", interval.to_filename(), reload_start.elapsed().as_secs_f64() * 1000.0);
                    }
                    Err(e) => {
                        tracing::warn!(
                            "[DEBUG:PERF:BG_TASK] Failed to auto-reload {} cache: {}. Will retry in {}s",
                            interval.to_filename(),
                            e,
                            CACHE_TTL_SECONDS
                        );
                        // Don't panic - continue loop and retry next cycle
                    }
                }
            }
        })
    }

    /// Smart data retrieval with aggregation awareness and centralized logic
    /// This method handles all query complexity internally
    pub async fn get_data_smart(
        &self,
        params: QueryParameters,
    ) -> HashMap<String, Vec<StockData>> {
        // Use existing method but with smart parameter handling
        let effective_limit = Some(params.effective_limit());
        let result = self.get_data_with_cache(
            params.tickers.clone(),
            params.interval,
            params.start_date,
            params.end_date,
            effective_limit,
            params.use_cache,
        ).await;

        // Apply aggregation if needed
        let aggregated_result = if let Some(agg_interval) = params.aggregated_interval {
            debug!("Applying {} aggregation with MA200 buffer", agg_interval);
            let mut result = result
                .into_iter()
                .map(|(ticker, records)| {
                    let aggregated = match agg_interval {
                        AggregatedInterval::Minutes5
                        | AggregatedInterval::Minutes15
                        | AggregatedInterval::Minutes30 => {
                            crate::services::Aggregator::aggregate_minute_data(records, agg_interval)
                        }
                        AggregatedInterval::Week
                        | AggregatedInterval::Week2
                        | AggregatedInterval::Month => {
                            crate::services::Aggregator::aggregate_daily_data(records, agg_interval)
                        }
                    };
                    (ticker, aggregated)
                })
                .collect();

            // Enhance aggregated data with technical indicators (MA, scores, changes)
            // BEFORE applying limit so we have enough data for MA calculations
            result = crate::services::Aggregator::enhance_aggregated_data(result);
            info!("Applied {} aggregation with full technical indicators", agg_interval);

            result
        } else {
            result
        };

        // Apply final limit AFTER enhancement to return only requested number of records
        let final_result = self.apply_final_limit(aggregated_result, params.limit, params.start_date);

        final_result
    }

    /// Apply final limit to results, trimming any buffer records that were fetched for MA calculations
    fn apply_final_limit(
        &self,
        mut data: HashMap<String, Vec<StockData>>,
        limit: usize,
        start_date: Option<DateTime<Utc>>,
    ) -> HashMap<String, Vec<StockData>> {
        // If no start date specified, limit to the most recent N records
        if start_date.is_none() && limit > 0 {
            data = data
                .into_iter()
                .map(|(ticker, mut records)| {
                    // Sort descending by time and take latest records
                    records.sort_by(|a, b| b.time.cmp(&a.time));
                    records.truncate(limit);
                    // Sort back to ascending order for consistency
                    records.sort_by(|a, b| a.time.cmp(&b.time));
                    (ticker, records)
                })
                .collect();
        }
        data
    }

    /// Get data with cache control option
    /// Daily and Hourly use in-memory cache (auto-reloaded by background task every 15s)
    /// Minute data always uses disk cache (needed for aggregated intervals with MA200)
    pub async fn get_data_with_cache(
        &self,
        tickers: Vec<String>,
        interval: Interval,
        start_date: Option<DateTime<Utc>>,
        end_date: Option<DateTime<Utc>>,
        limit: Option<usize>,
        use_cache: bool,
    ) -> HashMap<String, Vec<StockData>> {
        let start_time = std::time::Instant::now();
        tracing::debug!("[DEBUG:PERF] get_data_with_cache start: {} tickers, interval={}, limit={:?}", tickers.len(), interval.to_filename(), limit);

        // Minute data always uses disk cache (needed for aggregations)
        // OR when cache=false explicitly requested
        if interval == Interval::Minute || !use_cache {
            tracing::debug!("[DEBUG:PERF] Using disk cache (minute data or cache=false)");
            return self.get_data_from_disk_with_cache(tickers, interval, start_date, end_date, limit).await;
        }

        // Emergency fallback: if cache is VERY stale (2x TTL), force reload
        // This only happens if background auto-reload task is failing
        let cache_age = self.get_cache_age().await;
        if cache_age > CACHE_TTL_SECONDS * 2 {
            tracing::warn!(
                "Cache extremely stale ({}s > {}s), forcing emergency reload for {}",
                cache_age,
                CACHE_TTL_SECONDS * 2,
                interval.to_filename()
            );
            if let Err(e) = self.reload_interval(interval).await {
                tracing::error!("Emergency reload failed for {}: {}, falling back to disk", interval.to_filename(), e);
                return self.get_data_from_disk_with_cache(tickers, interval, start_date, end_date, limit).await;
            }
        }

        // If limit is provided, check if cache has enough records using representative tickers
        // Use well-known tickers (VNINDEX, VCB, VIC) that always have full history
        // Don't check ALL tickers because some new listings might have < limit days
        if let Some(limit_count) = limit {
            if start_date.is_none() {
                let representative_tickers = ["VNINDEX", "VCB", "VIC"];
                let store = self.data.read().await;

                let cache_insufficient = representative_tickers.iter().any(|ticker| {
                    let record_count = store.get(*ticker)
                        .and_then(|td| td.get(&interval))
                        .map(|data| data.len())
                        .unwrap_or(0);

                    if record_count < limit_count {
                        tracing::debug!(
                            "[DEBUG:PERF] Representative ticker {} has only {} records (need {})",
                            ticker, record_count, limit_count
                        );
                        true
                    } else {
                        false
                    }
                });
                drop(store);

                if cache_insufficient {
                    tracing::info!(
                        "Cache has insufficient records (limit: {}), reading from disk for {} tickers",
                        limit_count,
                        tickers.len()
                    );
                    tracing::debug!("[DEBUG:PERF] Cache insufficient check failed (representative tickers), falling back to disk for all {} tickers", tickers.len());
                    return self.get_data_from_disk_with_cache(tickers, interval, start_date, end_date, limit).await;
                }
            }
        }

        // Cache is fresh - try cache first, fall back to disk if insufficient
        let perf_cache_lookup_start = std::time::Instant::now();
        tracing::debug!("[DEBUG:PERF] Cache lookup start");
        let store = self.data.read().await;
        let mut result = HashMap::new();
        let mut need_disk_read: Vec<String> = Vec::new();

        for ticker in &tickers {
            if let Some(ticker_data) = store.get(ticker) {
                if let Some(interval_data) = ticker_data.get(&interval) {
                    // Check if cache has the requested date range
                    let has_required_range = {
                        let start_ok = if let Some(start) = start_date {
                            // Check if cache has data going back to requested start date
                            interval_data.first().map(|d| d.time <= start).unwrap_or(false)
                        } else {
                            true // No specific start requested
                        };

                        let end_ok = if let Some(end) = end_date {
                            // Check if cache has data for the requested end date
                            // Cache should either contain the end date or start before it
                            interval_data.first().map(|d| d.time <= end).unwrap_or(false)
                        } else {
                            true // No specific end requested
                        };

                        start_ok && end_ok
                    };

                    if has_required_range {
                        // Cache has sufficient data - use binary search for date range
                        // Data is sorted by time (ascending), so we can binary search
                        let start_idx = if let Some(start) = start_date {
                            // Find first record >= start_date
                            interval_data.partition_point(|d| d.time < start)
                        } else {
                            0
                        };

                        let end_idx = if let Some(end) = end_date {
                            // Find first record > end_date
                            interval_data.partition_point(|d| d.time <= end)
                        } else {
                            interval_data.len()
                        };

                        // Only clone the records in range (not all records)
                        if start_idx < end_idx {
                            let filtered: Vec<StockData> = interval_data[start_idx..end_idx]
                                .iter()
                                .cloned()
                                .collect();

                            if !filtered.is_empty() {
                                result.insert(ticker.clone(), filtered);
                            }
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

        let from_cache_count = result.len();
        tracing::debug!("[DEBUG:PERF] Cache lookup complete: {:.2}ms, {} from cache, {} need disk",
            perf_cache_lookup_start.elapsed().as_secs_f64() * 1000.0,
            from_cache_count,
            need_disk_read.len()
        );

        // Read missing tickers from disk
        if !need_disk_read.is_empty() {
            tracing::info!(
                "Reading {} tickers from disk (cache insufficient): {:?}",
                need_disk_read.len(),
                need_disk_read
            );
            let perf_disk_start = std::time::Instant::now();
            tracing::debug!("[DEBUG:PERF] Disk read start: {} tickers", need_disk_read.len());
            let disk_data = self.get_data_from_disk_with_cache(
                need_disk_read,
                interval,
                start_date,
                end_date,
                limit,
            ).await;
            tracing::debug!("[DEBUG:PERF] Disk read complete: {:.2}ms, {} tickers",
                perf_disk_start.elapsed().as_secs_f64() * 1000.0,
                disk_data.len()
            );

            // Merge disk data into result
            result.extend(disk_data);
        }

        // Apply limit if provided and start_date is None
        if let Some(limit_count) = limit {
            if start_date.is_none() && limit_count > 0 {
                result = result
                    .into_iter()
                    .map(|(ticker, mut records)| {
                        // Sort descending by time and take last N records
                        records.sort_by(|a, b| b.time.cmp(&a.time));
                        records.truncate(limit_count);
                        // Sort back to ascending order for consistency
                        records.sort_by(|a, b| a.time.cmp(&b.time));
                        (ticker, records)
                    })
                    .collect();
            }
        }

        let duration = start_time.elapsed();
        let total_records: usize = result.values().map(|v| v.len()).sum();
        tracing::debug!(
            "Cache lookup: {} tickers, {} records, {:.2}ms (interval: {})",
            tickers.len(),
            total_records,
            duration.as_secs_f64() * 1000.0,
            interval.to_filename()
        );

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
        limit: Option<usize>,
    ) -> HashMap<String, Vec<StockData>> {
        let mut result = HashMap::new();

        for ticker in tickers {
            let cache_key = (ticker.clone(), interval);

            // Check disk cache first
            let cache_hit = {
                let disk_cache = self.disk_cache.read().await;
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

        // Apply limit if provided and start_date is None
        if let Some(limit_count) = limit {
            if start_date.is_none() && limit_count > 0 {
                result = result
                    .into_iter()
                    .map(|(ticker, mut records)| {
                        // Sort descending by time and take last N records
                        records.sort_by(|a, b| b.time.cmp(&a.time));
                        records.truncate(limit_count);
                        // Sort back to ascending order for consistency
                        records.sort_by(|a, b| a.time.cmp(&b.time));
                        (ticker, records)
                    })
                    .collect();
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
        let mut disk_cache = self.disk_cache.write().await;
        let mut disk_cache_size = self.disk_cache_size.write().await;

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
        let store = self.data.read().await;
        estimate_memory_usage(&*store)
    }

    /// Get count of records per interval
    pub async fn get_record_counts(&self) -> (usize, usize, usize) {
        let store = self.data.read().await;
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
        let store = self.data.read().await;
        store.len()
    }

    /// Get all ticker names (from in-memory data)
    pub async fn get_all_ticker_names(&self) -> Vec<String> {
        let store = self.data.read().await;
        store.keys().cloned().collect()
    }

    /// Get disk cache statistics (entries count, size, limit)
    pub async fn get_disk_cache_stats(&self) -> (usize, usize, usize) {
        let disk_cache = self.disk_cache.read().await;
        let disk_cache_size = self.disk_cache_size.read().await;
        (disk_cache.len(), *disk_cache_size, self.max_cache_size_bytes)
    }

    /// Get cache age in seconds
    async fn get_cache_age(&self) -> i64 {
        let cache_last_updated = self.cache_last_updated.read().await;
        let now = Utc::now();
        now.signed_duration_since(*cache_last_updated).num_seconds()
    }

    /// Update cache timestamp (called after reading fresh data from disk)
    async fn update_cache_timestamp(&self) {
        let mut cache_last_updated = self.cache_last_updated.write().await;
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
