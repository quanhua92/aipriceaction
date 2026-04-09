pub mod performers;
pub mod ma_scores;
pub mod volume_profile;
pub mod rrg;

use serde::Serialize;
use std::collections::{BTreeMap, HashMap};

pub use performers::top_performers_handler;
pub use ma_scores::ma_scores_by_sector_handler;
pub use volume_profile::volume_profile_handler;
pub use rrg::rrg_handler;

/// All data sources used by mode=all
pub fn get_all_sources() -> Vec<&'static str> {
    vec!["vn", "yahoo", "sjc", "crypto"]
}

/// Resolve a data file path (current dir, then parent dir).
fn resolve_data_file(name: &str) -> Result<std::path::PathBuf, Box<dyn std::error::Error + Send + Sync>> {
    let cwd = std::path::Path::new(name);
    if cwd.exists() {
        return Ok(cwd.to_path_buf());
    }
    let parent = std::path::Path::new("..").join(name);
    if parent.exists() {
        return Ok(parent);
    }
    Err(format!("Data file not found: {name} (searched . and ../)").into())
}

/// Load groups from a {source}_tickers.json file, keyed by category.
pub fn load_groups_from_json(source: &str) -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    let filename = format!("{source}_tickers.json");
    let path = resolve_data_file(&filename)?;
    let content = std::fs::read_to_string(&path)?;
    let raw: serde_json::Value = serde_json::from_str(&content)?;

    let mut map = BTreeMap::new();
    if let Some(data) = raw["data"].as_array() {
        for item in data {
            let (symbol, category) = match (item["symbol"].as_str(), item["category"].as_str()) {
                (Some(s), Some(c)) => (s.to_string(), c.to_string()),
                (Some(s), None) => (s.to_string(), "Other".to_string()),
                _ => continue,
            };
            map.entry(category).or_insert_with(Vec::new).push(symbol);
        }
    }
    Ok(map)
}

/// Load yahoo/global groups including MERGE_WITH_YAHOO sources (e.g. SJC).
pub fn load_yahoo_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    let mut map = load_groups_from_json("global")?;
    for source in crate::constants::MERGE_WITH_YAHOO {
        let extra = load_groups_from_json(source)?;
        for (category, symbols) in extra {
            map.entry(category).or_insert_with(Vec::new).extend(symbols);
        }
    }
    Ok(map)
}

/// Load crypto groups from binance_tickers.json. All crypto go under "CRYPTO".
pub fn load_crypto_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    let groups = load_groups_from_json("binance")?;
    // binance_tickers.json has no categories — merge all symbols under "CRYPTO"
    let mut map = BTreeMap::new();
    let all_symbols: Vec<String> = groups.into_values().flatten().collect();
    if !all_symbols.is_empty() {
        map.insert("CRYPTO".to_string(), all_symbols);
    }
    Ok(map)
}

/// Common analysis response structure
#[derive(Debug, Serialize)]
pub struct AnalysisResponse<T> {
    pub analysis_date: String,
    pub analysis_type: String,
    pub total_analyzed: usize,
    pub data: T,
}

/// Validate and parse limit parameter
pub fn validate_limit(limit: Option<usize>) -> usize {
    limit.unwrap_or(10).min(100).max(1)
}

/// Parse date string or use latest available
pub fn parse_analysis_date(
    date_str: Option<&str>,
) -> chrono::DateTime<chrono::Utc> {
    if let Some(date_str) = date_str {
        match chrono::NaiveDate::parse_from_str(date_str, "%Y-%m-%d") {
            Ok(naive_date) => {
                let naive_dt = naive_date.and_hms_opt(23, 59, 59).unwrap();
                chrono::DateTime::<chrono::Utc>::from_naive_utc_and_offset(naive_dt, chrono::Utc)
            }
            Err(_) => chrono::Utc::now(),
        }
    } else {
        chrono::Utc::now()
    }
}

/// Load ticker groups from JSON file
pub fn load_ticker_groups() -> Result<HashMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    // Try current directory first, then parent
    let path = if std::path::Path::new("ticker_group.json").exists() {
        std::path::PathBuf::from("ticker_group.json")
    } else {
        std::path::Path::new("..").join("ticker_group.json")
    };

    let content = std::fs::read_to_string(&path)?;
    let groups: HashMap<String, Vec<String>> = serde_json::from_str(&content)?;
    Ok(groups)
}

/// Get tickers for a specific sector
pub fn get_tickers_in_sector(sector: &str, ticker_groups: &HashMap<String, Vec<String>>) -> Vec<String> {
    ticker_groups
        .get(sector)
        .cloned()
        .unwrap_or_default()
}

/// Get sector for a specific ticker
pub fn get_ticker_sector(ticker: &str, ticker_groups: &HashMap<String, Vec<String>>) -> Option<String> {
    for (sector, tickers) in ticker_groups {
        if tickers.contains(&ticker.to_string()) {
            return Some(sector.clone());
        }
    }
    None
}

/// Whether a ticker is an index
pub fn is_index_ticker(ticker: &str) -> bool {
    crate::constants::vci_worker::INDEX_TICKERS.contains(&ticker.to_uppercase().as_str())
}
