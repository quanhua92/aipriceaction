pub mod performers;
pub mod ma_scores;
pub mod volume_profile;
pub mod rrg;

use serde::Serialize;
use std::collections::{BTreeMap, HashMap};
use sqlx::PgPool;

use crate::models::ohlcv::OhlcvRow;
use crate::redis::RedisClient;

pub use performers::top_performers_handler;
pub use ma_scores::ma_scores_by_sector_handler;
pub use volume_profile::volume_profile_handler;
pub use rrg::rrg_handler;

/// Try reading pre-computed OhlcvJoined from snapshot cache.
/// A hit is valid only when every requested ticker has a matching snapshot;
/// partial hit sets would otherwise make symbol membership depend on cache state.
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
    let requested = symbols.iter().collect::<std::collections::HashSet<_>>();
    if result.len() == requested.len() {
        Some(result)
    } else {
        None
    }
}

/// Return requested symbols absent from a per-symbol result, preserving order
/// while removing duplicate catalog entries.
pub fn missing_symbols<T>(requested: &[String], present: &HashMap<String, T>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    requested
        .iter()
        .filter(|symbol| !present.contains_key(*symbol) && seen.insert(symbol.as_str()))
        .cloned()
        .collect()
}

/// Select the exact PostgreSQL fallback scope. `Some(Vec::new())` explicitly
/// means the whole source; `None` means there is no fallback query to run.
pub fn postgres_fallback_symbols<T>(
    requested: &[String],
    present: &HashMap<String, T>,
    include_all_when_empty: bool,
) -> Option<Vec<String>> {
    if requested.is_empty() {
        return include_all_when_empty.then(Vec::new);
    }
    let missing = missing_symbols(requested, present);
    (!missing.is_empty()).then_some(missing)
}

/// Fetch enhanced data for a single source with snapshot optimization.
/// Tries snapshot first, falls through to try_redis_batch + enhance_rows.
/// On miss, writes joined snapshots for future reads (fire-and-forget).
pub async fn fetch_source_enhanced(
    pool: &PgPool,
    redis_client: &Option<RedisClient>,
    source: &str,
    symbols: &[String],
    interval: &str,
    redis_limit: i64,
    ctx: &str,
    use_ema: bool,
    skip_snap: bool,
) -> std::collections::HashMap<String, Vec<crate::models::ohlcv::OhlcvJoined>> {
    if symbols.is_empty() {
        return std::collections::HashMap::new();
    }

    let ma_type = if use_ema { "ema" } else { "sma" };

    // Try snapshot cache
    if !skip_snap {
        // The cache field identifies the raw input window as well as the MA
        // type. A one-point response computed from 101 bars must not reuse a
        // snapshot computed from a different seed window (for example, 601).
        if let Some(snap_map) =
            try_snap_joined(redis_client, source, symbols, interval, redis_limit, ma_type).await
        {
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

    // Redis and the joined-snapshot cache can be partial per ticker. Fill only
    // missing requested symbols from PostgreSQL using the same bounded MA seed
    // depth as the Redis path.
    if let Some(missing) = postgres_fallback_symbols(symbols, &result, false) {
        let ma_buffer = redis_limit.saturating_sub(1).max(0) as usize;
        match crate::queries::ohlcv::get_latest_daily_per_ticker_with_ma_for_symbols(
            pool,
            source,
            Some(&missing),
            ma_buffer,
            use_ema,
        )
        .await
        {
            Ok(rows) => {
                for row in rows {
                    result.entry(row.ticker.clone()).or_insert_with(|| vec![row]);
                }
            }
            Err(error) => tracing::warn!(source, "analysis PostgreSQL fallback failed: {error}"),
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
                    &redis_clone,
                    &src_owned,
                    &[],
                    &iv_owned,
                    redis_limit,
                    &ma_owned,
                    &result_clone,
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
    try_redis_batch_until(redis_client, source, symbols, interval, total_limit, ctx, None).await
}

/// Read a bounded Redis tail ending at the supplied millisecond timestamp.
/// Applying the cutoff in Redis is important: fetching the newest N rows and
/// filtering afterward can leave too few pre-cutoff rows for indicator seeds.
pub async fn try_redis_batch_until(
    redis_client: &Option<RedisClient>,
    source: &str,
    symbols: &[String],
    interval: &str,
    total_limit: i64,
    ctx: &str,
    max_score: Option<i64>,
) -> Option<HashMap<String, Vec<OhlcvRow>>> {
    let result = crate::server::redis_reader::batch_read_ohlcv_from_redis(
        redis_client, source, symbols, interval, total_limit, ctx, max_score,
    )
    .await?;
    Some(result.into_iter().map(|(k, v)| (k, v.rows)).collect())
}

#[cfg(test)]
mod tests {
    use super::{missing_symbols, postgres_fallback_symbols};
    use std::collections::HashMap;

    #[test]
    fn missing_symbols_preserves_requested_order_and_deduplicates() {
        let requested = vec!["FPT".into(), "VNINDEX".into(), "FPT".into()];
        let present = HashMap::from([("FPT".to_owned(), ())]);

        assert_eq!(missing_symbols(&requested, &present), ["VNINDEX"]);
        let complete = HashMap::from([
            ("FPT".to_owned(), ()),
            ("VNINDEX".to_owned(), ()),
        ]);
        assert!(missing_symbols(&requested, &complete).is_empty());
    }

    #[test]
    fn empty_symbol_scope_is_not_a_wildcard_unless_explicitly_requested() {
        let present = HashMap::<String, ()>::new();
        assert_eq!(postgres_fallback_symbols::<()>(&[], &present, false), None);
        assert_eq!(postgres_fallback_symbols::<()>(&[], &present, true), Some(vec![]));
    }

    #[test]
    fn overlapping_ticker_groups_choose_lexicographically_first_sector() {
        let groups = HashMap::from([
            ("Zulu".to_owned(), vec!["VCB".to_owned()]),
            ("Alpha".to_owned(), vec!["VCB".to_owned()]),
        ]);

        assert_eq!(super::get_ticker_sector("VCB", &groups), Some("Alpha".to_owned()));
        assert_eq!(super::get_ticker_sector("FPT", &groups), None);
    }
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

/// Get the lexicographically first sector containing a ticker.
///
/// VN groups are loaded into a HashMap, whose iteration order is randomized;
/// explicitly choosing the first matching name keeps labels stable and aligned
/// with Python for overlapping memberships. Works with HashMap and BTreeMap.
pub fn get_ticker_sector<'a, I>(ticker: &str, groups: I) -> Option<String>
where
    I: IntoIterator<Item = (&'a String, &'a Vec<String>)>,
{
    groups
        .into_iter()
        .filter_map(|(sector, tickers)| {
            tickers
                .iter()
                .any(|member| member.as_str() == ticker)
                .then(|| sector.clone())
        })
        .min()
}

/// Whether a ticker is an index
pub fn is_index_ticker(ticker: &str) -> bool {
    crate::constants::vci_worker::INDEX_TICKERS.contains(&ticker.to_uppercase().as_str())
}
