use chrono::{Datelike, Duration, NaiveDate, Utc};
use futures::StreamExt;
use http::HeaderMap;
use sqlx::PgPool;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration as StdDuration;

use awscreds::Credentials;
use s3::{Bucket, BucketConfiguration, Region};

use crate::constants::s3_archive::{
    CSV_CONTENT_TYPE, FUNDAMENTAL_DELAY_MS, FUNDAMENTAL_MAX_CONSECUTIVE_RATE_LIMIT,
    FUNDAMENTAL_RATE_LIMIT, FUNDAMENTAL_VCI_DEAD_THRESHOLD, JSON_CONTENT_TYPE,
    LOOKBACK_DAYS, LOOP_SECS, STARTUP_CONSECUTIVE_SKIP_LIMIT, STARTUP_SCAN_INTERVAL_SECS,
    UPLOAD_CONCURRENCY,
};
use crate::constants::vci_worker::INDEX_TICKERS;
use crate::providers::vci::VciProvider;
use crate::queries::ohlcv::Ticker;
use crate::queries::s3_archive::{
    day_range, get_data_ranges, get_ohlcv_day_fingerprint, get_ohlcv_for_day,
    get_ohlcv_for_year, get_ohlcv_year_fingerprint, year_range, ArchiveTicker,
};

/// Result from a per-day or yearly scan task.
enum ScanResult {
    DayScan { uploaded: u64, skipped: u64 },
    YearlyScan { uploaded: u64, skipped: u64 },
}

// ── Enrichment data loaded once at startup ──

struct EnrichmentData {
    vn_names: HashMap<String, String>,          // ticker -> organ_name (from vn.csv)
    vn_exchange: HashMap<String, String>,       // ticker -> HOSE/HNX/UPCOM
    vn_type: HashMap<String, String>,           // ticker -> stock/index
    crypto_names: HashMap<String, String>,      // symbol -> name
    yahoo_names: HashMap<String, String>,       // symbol -> name
    yahoo_categories: HashMap<String, String>,  // symbol -> category
    sjc_names: HashMap<String, String>,         // symbol -> name
    sjc_categories: HashMap<String, String>,    // symbol -> category
    ticker_groups: BTreeMap<String, Vec<String>>, // group_name -> [tickers]
    ticker_to_group: HashMap<String, String>,   // ticker -> group_name (reverse lookup)
}

impl EnrichmentData {
    fn load() -> Self {
        let vn_names = load_vn_csv_field(1);
        let vn_exchange = load_vn_csv_field(3);
        let vn_type = load_vn_csv_field(4);
        let (crypto_names, _) = load_ticker_json("binance_tickers.json", "CRYPTO_TOP_100");
        let (yahoo_names, yahoo_categories) = load_ticker_json("global_tickers.json", "");
        let (sjc_names, sjc_categories) = load_ticker_json("sjc_tickers.json", "");

        let ticker_groups = load_ticker_groups();
        let mut ticker_to_group = HashMap::new();
        for (group, tickers) in &ticker_groups {
            for t in tickers {
                ticker_to_group.insert(t.clone(), group.clone());
            }
        }

        Self {
            vn_names,
            vn_exchange,
            vn_type,
            crypto_names,
            yahoo_names,
            yahoo_categories,
            sjc_names,
            sjc_categories,
            ticker_groups,
            ticker_to_group,
        }
    }

    fn enrich(&self, base: &mut ArchiveTicker) {
        match base.source.as_str() {
            "vn" => {
                if base.name.is_none() {
                    base.name = self.vn_names.get(&base.ticker).cloned();
                }
                base.exchange = self.vn_exchange.get(&base.ticker).cloned();
                base.ticker_type = self.vn_type.get(&base.ticker).cloned();
                base.group = self.ticker_to_group.get(&base.ticker).cloned();
            }
            "crypto" => {
                if base.name.is_none() {
                    base.name = self.crypto_names.get(&base.ticker).cloned();
                }
                base.group = Some("CRYPTO_TOP_100".into());
            }
            "yahoo" => {
                if base.name.is_none() {
                    base.name = self.yahoo_names.get(&base.ticker).cloned();
                }
                base.category = self.yahoo_categories.get(&base.ticker).cloned();
                base.group = self.ticker_to_group.get(&base.ticker)
                    .cloned()
                    .or_else(|| self.yahoo_categories.get(&base.ticker).cloned());
            }
            "sjc" => {
                if base.name.is_none() {
                    base.name = self.sjc_names.get(&base.ticker).cloned();
                }
                base.category = self.sjc_categories.get(&base.ticker).cloned();
                base.group = self.sjc_categories.get(&base.ticker).cloned();
            }
            _ => {}
        }
    }
}


/// Load a specific field index from vn.csv.
fn load_vn_csv_field(field_index: usize) -> HashMap<String, String> {
    let mut map = HashMap::new();
    let Ok(content) = std::fs::read_to_string("vn.csv") else {
        tracing::warn!("s3_archive: vn.csv not found, skipping VN enrichment");
        return map;
    };
    let mut rdr = csv::ReaderBuilder::new().from_reader(content.as_bytes());
    for result in rdr.records() {
        if let Ok(record) = result {
            let symbol = record.get(0).unwrap_or("").trim().to_uppercase();
            if symbol.is_empty() {
                continue;
            }
            if let Some(val) = record.get(field_index) {
                let val = val.trim();
                if !val.is_empty() {
                    map.insert(symbol, val.to_string());
                }
            }
        }
    }
    map
}

/// Load ticker names from a JSON file with a `data[]` array of `{symbol, name, category?}`.
fn load_ticker_json(path: &str, _default_group: &str) -> (HashMap<String, String>, HashMap<String, String>) {
    let mut names = HashMap::new();
    let mut categories = HashMap::new();
    let Ok(content) = std::fs::read_to_string(path) else {
        tracing::warn!("s3_archive: {path} not found, skipping");
        return (names, categories);
    };
    let Ok(raw) = serde_json::from_str::<serde_json::Value>(&content) else {
        tracing::warn!("s3_archive: failed to parse {path}");
        return (names, categories);
    };
    if let Some(items) = raw["data"].as_array() {
        for item in items {
            let symbol = item["symbol"].as_str().unwrap_or("").to_string();
            if symbol.is_empty() {
                continue;
            }
            if let Some(name) = item["name"].as_str() {
                names.insert(symbol.clone(), name.to_string());
            }
            if let Some(cat) = item["category"].as_str() {
                categories.insert(symbol.clone(), cat.to_string());
            }
        }
    }
    (names, categories)
}

/// Load ticker_group.json as a BTreeMap<String, Vec<String>>.
fn load_ticker_groups() -> BTreeMap<String, Vec<String>> {
    let Ok(content) = std::fs::read_to_string("ticker_group.json") else {
        tracing::warn!("s3_archive: ticker_group.json not found, skipping group enrichment");
        return BTreeMap::new();
    };
    serde_json::from_str(&content).unwrap_or_default()
}

// ── S3 helpers ──

fn create_s3_bucket() -> Result<Bucket, Box<dyn std::error::Error + Send + Sync>> {
    let bucket_name = std::env::var("S3_BUCKET")?;
    let region_str = std::env::var("S3_REGION").unwrap_or_else(|_| "us-east-1".into());

    let creds = if let Ok(key) = std::env::var("AWS_ACCESS_KEY_ID") {
        let secret = std::env::var("AWS_SECRET_ACCESS_KEY").unwrap_or_default();
        Credentials::new(
            Some(key.as_str()),
            Some(secret.as_str()),
            None,
            None,
            None,
        )?
    } else {
        Credentials::default()?
    };

    // If S3_ENDPOINT is set, use Region::Custom for S3-compatible storage (rustfs)
    let region = if let Ok(endpoint) = std::env::var("S3_ENDPOINT") {
        Region::Custom {
            region: region_str,
            endpoint,
        }
    } else {
        region_str.parse::<Region>()?
    };

    let bucket = Bucket::new(&bucket_name, region, creds)?;

    // Path-style URLs for S3-compatible storage (rustfs, MinIO, etc.)
    Ok(*bucket.with_path_style())
}

/// Create the S3 bucket if it doesn't exist.
async fn ensure_bucket(bucket: &Bucket) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    match bucket.exists().await {
        Ok(true) => {
            tracing::info!("s3_archive: bucket '{}' exists", bucket.name());
        }
        Ok(false) => {
            tracing::info!("s3_archive: creating bucket '{}'...", bucket.name());
            let creds = bucket.credentials().await?;
            Bucket::create_with_path_style(
                &bucket.name(),
                bucket.region.clone(),
                creds,
                BucketConfiguration::default(),
            )
            .await?;
            tracing::info!("s3_archive: bucket '{}' created", bucket.name());
        }
        Err(e) => {
            tracing::warn!("s3_archive: bucket.exists() failed: {e}, trying to create...");
            let creds = bucket.credentials().await?;
            Bucket::create_with_path_style(
                &bucket.name(),
                bucket.region.clone(),
                creds,
                BucketConfiguration::default(),
            )
            .await?;
            tracing::info!("s3_archive: bucket '{}' created", bucket.name());
        }
    }
    Ok(())
}

/// Build S3 key for a ticker+interval+date.
fn s3_key(source: &str, ticker: &str, interval: &str, date: NaiveDate) -> String {
    format!(
        "ohlcv/{}/{}/{}/{}-{}-{}.csv",
        source,
        ticker,
        interval,
        ticker,
        interval,
        date.format("%Y-%m-%d"),
    )
}

/// Build S3 key for a yearly aggregate CSV.
fn s3_key_yearly(source: &str, ticker: &str, interval: &str, year: i32) -> String {
    format!(
        "ohlcv/{}/{}/yearly/{}-{}-{}.csv",
        source, ticker, ticker, interval, year,
    )
}

/// Build CSV bytes from OHLCV rows.
fn rows_to_csv(rows: &[crate::models::ohlcv::OhlcvRow]) -> Vec<u8> {
    let mut wtr = csv::WriterBuilder::new()
        .has_headers(true)
        .from_writer(Vec::new());

    for row in rows {
        let _ = wtr.serialize(&[
            row.time.format("%Y-%m-%d %H:%M:%S").to_string(),
            row.open.to_string(),
            row.high.to_string(),
            row.low.to_string(),
            row.close.to_string(),
            row.volume.to_string(),
        ]);
    }

    wtr.into_inner().unwrap_or_default()
}

// ── Fundamental data (company_info + financial_ratios) ──────────────────────

/// Build S3 key for fundamental company info.
fn s3_key_company_info(source: &str, ticker: &str) -> String {
    format!("fundamental/{}/{}/company_info.json", source, ticker)
}

/// Build S3 key for fundamental financial ratios.
fn s3_key_financial_ratios(source: &str, ticker: &str) -> String {
    format!("fundamental/{}/{}/financial_ratios.json", source, ticker)
}

/// Build S3 key for fundamental per-ticker metadata checkpoint.
fn s3_key_meta(source: &str, ticker: &str) -> String {
    format!("fundamental/{}/{}/_meta.json", source, ticker)
}

/// Per-ticker metadata stored in S3 as `_meta.json`. Persists across restarts.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct FundamentalMeta {
    ticker: String,
    last_fetch: String,
    company_info_uploaded: bool,
    financial_ratios_uploaded: bool,
}

/// In-memory state tracking last successful fundamental fetch date per ticker.
/// Hydrated from S3 `_meta.json` on startup to avoid redundant VCI calls after restart.
#[derive(Default)]
struct FundamentalState {
    last_fetch: HashMap<String, NaiveDate>,
}

impl FundamentalState {
    fn is_due(&self, ticker: &str, today: NaiveDate) -> bool {
        self.last_fetch.get(ticker).map_or(true, |d| *d < today)
    }

    fn mark_done(&mut self, ticker: &str, date: NaiveDate) {
        self.last_fetch.insert(ticker.to_string(), date);
    }
}

/// Lazy-loaded fallback from local company_info.json (37MB).
/// Loaded once per fundamental cycle when the first rate-limited ticker needs it.
/// Avoids loading into memory unless VCI is unreachable.
/// If file is missing or corrupt, `tried` prevents re-reading on every ticker.
struct CompanyInfoFallback {
    data: Option<HashMap<String, FallbackEntry>>,
    tried: bool,
}

#[derive(serde::Deserialize)]
struct FallbackEntry {
    #[serde(default)]
    company_info: Option<crate::providers::vci::CompanyInfo>,
    #[serde(default)]
    financial_ratios: Option<Vec<std::collections::HashMap<String, serde_json::Value>>>,
}

impl CompanyInfoFallback {
    fn new() -> Self {
        Self {
            data: None,
            tried: false,
        }
    }

    fn company_info(&mut self, ticker: &str) -> Option<crate::providers::vci::CompanyInfo> {
        self.ensure_loaded();
        self.data
            .as_ref()?
            .get(ticker)
            .and_then(|e| e.company_info.clone())
    }

    fn financial_ratios(
        &mut self,
        ticker: &str,
    ) -> Option<Vec<std::collections::HashMap<String, serde_json::Value>>> {
        self.ensure_loaded();
        self.data
            .as_ref()?
            .get(ticker)
            .and_then(|e| e.financial_ratios.clone())
    }

    fn ensure_loaded(&mut self) {
        if self.tried {
            return;
        }
        self.tried = true;
        let start = std::time::Instant::now();
        match Self::load_from_file() {
            Ok(map) => {
                tracing::info!(
                    "[FUNDAMENTAL] loaded company_info.json fallback ({} tickers, {:.1}s)",
                    map.len(),
                    start.elapsed().as_secs_f64(),
                );
                self.data = Some(map);
            }
            Err(e) => {
                tracing::warn!("[FUNDAMENTAL] company_info.json fallback unavailable: {e}");
            }
        }
    }

    fn load_from_file() -> Result<HashMap<String, FallbackEntry>, Box<dyn std::error::Error + Send + Sync>> {
        let path = std::path::Path::new("company_info.json");
        if !path.exists() {
            return Err("company_info.json not found".into());
        }
        let file = std::fs::File::open(path)?;
        let reader = std::io::BufReader::with_capacity(8 * 1024 * 1024, file);
        let entries: Vec<FallbackEntryRaw> = serde_json::from_reader(reader)?;
        let mut map = HashMap::new();
        for entry in entries {
            if let Some(ref ci) = entry.company_info {
                if !ci.symbol.is_empty() {
                    map.insert(ci.symbol.clone(), FallbackEntry {
                        company_info: entry.company_info,
                        financial_ratios: entry.financial_ratios,
                    });
                    continue;
                }
            }
            if !entry.ticker.is_empty() {
                map.insert(entry.ticker.clone(), FallbackEntry {
                    company_info: entry.company_info,
                    financial_ratios: entry.financial_ratios,
                });
            }
        }
        Ok(map)
    }
}

#[derive(serde::Deserialize)]
struct FallbackEntryRaw {
    ticker: String,
    #[serde(default)]
    company_info: Option<crate::providers::vci::CompanyInfo>,
    #[serde(default)]
    financial_ratios: Option<Vec<std::collections::HashMap<String, serde_json::Value>>>,
}

/// Fetch existing JSON from S3, deserialize into T. Returns None if not found or parse error.
async fn fetch_existing_json<T: serde::de::DeserializeOwned>(
    bucket: &Bucket,
    key: &str,
) -> Option<T> {
    match bucket.get_object(key).await {
        Ok(response_data) => serde_json::from_slice::<T>(response_data.as_slice()).ok(),
        Err(_) => None,
    }
}

/// Merge new CompanyInfo into old. Prefers new data; falls back to old when new is None/empty.
fn merge_company_info(
    new: crate::providers::vci::CompanyInfo,
    old: Option<crate::providers::vci::CompanyInfo>,
) -> crate::providers::vci::CompanyInfo {
    let Some(old) = old else { return new };

    crate::providers::vci::CompanyInfo {
        symbol: new.symbol,
        exchange: new.exchange.or(old.exchange),
        industry: new.industry.or(old.industry),
        company_type: new.company_type.or(old.company_type),
        established_year: new.established_year.or(old.established_year),
        employees: new.employees.or(old.employees),
        market_cap: new.market_cap.or(old.market_cap),
        current_price: new.current_price.or(old.current_price),
        outstanding_shares: new.outstanding_shares.or(old.outstanding_shares),
        company_profile: new.company_profile.or(old.company_profile),
        website: new.website.or(old.website),
        shareholders: if new.shareholders.is_empty() {
            old.shareholders
        } else {
            new.shareholders
        },
        officers: if new.officers.is_empty() {
            old.officers
        } else {
            new.officers
        },
    }
}

/// Merge new financial ratios with old. Match entries by (yearReport, lengthReport).
/// Prefers new entries; keeps old entries that have no new counterpart.
/// For overlapping entries, keeps new (it's fresher data).
fn merge_financial_ratios(
    new: &[std::collections::HashMap<String, serde_json::Value>],
    old: &[std::collections::HashMap<String, serde_json::Value>],
) -> Vec<std::collections::HashMap<String, serde_json::Value>> {
    if old.is_empty() {
        return new.to_vec();
    }
    if new.is_empty() {
        return old.to_vec();
    }

    fn period_key(r: &std::collections::HashMap<String, serde_json::Value>) -> (i64, i64) {
        let year = r.get("yearReport").and_then(|v| v.as_i64()).unwrap_or(0);
        let length = r.get("lengthReport").and_then(|v| v.as_i64()).unwrap_or(0);
        (year, length)
    }

    let new_keys: std::collections::HashSet<(i64, i64)> =
        new.iter().map(period_key).collect();

    let mut result: Vec<std::collections::HashMap<String, serde_json::Value>> = new.to_vec();

    for old_entry in old {
        if !new_keys.contains(&period_key(old_entry)) {
            result.push(old_entry.clone());
        }
    }

    result.sort_by(|a, b| {
        let ka = period_key(a);
        let kb = period_key(b);
        kb.1.cmp(&ka.1).then(kb.0.cmp(&ka.0))
    });

    result
}

/// Check if company_info has meaningful data (not an empty shell).
/// A valid entry must have at least exchange OR industry, plus some identifying data.
fn is_valid_company_info(info: &crate::providers::vci::CompanyInfo) -> bool {
    let has_exchange = info.exchange.is_some();
    let has_industry = info.industry.is_some();
    let has_shares = info.outstanding_shares.is_some();
    let has_shareholders = !info.shareholders.is_empty();
    let has_price = info.current_price.is_some();
    let has_profile = info.company_profile.is_some();
    let has_any = has_exchange || has_industry || has_shareholders || has_profile;
    if !has_any {
        return false;
    }
    if has_price && has_shares {
        let shares = info.outstanding_shares.unwrap();
        let price = info.current_price.unwrap();
        if shares > 0 && price > 0.0 && info.market_cap.unwrap_or(0.0) <= 0.0 {
            return false;
        }
    }
    true
}

/// Check if financial ratios have at least one entry with key fields.
fn is_valid_financial_ratios(
    ratios: &[std::collections::HashMap<String, serde_json::Value>],
) -> bool {
    ratios.iter().any(|r| {
        r.contains_key("yearReport")
            && r.contains_key("ticker")
            && (r.contains_key("revenue") || r.contains_key("netProfit") || r.contains_key("pe"))
    })
}

/// Fetch fundamental data for VN tickers and upload to S3.
/// Each ticker is fetched at most once per day (in-memory tracking).
/// Failed tickers are not marked as done, so they retry on the next cycle.
async fn fundamental_cycle(
    pool: &PgPool,
    bucket: &Bucket,
    provider: &VciProvider,
    state: &mut FundamentalState,
    index_set: &HashSet<String>,
) {
    let today = Utc::now().date_naive();

    let all_tickers = match sqlx::query_as::<_, Ticker>(
        "SELECT id, source, ticker, name, status, next_1d FROM tickers WHERE source = 'vn' ORDER BY ticker",
    )
    .fetch_all(pool)
    .await
    {
        Ok(t) => t,
        Err(e) => {
            tracing::warn!("[FUNDAMENTAL] failed to load VN tickers: {e}");
            return;
        }
    };

    if std::env::var("FUNDAMENTAL_SKIP_S3_HYDRATE").as_deref() == Ok("true") {
        tracing::info!("[FUNDAMENTAL] per-ticker hydration disabled (FUNDAMENTAL_SKIP_S3_HYDRATE=true)");
    }

    let due: Vec<&Ticker> = all_tickers
        .iter()
        .filter(|t| !index_set.contains(t.ticker.as_str()))
        .filter(|t| state.is_due(&t.ticker, today))
        .collect();

    if due.is_empty() {
        tracing::debug!("[FUNDAMENTAL] no due tickers");
        return;
    }

    tracing::info!("[FUNDAMENTAL] fetching {} tickers", due.len());

    let mut ok: u32 = 0;
    let mut err: u32 = 0;
    let mut consecutive_rate_limits: u32 = 0;
    let mut vci_dead = false;
    let mut vci_healthy_requests: u32 = 0;
    let mut fallback = CompanyInfoFallback::new();

    for (i, ticker) in due.iter().enumerate() {
        if consecutive_rate_limits >= FUNDAMENTAL_MAX_CONSECUTIVE_RATE_LIMIT {
            tracing::warn!(
                "[FUNDAMENTAL] aborting after {consecutive_rate_limits} consecutive rate limits — {}/{} remaining tickers skipped",
                due.len() - i,
                due.len(),
            );
            break;
        }

        let ticker_start = std::time::Instant::now();
        tracing::info!("[FUNDAMENTAL] [{}/{}] {} — start", i + 1, due.len(), ticker.ticker);

        // Inline hydration: check _meta.json for this ticker (acts as cooldown)
        if std::env::var("FUNDAMENTAL_SKIP_S3_HYDRATE").as_deref() != Ok("true") {
            let meta_key = s3_key_meta("vn", &ticker.ticker);
            if let Some(meta) = fetch_existing_json::<FundamentalMeta>(bucket, &meta_key).await {
                if let Ok(date) = NaiveDate::parse_from_str(&meta.last_fetch, "%Y-%m-%d") {
                    if date >= today {
                        tracing::info!(
                            "[FUNDAMENTAL] [{}/{}] {} — already fetched {} (via _meta.json), skipping",
                            i + 1, due.len(), ticker.ticker, date
                        );
                        state.mark_done(&ticker.ticker, date);
                        ok += 1;
                        continue;
                    }
                }
            }
        }

        let mut company_ok = false;
        let mut ratios_ok = false;
        let mut ticker_rate_limited = false;
        let mut ticker_vci_ok = false;

        if vci_dead {
            let key_ci = s3_key_company_info("vn", &ticker.ticker);
            let has_s3 = fetch_existing_json::<crate::providers::vci::CompanyInfo>(bucket, &key_ci).await;
            if has_s3.is_none() {
                if let Some(info) = fallback.company_info(&ticker.ticker) {
                    if is_valid_company_info(&info) {
                        let _ = upload_json(bucket, &key_ci, &info).await;
                        company_ok = true;
                    }
                }
            } else {
                company_ok = true;
            }

            let key_fr = s3_key_financial_ratios("vn", &ticker.ticker);
            let has_s3 = fetch_existing_json::<std::collections::HashMap<String, serde_json::Value>>(bucket, &key_fr).await;
            if has_s3.is_none() {
                if let Some(ratios) = fallback.financial_ratios(&ticker.ticker) {
                    if !ratios.is_empty() && is_valid_financial_ratios(&ratios) {
                        let wrapper = serde_json::json!({
                            "ticker": ticker.ticker,
                            "updated_at": Utc::now().to_rfc3339(),
                            "count": ratios.len(),
                            "ratios": ratios,
                        });
                        let _ = upload_json(bucket, &key_fr, &wrapper).await;
                        ratios_ok = true;
                    }
                }
            } else {
                ratios_ok = true;
            }
        } else {
        tracing::debug!(
            "[FUNDAMENTAL] [{}/{}] {}",
            i + 1,
            due.len(),
            ticker.ticker
        );

        match provider.company_info(&ticker.ticker).await {
            Ok(info) => {
                ticker_vci_ok = true;
                let key_ci = s3_key_company_info("vn", &ticker.ticker);
                let existing: Option<crate::providers::vci::CompanyInfo> =
                    fetch_existing_json(bucket, &key_ci).await;
                let merged = merge_company_info(info, existing);
                if is_valid_company_info(&merged) {
                    match upload_json(bucket, &key_ci, &merged).await {
                        Ok(true) => tracing::info!("[FUNDAMENTAL] uploaded {key_ci}"),
                        Ok(false) => tracing::info!("[FUNDAMENTAL] skipped {key_ci} (unchanged)"),
                        Err(e) => tracing::warn!("[FUNDAMENTAL] upload failed for {key_ci}: {e}"),
                    }
                    company_ok = true;
                } else {
                    tracing::warn!(
                        "[FUNDAMENTAL] {} company_info still empty after merge (exchange={:?}, industry={:?}, shareholders={}), skipping",
                        ticker.ticker,
                        merged.exchange,
                        merged.industry,
                        merged.shareholders.len(),
                    );
                }
            }
            Err(e) => {
                let use_fallback = matches!(
                    e,
                    crate::providers::vci::VciError::RateLimit
                        | crate::providers::vci::VciError::NoData
                );
                if matches!(e, crate::providers::vci::VciError::RateLimit) {
                    ticker_rate_limited = true;
                }
                tracing::warn!("[FUNDAMENTAL] company_info {} failed: {e}", ticker.ticker);

                if use_fallback {
                    let key_ci = s3_key_company_info("vn", &ticker.ticker);
                    let has_s3 =
                        fetch_existing_json::<crate::providers::vci::CompanyInfo>(bucket, &key_ci)
                            .await;
                    if has_s3.is_none() {
                        if let Some(info) = fallback.company_info(&ticker.ticker) {
                            tracing::info!(
                                "[FUNDAMENTAL] {} using local fallback for company_info",
                                ticker.ticker
                            );
                            if is_valid_company_info(&info) {
                                let _ = upload_json(bucket, &key_ci, &info).await;
                                company_ok = true;
                            }
                        }
                    }
                }
            }
        }

        tokio::time::sleep(StdDuration::from_millis(FUNDAMENTAL_DELAY_MS)).await;

        match provider.financial_ratios(&ticker.ticker, "quarter").await {
            Ok(ratios) => {
                ticker_vci_ok = true;
                let key_fr = s3_key_financial_ratios("vn", &ticker.ticker);
                let existing: Option<std::collections::HashMap<String, serde_json::Value>> =
                    fetch_existing_json(bucket, &key_fr).await;
                let old_ratios = existing.map(|m: std::collections::HashMap<String, serde_json::Value>| {
                    m.get("ratios")
                        .and_then(|v| v.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.as_object().map(|obj| {
                                    obj.iter()
                                        .filter_map(|(k, v)| if k == "ratios" { None } else { Some((k.clone(), v.clone())) })
                                        .collect::<std::collections::HashMap<String, serde_json::Value>>()
                                }))
                                .collect::<Vec<_>>()
                        })
                        .unwrap_or_default()
                }).unwrap_or_default();
                let merged = merge_financial_ratios(&ratios, &old_ratios);
                if !merged.is_empty() && is_valid_financial_ratios(&merged) {
                    let wrapper = serde_json::json!({
                        "ticker": ticker.ticker,
                        "updated_at": Utc::now().to_rfc3339(),
                        "count": merged.len(),
                        "ratios": merged,
                    });
                    match upload_json(bucket, &key_fr, &wrapper).await {
                        Ok(true) => tracing::info!("[FUNDAMENTAL] uploaded {key_fr}"),
                        Ok(false) => tracing::info!("[FUNDAMENTAL] skipped {key_fr} (unchanged)"),
                        Err(e) => tracing::warn!("[FUNDAMENTAL] upload failed for {key_fr}: {e}"),
                    }
                    ratios_ok = true;
                } else {
                    tracing::warn!(
                        "[FUNDAMENTAL] {} financial_ratios empty after merge, skipping",
                        ticker.ticker,
                    );
                }
            }
            Err(e) => {
                let use_fallback = matches!(
                    e,
                    crate::providers::vci::VciError::RateLimit
                        | crate::providers::vci::VciError::NoData
                );
                if matches!(e, crate::providers::vci::VciError::RateLimit) {
                    ticker_rate_limited = true;
                }
                tracing::warn!("[FUNDAMENTAL] financial_ratios {} failed: {e}", ticker.ticker);

                if use_fallback {
                    let key_fr = s3_key_financial_ratios("vn", &ticker.ticker);
                    let has_s3 = fetch_existing_json::<std::collections::HashMap<String, serde_json::Value>>(bucket, &key_fr).await;
                    if has_s3.is_none() {
                        if let Some(ratios) = fallback.financial_ratios(&ticker.ticker) {
                            if !ratios.is_empty() && is_valid_financial_ratios(&ratios) {
                                tracing::info!("[FUNDAMENTAL] {} using local fallback for financial_ratios", ticker.ticker);
                                let wrapper = serde_json::json!({
                                    "ticker": ticker.ticker,
                                    "updated_at": Utc::now().to_rfc3339(),
                                    "count": ratios.len(),
                                    "ratios": ratios,
                                });
                                let _ = upload_json(bucket, &key_fr, &wrapper).await;
                                ratios_ok = true;
                            }
                        }
                    }
                }
            }
        }
        } // end else (vci not dead)

        if !vci_dead
            && !company_ok
            && !ratios_ok
            && !ticker_vci_ok
            && vci_healthy_requests == 0
            && i + 1 >= FUNDAMENTAL_VCI_DEAD_THRESHOLD as usize
        {
            tracing::warn!(
                "[FUNDAMENTAL] VCI appears dead (0 healthy requests after {} tickers), switching to fallback-only for remaining {} tickers",
                i + 1,
                due.len() - i - 1,
            );
            vci_dead = true;
        }

        if ticker_rate_limited {
            consecutive_rate_limits += 1;
        } else {
            consecutive_rate_limits = 0;
        }

        if ticker_vci_ok {
            vci_healthy_requests += 1;
        }

        if company_ok && ratios_ok {
            state.mark_done(&ticker.ticker, today);
            let meta = FundamentalMeta {
                ticker: ticker.ticker.clone(),
                last_fetch: today.to_string(),
                company_info_uploaded: true,
                financial_ratios_uploaded: true,
            };
            let meta_key = s3_key_meta("vn", &ticker.ticker);
            if let Err(e) = upload_json(bucket, &meta_key, &meta).await {
                tracing::warn!("[FUNDAMENTAL] failed to save _meta.json for {}: {e}", ticker.ticker);
            }
            ok += 1;
            tracing::info!(
                "[FUNDAMENTAL] [{}/{}] {} — done (ci={}, fr={}, {:.1}s)",
                i + 1, due.len(), ticker.ticker,
                if company_ok { "ok" } else { "skip" },
                if ratios_ok { "ok" } else { "skip" },
                ticker_start.elapsed().as_secs_f64(),
            );
        } else {
            err += 1;
            tracing::info!(
                "[FUNDAMENTAL] [{}/{}] {} — failed (ci={}, fr={}, vci={}, {:.1}s)",
                i + 1, due.len(), ticker.ticker,
                if company_ok { "ok" } else { "no" },
                if ratios_ok { "ok" } else { "no" },
                if ticker_vci_ok { "ok" } else { "no" },
                ticker_start.elapsed().as_secs_f64(),
            );
        }

        if i + 1 < due.len() && !vci_dead {
            tokio::time::sleep(StdDuration::from_millis(FUNDAMENTAL_DELAY_MS)).await;
        }
    }

    tracing::info!("[FUNDAMENTAL] cycle done — {ok} ok, {err} failed");

    let _ = upload_fundamental_index(bucket, &all_tickers, state, &today, index_set).await;
}

/// Upload a manifest of all VN tickers and their fundamental data status.
async fn upload_fundamental_index(
    bucket: &Bucket,
    all_tickers: &[Ticker],
    state: &FundamentalState,
    today: &NaiveDate,
    index_set: &HashSet<String>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let entries: Vec<serde_json::Value> = all_tickers
        .iter()
        .filter(|t| !index_set.contains(t.ticker.as_str()))
        .map(|t| {
            let fetched = state.last_fetch.contains_key(&t.ticker);
            serde_json::json!({
                "ticker": t.ticker,
                "name": t.name,
                "fetched_today": fetched,
                "date": if fetched { state.last_fetch.get(&t.ticker).map(|d| d.to_string()).unwrap_or_default() } else { String::new() },
            })
        })
        .collect();

    let index = serde_json::json!({
        "updated_at": Utc::now().to_rfc3339(),
        "date": today.to_string(),
        "count": entries.len(),
        "fetched_today": entries.iter().filter(|e| e["fetched_today"].as_bool().unwrap_or(false)).count(),
        "tickers": entries,
    });

    let key = "fundamental/vn/_index.json";
    upload_json(bucket, key, &index).await?;

    tracing::info!("[FUNDAMENTAL] uploaded {key}");
    Ok(())
}

/// Upload JSON to S3 with hash-based dedup via x-amz-meta-content-hash.
/// Returns Ok(true) if uploaded, Ok(false) if skipped (unchanged).
async fn upload_json(
    bucket: &Bucket,
    key: &str,
    data: &impl serde::Serialize,
) -> Result<bool, Box<dyn std::error::Error + Send + Sync>> {
    let json_bytes = serde_json::to_vec(data)?;
    let hash = {
        use sha2::{Digest, Sha256};
        let h = Sha256::digest(&json_bytes);
        hex::encode(h)
    };

    if let Ok((head, _status)) = bucket.head_object(key).await {
        if let Some(existing) = head.metadata.as_ref().and_then(|m| m.get("content-hash")) {
            if existing == &hash {
                return Ok(false);
            }
        }
    }

    let mut headers = HeaderMap::new();
    headers.insert("x-amz-meta-content-hash", hash.parse().unwrap());

    bucket
        .put_object_with_content_type_and_headers(key, &json_bytes, JSON_CONTENT_TYPE, Some(headers))
        .await?;

    Ok(true)
}

// ── Worker entry point ──

pub async fn run(pool: PgPool, _redis: Option<crate::redis::RedisClient>) {
    let bucket = match create_s3_bucket() {
        Ok(b) => b,
        Err(e) => {
            tracing::error!("s3_archive: failed to create S3 client: {e}");
            return;
        }
    };

    let enrichment = EnrichmentData::load();
    let interval_secs = std::env::var("S3_ARCHIVE_INTERVAL_SECS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(LOOP_SECS);

    let vci_provider = match VciProvider::new(FUNDAMENTAL_RATE_LIMIT) {
        Ok(p) => {
            tracing::info!(
                "s3_archive: VCI provider ready ({} clients, {FUNDAMENTAL_RATE_LIMIT}/min) for fundamental data",
                p.client_count(),
            );
            Some(Arc::new(p))
        }
        Err(e) => {
            tracing::warn!("s3_archive: VCI provider unavailable, fundamental data disabled: {e}");
            None
        }
    };

    let index_set: HashSet<String> = INDEX_TICKERS.iter().map(|s| s.to_string()).collect();
    let mut fundamental_state = FundamentalState::default();

    tracing::info!(
        "s3_archive: worker started (interval={}s, lookback_days={}, bucket={}, fundamental={})",
        interval_secs,
        LOOKBACK_DAYS,
        std::env::var("S3_BUCKET").unwrap_or_default(),
        vci_provider.is_some(),
    );

    // Ensure bucket exists (auto-create if not)
    if let Err(e) = ensure_bucket(&bucket).await {
        tracing::error!("s3_archive: failed to create/access bucket: {e}");
        return;
    }

    // ── Startup scan: full historical check ──
    let mut last_startup_scan = std::time::Instant::now();
    tracing::info!("s3_archive: starting full historical scan...");
    if let Err(e) = startup_scan(&pool, &bucket, &enrichment).await {
        tracing::error!("s3_archive: startup scan failed: {e}");
    }

    // ── Incremental loop ──
    loop {
        // Re-run startup scan periodically to catch new tickers + full history
        if last_startup_scan.elapsed() >= StdDuration::from_secs(STARTUP_SCAN_INTERVAL_SECS) {
            tracing::info!("s3_archive: re-running full historical scan (catch new tickers)...");
            if let Err(e) = startup_scan(&pool, &bucket, &enrichment).await {
                tracing::error!("s3_archive: startup scan failed: {e}");
            }
            last_startup_scan = std::time::Instant::now();
        }

        tracing::info!("s3_archive: incremental cycle starting...");
        if let Err(e) = incremental_cycle(&pool, &bucket, &enrichment).await {
            tracing::error!("s3_archive: incremental cycle failed: {e}");
        }

        // ── Fundamental data cycle (once per day per ticker, in-memory tracking) ──
        if let Some(ref provider) = vci_provider {
            fundamental_cycle(&pool, &bucket, provider, &mut fundamental_state, &index_set).await;
        }

        tracing::info!(
            "s3_archive: cycle complete, sleeping {}s",
            interval_secs
        );
        tokio::time::sleep(StdDuration::from_secs(interval_secs)).await;
    }
}

async fn upload_tickers_json(
    pool: &PgPool,
    bucket: &Bucket,
    enrichment: &EnrichmentData,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let mut tickers = crate::queries::s3_archive::get_all_tickers_base(pool).await?;
    for t in &mut tickers {
        enrichment.enrich(t);
    }

    let json_bytes = serde_json::to_vec_pretty(&tickers)?;
    let hash = {
        use sha2::{Digest, Sha256};
        let h = Sha256::digest(&json_bytes);
        hex::encode(h)
    };

    let key = "meta/tickers.json";

    // HEAD check — skip if hash matches
    if let Ok((head, _status)) = bucket.head_object(key).await {
        if let Some(existing) = head.metadata.as_ref().and_then(|m| m.get("content-hash")) {
            if existing == &hash {
                tracing::debug!("s3_archive: tickers.json unchanged, skipping");
                return Ok(());
            }
        }
    }

    let mut headers = HeaderMap::new();
    headers.insert("x-amz-meta-content-hash", hash.parse().unwrap());

    bucket
        .put_object_with_content_type_and_headers(key, &json_bytes, JSON_CONTENT_TYPE, Some(headers))
        .await?;

    tracing::info!("s3_archive: uploaded meta/tickers.json ({} tickers)", tickers.len());
    Ok(())
}

async fn startup_scan(
    pool: &PgPool,
    bucket: &Bucket,
    enrichment: &EnrichmentData,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Upload tickers.json first
    upload_tickers_json(pool, bucket, enrichment).await?;

    // Get all tickers for source mapping
    let all_tickers = sqlx::query_as::<_, Ticker>(
        "SELECT id, source, ticker, name, status, next_1d FROM tickers ORDER BY id",
    )
    .fetch_all(pool)
    .await?;

    let ticker_map: HashMap<i32, (String, String)> = all_tickers
        .iter()
        .map(|t| (t.id, (t.source.clone(), t.ticker.clone())))
        .collect();

    // Get data ranges, grouped by ticker
    let ranges = get_data_ranges(pool).await?;
    let total_ranges = ranges.len();
    tracing::info!(
        "s3_archive: scanning {total_ranges} ticker+interval combinations (concurrency={UPLOAD_CONCURRENCY})..."
    );

    let today = Utc::now().date_naive();
    let intervals = ["1D", "1h", "1m"];
    let sem = std::sync::Arc::new(tokio::sync::Semaphore::new(UPLOAD_CONCURRENCY));
    let mut uploaded: u64 = 0;
    let mut skipped: u64 = 0;
    let mut processed: u64 = 0;
    let mut last_log: u64 = 0;

    let mut in_flight: futures::stream::FuturesUnordered<_> =
        futures::stream::FuturesUnordered::new();

    // ── Phase 1: Collect and process yearly aggregate files first ──
    // Yearly files are fast (~5s for 6K files) and the Python SDK prefers them
    // for 1D and 1h intervals, so process them before the slow per-day scan.
    let mut yearly_tasks: Vec<(i32, String, String, String, i32)> = Vec::new(); // (ticker_id, source, ticker, interval, year)
    let mut yearly_ticker_seen: std::collections::HashSet<(i32, String)> = std::collections::HashSet::new();

    for range in &ranges {
        if range.interval != "1D" && range.interval != "1h" {
            continue;
        }
        if yearly_ticker_seen.contains(&(range.ticker_id, range.interval.clone())) {
            continue;
        }
        let (source, ticker_sym) = match ticker_map.get(&range.ticker_id) {
            Some(t) => t,
            None => continue,
        };
        yearly_ticker_seen.insert((range.ticker_id, range.interval.clone()));
        let earliest_year = range.earliest.year();
        let latest_year = range.latest.year();
        for year in earliest_year..=latest_year {
            yearly_tasks.push((range.ticker_id, source.clone(), ticker_sym.clone(), range.interval.clone(), year));
        }
    }

    let yearly_count = yearly_tasks.len();
    let mut yearly_uploaded: u64 = 0;
    let mut yearly_skipped: u64 = 0;
    if yearly_count > 0 {
        tracing::info!(
            "s3_archive: processing {yearly_count} yearly files (1D + 1h) for {} ticker+interval combos...",
            yearly_ticker_seen.len()
        );
    }
    for (ticker_id, source, ticker_sym, interval, year) in yearly_tasks {
        let key = s3_key_yearly(&source, &ticker_sym, &interval, year);
        let (year_start, year_end) = year_range(year);
        let pool = pool.clone();
        let bucket = bucket.clone();
        let permit = sem.clone();

        in_flight.push(tokio::spawn(async move {
            let _permit = permit.acquire().await.unwrap();
            let inner_uploaded = match process_yearly(&pool, &bucket, ticker_id, &interval, &key, year_start, year_end).await {
                Ok(true) => 1u64,
                Ok(false) => 0,
                Err(e) => { tracing::warn!("s3_archive: error processing yearly {key}: {e}"); 0 }
            };
            let inner_skipped = if inner_uploaded == 0 { 1u64 } else { 0u64 };
            ScanResult::YearlyScan { uploaded: inner_uploaded, skipped: inner_skipped }
        }));

        while in_flight.len() >= UPLOAD_CONCURRENCY * 2 {
            if let Some(result) = in_flight.next().await {
                match result {
                    Ok(ScanResult::YearlyScan { uploaded: u, skipped: s }) => {
                        yearly_uploaded += u;
                        yearly_skipped += s;
                    }
                    Err(e) => {
                        tracing::warn!("s3_archive: task panicked: {e}");
                    }
                    _ => {}
                }
            }
        }
    }

    // Drain remaining yearly tasks
    while let Some(result) = in_flight.next().await {
        match result {
            Ok(ScanResult::YearlyScan { uploaded: u, skipped: s }) => {
                yearly_uploaded += u;
                yearly_skipped += s;
            }
            Err(e) => {
                tracing::warn!("s3_archive: task panicked: {e}");
            }
            _ => {}
        }
    }

    if yearly_count > 0 {
        tracing::info!(
            "s3_archive: yearly files complete — {yearly_count} yearly files, {yearly_uploaded} uploaded, {yearly_skipped} skipped"
        );
    }

    // ── Phase 2: Per-day scan (newest→oldest with consecutive skip early-stop) ──
    let mut last_ticker_id: i32 = -1;
    let mut ticker_count = 0;

    for range in &ranges {
        let (source, ticker_sym) = match ticker_map.get(&range.ticker_id) {
            Some(t) => t,
            None => continue,
        };

        if range.ticker_id != last_ticker_id {
            if last_ticker_id != -1 && ticker_count > 0 {
                tracing::info!(
                    "s3_archive: [{ticker_count}/{total_ranges}] {source}/{ticker_sym} — scanning"
                );
            }
            last_ticker_id = range.ticker_id;
            ticker_count += 1;
        }

        for interval in &intervals {
            if range.interval != *interval {
                continue;
            }

            let earliest_date = range.earliest.date_naive();
            let latest_date = range.latest.date_naive().min(today);

            // Spawn one task per ticker+interval that iterates newest→oldest.
            // Stops after STARTUP_CONSECUTIVE_SKIP_LIMIT consecutive skips —
            // historical data never changes once uploaded (except 1D dividends,
            // which hit early days first and reset the counter if adjusted).
            let source = source.to_string();
            let ticker_sym = ticker_sym.to_string();
            let interval = interval.to_string();
            let pool = pool.clone();
            let bucket = bucket.clone();
            let permit = sem.clone();
            let ticker_id = range.ticker_id;
            let skip_limit = STARTUP_CONSECUTIVE_SKIP_LIMIT;

            in_flight.push(tokio::spawn(async move {
                let _permit = permit.acquire().await.unwrap();
                let mut current = latest_date;
                let mut inner_uploaded: u64 = 0;
                let mut inner_skipped: u64 = 0;
                let mut consecutive_skips: u32 = 0;
                let mut _stopped_early = false;

                while current >= earliest_date {
                    let (day_start, day_end) = day_range(current);
                    let key = s3_key(&source, &ticker_sym, &interval, current);

                    match process_day(&pool, &bucket, ticker_id, &interval, &key, day_start, day_end).await {
                        Ok(true) => {
                            inner_uploaded += 1;
                            consecutive_skips = 0;
                        }
                        Ok(false) => {
                            inner_skipped += 1;
                            consecutive_skips += 1;
                        }
                        Err(e) => {
                            consecutive_skips = 0;
                            tracing::warn!("s3_archive: error processing {key}: {e}");
                        }
                    }

                    if consecutive_skips >= skip_limit {
                        _stopped_early = true;
                        tracing::info!(
                            "s3_archive: {source}/{ticker_sym}/{interval} — stopped after {} days ({} uploaded, {} skipped, {} consecutive skips)",
                            inner_uploaded + inner_skipped, inner_uploaded, inner_skipped, consecutive_skips
                        );
                        break;
                    }

                    current -= Duration::days(1);
                }

                ScanResult::DayScan { uploaded: inner_uploaded, skipped: inner_skipped }
            }));

            // Drain completed tasks to avoid unbounded memory growth
            while in_flight.len() >= UPLOAD_CONCURRENCY * 2 {
                if let Some(result) = in_flight.next().await {
                    match result {
                        Ok(ScanResult::DayScan { uploaded: u, skipped: s }) => {
                            uploaded += u;
                            skipped += s;
                            processed += u + s;
                        }
                        Err(e) => {
                            tracing::warn!("s3_archive: task panicked: {e}");
                        }
                        _ => {}
                    }
                    if processed - last_log >= 2000 {
                        tracing::info!(
                            "s3_archive: startup progress — {processed} processed, {uploaded} uploaded, {skipped} skipped"
                        );
                        last_log = processed;
                    }
                }
            }
        }
    }

    // Drain remaining per-day tasks
    while let Some(result) = in_flight.next().await {
        match result {
            Ok(ScanResult::DayScan { uploaded: u, skipped: s }) => {
                uploaded += u;
                skipped += s;
                processed += u + s;
            }
            Err(e) => {
                tracing::warn!("s3_archive: task panicked: {e}");
            }
            _ => {}
        }
        if processed - last_log >= 2000 {
            tracing::info!(
                "s3_archive: progress — {processed} processed, {uploaded} uploaded, {skipped} skipped"
            );
            last_log = processed;
        }
    }

    tracing::info!(
        "s3_archive: startup scan complete — {ticker_count} tickers, uploaded: {uploaded}, skipped: {skipped}"
    );

    Ok(())
}

async fn incremental_cycle(
    pool: &PgPool,
    bucket: &Bucket,
    enrichment: &EnrichmentData,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Upload tickers.json
    upload_tickers_json(pool, bucket, enrichment).await?;

    // Get all tickers
    let all_tickers = sqlx::query_as::<_, Ticker>(
        "SELECT id, source, ticker, name, status, next_1d FROM tickers ORDER BY id",
    )
    .fetch_all(pool)
    .await?;

    let today = Utc::now().date_naive();
    let intervals = ["1D", "1h", "1m"];
    let total_tickers = all_tickers.len();
    let sem = std::sync::Arc::new(tokio::sync::Semaphore::new(UPLOAD_CONCURRENCY));
    let mut uploaded: u64 = 0;
    let mut skipped: u64 = 0;
    let mut processed: u64 = 0;
    let mut last_log: u64 = 0;

    tracing::info!(
        "s3_archive: incremental cycle — checking {total_tickers} tickers × {intervals:?} × {LOOKBACK_DAYS} days (concurrency={UPLOAD_CONCURRENCY})"
    );

    let mut in_flight: futures::stream::FuturesUnordered<_> =
        futures::stream::FuturesUnordered::new();

    for ticker in &all_tickers {
        for interval in &intervals {
            for day_offset in 0..LOOKBACK_DAYS {
                let date = today - Duration::days(day_offset as i64);
                let (day_start, day_end) = day_range(date);
                let key = s3_key(&ticker.source, &ticker.ticker, interval, date);
                let interval = interval.to_string();
                let pool = pool.clone();
                let bucket = bucket.clone();
                let permit = sem.clone();
                let ticker_id = ticker.id;

                in_flight.push(tokio::spawn(async move {
                    let _permit = permit.acquire().await.unwrap();
                    match process_day(&pool, &bucket, ticker_id, &interval, &key, day_start, day_end).await {
                        Ok(true) => ScanResult::DayScan { uploaded: 1, skipped: 0 },
                        Ok(false) => ScanResult::DayScan { uploaded: 0, skipped: 1 },
                        Err(e) => {
                            tracing::warn!("s3_archive: error processing {key}: {e}");
                            ScanResult::DayScan { uploaded: 0, skipped: 0 }
                        }
                    }
                }));

                // Drain completed tasks
                while in_flight.len() >= UPLOAD_CONCURRENCY * 2 {
                    if let Some(result) = in_flight.next().await {
                        processed += 1;
                        match result {
                            Ok(ScanResult::DayScan { uploaded: u, skipped: s }) => {
                                uploaded += u;
                                skipped += s;
                            }
                            Err(e) => {
                                tracing::warn!("s3_archive: task panicked: {e}");
                            }
                            _ => {}
                        }
                        if processed - last_log >= 2000 {
                            tracing::info!(
                                "s3_archive: incremental progress — {processed} processed, {uploaded} uploaded, {skipped} skipped"
                            );
                            last_log = processed;
                        }
                    }
                }
            }
        }
    }

    // Drain remaining tasks
    while let Some(result) = in_flight.next().await {
        processed += 1;
        match result {
            Ok(ScanResult::DayScan { uploaded: u, skipped: s }) => {
                uploaded += u;
                skipped += s;
            }
            Err(e) => {
                tracing::warn!("s3_archive: task panicked: {e}");
            }
            _ => {}
        }
        if processed - last_log >= 2000 {
            tracing::info!(
                "s3_archive: progress — {processed} processed, {uploaded} uploaded, {skipped} skipped"
            );
            last_log = processed;
        }
    }

    tracing::info!(
        "s3_archive: incremental cycle — {total_tickers} tickers, uploaded: {uploaded}, skipped: {skipped}"
    );

    // Process yearly aggregate files for current year (1D and 1h)
    let current_year = today.year();
    for ticker in &all_tickers {
        for interval in &["1D", "1h"] {
            let year = current_year;
            let key = s3_key_yearly(&ticker.source, &ticker.ticker, interval, year);
            let (year_start, year_end) = year_range(year);
            let pool = pool.clone();
            let bucket = bucket.clone();
            let permit = sem.clone();
            let ticker_id = ticker.id;
            let interval = interval.to_string();

            in_flight.push(tokio::spawn(async move {
                let _permit = permit.acquire().await.unwrap();
                let inner_uploaded = match process_yearly(&pool, &bucket, ticker_id, &interval, &key, year_start, year_end).await {
                    Ok(true) => 1u64,
                    Ok(false) => 0,
                    Err(e) => { tracing::warn!("s3_archive: error processing yearly {key}: {e}"); 0 }
                };
                let inner_skipped = if inner_uploaded == 0 { 1u64 } else { 0u64 };
                ScanResult::YearlyScan { uploaded: inner_uploaded, skipped: inner_skipped }
            }));

            while in_flight.len() >= UPLOAD_CONCURRENCY * 2 {
                if let Some(result) = in_flight.next().await {
                    match result {
                        Ok(ScanResult::YearlyScan { uploaded: u, skipped: s }) => {
                            uploaded += u;
                            skipped += s;
                        }
                        Err(e) => {
                            tracing::warn!("s3_archive: task panicked: {e}");
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    // Drain remaining yearly tasks
    while let Some(result) = in_flight.next().await {
        match result {
            Ok(ScanResult::YearlyScan { uploaded: u, skipped: s }) => {
                uploaded += u;
                skipped += s;
            }
            Err(e) => {
                tracing::warn!("s3_archive: task panicked: {e}");
            }
            _ => {}
        }
    }

    Ok(())
}

/// Process a single day: check fingerprint, upload if changed.
/// Returns Ok(true) if uploaded, Ok(false) if skipped.
async fn process_day(
    pool: &PgPool,
    bucket: &Bucket,
    ticker_id: i32,
    interval: &str,
    key: &str,
    day_start: chrono::DateTime<Utc>,
    day_end: chrono::DateTime<Utc>,
) -> Result<bool, Box<dyn std::error::Error + Send + Sync>> {
    // Get fingerprint
    let fp = match get_ohlcv_day_fingerprint(pool, ticker_id, interval, day_start, day_end).await? {
        Some(fp) => fp,
        None => return Ok(false), // No data for this day
    };

    let hash = fp.to_hash();

    // HEAD check — skip if hash matches
    match bucket.head_object(key).await {
        Ok((head, _status)) => {
            if let Some(existing) = head.metadata.as_ref().and_then(|m| m.get("content-hash")) {
                if existing == &hash {
                    return Ok(false);
                }
            }
        }
        Err(_) => {
            // Object doesn't exist yet or HEAD failed, proceed to upload
        }
    }

    // Fetch rows and build CSV
    let rows = get_ohlcv_for_day(pool, ticker_id, interval, day_start, day_end).await?;
    if rows.is_empty() {
        return Ok(false);
    }

    let csv_bytes = rows_to_csv(&rows);

    // Upload with metadata
    let mut headers = HeaderMap::new();
    headers.insert("x-amz-meta-content-hash", hash.parse().unwrap());

    bucket
        .put_object_with_content_type_and_headers(key, &csv_bytes, CSV_CONTENT_TYPE, Some(headers))
        .await?;

    tracing::debug!("s3_archive: uploaded {key} ({} rows)", rows.len());
    Ok(true)
}

/// Process a single yearly aggregate file: check fingerprint, upload if changed.
/// Returns Ok(true) if uploaded, Ok(false) if skipped.
async fn process_yearly(
    pool: &PgPool,
    bucket: &Bucket,
    ticker_id: i32,
    interval: &str,
    key: &str,
    year_start: chrono::DateTime<Utc>,
    year_end: chrono::DateTime<Utc>,
) -> Result<bool, Box<dyn std::error::Error + Send + Sync>> {
    // Get fingerprint
    let fp = match get_ohlcv_year_fingerprint(pool, ticker_id, interval, year_start, year_end).await? {
        Some(fp) => fp,
        None => return Ok(false), // No data for this year
    };

    let hash = fp.to_hash();

    // HEAD check — skip if hash matches
    match bucket.head_object(key).await {
        Ok((head, _status)) => {
            if let Some(existing) = head.metadata.as_ref().and_then(|m| m.get("content-hash")) {
                if existing == &hash {
                    return Ok(false);
                }
            }
        }
        Err(_) => {
            // Object doesn't exist yet or HEAD failed, proceed to upload
        }
    }

    // Fetch rows and build CSV
    let rows = get_ohlcv_for_year(pool, ticker_id, interval, year_start, year_end).await?;
    if rows.is_empty() {
        return Ok(false);
    }

    let csv_bytes = rows_to_csv(&rows);

    // Upload with metadata
    let mut headers = HeaderMap::new();
    headers.insert("x-amz-meta-content-hash", hash.parse().unwrap());

    bucket
        .put_object_with_content_type_and_headers(key, &csv_bytes, CSV_CONTENT_TYPE, Some(headers))
        .await?;

    tracing::debug!("s3_archive: uploaded {key} ({} rows)", rows.len());
    Ok(true)
}

// ── Test CLI command ──

/// CLI test command: verify S3 connectivity, optionally create bucket,
/// query OHLCV data from DB, build CSV, and upload to S3.
pub async fn test_s3(ticker: String, interval: String, days: u32, create_bucket: bool) {
    tracing::info!("{}", "═".repeat(60));
    tracing::info!("S3 Archive Test");
    tracing::info!("{}", "═".repeat(60));

    // 1. Create S3 client
    let bucket = match create_s3_bucket() {
        Ok(b) => b,
        Err(e) => {
            tracing::error!("Failed to create S3 client: {e}");
            tracing::error!("Required env vars: S3_BUCKET, S3_REGION");
            tracing::error!("Optional env vars: S3_ENDPOINT, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY");
            return;
        }
    };
    tracing::info!("S3 bucket: {}", bucket.name());

    // 2. Create bucket if requested
    if create_bucket {
        match ensure_bucket(&bucket).await {
            Ok(()) => tracing::info!("Bucket ready"),
            Err(e) => {
                tracing::error!("Failed to create bucket: {e}");
                return;
            }
        }
    }

    // 3. Test HEAD bucket
    match bucket.exists().await {
        Ok(true) => tracing::info!("HEAD bucket: OK (exists)"),
        Ok(false) => tracing::info!("HEAD bucket: OK (does not exist yet)"),
        Err(e) => {
            tracing::error!("HEAD bucket failed: {e}");
            tracing::error!("Bucket may not exist. Use --create-bucket to create it.");
            return;
        }
    }

    // 3.5. Test public access (plain HTTP GET without credentials)
    tracing::info!("{}", "─".repeat(60));
    let public_url = match &bucket.region {
        s3::Region::Custom { endpoint, .. } => {
            format!("{endpoint}/{}/meta/tickers.json", bucket.name())
        }
        other => {
            let region_str = format!("{:?}", other);
            format!("https://{}.s3.{region_str}.amazonaws.com/meta/tickers.json", bucket.name())
        }
    };

    let public_check = reqwest::get(&public_url).await;
    match public_check {
        Ok(resp) if resp.status().as_u16() == 200 => {
            tracing::info!("Public access: OK — {public_url}");
        }
        Ok(resp) => {
            tracing::warn!("Public access: FAILED (status={}) — bucket requires public-read for Python SDK", resp.status());
        }
        Err(e) => {
            tracing::warn!("Public access: FAILED ({e}) — could not reach S3 endpoint");
        }
    }

    // 4. Connect to DB
    let database_url = match std::env::var("DATABASE_URL") {
        Ok(url) => url,
        Err(_) => {
            tracing::error!("DATABASE_URL not set");
            return;
        }
    };

    let pool = match crate::db::connect(&database_url).await {
        Ok(p) => {
            tracing::info!("Connected to PostgreSQL");
            p
        }
        Err(e) => {
            tracing::error!("Failed to connect to database: {e}");
            return;
        }
    };

    // 5. Find ticker
    let tickers = sqlx::query_as::<_, Ticker>(
        "SELECT id, source, ticker, name, status, next_1d FROM tickers WHERE ticker = $1",
    )
    .bind(&ticker)
    .fetch_all(&pool)
    .await
    .unwrap_or_default();

    if tickers.is_empty() {
        tracing::error!("Ticker '{}' not found in database", ticker);
        return;
    }

    let chosen = &tickers[0];
    tracing::info!(
        "Found ticker: {} (id={}, source={})",
        chosen.ticker,
        chosen.id,
        chosen.source
    );

    // 6. Upload tickers.json
    tracing::info!("{}", "─".repeat(60));
    let enrichment = EnrichmentData::load();
    match upload_tickers_json(&pool, &bucket, &enrichment).await {
        Ok(()) => tracing::info!("tickers.json: uploaded"),
        Err(e) => tracing::error!("tickers.json: failed — {e}"),
    }

    // 7. Upload CSV files for the requested days
    tracing::info!("{}", "─".repeat(60));
    tracing::info!("Uploading {} days of {}/{} data...", days, ticker, interval);

    let today = chrono::Utc::now().date_naive();
    let mut uploaded = 0u64;
    let mut skipped = 0u64;

    for day_offset in 0..days {
        let date = today - Duration::days(day_offset as i64);
        let (day_start, day_end) = day_range(date);
        let key = s3_key(&chosen.source, &chosen.ticker, &interval, date);

        match process_day(&pool, &bucket, chosen.id, &interval, &key, day_start, day_end).await {
            Ok(true) => {
                uploaded += 1;
                tracing::info!("  [UPLOAD] {} (changed)", key);
            }
            Ok(false) => {
                skipped += 1;
                tracing::info!("  [SKIP]   {} (unchanged or no data)", key);
            }
            Err(e) => {
                tracing::error!("  [ERROR]  {}: {e}", key);
            }
        }
    }

    tracing::info!("{}", "─".repeat(60));
    tracing::info!("Done — uploaded: {uploaded}, skipped: {skipped}");

    // 8. Verify: HEAD the last uploaded key
    if uploaded > 0 {
        let last_date = today;
        let verify_key = s3_key(&chosen.source, &chosen.ticker, &interval, last_date);
        tracing::info!("Verifying HEAD {}", verify_key);
        match bucket.head_object(&verify_key).await {
            Ok((head, _status)) => {
                let hash = head.metadata.as_ref()
                    .and_then(|m| m.get("content-hash"))
                    .map(|s| s.as_str())
                    .unwrap_or("none");
                let size = head.content_length.map(|s| s.to_string()).unwrap_or_else(|| "unknown".to_string());
                tracing::info!("  content-hash: {hash}");
                tracing::info!("  content-length: {size} bytes");
            }
            Err(e) => tracing::error!("  HEAD failed: {e}"),
        }
    }
}
