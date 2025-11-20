use crate::constants::INDEX_TICKERS;
use crate::models::{AggregatedInterval, Interval, Mode};
use crate::server::AppState;
use crate::services::{SharedDataStore, SharedHealthStats, ApiPerformanceMetrics, ApiStatus, write_api_log_entry, determine_data_source};
use crate::services::data_store::QueryParameters;
use crate::services::trading_hours::get_cache_max_age;
use crate::utils::{get_public_dir, format_date, format_timestamp};
use axum::{
    extract::{State, Json},
    http::{HeaderMap, StatusCode, header::{CACHE_CONTROL, CONTENT_TYPE}},
    response::{IntoResponse, Response, Html},
    body::HttpBody,
};
use axum_extra::extract::Query;
use chrono::{NaiveDate, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use tracing::{debug, info, warn, instrument};

/// Stock data response with VCI time format and optional technical indicators
#[derive(Debug, Serialize)]
pub struct StockDataResponse {
    /// Time in YYYY-MM-DD format (daily) or YYYY-MM-DD HH:MM:SS format (intraday)
    pub time: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: u64,
    pub symbol: String,

    // Technical indicators (only included when available)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma10: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma20: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma50: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma100: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma200: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma10_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma20_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma50_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma100_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ma200_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub close_changed: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub volume_changed: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_money_changed: Option<f64>,
}

/// Query parameters for /tickers endpoint
#[derive(Debug, Deserialize, Clone)]
pub struct TickerQuery {
    /// Ticker symbols to query (can be repeated: symbol=VCB&symbol=FPT)
    pub symbol: Option<Vec<String>>,

    /// Interval: 1D (default), 1H, 1m
    pub interval: Option<String>,

    /// Start date filter (YYYY-MM-DD)
    pub start_date: Option<String>,

    /// End date filter (YYYY-MM-DD)
    pub end_date: Option<String>,

    /// Limit number of records to return (works with end_date to get N rows back in history)
    /// If start_date is provided, limit is ignored
    pub limit: Option<usize>,

    /// Legacy price format: divide by 1000 for old proxy compatibility - default: false
    #[serde(default)]
    pub legacy: bool,

    /// Response format: json (default) or csv
    #[serde(default = "default_format")]
    pub format: String,

    /// Use memory cache (default: true) - set to false to force disk read
    #[serde(default = "default_cache")]
    pub cache: bool,

    /// Market mode: vn (default) or crypto
    #[serde(default)]
    pub mode: Mode,
}

fn default_format() -> String {
    "json".to_string() // Default to JSON format
}

fn default_cache() -> bool {
    true // Default to using memory cache
}

/// GET /tickers - Query stock data from in-memory store or disk
///
/// Examples:
/// - /tickers?symbol=VCB (default to daily, uses cache, vn mode)
/// - /tickers?symbol=VCB&interval=1H
/// - /tickers?symbol=VCB&interval=1D&start_date=2024-01-01&end_date=2024-12-31
/// - /tickers?symbol=VCB&end_date=2024-06-15&limit=5 (get 5 rows back from June 15, 2024)
/// - /tickers?symbol=VCB&cache=false (force disk read, bypass memory cache)
/// - /tickers?symbol=BTC&mode=crypto (query crypto data)
#[instrument(skip(app_state))]
pub async fn get_tickers_handler(
    State(app_state): State<AppState>,
    Query(params): Query<TickerQuery>,
) -> impl IntoResponse {
    let perf_start = std::time::Instant::now();
    debug!("[DEBUG:PERF] Request start");

    let start_time = Utc::now();
    let mut performance_metrics = ApiPerformanceMetrics::new(start_time);
    performance_metrics.endpoint = "/tickers".to_string();
    performance_metrics.response_format = params.format.clone();

    debug!("Received request for tickers with params: {:?}", params);

    // Get DataStore based on mode
    let data_state = app_state.get_data_store(params.mode);

    // Parse and validate parameters
    let perf_parse_start = std::time::Instant::now();
    let query_params = match parse_query_parameters(params.clone(), data_state).await {
        Ok(params) => {
            performance_metrics.interval = params.interval.to_filename().to_string();
            performance_metrics.ticker_count = params.tickers.len();
            performance_metrics.cache_used = params.use_cache;
            debug!("[DEBUG:PERF] Parse params: {:.2}ms, {} tickers", perf_parse_start.elapsed().as_secs_f64() * 1000.0, params.tickers.len());
            params
        },
        Err(error_response) => {
            performance_metrics.status = ApiStatus::Fail;
            performance_metrics.complete();
            performance_metrics.error_message = Some("Invalid parameters".to_string());
            write_api_log_entry(&performance_metrics);
            return error_response;
        },
    };

    // Smart data retrieval - DataStore handles all the complexity
    let perf_data_start = std::time::Instant::now();
    debug!("[DEBUG:PERF] get_data_smart start: {} tickers, interval={}", query_params.tickers.len(), query_params.interval.to_filename());
    let result_data = data_state.get_data_smart(query_params.clone()).await;

    let ticker_count = result_data.len();
    let total_records: usize = result_data.values().map(|v| v.len()).sum();
    debug!("[DEBUG:PERF] get_data_smart complete: {:.2}ms, {} tickers, {} records", perf_data_start.elapsed().as_secs_f64() * 1000.0, ticker_count, total_records);

    info!(
        ticker_count,
        total_records,
        interval = %query_params.interval.to_filename(),
        symbols = ?query_params.tickers,
        legacy_prices = query_params.legacy_prices,
        format = params.format,
        use_cache = query_params.use_cache,
        "Returning ticker data"
    );

    let mut headers = HeaderMap::new();
    let cache_max_age = get_cache_max_age();
    headers.insert(CACHE_CONTROL, format!("max-age={}", cache_max_age).parse().unwrap());

    debug!(
        cache_max_age = cache_max_age,
        "Applied cache control header based on trading hours"
    );

    // Check format parameter
    if params.format == "csv" {
        // Generate CSV format
        let perf_csv_start = std::time::Instant::now();
        debug!("[DEBUG:PERF] CSV generation start: {} tickers, {} records", ticker_count, total_records);
        let response = generate_csv_response(result_data, query_params.interval, query_params.legacy_prices, params.mode, headers);
        debug!("[DEBUG:PERF] CSV generation complete: {:.2}ms", perf_csv_start.elapsed().as_secs_f64() * 1000.0);
        debug!("[DEBUG:PERF] Total request: {:.2}ms", perf_start.elapsed().as_secs_f64() * 1000.0);

        // Complete performance metrics and log for CSV response
        performance_metrics.response_size_bytes = response.body().size_hint().lower() as usize;
        performance_metrics.data_source = determine_data_source(query_params.use_cache, !query_params.use_cache);
        performance_metrics.complete();
        write_api_log_entry(&performance_metrics);

        return response;
    }

    // Return JSON format - use BTreeMap for alphabetically sorted keys
    let response_data: BTreeMap<String, Vec<StockDataResponse>> = result_data
        .into_iter()
        .map(|(ticker, data)| {
            let records = data.into_iter().map(|d| {
                let time_str = match query_params.interval {
                    Interval::Daily => format_date(&d.time),
                    Interval::Hourly | Interval::Minute => format_timestamp(&d.time),
                };

                // Apply legacy price format if requested (divide by 1000 for VN stocks only, not indices or crypto)
                let price_divisor = if params.mode == Mode::Vn && query_params.legacy_prices && !INDEX_TICKERS.contains(&ticker.as_str()) {
                    1000.0
                } else {
                    1.0
                };

                StockDataResponse {
                    time: time_str,
                    open: d.open / price_divisor,
                    high: d.high / price_divisor,
                    low: d.low / price_divisor,
                    close: d.close / price_divisor,
                    volume: d.volume,
                    symbol: d.ticker,
                    ma10: d.ma10.map(|v| v / price_divisor),
                    ma20: d.ma20.map(|v| v / price_divisor),
                    ma50: d.ma50.map(|v| v / price_divisor),
                    ma100: d.ma100.map(|v| v / price_divisor),
                    ma200: d.ma200.map(|v| v / price_divisor),
                    ma10_score: d.ma10_score,
                    ma20_score: d.ma20_score,
                    ma50_score: d.ma50_score,
                    ma100_score: d.ma100_score,
                    ma200_score: d.ma200_score,
                    close_changed: d.close_changed,
                    volume_changed: d.volume_changed,
                    total_money_changed: d.total_money_changed,
                }
            }).collect();
            (ticker, records)
        })
        .collect();

    // Complete performance metrics and log
    performance_metrics.response_size_bytes = response_data.len() * 200; // Approximate size
    performance_metrics.data_source = determine_data_source(query_params.use_cache, !query_params.use_cache);
    performance_metrics.complete();
    write_api_log_entry(&performance_metrics);

    (StatusCode::OK, headers, Json(response_data)).into_response()
}

/// Parse and validate query parameters into a QueryParameters struct
async fn parse_query_parameters(
    params: TickerQuery,
    data_state: &SharedDataStore,
) -> Result<QueryParameters, Response> {
    // Parse interval and detect aggregation
    let aggregated_interval = params.interval.as_deref()
        .and_then(|s| AggregatedInterval::from_str(s));

    let interval = if let Some(agg) = aggregated_interval {
        agg.base_interval()
    } else {
        match params.interval.as_deref() {
            Some("1D") | Some("daily") | None => Interval::Daily,
            Some("1H") | Some("hourly") => Interval::Hourly,
            Some("1m") | Some("minute") => Interval::Minute,
            Some(other) => {
                warn!(interval = %other, "Invalid interval parameter");
                return Err((
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({
                        "error": "Invalid interval. Valid values: 1D, 1H, 1m, 5m, 15m, 30m, 1W, 2W, 1M (or daily, hourly, minute)"
                    }))
                ).into_response());
            }
        }
    };

    // Parse date filters
    let start_date_filter = match &params.start_date {
        Some(date_str) => {
            match NaiveDate::parse_from_str(date_str, "%Y-%m-%d") {
                Ok(date) => Some(date.and_hms_opt(0, 0, 0).unwrap().and_utc()),
                Err(_) => {
                    warn!(start_date = %date_str, "Invalid start_date format");
                    return Err((
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({
                            "error": "Invalid start_date format. Expected YYYY-MM-DD"
                        }))
                    ).into_response());
                }
            }
        }
        None => None,
    };

    let end_date_filter = match &params.end_date {
        Some(date_str) => {
            match NaiveDate::parse_from_str(date_str, "%Y-%m-%d") {
                Ok(date) => Some(date.and_hms_opt(23, 59, 59).unwrap().and_utc()),
                Err(_) => {
                    warn!(end_date = %date_str, "Invalid end_date format");
                    return Err((
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({
                            "error": "Invalid end_date format. Expected YYYY-MM-DD"
                        }))
                    ).into_response());
                }
            }
        }
        None => None,
    };

    // Determine which symbols to query
    let symbols_to_query: Vec<String> = match params.symbol.filter(|s| !s.is_empty()) {
        Some(symbols) => symbols,
        None => data_state.get_all_ticker_names().await, // All tickers
    };

    // Create QueryParameters with all the logic
    Ok(QueryParameters::new(
        symbols_to_query,
        interval,
        aggregated_interval,
        start_date_filter,
        end_date_filter,
        params.limit,
        params.cache,
        params.legacy,
    ))
}

/// Generate CSV response from stock data
fn generate_csv_response(
    data: HashMap<String, Vec<crate::models::StockData>>,
    interval: Interval,
    legacy_prices: bool,
    mode: Mode,
    mut headers: HeaderMap,
) -> Response {
    use std::fmt::Write;

    let start_time = std::time::Instant::now();

    // Helper function to check if ticker is a market index
    let is_index = |ticker: &str| -> bool {
        INDEX_TICKERS.contains(&ticker)
    };

    // CSV header - adapt based on whether we have technical indicators
    // For simplicity, we'll check the first record to see what fields are available
    let has_indicators = data.values().next()
        .and_then(|records| records.first())
        .map(|record| record.ma10.is_some())
        .unwrap_or(false);

    // Calculate total record count for buffer pre-allocation
    let total_records: usize = data.values().map(|v| v.len()).sum();

    // Pre-allocate buffer: header (~200) + (records * avg_bytes_per_row)
    // Each row ~200 bytes (20 fields with indicators) or ~80 bytes (7 fields basic)
    let bytes_per_row = if has_indicators { 200 } else { 80 };
    let estimated_size = 200 + (total_records * bytes_per_row);
    let mut csv_content = String::with_capacity(estimated_size);

    if has_indicators {
        // Full header with technical indicators (NEW 20-column format)
        csv_content.push_str(
            "symbol,time,open,high,low,close,volume,ma10,ma20,ma50,ma100,ma200,ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,close_changed,volume_changed,total_money_changed\n"
        );
    } else {
        // Basic header without technical indicators
        csv_content.push_str("symbol,time,open,high,low,close,volume\n");
    }

    // Add data rows - sort by ticker for consistency
    let mut tickers: Vec<_> = data.keys().cloned().collect();
    tickers.sort();

    for ticker in tickers {
        if let Some(records) = data.get(&ticker) {
            // Determine price divisor for this ticker (only for VN mode stocks, not indices or crypto)
            let price_divisor = if mode == Mode::Vn && legacy_prices && !is_index(&ticker) { 1000.0 } else { 1.0 };

            for record in records {
                let time_str = match interval {
                    Interval::Daily => format_date(&record.time),
                    Interval::Hourly | Interval::Minute => format_timestamp(&record.time),
                };

                if has_indicators {
                    // Write row with all fields (NEW 20-column format)
                    // Use write! macro which is more efficient than format!
                    let _ = write!(
                        csv_content,
                        "{},{},{},{},{},{},{},",
                        ticker,
                        time_str,
                        record.open / price_divisor,
                        record.high / price_divisor,
                        record.low / price_divisor,
                        record.close / price_divisor,
                        record.volume,
                    );

                    // MA values
                    if let Some(v) = record.ma10 { let _ = write!(csv_content, "{}", v / price_divisor); }
                    csv_content.push(',');
                    if let Some(v) = record.ma20 { let _ = write!(csv_content, "{}", v / price_divisor); }
                    csv_content.push(',');
                    if let Some(v) = record.ma50 { let _ = write!(csv_content, "{}", v / price_divisor); }
                    csv_content.push(',');
                    if let Some(v) = record.ma100 { let _ = write!(csv_content, "{}", v / price_divisor); }
                    csv_content.push(',');
                    if let Some(v) = record.ma200 { let _ = write!(csv_content, "{}", v / price_divisor); }
                    csv_content.push(',');

                    // MA scores (no price divisor)
                    if let Some(v) = record.ma10_score { let _ = write!(csv_content, "{}", v); }
                    csv_content.push(',');
                    if let Some(v) = record.ma20_score { let _ = write!(csv_content, "{}", v); }
                    csv_content.push(',');
                    if let Some(v) = record.ma50_score { let _ = write!(csv_content, "{}", v); }
                    csv_content.push(',');
                    if let Some(v) = record.ma100_score { let _ = write!(csv_content, "{}", v); }
                    csv_content.push(',');
                    if let Some(v) = record.ma200_score { let _ = write!(csv_content, "{}", v); }
                    csv_content.push(',');

                    // Change percentages
                    if let Some(v) = record.close_changed { let _ = write!(csv_content, "{}", v); }
                    csv_content.push(',');
                    if let Some(v) = record.volume_changed { let _ = write!(csv_content, "{}", v); }
                    csv_content.push(',');
                    if let Some(v) = record.total_money_changed { let _ = write!(csv_content, "{}", v); }

                    csv_content.push('\n');
                } else {
                    // Write row with basic fields only
                    let _ = write!(
                        csv_content,
                        "{},{},{},{},{},{},{}\n",
                        ticker,
                        time_str,
                        record.open / price_divisor,
                        record.high / price_divisor,
                        record.low / price_divisor,
                        record.close / price_divisor,
                        record.volume,
                    );
                }
            }
        }
    }

    let duration = start_time.elapsed();
    tracing::info!(
        "CSV generation: {} records, {} bytes, {:.2}ms",
        total_records,
        csv_content.len(),
        duration.as_secs_f64() * 1000.0
    );

    // Set CSV content type
    headers.insert(
        CONTENT_TYPE,
        "text/csv; charset=utf-8".parse().unwrap()
    );

    // Suggest filename for download
    headers.insert(
        "Content-Disposition",
        format!("attachment; filename=\"tickers_{}.csv\"", interval.to_filename().trim_end_matches(".csv"))
            .parse()
            .unwrap()
    );

    (StatusCode::OK, headers, csv_content).into_response()
}

/// GET /health - Health statistics endpoint
#[instrument(skip(health_state, app_state))]
pub async fn health_handler(
    State(health_state): State<SharedHealthStats>,
    State(app_state): State<AppState>,
) -> impl IntoResponse {
    debug!("Received request for health stats");

    // NOTE: Currently shows only VN market stats for backward compatibility
    // TODO: Consider adding separate vn_stats and crypto_stats in future version
    let data_state = app_state.get_data_store(Mode::Vn);

    let mut health_stats = health_state.read().await.clone();

    // Calculate current metrics dynamically using DataStore methods
    let memory_bytes = data_state.estimate_memory_usage().await;
    let (daily_count, hourly_count, minute_count) = data_state.get_record_counts().await;
    let active_tickers = data_state.get_active_ticker_count().await;
    let (disk_cache_entries, disk_cache_size_bytes, disk_cache_limit_bytes) = data_state.get_disk_cache_stats().await;

    health_stats.memory_usage_bytes = memory_bytes;
    health_stats.memory_usage_mb = memory_bytes as f64 / (1024.0 * 1024.0);
    health_stats.memory_usage_percent =
        (memory_bytes as f64 / (health_stats.memory_limit_mb * 1024 * 1024) as f64) * 100.0;
    health_stats.active_tickers_count = active_tickers;
    health_stats.daily_records_count = daily_count;
    health_stats.hourly_records_count = hourly_count;
    health_stats.minute_records_count = minute_count;

    health_stats.disk_cache_entries = disk_cache_entries;
    health_stats.disk_cache_size_bytes = disk_cache_size_bytes;
    health_stats.disk_cache_size_mb = disk_cache_size_bytes as f64 / (1024.0 * 1024.0);
    health_stats.disk_cache_limit_mb = disk_cache_limit_bytes / (1024 * 1024);
    health_stats.disk_cache_usage_percent = if disk_cache_limit_bytes > 0 {
        (disk_cache_size_bytes as f64 / disk_cache_limit_bytes as f64) * 100.0
    } else {
        0.0
    };

    health_stats.current_system_time = Utc::now().to_rfc3339();

    info!(
        memory_mb = health_stats.memory_usage_mb,
        active_tickers = health_stats.active_tickers_count,
        daily_records = health_stats.daily_records_count,
        "Returning health stats"
    );

    // No logging for /health endpoint (too noisy)

    (StatusCode::OK, Json(health_stats)).into_response()
}

/// Query parameters for /tickers/group endpoint
#[derive(Debug, Deserialize)]
pub struct TickerGroupQuery {
    /// Market mode: vn (default) or crypto
    #[serde(default)]
    pub mode: Mode,
}

/// GET /tickers/group - Get ticker groups from ticker_group.json or crypto_top_100.json
///
/// Examples:
/// - /tickers/group (VN mode by default)
/// - /tickers/group?mode=vn (VN stocks grouped by sector)
/// - /tickers/group?mode=crypto (All cryptos in a single group)
#[instrument]
pub async fn get_ticker_groups_handler(
    Query(params): Query<TickerGroupQuery>,
) -> impl IntoResponse {
    debug!("Received request for ticker groups with mode: {:?}", params.mode);

    match params.mode {
        Mode::Vn => {
            // Read VN ticker groups from ticker_group.json
            let ticker_groups_path = "ticker_group.json";

            match std::fs::read_to_string(ticker_groups_path) {
                Ok(content) => {
                    match serde_json::from_str::<HashMap<String, Vec<String>>>(&content) {
                        Ok(groups) => {
                            let group_count = groups.len();
                            let group_names: Vec<_> = groups.keys().cloned().collect();

                            info!(group_count, groups = ?group_names, mode = "vn", "Returning ticker groups");
                            (StatusCode::OK, Json(groups)).into_response()
                        }
                        Err(e) => {
                            warn!(error = %e, "Failed to parse ticker_group.json");
                            (
                                StatusCode::INTERNAL_SERVER_ERROR,
                                Json(serde_json::json!({
                                    "error": "Failed to parse ticker groups"
                                }))
                            ).into_response()
                        }
                    }
                }
                Err(e) => {
                    warn!(error = %e, "Failed to read ticker_group.json");
                    (
                        StatusCode::NOT_FOUND,
                        Json(serde_json::json!({
                            "error": "Ticker groups file not found"
                        }))
                    ).into_response()
                }
            }
        }
        Mode::Crypto => {
            // Read crypto list from crypto_top_100.json and format as a single group
            use crate::models::load_crypto_metadata;

            let crypto_list_path = "crypto_top_100.json";

            match load_crypto_metadata(crypto_list_path) {
                Ok(crypto_metadata) => {
                    // Extract symbols in rank order (already sorted in JSON)
                    let symbols: Vec<String> = crypto_metadata
                        .iter()
                        .map(|c| c.symbol.clone())
                        .collect();

                    let count = symbols.len();

                    // Create a single group with all cryptos
                    let mut groups = HashMap::new();
                    groups.insert("CRYPTO_TOP_100".to_string(), symbols);

                    info!(count, mode = "crypto", "Returning crypto groups");
                    (StatusCode::OK, Json(groups)).into_response()
                }
                Err(e) => {
                    warn!(error = %e, "Failed to load crypto_top_100.json");
                    (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(serde_json::json!({
                            "error": format!("Failed to load crypto groups: {}", e)
                        }))
                    ).into_response()
                }
            }
        }
    }
}

/// Handler for /explorer route - serves the API explorer UI
#[instrument]
pub async fn explorer_handler() -> impl IntoResponse {
    let public_dir = get_public_dir();
    let index_path = public_dir.join("index.html");

    match tokio::fs::read_to_string(&index_path).await {
        Ok(html) => {
            info!("Serving explorer UI from {}", index_path.display());
            Html(html).into_response()
        }
        Err(e) => {
            warn!(error = %e, path = %index_path.display(), "Failed to read index.html");
            (
                StatusCode::NOT_FOUND,
                Html("<h1>Explorer not found</h1><p>Unable to load the API explorer interface.</p>")
            ).into_response()
        }
    }
}
