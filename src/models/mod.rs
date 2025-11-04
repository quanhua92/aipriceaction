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
