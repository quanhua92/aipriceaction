//! Stock market data models and indicators
//!
//! # Price Format Convention
//! **CRITICAL**: All prices in this module use **full VND format**.
//!
//! ## Stock Tickers (e.g., VCB, FPT, HPG)
//! - API returns: 23200
//! - CSV stores: 23.2 (divided by 1000)
//! - **We use**: 23200 (multiply CSV by 1000)
//!
//! ## Market Indices (VNINDEX, VN30)
//! - API returns: 1250.5
//! - CSV stores: 1250.5 (NO division)
//! - **We use**: 1250.5 (no conversion needed)
//!
//! **Rule**: Only multiply by 1000 for stock tickers, NOT for indices.

mod aggregated_interval;
mod mode;
mod ohlcv;
mod stock_data;
mod timeframe;
mod ticker_group;
pub mod indicators;
pub mod sync_config;
mod crypto_list;

pub use aggregated_interval::AggregatedInterval;
pub use mode::Mode;
pub use ohlcv::Ohlcv;
pub use stock_data::StockData;
pub use ticker_group::TickerGroups;
pub use timeframe::Timeframe;
pub use sync_config::{
    Interval, SyncConfig, TickerCategory, FetchProgress, SyncStats,
    MIN_MINUTE_RESUME_DAYS, MID_MINUTE_RESUME_DAYS, MAX_MINUTE_RESUME_DAYS,
    STALE_TICKER_THRESHOLD_DAYS,
};
pub use crypto_list::{
    CryptoMetadata, CryptoList, load_crypto_symbols, load_crypto_metadata,
    get_default_crypto_list_path,
};

use std::collections::HashMap;

/// Time series data for a single ticker
pub type TimeSeries = Vec<StockData>;

/// Market data collection (ticker -> time series)
pub type MarketData = HashMap<String, TimeSeries>;
