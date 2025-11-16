//! Cryptocurrency List Model
//!
//! This module provides functionality to load and manage the list of
//! cryptocurrencies from crypto_top_100.json.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

/// Cryptocurrency metadata from CoinMarketCap
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CryptoMetadata {
    pub rank: u32,
    pub name: String,
    pub symbol: String,
    pub price: String,
    pub change_24h: String,
    pub change_7d: String,
    pub market_cap: String,
    pub volume_24h: String,
    pub circulating_supply: String,
}

/// Cryptocurrency list container
#[derive(Debug, Deserialize)]
pub struct CryptoList {
    pub fetched_at: String,
    pub count: usize,
    pub data: Vec<CryptoMetadata>,
}

/// Load cryptocurrency list from JSON file
///
/// # Arguments
///
/// * `file_path` - Path to crypto_top_100.json
///
/// # Returns
///
/// Vector of cryptocurrency symbols (e.g., ["BTC", "ETH", "USDT", ...])
pub fn load_crypto_symbols<P: AsRef<Path>>(file_path: P) -> Result<Vec<String>, std::io::Error> {
    let contents = fs::read_to_string(file_path)?;
    let crypto_list: CryptoList = serde_json::from_str(&contents)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

    Ok(crypto_list
        .data
        .into_iter()
        .map(|c| c.symbol)
        .collect())
}

/// Load full cryptocurrency metadata from JSON file
///
/// # Arguments
///
/// * `file_path` - Path to crypto_top_100.json
///
/// # Returns
///
/// Vector of CryptoMetadata structs
pub fn load_crypto_metadata<P: AsRef<Path>>(
    file_path: P,
) -> Result<Vec<CryptoMetadata>, std::io::Error> {
    let contents = fs::read_to_string(file_path)?;
    let crypto_list: CryptoList = serde_json::from_str(&contents)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

    Ok(crypto_list.data)
}

/// Get default path to crypto_top_100.json (project root)
pub fn get_default_crypto_list_path() -> std::path::PathBuf {
    std::path::PathBuf::from("crypto_top_100.json")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore] // Requires crypto_top_100.json file
    fn test_load_crypto_symbols() {
        let symbols = load_crypto_symbols("crypto_top_100.json").unwrap();

        assert_eq!(symbols.len(), 100, "Should load 100 cryptocurrencies");
        assert_eq!(symbols[0], "BTC", "First symbol should be BTC");
        assert_eq!(symbols[1], "ETH", "Second symbol should be ETH");
    }

    #[test]
    #[ignore] // Requires crypto_top_100.json file
    fn test_load_crypto_metadata() {
        let metadata = load_crypto_metadata("crypto_top_100.json").unwrap();

        assert_eq!(metadata.len(), 100, "Should load 100 cryptocurrencies");

        let btc = &metadata[0];
        assert_eq!(btc.rank, 1);
        assert_eq!(btc.name, "Bitcoin");
        assert_eq!(btc.symbol, "BTC");
    }
}
