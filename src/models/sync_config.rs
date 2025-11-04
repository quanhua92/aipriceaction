use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::time::Duration;

/// Interval types for market data
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Interval {
    /// Daily data -> daily.csv
    Daily,
    /// Hourly data -> 1h.csv
    Hourly,
    /// Minute data -> 1m.csv
    Minute,
}

impl Interval {
    /// Convert to VCI API format ("1D", "1H", "1m")
    pub fn to_vci_format(&self) -> &'static str {
        match self {
            Interval::Daily => "1D",
            Interval::Hourly => "1H",
            Interval::Minute => "1m",
        }
    }

    /// Convert to filename (daily.csv, 1h.csv, 1m.csv)
    pub fn to_filename(&self) -> &'static str {
        match self {
            Interval::Daily => "daily.csv",
            Interval::Hourly => "1h.csv",
            Interval::Minute => "1m.csv",
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

    /// Number of recent days to fetch in resume mode
    pub resume_days: u32,

    /// Intervals to sync
    pub intervals: Vec<Interval>,

    /// Force full download (disable resume mode)
    pub force_full: bool,
}

impl Default for SyncConfig {
    fn default() -> Self {
        Self {
            start_date: "2015-01-05".to_string(),
            end_date: Utc::now().format("%Y-%m-%d").to_string(),
            batch_size: 10,
            resume_days: 30,
            intervals: vec![Interval::Daily],
            force_full: false,
        }
    }
}

impl SyncConfig {
    /// Create new config with custom values
    pub fn new(
        start_date: String,
        end_date: Option<String>,
        batch_size: usize,
        resume_days: u32,
        intervals: Vec<Interval>,
        force_full: bool,
    ) -> Self {
        Self {
            start_date,
            end_date: end_date.unwrap_or_else(|| Utc::now().format("%Y-%m-%d").to_string()),
            batch_size,
            resume_days,
            intervals,
            force_full,
        }
    }

    /// Get fetch start date based on resume mode
    pub fn get_fetch_start_date(&self) -> String {
        if self.force_full {
            self.start_date.clone()
        } else {
            let resume_date = Utc::now() - chrono::Duration::days(self.resume_days as i64);
            resume_date.format("%Y-%m-%d").to_string()
        }
    }

    /// Get batch size (reduce for full downloads)
    pub fn get_batch_size(&self) -> usize {
        if self.force_full {
            2 // Smaller batches for full downloads
        } else {
            self.batch_size
        }
    }
}

/// Categorization of tickers by data needs
#[derive(Debug, Default)]
pub struct TickerCategory {
    /// Tickers with sufficient existing data (can use resume mode)
    pub resume_tickers: Vec<String>,

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
}

/// Statistics for sync operation
#[derive(Debug, Default)]
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
        assert_eq!(Interval::Daily.to_filename(), "daily.csv");
        assert_eq!(Interval::Hourly.to_filename(), "1h.csv");
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
        assert_eq!(config.resume_days, 30);
        assert!(!config.force_full);
    }

    #[test]
    fn test_sync_config_batch_size() {
        let mut config = SyncConfig::default();
        assert_eq!(config.get_batch_size(), 10);

        config.force_full = true;
        assert_eq!(config.get_batch_size(), 2);
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
}
