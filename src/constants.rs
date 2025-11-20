//! CSV Format Constants
//!
//! Defines the structure and column counts for CSV files in the project.
//!
//! ## CSV Format Evolution
//!
//! **Current Format (v0.3.0)**: 19 columns
//! - 7 basic OHLCV columns
//! - 12 technical indicator columns
//!
//! **Previous Format (v0.2.0)**: 15 columns (deprecated)
//! - Only ma10, ma20, ma50
//!
//! **Previous Format (v0.1.0)**: 16 columns (deprecated)
//! - Included money_flow, dollar_flow, trend_score

/// Number of basic OHLCV columns (ticker, time, open, high, low, close, volume)
pub const CSV_BASIC_COLUMNS: usize = 7;

/// Number of enhanced columns with technical indicators
///
/// Enhanced format includes:
/// - 7 basic OHLCV columns
/// - 5 moving averages (ma10, ma20, ma50, ma100, ma200)
/// - 5 MA scores (ma10_score, ma20_score, ma50_score, ma100_score, ma200_score)
/// - 3 change indicators (close_changed, volume_changed, total_money_changed)
pub const CSV_ENHANCED_COLUMNS: usize = 20;

/// Column indices for enhanced CSV format (0-indexed)
pub mod csv_column {
    // Basic OHLCV columns (0-6)
    pub const TICKER: usize = 0;
    pub const TIME: usize = 1;
    pub const OPEN: usize = 2;
    pub const HIGH: usize = 3;
    pub const LOW: usize = 4;
    pub const CLOSE: usize = 5;
    pub const VOLUME: usize = 6;

    // Technical indicator columns (7-19)
    pub const MA10: usize = 7;
    pub const MA20: usize = 8;
    pub const MA50: usize = 9;
    pub const MA100: usize = 10;
    pub const MA200: usize = 11;
    pub const MA10_SCORE: usize = 12;
    pub const MA20_SCORE: usize = 13;
    pub const MA50_SCORE: usize = 14;
    pub const MA100_SCORE: usize = 15;
    pub const MA200_SCORE: usize = 16;
    pub const CLOSE_CHANGED: usize = 17;
    pub const VOLUME_CHANGED: usize = 18;
    pub const TOTAL_MONEY_CHANGED: usize = 19;
}

/// Minimum number of records required to calculate MA50
pub const MIN_RECORDS_FOR_MA50: usize = 50;

/// Minimum number of records required to calculate MA100
pub const MIN_RECORDS_FOR_MA100: usize = 100;

/// Minimum number of records required to calculate MA200
pub const MIN_RECORDS_FOR_MA200: usize = 200;

/// Minimum number of records recommended for historical analysis
pub const MIN_RECORDS_FOR_ANALYSIS: usize = 1500;

/// List of index tickers (not individual stocks)
/// These indices should NOT be scaled when using legacy price format
pub const INDEX_TICKERS: &[&str] = &["VNINDEX", "VN30"];

/// Batch API failure tracking threshold
/// Only fall back to individual fetches after batch API fails continuously for this many minutes
/// During temporary API issues, iterations will be skipped rather than triggering expensive fallback
pub const BATCH_FAILURE_THRESHOLD_MINUTES: i64 = 15;

/// Cryptocurrencies to ignore during sync (no data available from CryptoCompare API)
/// These symbols consistently fail with "Max retries exceeded" errors
pub const IGNORED_CRYPTOS: &[&str] = &["MNT", "IOTA"];

/// Vietnamese Stock Market Tick Sizes
///
/// Vietnamese stocks use dynamic tick sizes based on current price level:
///
/// | Price Range (VND) | Tick Size (VND) | Example             |
/// |-------------------|-----------------|---------------------|
/// | < 10,000          | 10              | 9,990 → 10,000     |
/// | 10,000 - 49,990   | 50              | 23,200 → 23,250    |
/// | ≥ 50,000          | 100             | 95,400 → 95,500    |
///
/// These tick sizes are used in Volume Profile calculations to determine
/// the granularity of price level aggregation.
///
/// **Note**: All prices in aipriceaction are stored in full VND format
/// (e.g., 23,200 not 23.2), so tick sizes are also in full format.
pub const TICK_SIZE_VN_LOW: f64 = 10.0;      // For prices < 10,000 VND
pub const TICK_SIZE_VN_MID: f64 = 50.0;      // For prices 10,000 - 49,990 VND
pub const TICK_SIZE_VN_HIGH: f64 = 100.0;    // For prices >= 50,000 VND

/// Cryptocurrency Tick Sizes
///
/// Cryptocurrencies use much finer tick sizes due to their decimal nature:
///
/// | Price Range (USD) | Tick Size | Example              |
/// |-------------------|-----------|----------------------|
/// | < 1.0             | 0.0001    | 0.5234 → 0.5235     |
/// | 1.0 - 99.99       | 0.01      | 45.67 → 45.68       |
/// | 100.0 - 999.99    | 0.1       | 456.7 → 456.8       |
/// | ≥ 1,000           | 1.0       | 45,678 → 45,679     |
///
/// These are used for crypto Volume Profile calculations.
pub const TICK_SIZE_CRYPTO_MICRO: f64 = 0.0001;  // For prices < $1
pub const TICK_SIZE_CRYPTO_SMALL: f64 = 0.01;    // For prices $1 - $99.99
pub const TICK_SIZE_CRYPTO_MID: f64 = 0.1;       // For prices $100 - $999.99
pub const TICK_SIZE_CRYPTO_LARGE: f64 = 1.0;     // For prices >= $1,000
