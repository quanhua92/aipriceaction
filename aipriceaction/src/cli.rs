use clap::{Parser, Subcommand};

use crate::db;
use crate::models::interval::Interval;
use crate::providers::vci::VciProvider;
use crate::services::ohlcv;

#[derive(Parser)]
#[command(name = "aipriceaction")]
#[command(about = "AI Price Action CLI", long_about = None)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Subcommand)]
pub enum Commands {
    /// Start the server
    Serve {
        /// Host to bind to
        #[arg(long, default_value = "0.0.0.0")]
        host: String,
        /// Port to listen on
        #[arg(short, long, default_value = "3000")]
        port: u16,
    },
    /// Show current status
    Status,
    /// Run benchmark queries to estimate API timing
    Stats {
        /// Data source label (default: "vn")
        #[arg(long, default_value = "vn")]
        source: String,
        /// Comma-separated ticker symbols (default: VCB,FPT,VNINDEX)
        #[arg(long, default_value = "VCB,FPT,VNINDEX")]
        tickers: String,
        /// Comma-separated intervals to test (default: 1D,1h,1m)
        #[arg(long, default_value = "1D,1h,1m")]
        intervals: String,
        /// Row limit per query (default: 100)
        #[arg(long, default_value = "100")]
        limit: i64,
        /// Include unoptimized limit-only queries (no date range, slow on 1h/1m)
        #[arg(long)]
        with_raw: bool,
        /// Include full-history queries (no limit, very slow on 1h/1m)
        #[arg(long)]
        with_all: bool,
    },
    /// Import CSV files from market_data directory into PostgreSQL
    Import {
        /// Path to the market_data directory
        #[arg(long)]
        market_data: String,
        /// Only import a specific ticker symbol (e.g. "VNINDEX")
        #[arg(long)]
        ticker: Option<String>,
        /// Only import a specific interval (e.g. "1D", "1H", "1m")
        #[arg(long)]
        interval: Option<String>,
        /// Data source label (default: "vn")
        #[arg(long, default_value = "vn")]
        source: String,
    },
    /// Test VCI provider connectivity and data fetching
    TestVci {
        /// Ticker symbol to test (default: VNINDEX)
        #[arg(long, default_value = "VNINDEX")]
        ticker: String,
        /// Rate limit per client (requests per minute, default: 30)
        #[arg(long, default_value = "30")]
        rate_limit: u32,
        /// Number of data points to request (default: 10)
        #[arg(long, default_value = "10")]
        count_back: u32,
    },
}

pub fn run() {
    // Load .env file if present (optional — won't error if missing)
    dotenvy::dotenv().ok();

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .with_target(false)
        .init();

    let cli = Cli::parse();

    match cli.command {
        Commands::Serve { host, port } => {
            let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
            rt.block_on(async {
                let database_url = std::env::var("DATABASE_URL").unwrap_or_else(|_| {
                    tracing::warn!("DATABASE_URL not set, server will run without database");
                    String::new()
                });

                if database_url.is_empty() {
                    tracing::error!("DATABASE_URL not set");
                    return;
                }

                let pool = match db::connect(&database_url).await {
                    Ok(pool) => {
                        tracing::info!("Connected to PostgreSQL, migrations applied");
                        pool
                    }
                    Err(e) => {
                        tracing::error!("Failed to connect to database: {e}");
                        return;
                    }
                };

                tracing::info!("Starting server on {host}:{port}");

                let app = crate::server::create_app(pool);
                let listener = tokio::net::TcpListener::bind(format!("{host}:{port}"))
                    .await
                    .expect("Failed to bind to address");
                tracing::info!("Listening on {host}:{port}");
                axum::serve(listener, app)
                    .await
                    .expect("Server error");
            });
        }
        Commands::Status => {
            let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
            rt.block_on(async {
                let database_url =
                    std::env::var("DATABASE_URL").unwrap_or_else(|_| String::new());

                if database_url.is_empty() {
                    tracing::error!("DATABASE_URL not set");
                    return;
                }

                let pool = match db::connect(&database_url).await {
                    Ok(pool) => {
                        tracing::info!("Connected to PostgreSQL, migrations applied");
                        pool
                    }
                    Err(e) => {
                        tracing::error!("Failed to connect to database: {e}");
                        return;
                    }
                };

                match db::health_check(&pool).await {
                    Ok(()) => tracing::info!("Database health check: OK"),
                    Err(e) => tracing::error!("Database health check failed: {e}"),
                }

                let source = "vn";

                // Total tickers
                let ticker_count = ohlcv::count_tickers(&pool, source)
                    .await
                    .expect("Failed to count tickers");

                // Total OHLCV and indicator rows (all tickers)
                let total_ohlcv = ohlcv::count_ohlcv(&pool, source, None, None)
                    .await
                    .expect("Failed to count OHLCV");
                let total_indicators = ohlcv::count_indicators(&pool, source, None, None)
                    .await
                    .expect("Failed to count indicators");

                tracing::info!("Source: {source} | Tickers: {ticker_count} | OHLCV: {total_ohlcv} | Indicators: {total_indicators}");

                // Per-interval totals
                for iv in &["1D", "1h", "1m"] {
                    let ohlcv_count = ohlcv::count_ohlcv(&pool, source, None, Some(iv))
                        .await
                        .expect("Failed to count OHLCV");
                    let ind_count = ohlcv::count_indicators(&pool, source, None, Some(iv))
                        .await
                        .expect("Failed to count indicators");
                    if ohlcv_count > 0 || ind_count > 0 {
                        tracing::info!("  {iv}: {ohlcv_count} OHLCV, {ind_count} indicators");
                    }
                }

                // VNINDEX breakdown
                let vnindex_ohlcv = ohlcv::count_ohlcv(&pool, source, Some("VNINDEX"), None)
                    .await
                    .expect("Failed to count VNINDEX OHLCV");
                let vnindex_ind = ohlcv::count_indicators(&pool, source, Some("VNINDEX"), None)
                    .await
                    .expect("Failed to count VNINDEX indicators");
                tracing::info!("VNINDEX: {vnindex_ohlcv} OHLCV, {vnindex_ind} indicators");
                for iv in &["1D", "1h", "1m"] {
                    let count = ohlcv::count_ohlcv(&pool, source, Some("VNINDEX"), Some(iv))
                        .await
                        .expect("Failed to count VNINDEX OHLCV");
                    if count > 0 {
                        tracing::info!("  {iv}: {count}");
                    }
                }
            });
        }
        Commands::Stats {
            source,
            tickers,
            intervals,
            limit,
            with_raw,
            with_all,
        } => {
            let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
            rt.block_on(async {
                let database_url =
                    std::env::var("DATABASE_URL").unwrap_or_else(|_| String::new());

                if database_url.is_empty() {
                    tracing::error!("DATABASE_URL not set");
                    return;
                }

                let pool = match db::connect(&database_url).await {
                    Ok(pool) => pool,
                    Err(e) => {
                        tracing::error!("Failed to connect to database: {e}");
                        return;
                    }
                };

                let ticker_list: Vec<&str> = tickers.split(',').map(|s| s.trim()).collect();
                let interval_list: Vec<&str> = intervals.split(',').map(|s| s.trim()).collect();

                tracing::info!(
                    "Stats: source={}, tickers=[{}], intervals=[{}], limit={}",
                    source, tickers, intervals, limit
                );
                tracing::info!("{}", "─".repeat(80));

                use crate::queries::ohlcv as q;
                use std::time::Instant;

                let mut results: Vec<(&str, &str, &str, usize, u128)> = Vec::new();

                for ticker in &ticker_list {
                    for interval in &interval_list {
                        // ── Q1: Single ticker, limit only (unoptimized — no date range) ──
                        if with_raw {
                            let label = "limit-only (raw)";
                            let start = Instant::now();
                            match q::get_ohlcv_joined(&pool, &source, ticker, interval, Some(limit)).await {
                                Ok(rows) => {
                                    let ms = start.elapsed().as_millis();
                                    tracing::info!(
                                        "  {:>10} | {:>8} | {:>6} | {} rows | {} ms",
                                        label, ticker, interval, rows.len(), ms
                                    );
                                    results.push((ticker, interval, label, rows.len(), ms));
                                }
                                Err(e) => tracing::warn!("  {label} | {ticker} | {interval} | ERROR: {e}"),
                            }
                        }

                        // ── Q2: Date range (last 30 days) ──
                        let label = "30d-range";
                        let now = chrono::Utc::now();
                        let start_time = now - chrono::Duration::days(30);
                        let start = Instant::now();
                        match ohlcv::get_ohlcv_joined_range(
                            &pool, &source, ticker, interval,
                            Some(limit), Some(start_time), None,
                        ).await {
                            Ok(rows) => {
                                let ms = start.elapsed().as_millis();
                                tracing::info!(
                                    "  {:>10} | {:>8} | {:>6} | {} rows | {} ms",
                                    label, ticker, interval, rows.len(), ms
                                );
                                results.push((ticker, interval, label, rows.len(), ms));
                            }
                            Err(e) => tracing::warn!("  {label} | {ticker} | {interval} | ERROR: {e}"),
                        }

                        // ── Q3: Smart heuristic (progressive date-range) ──
                        let label = "smart";
                        let start = Instant::now();
                        match ohlcv::get_ohlcv_joined(&pool, &source, ticker, interval, Some(limit)).await {
                            Ok(rows) => {
                                let ms = start.elapsed().as_millis();
                                tracing::info!(
                                    "  {:>10} | {:>8} | {:>6} | {} rows | {} ms",
                                    label, ticker, interval, rows.len(), ms
                                );
                                results.push((ticker, interval, label, rows.len(), ms));
                            }
                            Err(e) => tracing::warn!("  {label} | {ticker} | {interval} | ERROR: {e}"),
                        }

                        // ── Q5: Full history (no limit) ──
                        if with_all {
                            let label = "all-rows";
                            let start = Instant::now();
                            match q::get_ohlcv_joined(&pool, &source, ticker, interval, None).await {
                                Ok(rows) => {
                                    let ms = start.elapsed().as_millis();
                                    tracing::info!(
                                        "  {:>10} | {:>8} | {:>6} | {} rows | {} ms",
                                        label, ticker, interval, rows.len(), ms
                                    );
                                    results.push((ticker, interval, label, rows.len(), ms));
                                }
                                Err(e) => tracing::warn!("  {label} | {ticker} | {interval} | ERROR: {e}"),
                            }
                        }
                    }
                }

                // ── Q5: Multi-ticker simulation (sequential) ──
                tracing::info!("{}", "─".repeat(80));
                let label = "multi-ticker";
                for interval in &interval_list {
                    let start = Instant::now();
                    let mut total_rows = 0usize;
                    for ticker in &ticker_list {
                        match ohlcv::get_ohlcv_joined(&pool, &source, ticker, interval, Some(limit)).await {
                            Ok(rows) => total_rows += rows.len(),
                            Err(_) => {}
                        }
                    }
                    let ms = start.elapsed().as_millis();
                    tracing::info!(
                        "  {:>14} | {:>6} | {} rows ({} tickers x {limit}) | {} ms",
                        label, interval, total_rows, ticker_list.len(), ms
                    );
                }

                // ── Edge cases (progressive range heuristic) ──
                tracing::info!("{}", "─".repeat(80));
                tracing::info!("Edge cases (range heuristic on VCB 1m):");

                let ec_ticker = "VCB";
                let ec_interval = "1m";
                use chrono::Utc;

                // EC1: Narrow 1-day range — should stay in 1 partition
                {
                    let label = "range=1d";
                    let start = Instant::now();
                    let s = Utc::now() - chrono::Duration::days(1);
                    match ohlcv::get_ohlcv_joined_range(&pool, &source, ec_ticker, ec_interval, Some(limit), Some(s), None).await {
                        Ok(rows) => {
                            let ms = start.elapsed().as_millis();
                            tracing::info!("  {:>16} | {} rows | {} ms", label, rows.len(), ms);
                            results.push((ec_ticker, ec_interval, label, rows.len(), ms));
                        }
                        Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                    }
                }

                // EC2: Range spanning year boundary (Dec 2025 → now) — crosses 2 partitions
                {
                    let label = "range=cross-year";
                    let start = Instant::now();
                    let s = chrono::NaiveDate::from_ymd_opt(2025, 12, 1).unwrap().and_hms_opt(0,0,0).unwrap().and_utc();
                    match ohlcv::get_ohlcv_joined_range(&pool, &source, ec_ticker, ec_interval, Some(limit), Some(s), None).await {
                        Ok(rows) => {
                            let ms = start.elapsed().as_millis();
                            tracing::info!("  {:>16} | {} rows | {} ms", label, rows.len(), ms);
                            results.push((ec_ticker, ec_interval, label, rows.len(), ms));
                        }
                        Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                    }
                }

                // EC3: Very broad 2-year range — heuristic should clamp to narrow window
                {
                    let label = "range=2y";
                    let start = Instant::now();
                    let s = Utc::now() - chrono::Duration::days(730);
                    match ohlcv::get_ohlcv_joined_range(&pool, &source, ec_ticker, ec_interval, Some(limit), Some(s), None).await {
                        Ok(rows) => {
                            let ms = start.elapsed().as_millis();
                            tracing::info!("  {:>16} | {} rows | {} ms", label, rows.len(), ms);
                            results.push((ec_ticker, ec_interval, label, rows.len(), ms));
                        }
                        Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                    }
                }

                // EC4: Range entirely in the past (2024 Q1) — heuristic expands from end_time
                {
                    let label = "range=past-2024";
                    let start = Instant::now();
                    let s = chrono::NaiveDate::from_ymd_opt(2024, 1, 1).unwrap().and_hms_opt(0,0,0).unwrap().and_utc();
                    let e = chrono::NaiveDate::from_ymd_opt(2024, 3, 31).unwrap().and_hms_opt(23,59,59).unwrap().and_utc();
                    match ohlcv::get_ohlcv_joined_range(&pool, &source, ec_ticker, ec_interval, Some(limit), Some(s), Some(e)).await {
                        Ok(rows) => {
                            let ms = start.elapsed().as_millis();
                            tracing::info!("  {:>16} | {} rows | {} ms", label, rows.len(), ms);
                            results.push((ec_ticker, ec_interval, label, rows.len(), ms));
                        }
                        Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                    }
                }

                // EC4b: Broad historical range (2022-2024) — tests 730d window expansion
                {
                    let label = "range=2022-2024";
                    let start = Instant::now();
                    let s = chrono::NaiveDate::from_ymd_opt(2022, 1, 1).unwrap().and_hms_opt(0,0,0).unwrap().and_utc();
                    let e = chrono::NaiveDate::from_ymd_opt(2024, 12, 31).unwrap().and_hms_opt(23,59,59).unwrap().and_utc();
                    match ohlcv::get_ohlcv_joined_range(&pool, &source, ec_ticker, ec_interval, Some(limit), Some(s), Some(e)).await {
                        Ok(rows) => {
                            let ms = start.elapsed().as_millis();
                            tracing::info!("  {:>16} | {} rows | {} ms", label, rows.len(), ms);
                            results.push((ec_ticker, ec_interval, label, rows.len(), ms));
                        }
                        Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                    }
                }

                // EC5: No limit with a range — heuristic should NOT activate
                {
                    let label = "range=30d, no-limit";
                    let start = Instant::now();
                    let s = Utc::now() - chrono::Duration::days(30);
                    match ohlcv::get_ohlcv_joined_range(&pool, &source, ec_ticker, ec_interval, None, Some(s), None).await {
                        Ok(rows) => {
                            let ms = start.elapsed().as_millis();
                            tracing::info!("  {:>16} | {} rows | {} ms", label, rows.len(), ms);
                            results.push((ec_ticker, ec_interval, label, rows.len(), ms));
                        }
                        Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                    }
                }

                // EC6: Small limit (5 rows) — should resolve on first 30d window
                {
                    let label = "smart, limit=5";
                    let start = Instant::now();
                    match ohlcv::get_ohlcv_joined(&pool, &source, ec_ticker, ec_interval, Some(5)).await {
                        Ok(rows) => {
                            let ms = start.elapsed().as_millis();
                            tracing::info!("  {:>16} | {} rows | {} ms", label, rows.len(), ms);
                            results.push((ec_ticker, ec_interval, label, rows.len(), ms));
                        }
                        Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                    }
                }

                // EC7: Large limit (10000) on 1h — may need window expansion
                {
                    let label = "smart, limit=10k";
                    let start = Instant::now();
                    match ohlcv::get_ohlcv_joined(&pool, &source, "VCB", "1h", Some(10000)).await {
                        Ok(rows) => {
                            let ms = start.elapsed().as_millis();
                            tracing::info!("  {:>16} | {} rows | {} ms", label, rows.len(), ms);
                            results.push(("VCB", "1h", label, rows.len(), ms));
                        }
                        Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                    }
                }

                // ── Summary ──
                tracing::info!("{}", "─".repeat(80));
                tracing::info!("Summary:");
                let max_ms = results.iter().map(|r| r.4).max().unwrap_or(0);
                let avg_ms = if results.is_empty() { 0 } else { results.iter().map(|r| r.4).sum::<u128>() / results.len() as u128 };
                tracing::info!("  Queries: {} | Avg: {} ms | Max: {} ms", results.len(), avg_ms, max_ms);
            });
        }
        Commands::Import {
            market_data,
            ticker,
            interval,
            source,
        } => {
            let market_data_path = std::path::Path::new(&market_data);
            if !market_data_path.is_dir() {
                tracing::error!("--market-data path does not exist or is not a directory: {market_data}");
                return;
            }

            let interval_filter = match interval {
                Some(ref s) => match Interval::from_arg(s) {
                    Ok(iv) => Some(iv),
                    Err(e) => {
                        tracing::error!("Invalid --interval: {e}");
                        return;
                    }
                },
                None => None,
            };

            let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
            rt.block_on(async {
                let database_url =
                    std::env::var("DATABASE_URL").unwrap_or_else(|_| String::new());

                if database_url.is_empty() {
                    tracing::error!("DATABASE_URL not set");
                    return;
                }

                let pool = match db::connect(&database_url).await {
                    Ok(pool) => {
                        tracing::info!("Connected to PostgreSQL, migrations applied");
                        pool
                    }
                    Err(e) => {
                        tracing::error!("Failed to connect to database: {e}");
                        return;
                    }
                };

                tracing::info!(
                    "Importing from {} (source={}, ticker={}, interval={})",
                    market_data,
                    source,
                    ticker.as_deref().unwrap_or("all"),
                    interval.as_deref().unwrap_or("all"),
                );

                let stats = crate::services::import::import_csv(
                    &pool,
                    market_data_path,
                    &source,
                    ticker.as_deref(),
                    interval_filter.as_ref(),
                )
                .await;

                tracing::info!(
                    "Import complete: {} files, {} rows, {} batches, {} errors",
                    stats.files_processed,
                    stats.total_rows,
                    stats.total_batches,
                    stats.errors.len(),
                );

                for err in &stats.errors {
                    tracing::warn!("  error: {err}");
                }

                // Verification: read back last 5 rows for the last imported ticker+interval
                if stats.files_processed > 0 {
                    let verify_ticker = ticker.as_deref().unwrap_or("VCB");
                    let verify_interval = interval
                        .as_deref()
                        .map(|s| {
                            if s.eq_ignore_ascii_case("1h") {
                                "1h"
                            } else {
                                s
                            }
                        })
                        .unwrap_or("1D");

                    tracing::info!(
                        "Verification: reading last 5 rows for {} ({})",
                        verify_ticker,
                        verify_interval,
                    );
                    match ohlcv::get_ohlcv_joined(
                        &pool,
                        &source,
                        verify_ticker,
                        verify_interval,
                        Some(5),
                    )
                    .await
                    {
                        Ok(rows) => {
                            for row in &rows {
                                tracing::info!("  {row}");
                            }
                        }
                        Err(e) => {
                            tracing::warn!("Verification query failed: {e}");
                        }
                    }
                }
            });
        }
        Commands::TestVci {
            ticker,
            rate_limit,
            count_back,
        } => {
            let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
            rt.block_on(async {
                // 1. Create VciProvider
                tracing::info!("Initializing Vci provider (rate_limit={}/min per client)...", rate_limit);
                let provider = match VciProvider::new(rate_limit) {
                    Ok(p) => p,
                    Err(e) => {
                        tracing::error!("Failed to create Vci provider: {e}");
                        return;
                    }
                };
                tracing::info!("Connected with {} client(s)", provider.client_count());

                // Detect if ticker is an index (indices don't have company/financial data)
                let index_tickers = ["VNINDEX", "VN30", "HNX", "UPCOM", "HNX30", "UPCOMINDEX"];
                let is_index = index_tickers.iter().any(|t| t.eq_ignore_ascii_case(&ticker.as_str()));

                // 2. OHLCV test for each interval
                tracing::info!("{}", "─".repeat(60));
                tracing::info!("OHLCV Test — ticker={}, count_back={}", ticker, count_back);

                for interval in &["1D", "1H", "1m"] {
                    tracing::info!("  Fetching {} ...", interval);
                    match provider.get_history(&ticker, interval, count_back).await {
                        Ok(data) => {
                            let count = data.len();
                            if count > 0 {
                                let first = &data[0];
                                let last = &data[count - 1];
                                tracing::info!(
                                    "    ✅ {} | {} records | {} → {}",
                                    interval,
                                    count,
                                    first.time.format("%Y-%m-%d %H:%M"),
                                    last.time.format("%Y-%m-%d %H:%M"),
                                );
                                // Print first 3 rows
                                for row in data.iter().take(3) {
                                    tracing::info!(
                                        "       {} | O:{} H:{} L:{} C:{} V:{}",
                                        row.time.format("%Y-%m-%d %H:%M"),
                                        row.open,
                                        row.high,
                                        row.low,
                                        row.close,
                                        row.volume,
                                    );
                                }
                                if count > 3 {
                                    tracing::info!("       ... ({} more)", count - 3);
                                }
                            } else {
                                tracing::info!("    ⚠️  {} | 0 records returned", interval);
                            }
                        }
                        Err(e) => {
                            tracing::error!("    ❌ {} | error: {}", interval, e);
                        }
                    }

                    // Sleep between intervals to respect rate limits
                    if *interval != "1m" {
                        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                    }
                }

                // 3. Company info test (skip for index tickers)
                tracing::info!("{}", "─".repeat(60));
                if is_index {
                    tracing::info!(
                        "Company Info Test — SKIPPED ({} is an index, use --ticker VCB to test)",
                        ticker,
                    );
                } else {
                    tracing::info!("Company Info Test — ticker={}", ticker);
                    match provider.company_info(&ticker).await {
                        Ok(info) => {
                            tracing::info!(
                                "    ✅ exchange={} | industry={}",
                                info.exchange.unwrap_or_else(|| "-".to_string()),
                                info.industry.unwrap_or_else(|| "-".to_string()),
                            );
                            if let Some(mcap) = info.market_cap {
                                tracing::info!("    market_cap: {:.2}", mcap);
                            }
                            tracing::info!("    shareholders: {}", info.shareholders.len());
                            for sh in info.shareholders.iter().take(3) {
                                tracing::info!("      - {} ({:.2}%)", sh.name, sh.percentage);
                            }
                            if info.shareholders.len() > 3 {
                                tracing::info!("      ... ({} more)", info.shareholders.len() - 3);
                            }
                            tracing::info!("    officers: {}", info.officers.len());
                        }
                        Err(e) => {
                            tracing::error!("    ❌ company_info error: {}", e);
                        }
                    }
                }

                // 4. Financial ratios test (skip for index tickers)
                if !is_index {
                    tracing::info!("{}", "─".repeat(60));
                    tracing::info!("Financial Ratios Test — ticker={}, period=quarter", ticker);
                    match provider.financial_ratios(&ticker, "quarter").await {
                        Ok(ratios) => {
                            tracing::info!("    ✅ {} ratio entries", ratios.len());
                            if !ratios.is_empty() {
                                // Print first entry's yearReport and a few key fields
                                let first = &ratios[0];
                                if let Some(year) = first.get("yearReport") {
                                    tracing::info!("    latest yearReport: {}", year);
                                }
                                for key in &["revenue", "netProfit", "pe", "roe"] {
                                    if let Some(val) = first.get(*key) {
                                        tracing::info!("      {}: {}", key, val);
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            tracing::error!("    ❌ financial_ratios error: {}", e);
                        }
                    }
                }

                // 5. Summary
                tracing::info!("{}", "─".repeat(60));
                tracing::info!("Test complete — ticker={}, clients={}, rate_limit={}/min", ticker, provider.client_count(), rate_limit);
            });
        }
    }
}
