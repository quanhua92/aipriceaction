pub mod performers;
pub mod ma_scores;
pub mod volume_profile;
pub mod rrg;

use serde::Serialize;
use std::collections::{BTreeMap, HashMap};

use crate::models::ohlcv::OhlcvRow;
use crate::redis::RedisClient;

pub use performers::top_performers_handler;
pub use ma_scores::ma_scores_by_sector_handler;
pub use volume_profile::volume_profile_handler;
pub use rrg::rrg_handler;

/// Try reading OHLCV from Redis, stripping metadata to plain HashMap.
/// Returns `None` on any failure → caller falls through to PG.
pub async fn try_redis_batch(
    redis_client: &Option<RedisClient>,
    source: &str,
    symbols: &[String],
    interval: &str,
    total_limit: i64,
    ctx: &str,
) -> Option<HashMap<String, Vec<OhlcvRow>>> {
    let result = crate::server::redis_reader::batch_read_ohlcv_from_redis(
        redis_client, source, symbols, interval, total_limit, ctx,
    )
    .await?;
    Some(result.into_iter().map(|(k, v)| (k, v.rows)).collect())
}

/// All data sources used by mode=all
pub fn get_all_sources() -> Vec<&'static str> {
    vec!["vn", "yahoo", "sjc", "crypto"]
}

/// Load groups from a {source}_tickers.json file, keyed by category.
pub fn load_groups_from_json(source: &str) -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    crate::server::api::data_loader::load_groups_from_source(source)
}

/// Load yahoo/global groups including MERGE_WITH_YAHOO sources (e.g. SJC).
pub fn load_yahoo_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    crate::server::api::data_loader::load_yahoo_groups()
}

/// Load crypto groups from binance_tickers.json. All crypto go under "CRYPTO".
pub fn load_crypto_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    crate::server::api::data_loader::load_crypto_groups()
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

/// Get sector for a specific ticker from a map of group → tickers.
/// Works with both HashMap and BTreeMap via iteration.
pub fn get_ticker_sector<'a, I>(ticker: &str, groups: I) -> Option<String>
where
    I: IntoIterator<Item = (&'a String, &'a Vec<String>)>,
{
    for (sector, tickers) in groups {
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
