/// Market mode for API endpoints
///
/// Determines which data source to use for queries.
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    /// Vietnamese stock market (default)
    ///
    /// Data source: market_data/
    /// Supports: VN stocks, indices (VNINDEX, VN30)
    #[serde(alias = "stock", alias = "stocks")]
    Vn,

    /// Cryptocurrency market
    ///
    /// Data source: crypto_data/
    /// Supports: Top 100 cryptocurrencies
    #[serde(alias = "cryptos")]
    Crypto,
}

impl Default for Mode {
    fn default() -> Self {
        Mode::Vn
    }
}

impl Mode {
    /// Get data directory path for this mode
    pub fn get_data_dir(&self) -> PathBuf {
        match self {
            Mode::Vn => crate::utils::get_market_data_dir(),
            Mode::Crypto => crate::utils::get_crypto_data_dir(),
        }
    }

    /// Get ticker groups file path
    pub fn get_groups_file(&self) -> &'static str {
        match self {
            Mode::Vn => "ticker_group.json",
            Mode::Crypto => "crypto_top_100.json",
        }
    }

    /// Parse from string
    pub fn from_str(s: &str) -> Result<Self, String> {
        match s.to_lowercase().as_str() {
            "vn" | "stock" | "stocks" => Ok(Mode::Vn),
            "crypto" | "cryptos" => Ok(Mode::Crypto),
            _ => Err(format!("Invalid mode: '{}'. Valid values: vn, crypto", s)),
        }
    }

    /// Convert to string representation
    pub fn as_str(&self) -> &'static str {
        match self {
            Mode::Vn => "vn",
            Mode::Crypto => "crypto",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mode_default() {
        let mode = Mode::default();
        assert_eq!(mode, Mode::Vn);
    }

    #[test]
    fn test_mode_from_str() {
        assert_eq!(Mode::from_str("vn").unwrap(), Mode::Vn);
        assert_eq!(Mode::from_str("VN").unwrap(), Mode::Vn);
        assert_eq!(Mode::from_str("stock").unwrap(), Mode::Vn);
        assert_eq!(Mode::from_str("stocks").unwrap(), Mode::Vn);
        assert_eq!(Mode::from_str("crypto").unwrap(), Mode::Crypto);
        assert_eq!(Mode::from_str("CRYPTO").unwrap(), Mode::Crypto);
        assert_eq!(Mode::from_str("cryptos").unwrap(), Mode::Crypto);
        assert!(Mode::from_str("invalid").is_err());
    }

    #[test]
    fn test_mode_as_str() {
        assert_eq!(Mode::Vn.as_str(), "vn");
        assert_eq!(Mode::Crypto.as_str(), "crypto");
    }

    #[test]
    fn test_mode_groups_file() {
        assert_eq!(Mode::Vn.get_groups_file(), "ticker_group.json");
        assert_eq!(Mode::Crypto.get_groups_file(), "crypto_top_100.json");
    }

    #[test]
    fn test_mode_serialize() {
        let vn_json = serde_json::to_string(&Mode::Vn).unwrap();
        assert_eq!(vn_json, r#""vn""#);

        let crypto_json = serde_json::to_string(&Mode::Crypto).unwrap();
        assert_eq!(crypto_json, r#""crypto""#);
    }

    #[test]
    fn test_mode_deserialize() {
        let vn: Mode = serde_json::from_str(r#""vn""#).unwrap();
        assert_eq!(vn, Mode::Vn);

        let stock: Mode = serde_json::from_str(r#""stock""#).unwrap();
        assert_eq!(stock, Mode::Vn);

        let crypto: Mode = serde_json::from_str(r#""crypto""#).unwrap();
        assert_eq!(crypto, Mode::Crypto);
    }
}
