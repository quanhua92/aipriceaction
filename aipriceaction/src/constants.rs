/// VCI worker timing and configuration constants.
pub mod vci_worker {
    /// Hard cap on next_* schedule delay regardless of tier or off-hours multiplier.
    pub const MAX_SCHEDULE_SECS: i64 = 900; // 15 minutes
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

    /// Dividend detection: price ratio threshold (3%)
    pub const DIVIDEND_RATIO_THRESHOLD: f64 = 1.03;
    /// Number of recent daily bars to compare for dividend detection
    pub const DIVIDEND_CHECK_BARS: i64 = 20;
    /// Minimum number of diverging candles to confirm a dividend (reduces false positives from data corrections)
    pub const DIVIDEND_MIN_DIVERGING_BARS: usize = 5;

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
    pub const DIVIDEND_CHUNK_SLEEP_SECS: u64 = 1;
    /// Percentage increase per consecutive stall (gap/holiday skip)
    pub const DIVIDEND_STALL_INCREASE_PCT: u32 = 50;
    /// Earliest date for hourly/minute re-download (VCI has no minute data before this)
    pub const DIVIDEND_HM_FLOOR_YEAR: i32 = 2023;
    pub const DIVIDEND_HM_FLOOR_MONTH: u32 = 8;

    /// Backoff before trying next client on HTTP 429/403
    pub const RATE_LIMIT_CLIENT_BACKOFF_SECS: u64 = 1;
    /// Cooldown when rate limited (HTTP 429) detected in a batch
    pub const RATE_LIMIT_COOLDOWN_SECS: u64 = 60;

    /// Hourly worker: initial delay before first sync
    pub const HOURLY_INITIAL_DELAY_SECS: u64 = 60;
    /// Minute worker: initial delay before first sync
    pub const MINUTE_INITIAL_DELAY_SECS: u64 = 60;

    /// Index tickers (no dividend detection)
    pub const INDEX_TICKERS: &[&str] = &["VNINDEX", "VN30", "VN30F1M"];

    /// Concurrent API batches based on VCI client count.
    /// 3 concurrent requests per client, each with its own rate limiter.
    pub fn concurrent_batches(client_count: usize) -> usize {
        let batches = (client_count * 3).min(24).max(1);
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
        pub const HOURLY_SECS: [i64; 4] = [120, 300, 600, 1200];
        pub const MINUTE_SECS: [i64; 4] = [180, 600, 1200, 2400];
    }
}

/// Major crypto tickers — synced every 60s for all intervals.
pub const MAJOR_CRYPTO: &[&str] = &["BTCUSDT", "ETHUSDT", "XRPUSDT", "TONUSDT"];

/// Major global tickers (indices, commodities) — synced every 60s for all intervals.
pub const MAJOR_GLOBAL: &[&str] = &["^GSPC", "^DJI", "^NDX", "GC=F", "CL=F"];

/// Major VN stock index tickers — synced every 60s for all intervals.
pub const MAJOR_VN: &[&str] = &["VNINDEX", "VN30", "VN30F1M"];

/// Schedule interval for major tickers (all intervals).
pub const MAJOR_SCHEDULE_SECS: i64 = 60;

/// Binance crypto worker timing and configuration constants.
pub mod binance_worker {
    /// Daily worker: loop interval (24/7, no trading hours)
    pub const DAILY_LOOP_SECS: u64 = 60;
    /// Hourly worker: loop interval
    pub const HOURLY_LOOP_SECS: u64 = 60;
    /// Minute worker: loop interval
    pub const MINUTE_LOOP_SECS: u64 = 60;

    /// Hourly worker: initial delay before first sync
    pub const HOURLY_INITIAL_DELAY_SECS: u64 = 60;
    /// Minute worker: initial delay before first sync
    pub const MINUTE_INITIAL_DELAY_SECS: u64 = 60;

    /// Cooldown when rate limited (HTTP 429/403) detected in a batch
    pub const RATE_LIMIT_COOLDOWN_SECS: u64 = 60;

    /// Max tickers to process per loop iteration
    pub const DUE_TICKER_BATCH_SIZE: usize = 20;

    /// Concurrent API batches based on Binance API client count.
    /// Conservative: 2 per client, max 6 total.
    pub fn concurrent_batches(client_count: usize) -> usize {
        (client_count * 2).min(6).max(1)
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

    /// Schedule interval for a ticker: major crypto tickers get MAJOR_SCHEDULE_SECS,
    /// others get the standard interval for their timeframe.
    pub fn schedule_secs(ticker: &str, default_secs: i64) -> i64 {
        if super::MAJOR_CRYPTO.contains(&ticker) {
            super::MAJOR_SCHEDULE_SECS
        } else {
            default_secs
        }
    }
}

/// Yahoo Finance worker timing and configuration constants.
pub mod yahoo_worker {
    /// Daily worker: loop interval
    pub const DAILY_LOOP_SECS: u64 = 60;
    /// Hourly worker: loop interval
    pub const HOURLY_LOOP_SECS: u64 = 60;
    /// Minute worker: loop interval
    pub const MINUTE_LOOP_SECS: u64 = 60;

    /// Hourly worker: initial delay before first sync
    pub const HOURLY_INITIAL_DELAY_SECS: u64 = 60;
    /// Minute worker: initial delay before first sync
    pub const MINUTE_INITIAL_DELAY_SECS: u64 = 60;

    /// Cooldown when rate limited (HTTP 429) detected in a batch
    pub const RATE_LIMIT_COOLDOWN_SECS: u64 = 60;

    /// Max tickers to process per loop iteration
    pub const DUE_TICKER_BATCH_SIZE: usize = 20;

    /// Concurrent API batches based on Yahoo client count.
    pub fn concurrent_batches(client_count: usize) -> usize {
        (client_count * 2).min(4).max(1)
    }

    /// Ranges for incremental fetches
    pub const DAILY_RANGE: &str = "5d";
    pub const HOURLY_RANGE: &str = "5d";
    pub const MINUTE_RANGE: &str = "1d";

    /// Fixed scheduling intervals (seconds)
    pub const SCHEDULE_DAILY_SECS: i64 = 60;
    pub const SCHEDULE_HOURLY_SECS: i64 = 300;
    pub const SCHEDULE_MINUTE_SECS: i64 = 600;

    /// Schedule interval for a ticker: major global tickers get MAJOR_SCHEDULE_SECS,
    /// others get the standard interval for their timeframe.
    pub fn schedule_secs(ticker: &str, default_secs: i64) -> i64 {
        if super::MAJOR_GLOBAL.contains(&ticker) {
            super::MAJOR_SCHEDULE_SECS
        } else {
            default_secs
        }
    }

    /// Bootstrap chunk sizes (days)
    pub const BOOTSTRAP_DAILY_CHUNK_DAYS: i64 = 365;
    pub const BOOTSTRAP_HOURLY_CHUNK_DAYS: i64 = 30;
    pub const BOOTSTRAP_MINUTE_CHUNK_DAYS: i64 = 7;
    pub const BOOTSTRAP_LOOP_SECS: u64 = 60;
    /// Sleep between chunk fetches while actively downloading a ticker
    pub const BOOTSTRAP_CHUNK_SLEEP_SECS: u64 = 2;
    /// Earliest year for hourly/minute bootstrap data
    pub const BOOTSTRAP_HM_FLOOR_YEAR: i32 = 2023;
    /// Yahoo Finance only serves hourly data within this many days from now
    pub const BOOTSTRAP_HOURLY_LOOKBACK_DAYS: i64 = 730;
    /// Yahoo Finance only serves minute data within this many days from now
    pub const BOOTSTRAP_MINUTE_LOOKBACK_DAYS: i64 = 30;

    /// Dividend / stock-split detection for Yahoo tickers
    pub const DIVIDEND_RATIO_THRESHOLD: f64 = 1.03;
    pub const DIVIDEND_CHECK_BARS: i64 = 20;
    pub const DIVIDEND_MIN_DIVERGING_BARS: usize = 5;
}

/// SJC gold price worker timing and configuration constants.
pub mod sjc_worker {
    /// Daily worker: loop interval during trading hours (5 min)
    pub const DAILY_LOOP_TRADE_SECS: u64 = 300;
    /// Daily worker: loop interval outside trading hours (30 min)
    pub const DAILY_LOOP_OFF_SECS: u64 = 1800;

    /// Schedule interval for next daily sync (seconds)
    pub const SCHEDULE_DAILY_SECS: i64 = 300;
    /// Cooldown after API error (seconds)
    pub const API_ERROR_COOLDOWN_SECS: u64 = 60;

    /// Ticker symbol for SJC gold
    pub const TICKER: &str = "SJC-GOLD";
    /// Data source label
    pub const SOURCE: &str = "sjc";
    /// Display name
    pub const NAME: &str = "SJC Gold Bar";
    /// CSV file name (historical data)
    pub const CSV_PATH: &str = "sjc-batch.csv";
    /// Import batch size
    pub const IMPORT_BATCH_SIZE: usize = 500;

    /// SJC API endpoint (PriceService.ashx — the working legacy endpoint)
    pub const API_URL: &str = "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx";
    /// Branch filter for Ho Chi Minh prices
    pub const BRANCH: &str = "Hồ Chí Minh";
}

/// Additional data sources whose tickers should appear under the yahoo/global mode.
/// Each source maps to its ticker JSON file: {source}_tickers.json
pub const MERGE_WITH_YAHOO: &[&str] = &["sjc"];

/// API server constants.
pub mod api {
    /// Cache TTL for /tickers responses (seconds).
    pub const CACHE_TTL_SECS: u64 = 5;
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
    /// Compute the lookback buffer size for a given max MA period.
    pub const fn sma_buffer_for(max_period: usize) -> i64 {
        max_period as i64
    }
    /// Default value for the `ema` query parameter across all endpoints.
    /// Set to true to use EMA by default, false to use SMA.
    pub const DEFAULT_USE_EMA: bool = false;
    /// Max allowed ?limit= value when requesting multiple/all tickers (no symbol or >1 symbol).
    /// Single-ticker requests are uncapped. Override via API_MAX_LIMIT env var. Default: 40.
    pub fn max_limit() -> i64 {
        std::env::var("API_MAX_LIMIT")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(40)
    }
}

/// Redis ZSET OHLCV cache configuration constants.
pub mod redis_ts {
    /// Maximum ZSET members for daily data (~10 years of daily bars).
    /// Override via `REDIS_DAILY_MAX_SIZE` env var. Default: 5000.
    pub fn daily_max_size() -> usize { env("REDIS_DAILY_MAX_SIZE", 5000) }
    /// Maximum ZSET members for hourly data (~3 years of hourly bars).
    /// Override via `REDIS_HOURLY_MAX_SIZE` env var. Default: 30000.
    pub fn hourly_max_size() -> usize { env("REDIS_HOURLY_MAX_SIZE", 30000) }
    /// Maximum ZSET members for minute data (~14 days of minute bars).
    /// Override via `REDIS_MINUTE_MAX_SIZE` env var. Default: 20000.
    pub fn minute_max_size() -> usize { env("REDIS_MINUTE_MAX_SIZE", 20000) }

    /// Backfill limit for daily data (rows fetched from PG).
    /// Override via `REDIS_DAILY_BACKFILL_LIMIT` env var. Default: 5000.
    pub fn daily_backfill_limit() -> i64 { env("REDIS_DAILY_BACKFILL_LIMIT", 5000) }
    /// Backfill limit for hourly data (rows fetched from PG).
    /// Override via `REDIS_HOURLY_BACKFILL_LIMIT` env var. Default: 30000.
    pub fn hourly_backfill_limit() -> i64 { env("REDIS_HOURLY_BACKFILL_LIMIT", 30000) }
    /// Backfill limit for minute data (rows fetched from PG).
    /// Override via `REDIS_MINUTE_BACKFILL_LIMIT` env var. Default: 20000.
    pub fn minute_backfill_limit() -> i64 { env("REDIS_MINUTE_BACKFILL_LIMIT", 20000) }

    /// Parse an env var as type T, falling back to default.
    fn env<T: std::str::FromStr>(key: &str, default: T) -> T {
        std::env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
    }

    /// Backfill loop interval in seconds (60 minutes).
    pub const BACKFILL_LOOP_SECS: u64 = 3600;
    /// Concurrency for backfill worker (parallel tasks per cycle).
    pub const BACKFILL_CONCURRENCY: usize = 2;

    /// ZSET member format: "{ts_ms}|{open}|{high}|{low}|{close}|{volume}|{crawl_ts_ms}"
    /// Separator used between fields in the member string.
    pub const MEMBER_SEP: &str = "|";

    /// Redis key for cached ticker list (JSON array of {source, ticker}).
    pub const TICKER_LIST_KEY: &str = "meta:ticker_list";
    /// TTL for cached ticker list in seconds (15 minutes).
    pub const TICKER_LIST_TTL_SECS: u64 = 900;
}
