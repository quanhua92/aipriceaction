/// VCI worker timing and configuration constants.
pub mod vci_worker {
    /// Daily worker: loop interval during trading hours
    pub const DAILY_LOOP_TRADE_SECS: u64 = 15;
    /// Daily worker: loop interval outside trading hours
    pub const DAILY_LOOP_OFF_SECS: u64 = 300;

    /// Hourly worker: loop interval during trading hours
    pub const HOURLY_LOOP_TRADE_SECS: u64 = 300;
    /// Hourly worker: loop interval outside trading hours
    pub const HOURLY_LOOP_OFF_SECS: u64 = 1800;

    /// Minute worker: loop interval during trading hours
    pub const MINUTE_LOOP_TRADE_SECS: u64 = 300;
    /// Minute worker: loop interval outside trading hours
    pub const MINUTE_LOOP_OFF_SECS: u64 = 1800;

    /// Dividend worker: loop interval (polling for flagged tickers)
    pub const DIVIDEND_LOOP_SECS: u64 = 60;

    /// Sleep between ticker API calls (rate limit spacing)
    pub const TICKER_SLEEP_SECS: u64 = 2;
    /// Sleep between ticker API calls for minute worker (higher pressure)
    pub const MINUTE_TICKER_SLEEP_SECS: u64 = 3;

    /// Dividend detection: price ratio threshold (2%)
    pub const DIVIDEND_RATIO_THRESHOLD: f64 = 1.02;
    /// Number of recent daily bars to compare for dividend detection
    pub const DIVIDEND_CHECK_BARS: i64 = 20;

    /// Adaptive countBack: daily (recent data, < 14 days gap)
    pub const DAILY_COUNTBACK_RECENT: u32 = 7;
    /// Adaptive countBack: daily (gap or first sync)
    pub const DAILY_COUNTBACK_GAP: u32 = 30;
    /// Gap threshold in days for daily data
    pub const DAILY_GAP_THRESHOLD_DAYS: i64 = 14;

    /// Adaptive countBack: hourly (recent)
    pub const HOURLY_COUNTBACK_RECENT: u32 = 48;
    /// Adaptive countBack: hourly (gap)
    pub const HOURLY_COUNTBACK_GAP: u32 = 78;
    /// Gap threshold in days for hourly data
    pub const HOURLY_GAP_THRESHOLD_DAYS: i64 = 14;

    /// Adaptive countBack: minute (recent)
    pub const MINUTE_COUNTBACK_RECENT: u32 = 960;
    /// Adaptive countBack: minute (gap)
    pub const MINUTE_COUNTBACK_GAP: u32 = 2400;
    /// Gap threshold in days for minute data
    pub const MINUTE_GAP_THRESHOLD_DAYS: i64 = 5;

    /// Dividend worker: bars per chunk for backward walk (daily)
    pub const DIVIDEND_CHUNK_SIZE_DAILY: u32 = 500;
    /// Dividend worker: bars per chunk for backward walk (hourly)
    pub const DIVIDEND_CHUNK_SIZE_HOURLY: u32 = 2000;
    /// Dividend worker: bars per chunk for backward walk (minute)
    pub const DIVIDEND_CHUNK_SIZE_MINUTE: u32 = 5000;
    /// Sleep between dividend chunk fetches
    pub const DIVIDEND_CHUNK_SLEEP_SECS: u64 = 2;

    /// Hourly worker: initial delay before first sync
    pub const HOURLY_INITIAL_DELAY_SECS: u64 = 300;
    /// Minute worker: initial delay before first sync
    pub const MINUTE_INITIAL_DELAY_SECS: u64 = 300;

    /// Index tickers (no dividend detection)
    pub const INDEX_TICKERS: &[&str] = &["VNINDEX", "VN30", "HNX", "UPCOM"];

    /// Concurrent API batches based on CPU cores and VCI client count.
    /// Caps concurrency to 3 per client to avoid rate limit exhaustion.
    pub fn concurrent_batches(client_count: usize) -> usize {
        let cpu_based = {
            let cpus = std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(1);
            match cpus {
                1..=2 => 3,
                3..=4 => 5,
                _ => 8,
            }
        };
        let client_based = client_count * 3;
        cpu_based.min(client_based)
    }
}
