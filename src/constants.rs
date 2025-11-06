//! CSV Format Constants
//!
//! Defines the structure and column counts for CSV files in the project.
//!
//! ## CSV Format Evolution
//!
//! **Current Format (v0.2.0)**: 15 columns
//! - 7 basic OHLCV columns
//! - 8 technical indicator columns
//!
//! **Previous Format (v0.1.0)**: 16 columns (deprecated)
//! - Included money_flow, dollar_flow, trend_score

/// Number of basic OHLCV columns (ticker, time, open, high, low, close, volume)
pub const CSV_BASIC_COLUMNS: usize = 7;

/// Number of enhanced columns with technical indicators
///
/// Enhanced format includes:
/// - 7 basic OHLCV columns
/// - 3 moving averages (ma10, ma20, ma50)
/// - 3 MA scores (ma10_score, ma20_score, ma50_score)
/// - 2 change indicators (close_changed, volume_changed)
pub const CSV_ENHANCED_COLUMNS: usize = 15;

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

    // Technical indicator columns (7-14)
    pub const MA10: usize = 7;
    pub const MA20: usize = 8;
    pub const MA50: usize = 9;
    pub const MA10_SCORE: usize = 10;
    pub const MA20_SCORE: usize = 11;
    pub const MA50_SCORE: usize = 12;
    pub const CLOSE_CHANGED: usize = 13;
    pub const VOLUME_CHANGED: usize = 14;
}

/// Minimum number of records required to calculate MA50
pub const MIN_RECORDS_FOR_MA50: usize = 50;

/// Minimum number of records recommended for historical analysis
pub const MIN_RECORDS_FOR_ANALYSIS: usize = 1500;
