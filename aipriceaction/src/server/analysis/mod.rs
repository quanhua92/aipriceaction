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

/// Try reading pre-computed OhlcvJoined from snapshot cache.
/// Returns `Some` if snapshots exist for enough tickers (>=90% hit rate).
pub async fn try_snap_joined(
    redis_client: &Option<RedisClient>,
    source: &str,
    symbols: &[String],
    interval: &str,
    limit: i64,
    ma_type: &str,
) -> Option<std::collections::HashMap<String, Vec<crate::models::ohlcv::OhlcvJoined>>> {
    let client = redis_client.as_ref()?;
    let result = crate::workers::redis_worker::batch_read_joined_snapshots(
        client, source, symbols, interval, limit, ma_type,
    ).await?;
    if result.len() >= symbols.len() * 9 / 10 {
        Some(result)
    } else {
        None
    }
}

/// Fetch enhanced data for a single source with snapshot optimization.
/// Tries snapshot first, falls through to try_redis_batch + enhance_rows.
/// On miss, writes joined snapshots for future reads (fire-and-forget).
pub async fn fetch_source_enhanced(
    redis_client: &Option<RedisClient>,
    source: &str,
    symbols: &[String],
    interval: &str,
    redis_limit: i64,
    ctx: &str,
    use_ema: bool,
    skip_snap: bool,
) -> std::collections::HashMap<String, Vec<crate::models::ohlcv::OhlcvJoined>> {
    let ma_type = if use_ema { "ema" } else { "sma" };

    // Try snapshot cache
    if !skip_snap {
        if let Some(snap_map) = try_snap_joined(redis_client, source, symbols, interval, 1, ma_type).await {
            return snap_map;
        }
    }

    // Fall through to Redis batch + enhance
    let mut result = std::collections::HashMap::new();
    if let Some(map) = try_redis_batch(redis_client, source, symbols, interval, redis_limit, ctx).await {
        for (ticker, orows) in map {
            let enhanced = crate::queries::ohlcv::enhance_rows(&ticker, orows, Some(1), None, true, use_ema);
            if !enhanced.is_empty() {
                result.insert(ticker, enhanced);
            }
        }
    }

    // Write joined snapshots for future reads (fire-and-forget)
    if !result.is_empty() {
        if let Some(redis) = redis_client {
            let ma_owned = ma_type.to_string();
            let src_owned = source.to_string();
            let iv_owned = interval.to_string();
            let result_clone = result.clone();
            let redis_clone = redis.clone();
            tokio::spawn(async move {
                crate::workers::redis_worker::batch_write_joined_snapshots(
                    &redis_clone, &src_owned, &[], &iv_owned, 1, &ma_owned, &result_clone,
                ).await;
            });
        }
    }

    result
}

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
        redis_client, source, symbols, interval, total_limit, ctx, None,
    )
    .await?;
    Some(result.into_iter().map(|(k, v)| (k, v.rows)).collect())
}

/// All data sources used by mode=all
pub fn get_all_sources() -> Vec<&'static str> {
    vec!["vn", "yahoo", "sjc", "crypto"]
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
