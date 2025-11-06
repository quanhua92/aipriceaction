use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::time::Duration;

/// Interval types for market data
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Interval {
    /// Daily data -> 1D.csv
    Daily,
    /// Hourly data -> 1H.csv
    Hourly,
    /// Minute data -> 1m.csv
    Minute,
}

impl Interval {
    /// Get the minimum allowed start date for each interval
    ///
    /// Hourly and Minute data are only available from 2023-09-01 onwards.
    /// Daily data can go back to 2015-01-05.
    pub fn min_start_date(&self) -> &'static str {
        match self {
            Interval::Daily => "2015-01-05",
            Interval::Hourly => "2023-09-01",  // Hourly data only from Sept 2023
            Interval::Minute => "2023-09-01",  // Minute data only from Sept 2023
        }
    }

    /// Convert to VCI API format ("1D", "1H", "1m")
    pub fn to_vci_format(&self) -> &'static str {
        match self {
            Interval::Daily => "1D",
            Interval::Hourly => "1H",
            Interval::Minute => "1m",
        }
    }

    /// Convert to filename (1D.csv, 1H.csv, 1m.csv)
    pub fn to_filename(&self) -> &'static str {
        match self {
            Interval::Daily => "1D.csv",
            Interval::Hourly => "1H.csv",
            Interval::Minute => "1m.csv",
        }
    }

    /// Get minimal resume days as fallback (only used when CSV read fails)
    ///
    /// With adaptive mode, these are rarely used - only as safety fallback when:
    /// - CSV file is corrupted/unreadable
    /// - First run with empty database
    ///
    /// All intervals use 2 days as minimal safe fallback
    pub fn default_resume_days(&self) -> u32 {
        match self {
            Interval::Daily => 2,
            Interval::Hourly => 2,  // Reduced from 5 - adaptive mode handles the rest
            Interval::Minute => 2,
        }
    }

    /// Parse from string (case-insensitive)
    pub fn from_str(s: &str) -> Result<Self, String> {
        match s.to_uppercase().as_str() {
            "1D" | "DAILY" => Ok(Interval::Daily),
            "1H" | "HOURLY" => Ok(Interval::Hourly),
            "1M" | "MINUTE" => Ok(Interval::Minute),
            _ => Err(format!("Invalid interval: {}. Valid options: 1D, 1H, 1m", s)),
        }
    }

    /// Parse multiple intervals from comma-separated string or "all"
    pub fn parse_intervals(s: &str) -> Result<Vec<Self>, String> {
        if s.to_lowercase() == "all" {
            return Ok(vec![Interval::Daily, Interval::Hourly, Interval::Minute]);
        }

        s.split(',')
            .map(|part| Interval::from_str(part.trim()))
            .collect()
    }
}

/// Configuration for data sync operation
#[derive(Debug, Clone)]
pub struct SyncConfig {
    /// Start date for historical data (e.g., "2015-01-05")
    pub start_date: String,

    /// End date for data fetch (usually today)
    pub end_date: String,

    /// Batch size for API calls (10 for resume mode, 2 for full downloads)
    pub batch_size: usize,

    /// Number of recent days to fetch in resume mode (None = use interval-specific defaults)
    pub resume_days: Option<u32>,

    /// Intervals to sync
    pub intervals: Vec<Interval>,

    /// Force full download (disable resume mode)
    pub force_full: bool,

    /// Number of concurrent batch requests (1 = sequential, 3 = default, higher = faster but more load)
    pub concurrent_batches: usize,
}

impl Default for SyncConfig {
    fn default() -> Self {
        Self {
            start_date: "2015-01-05".to_string(),
            end_date: Utc::now().format("%Y-%m-%d").to_string(),
            batch_size: 10,
            resume_days: None, // Use interval-specific defaults
            intervals: vec![Interval::Daily],
            force_full: false,
            concurrent_batches: 3, // Default: 3 concurrent batch requests
        }
    }
}

impl SyncConfig {
    /// Create new config with custom values
    pub fn new(
        start_date: String,
        end_date: Option<String>,
        batch_size: usize,
        resume_days: Option<u32>,
        intervals: Vec<Interval>,
        force_full: bool,
        concurrent_batches: usize,
    ) -> Self {
        Self {
            start_date,
            end_date: end_date.unwrap_or_else(|| Utc::now().format("%Y-%m-%d").to_string()),
            batch_size,
            resume_days,
            intervals,
            force_full,
            concurrent_batches,
        }
    }

    /// Get effective start date for an interval, respecting minimum allowed dates
    ///
    /// Ensures that minute data never goes before 2023-09-01, even if user
    /// specifies an earlier date or if full download is requested.
    pub fn get_effective_start_date(&self, interval: Interval) -> String {
        let requested_start = if self.force_full {
            self.start_date.clone()
        } else {
            let days = self.resume_days.unwrap_or_else(|| interval.default_resume_days());
            let resume_date = Utc::now() - chrono::Duration::days(days as i64);
            resume_date.format("%Y-%m-%d").to_string()
        };

        // Enforce interval-specific minimum start date
        // Return the LATER of the two dates (max) to ensure we don't go before the minimum
        let min_date = interval.min_start_date().to_string();
        if requested_start < min_date {
            min_date
        } else {
            requested_start
        }
    }

    /// Get fetch start date based on resume mode and interval
    ///
    /// If resume_days is explicitly provided, uses that value.
    /// Otherwise, uses interval-specific optimal defaults (3 days for daily, 5 for hourly, 2 for minute).
    ///
    /// **Deprecated**: Use `get_effective_start_date()` instead to respect interval minimums.
    #[deprecated(since = "0.1.0", note = "Use get_effective_start_date() to respect interval-specific minimum dates")]
    pub fn get_fetch_start_date(&self, interval: Interval) -> String {
        self.get_effective_start_date(interval)
    }

    /// Get batch size based on interval and mode
    ///
    /// Optimized batch sizes for adaptive resume mode:
    /// - Daily: 50 tickers/batch (lightweight, ~1-2 records per ticker)
    /// - Hourly: 20 tickers/batch (moderate, adapts to actual date range)
    /// - Minute: 3 tickers/batch (heavy, adapts to actual date range)
    ///
    /// These sizes work well with adaptive mode since fetch range
    /// automatically adjusts based on actual last dates in CSV files.
    pub fn get_batch_size(&self, interval: Interval) -> usize {
        if self.force_full {
            2 // Smaller batches for full downloads
        } else {
            match interval {
                Interval::Daily => 50,   // Adaptive: fetches only what's needed per ticker
                Interval::Hourly => 20,  // Adaptive: scales with actual staleness
                Interval::Minute => 3,   // Adaptive: handles varying date ranges
            }
        }
    }
}

/// Categorization of tickers by data needs
#[derive(Debug, Default)]
pub struct TickerCategory {
    /// Tickers with sufficient existing data (can use resume mode)
    /// Each tuple contains (ticker, last_date_in_csv)
    pub resume_tickers: Vec<(String, String)>,

    /// Tickers needing full history (new or insufficient data)
    pub full_history_tickers: Vec<String>,
}

impl TickerCategory {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn total_count(&self) -> usize {
        self.resume_tickers.len() + self.full_history_tickers.len()
    }

    /// Get the minimum (earliest) last date from all resume tickers
    /// Returns None if no resume tickers
    pub fn get_min_resume_date(&self) -> Option<String> {
        self.resume_tickers
            .iter()
            .map(|(_, date)| date.clone())
            .min()
    }

    /// Get just ticker names from resume tickers
    pub fn get_resume_ticker_names(&self) -> Vec<String> {
        self.resume_tickers
            .iter()
            .map(|(ticker, _)| ticker.clone())
            .collect()
    }
}

/// Progress tracking for data sync
#[derive(Debug, Clone)]
pub struct FetchProgress {
    /// Current ticker index (1-based)
    pub current: usize,

    /// Total number of tickers
    pub total: usize,

    /// Current ticker being processed
    pub ticker: String,

    /// Time elapsed for current ticker
    pub ticker_elapsed: Duration,

    /// Total time elapsed
    pub total_elapsed: Duration,

    /// Estimated time remaining
    pub eta: Duration,

    /// Current interval being processed
    pub interval: Interval,
}

impl FetchProgress {
    pub fn new(current: usize, total: usize, ticker: String, interval: Interval) -> Self {
        Self {
            current,
            total,
            ticker,
            ticker_elapsed: Duration::ZERO,
            total_elapsed: Duration::ZERO,
            eta: Duration::ZERO,
            interval,
        }
    }

    /// Calculate progress percentage
    pub fn percentage(&self) -> f64 {
        if self.total == 0 {
            0.0
        } else {
            (self.current as f64 / self.total as f64) * 100.0
        }
    }

    /// Update timing information
    pub fn update_timing(&mut self, ticker_elapsed: Duration, total_elapsed: Duration) {
        self.ticker_elapsed = ticker_elapsed;
        self.total_elapsed = total_elapsed;

        // Calculate ETA
        if self.current > 0 {
            let avg_time_per_ticker = total_elapsed.as_secs_f64() / self.current as f64;
            let remaining_tickers = self.total.saturating_sub(self.current);
            let eta_secs = avg_time_per_ticker * remaining_tickers as f64;
            self.eta = Duration::from_secs_f64(eta_secs);
        }
    }

    /// Format for display
    pub fn format_display(&self) -> String {
        format!(
            "[{:03}/{:03}] {} | {:.1}s | Elapsed: {:.1}min | ETA: {:.1}min",
            self.current,
            self.total,
            self.ticker,
            self.ticker_elapsed.as_secs_f64(),
            self.total_elapsed.as_secs_f64() / 60.0,
            self.eta.as_secs_f64() / 60.0,
        )
    }

    /// Format compact progress (one line)
    pub fn format_compact(&self) -> String {
        let elapsed_min = self.total_elapsed.as_secs_f64() / 60.0;
        let eta_min = self.eta.as_secs_f64() / 60.0;

        format!(
            "[{:3}/{:3}] {:8} | {:4.1}m elapsed | {:4.1}m ETA",
            self.current,
            self.total,
            self.ticker,
            elapsed_min,
            eta_min,
        )
    }
}

/// Statistics for sync operation
#[derive(Debug, Default, Clone)]
pub struct SyncStats {
    pub successful: usize,
    pub failed: usize,
    pub skipped: usize,
    pub updated: usize,
    pub files_written: usize,
    pub total_records: usize,
}

impl SyncStats {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn total_processed(&self) -> usize {
        self.successful + self.failed + self.skipped
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_interval_to_vci_format() {
        assert_eq!(Interval::Daily.to_vci_format(), "1D");
        assert_eq!(Interval::Hourly.to_vci_format(), "1H");
        assert_eq!(Interval::Minute.to_vci_format(), "1m");
    }

    #[test]
    fn test_interval_to_filename() {
        assert_eq!(Interval::Daily.to_filename(), "1D.csv");
        assert_eq!(Interval::Hourly.to_filename(), "1H.csv");
        assert_eq!(Interval::Minute.to_filename(), "1m.csv");
    }

    #[test]
    fn test_interval_from_str() {
        assert_eq!(Interval::from_str("1D").unwrap(), Interval::Daily);
        assert_eq!(Interval::from_str("daily").unwrap(), Interval::Daily);
        assert_eq!(Interval::from_str("1H").unwrap(), Interval::Hourly);
        assert_eq!(Interval::from_str("hourly").unwrap(), Interval::Hourly);
        assert_eq!(Interval::from_str("1m").unwrap(), Interval::Minute);
        assert_eq!(Interval::from_str("minute").unwrap(), Interval::Minute);
        assert!(Interval::from_str("invalid").is_err());
    }

    #[test]
    fn test_parse_intervals() {
        let all = Interval::parse_intervals("all").unwrap();
        assert_eq!(all.len(), 3);

        let daily = Interval::parse_intervals("1D").unwrap();
        assert_eq!(daily, vec![Interval::Daily]);

        let multiple = Interval::parse_intervals("1D,1H").unwrap();
        assert_eq!(multiple, vec![Interval::Daily, Interval::Hourly]);
    }

    #[test]
    fn test_sync_config_default() {
        let config = SyncConfig::default();
        assert_eq!(config.start_date, "2015-01-05");
        assert_eq!(config.batch_size, 10);
        assert_eq!(config.resume_days, None); // Uses interval-specific defaults
        assert!(!config.force_full);
    }

    #[test]
    fn test_sync_config_batch_size() {
        let mut config = SyncConfig::default();

        // Resume mode: interval-specific batch sizes
        assert_eq!(config.get_batch_size(Interval::Daily), 50); // Optimal for fast resume mode
        assert_eq!(config.get_batch_size(Interval::Hourly), 20); // Balanced speed and reliability
        assert_eq!(config.get_batch_size(Interval::Minute), 3); // Prevents API overload

        // Full mode: always use 2 regardless of interval
        config.force_full = true;
        assert_eq!(config.get_batch_size(Interval::Daily), 2);
        assert_eq!(config.get_batch_size(Interval::Hourly), 2);
        assert_eq!(config.get_batch_size(Interval::Minute), 2);
    }

    #[test]
    fn test_fetch_progress() {
        let mut progress = FetchProgress::new(1, 100, "VCB".to_string(), Interval::Daily);
        assert_eq!(progress.percentage(), 1.0);

        progress.current = 50;
        assert_eq!(progress.percentage(), 50.0);

        progress.update_timing(Duration::from_secs(2), Duration::from_secs(100));
        assert!(progress.eta.as_secs() > 0);
    }

    #[test]
    fn test_interval_default_resume_days() {
        // Test minimal fallback defaults (all use 2 days)
        assert_eq!(Interval::Daily.default_resume_days(), 2);
        assert_eq!(Interval::Hourly.default_resume_days(), 2);
        assert_eq!(Interval::Minute.default_resume_days(), 2);
    }

    #[test]
    fn test_get_fetch_start_date_with_smart_defaults() {
        // Test that all intervals use same minimal fallback (2 days)
        let config = SyncConfig::default();

        // All intervals use 2 days as fallback
        let daily_start = config.get_fetch_start_date(Interval::Daily);
        let hourly_start = config.get_fetch_start_date(Interval::Hourly);
        let minute_start = config.get_fetch_start_date(Interval::Minute);

        // All use 2 days fallback (adaptive mode is the real logic)
        assert_eq!(daily_start, hourly_start);
        assert_eq!(hourly_start, minute_start);
        assert_eq!(daily_start, minute_start);
    }

    #[test]
    fn test_get_fetch_start_date_with_custom_resume_days() {
        // Test that custom resume_days overrides smart defaults
        let config = SyncConfig::new(
            "2015-01-05".to_string(),
            None,
            10,
            Some(7), // Custom resume days
            vec![Interval::Daily, Interval::Hourly],
            false,
            3, // concurrent_batches
        );

        // All intervals should use the same custom value
        let daily_start = config.get_fetch_start_date(Interval::Daily);
        let hourly_start = config.get_fetch_start_date(Interval::Hourly);
        let minute_start = config.get_fetch_start_date(Interval::Minute);

        // They should all be the same (7 days ago)
        assert_eq!(daily_start, hourly_start);
        assert_eq!(hourly_start, minute_start);
    }

    #[test]
    fn test_interval_min_start_date() {
        // Test that each interval has correct minimum start date
        assert_eq!(Interval::Daily.min_start_date(), "2015-01-05");
        assert_eq!(Interval::Hourly.min_start_date(), "2023-09-01");
        assert_eq!(Interval::Minute.min_start_date(), "2023-09-01");
    }

    #[test]
    fn test_get_effective_start_date_respects_minimums() {
        // Test that minute and hourly intervals never go before 2023-09-01
        let config = SyncConfig::new(
            "2015-01-05".to_string(),  // Request very old data
            None,
            10,
            None,
            vec![Interval::Daily, Interval::Hourly, Interval::Minute],
            true,  // Force full download
            3,
        );

        // Daily should honor the requested date
        let daily_start = config.get_effective_start_date(Interval::Daily);
        assert_eq!(daily_start, "2015-01-05");

        // Hourly should be clamped to 2023-09-01
        let hourly_start = config.get_effective_start_date(Interval::Hourly);
        assert_eq!(hourly_start, "2023-09-01");

        // Minute should be clamped to 2023-09-01
        let minute_start = config.get_effective_start_date(Interval::Minute);
        assert_eq!(minute_start, "2023-09-01");
    }

    #[test]
    fn test_get_effective_start_date_allows_newer_dates() {
        // Test that intervals can use dates newer than their minimums
        let config = SyncConfig::new(
            "2024-01-01".to_string(),  // Request newer data
            None,
            10,
            None,
            vec![Interval::Daily, Interval::Hourly, Interval::Minute],
            true,
            3,
        );

        // All should honor the requested date since it's after their minimums
        assert_eq!(config.get_effective_start_date(Interval::Daily), "2024-01-01");
        assert_eq!(config.get_effective_start_date(Interval::Hourly), "2024-01-01");
        assert_eq!(config.get_effective_start_date(Interval::Minute), "2024-01-01");
    }
}
