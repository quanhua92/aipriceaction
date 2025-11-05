use std::path::PathBuf;

/// Get market data directory from environment variable or use default
pub fn get_market_data_dir() -> PathBuf {
    std::env::var("MARKET_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("market_data"))
}
