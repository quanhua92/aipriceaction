//! Binance Vision Data Module
//!
//! This module provides utilities for reading Binance Vision cryptocurrency data
//! stored in ZIP files within the spot directory structure.

pub mod binance_utils;

// Re-export commonly used functions
pub use binance_utils::{
    read_binance_ticker_data,
    read_binance_ticker_data_limited,
    read_binance_ticker_data_date_range,
    list_available_tickers,
    list_available_dates,
};