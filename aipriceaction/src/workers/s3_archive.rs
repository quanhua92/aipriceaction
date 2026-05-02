use chrono::{Duration, NaiveDate, Utc};
use futures::StreamExt;
use http::HeaderMap;
use sqlx::PgPool;
use std::collections::HashMap;
use std::time::Duration as StdDuration;

use awscreds::Credentials;
use s3::{Bucket, BucketConfiguration, Region};

use crate::constants::s3_archive::{
    CSV_CONTENT_TYPE, JSON_CONTENT_TYPE, LOOKBACK_DAYS, LOOP_SECS, STARTUP_SCAN_INTERVAL_SECS,
    UPLOAD_CONCURRENCY,
};
use crate::queries::ohlcv::Ticker;
use crate::queries::s3_archive::{
    day_range, get_data_ranges, get_ohlcv_day_fingerprint, get_ohlcv_for_day, ArchiveTicker,
};

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

use std::collections::BTreeMap;

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

    tracing::info!(
        "s3_archive: worker started (interval={}s, lookback_days={}, bucket={})",
        interval_secs,
        LOOKBACK_DAYS,
        std::env::var("S3_BUCKET").unwrap_or_default(),
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
        tracing::info!(
            "s3_archive: incremental cycle complete, sleeping {}s",
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

    let mut in_flight: futures::stream::FuturesUnordered<_> =
        futures::stream::FuturesUnordered::new();

    // Group ranges by ticker_id so we can log per-ticker progress
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

            let mut current = earliest_date;
            while current <= latest_date {
                let (day_start, day_end) = day_range(current);
                let key = s3_key(source, ticker_sym, interval, current);
                let interval = interval.to_string();
                let pool = pool.clone();
                let bucket = bucket.clone();
                let permit = sem.clone();
                let ticker_id = range.ticker_id;

                in_flight.push(tokio::spawn(async move {
                    let _permit = permit.acquire().await.unwrap();
                    let result = process_day(&pool, &bucket, ticker_id, &interval, &key, day_start, day_end).await;
                    (key, result)
                }));

                current += Duration::days(1);

                // Drain completed tasks to avoid unbounded memory growth
                while in_flight.len() >= UPLOAD_CONCURRENCY * 2 {
                    if let Some(result) = in_flight.next().await {
                        match result {
                            Ok((key, Ok(true))) => uploaded += 1,
                            Ok((_key, Ok(false))) => skipped += 1,
                            Ok((key, Err(e))) => {
                                tracing::warn!("s3_archive: error processing {key}: {e}");
                            }
                            Err(e) => {
                                tracing::warn!("s3_archive: task panicked: {e}");
                            }
                        }
                    }
                }
            }
        }
    }

    // Drain remaining tasks
    while let Some(result) = in_flight.next().await {
        match result {
            Ok((_key, Ok(true))) => uploaded += 1,
            Ok((_key, Ok(false))) => skipped += 1,
            Ok((key, Err(e))) => {
                tracing::warn!("s3_archive: error processing {key}: {e}");
            }
            Err(e) => {
                tracing::warn!("s3_archive: task panicked: {e}");
            }
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
                    let result = process_day(&pool, &bucket, ticker_id, &interval, &key, day_start, day_end).await;
                    (key, result)
                }));

                // Drain completed tasks
                while in_flight.len() >= UPLOAD_CONCURRENCY * 2 {
                    if let Some(result) = in_flight.next().await {
                        match result {
                            Ok((_key, Ok(true))) => uploaded += 1,
                            Ok((_key, Ok(false))) => skipped += 1,
                            Ok((key, Err(e))) => {
                                tracing::warn!("s3_archive: error processing {key}: {e}");
                            }
                            Err(e) => {
                                tracing::warn!("s3_archive: task panicked: {e}");
                            }
                        }
                    }
                }
            }
        }
    }

    // Drain remaining tasks
    while let Some(result) = in_flight.next().await {
        match result {
            Ok((_key, Ok(true))) => uploaded += 1,
            Ok((_key, Ok(false))) => skipped += 1,
            Ok((key, Err(e))) => {
                tracing::warn!("s3_archive: error processing {key}: {e}");
            }
            Err(e) => {
                tracing::warn!("s3_archive: task panicked: {e}");
            }
        }
    }

    tracing::info!(
        "s3_archive: incremental cycle — {total_tickers} tickers, uploaded: {uploaded}, skipped: {skipped}"
    );
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
