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

mod ohlcv;
mod stock_data;
mod timeframe;
mod ticker_group;
pub mod indicators;

pub use ohlcv::Ohlcv;
pub use stock_data::StockData;
pub use ticker_group::TickerGroups;
pub use timeframe::Timeframe;

use std::collections::HashMap;

/// Time series data for a single ticker
pub type TimeSeries = Vec<StockData>;

/// Market data collection (ticker -> time series)
pub type MarketData = HashMap<String, TimeSeries>;
