/// VCI worker timing and configuration constants.
pub mod vci_worker {
    /// Daily worker: loop interval during trading hours
    pub const DAILY_LOOP_TRADE_SECS: u64 = 15;
    /// Daily worker: loop interval outside trading hours
    pub const DAILY_LOOP_OFF_SECS: u64 = 60;

    /// Hourly worker: loop interval during trading hours
    pub const HOURLY_LOOP_TRADE_SECS: u64 = 60;
    /// Hourly worker: loop interval outside trading hours
    pub const HOURLY_LOOP_OFF_SECS: u64 = 60;

    /// Minute worker: loop interval during trading hours
    pub const MINUTE_LOOP_TRADE_SECS: u64 = 60;
    /// Minute worker: loop interval outside trading hours
    pub const MINUTE_LOOP_OFF_SECS: u64 = 60;

    /// Dividend worker: loop interval (polling for flagged tickers)
    pub const DIVIDEND_LOOP_SECS: u64 = 60;

    /// Dividend detection: price ratio threshold (2%)
    pub const DIVIDEND_RATIO_THRESHOLD: f64 = 1.02;
    /// Number of recent daily bars to compare for dividend detection
    pub const DIVIDEND_CHECK_BARS: i64 = 20;

    /// Daily countBack: always fetch 100 bars
    pub const DAILY_COUNTBACK: u32 = 100;

    /// Adaptive countBack: hourly (recent)
    pub const HOURLY_COUNTBACK_RECENT: u32 = 200;
    /// Adaptive countBack: hourly (gap)
    pub const HOURLY_COUNTBACK_GAP: u32 = 500;
    /// Gap threshold in days for hourly data
    pub const HOURLY_GAP_THRESHOLD_DAYS: i64 = 14;

    /// Adaptive countBack: minute (recent)
    pub const MINUTE_COUNTBACK_RECENT: u32 = 3000;
    /// Adaptive countBack: minute (gap)
    pub const MINUTE_COUNTBACK_GAP: u32 = 5000;
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
    /// Percentage increase per consecutive stall (gap/holiday skip)
    pub const DIVIDEND_STALL_INCREASE_PCT: u32 = 50;

    /// Cooldown when rate limited (HTTP 429) detected in a batch
    pub const RATE_LIMIT_COOLDOWN_SECS: u64 = 60;

    /// Hourly worker: initial delay before first sync
    pub const HOURLY_INITIAL_DELAY_SECS: u64 = 300;
    /// Minute worker: initial delay before first sync
    pub const MINUTE_INITIAL_DELAY_SECS: u64 = 300;

    /// Index tickers (no dividend detection)
    pub const INDEX_TICKERS: &[&str] = &["VNINDEX", "VN30", "HNX", "UPCOM"];

    /// Concurrent API batches based on VCI client count.
    /// 3 concurrent requests per client, each with its own rate limiter.
    pub fn concurrent_batches(client_count: usize) -> usize {
        let batches = (client_count * 3).min(24);
        tracing::debug!(client_count, batches, "concurrent_batches calculated");
        batches
    }

    /// Multiplier applied to tier intervals outside trading hours.
    /// No new data arrives off-hours, so less frequent polling is fine.
    pub const OFF_HOURS_MULTIPLIER: i64 = 20;

    /// Max tickers to process per loop iteration.
    /// All due tickers are fetched, shuffled, and this many are taken.
    /// Shuffling avoids multiple containers competing for the same tickers.
    pub const DUE_TICKER_BATCH_SIZE: usize = 50;

    /// Priority scheduling based on money flow (close * volume).
    pub mod priority {
        /// Money flow thresholds (close * volume in VND) for tier boundaries.
        /// Tier 1: >= 50B VND  (VCB, VIC, VNM, FPT, HPG...)
        /// Tier 2: >=  5B VND
        /// Tier 3: >=  0.5B VND
        /// Tier 4: <  0.5B VND  (illiquid / small-cap)
        pub const THRESHOLDS: [f64; 3] = [
            50_000_000_000.0,
             5_000_000_000.0,
               500_000_000.0,
        ];

        /// Check interval per tier (seconds, trading hours). Index 0 = top tier.
        pub const DAILY_SECS:  [i64; 4] = [15, 30, 60, 120];
        pub const HOURLY_SECS: [i64; 4] = [60, 180, 300, 600];
        pub const MINUTE_SECS: [i64; 4] = [60, 120, 300, 600];
    }
}

/// Binance crypto worker timing and configuration constants.
pub mod binance_worker {
    /// Daily worker: loop interval (24/7, no trading hours)
    pub const DAILY_LOOP_SECS: u64 = 60;
    /// Hourly worker: loop interval
    pub const HOURLY_LOOP_SECS: u64 = 300;
    /// Minute worker: loop interval
    pub const MINUTE_LOOP_SECS: u64 = 600;

    /// Hourly worker: initial delay before first sync
    pub const HOURLY_INITIAL_DELAY_SECS: u64 = 300;
    /// Minute worker: initial delay before first sync
    pub const MINUTE_INITIAL_DELAY_SECS: u64 = 300;

    /// Cooldown when rate limited (HTTP 429/403) detected in a batch
    pub const RATE_LIMIT_COOLDOWN_SECS: u64 = 60;

    /// Max tickers to process per loop iteration
    pub const DUE_TICKER_BATCH_SIZE: usize = 20;

    /// Concurrent API batches based on Binance API client count.
    /// Conservative: 2 per client, max 6 total.
    pub fn concurrent_batches(client_count: usize) -> usize {
        (client_count * 2).min(6)
    }

    /// Daily limit: number of records to return from get_history
    pub const DAILY_LIMIT: u32 = 100;
    /// Hourly limit
    pub const HOURLY_LIMIT: u32 = 800;
    /// Minute limit
    pub const MINUTE_LIMIT: u32 = 1000;

    /// Fixed scheduling intervals (seconds) — all crypto tickers get the same delay
    pub const SCHEDULE_DAILY_SECS: i64 = 60;
    pub const SCHEDULE_HOURLY_SECS: i64 = 300;
    pub const SCHEDULE_MINUTE_SECS: i64 = 600;
}

/// API server constants.
pub mod api {
    /// Cache TTL for /tickers responses (seconds).
    pub const CACHE_TTL_SECS: u64 = 10;
    /// Max cached entries before eviction.
    pub const CACHE_MAX_ENTRIES: usize = 500;
    /// Default ?limit= when not specified.
    pub const DEFAULT_LIMIT: i64 = 252;
    /// Extra rows fetched for MA200 accuracy in aggregated intervals.
    pub const AGGREGATED_LOOKBACK: i64 = 5000;
    /// Divisor for VN stock prices in legacy mode.
    pub const LEGACY_DIVISOR: f64 = 1000.0;
    /// Max SMA period — DB lookback buffer.
    pub const SMA_MAX_PERIOD: i64 = 200;
}
