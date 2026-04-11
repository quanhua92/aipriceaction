use clap::{Parser, Subcommand};

use crate::db;
use crate::models::interval::Interval;
use crate::providers::binance::BinanceProvider;
use crate::providers::vci::VciProvider;
use crate::providers::yahoo::YahooProvider;
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
    /// Benchmark critical API query paths directly against the database
    TestPerf,
    /// Test Binance provider connectivity and data fetching
    TestBinance {
        /// Ticker symbol to test (default: BTCUSDT)
        #[arg(long, default_value = "BTCUSDT")]
        ticker: String,
        /// Interval to test: 1d, 1h, 1m, or all (default: all)
        #[arg(long, default_value = "all")]
        interval: String,
        /// Number of data points to return (default: 100)
        #[arg(long, default_value = "100")]
        limit: u32,
        /// Rate limit per client (requests per minute, default: 120)
        #[arg(long, default_value = "120")]
        rate_limit: u32,
    },
    /// Test Yahoo Finance provider connectivity and data fetching
    TestYahoo {
        /// Ticker symbol to test (default: AAPL)
        #[arg(long, default_value = "AAPL")]
        ticker: String,
        /// Rate limit per client (requests per minute, default: 60)
        #[arg(long, default_value = "60")]
        rate_limit: u32,
    },
    /// Test SOCKS5 proxy connectivity against Yahoo Finance API
    TestProxy,
    /// Test Redis TimeSeries connectivity and commands
    TestRedis {
        /// Ticker symbol to test with (default: VNINDEX)
        #[arg(long, default_value = "VNINDEX")]
        ticker: String,
    },
    /// Fetch company info and financial ratios for VN tickers from VCI
    GenerateCompanyInfo {
        /// Optional: query a single ticker (e.g. VCB)
        #[arg(long)]
        ticker: Option<String>,
        /// Rate limit per VCI client (default: 30)
        #[arg(long, default_value = "30")]
        rate_limit: u32,
        /// Save fetched data to company_info.json
        #[arg(long)]
        save: bool,
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

                // Connect to Redis (optional — degrades gracefully if REDIS_URL is not set)
                let redis_client = crate::redis::connect().await;

                // Spawn VCI data workers if enabled
                let vci_workers_enabled = std::env::var("VCI_WORKERS")
                    .map(|v| v == "true" || v == "1")
                    .unwrap_or(true);

                if vci_workers_enabled {
                    tracing::info!("VCI workers enabled");

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::vci_daily::run(pool_clone, redis_clone).await;
                    });

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::vci_hourly::run(pool_clone, redis_clone).await;
                    });

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::vci_minute::run(pool_clone, redis_clone).await;
                    });

                    // Dividend worker has its own toggle
                    let dividend_worker_enabled = std::env::var("VCI_DIVIDEND_WORKER")
                        .map(|v| v == "true" || v == "1")
                        .unwrap_or(true);

                    if dividend_worker_enabled {
                        let pool_clone = pool.clone();
                        let redis_clone = redis_client.clone();
                        tokio::spawn(async move {
                            crate::workers::vci_dividend::run(pool_clone, redis_clone).await;
                        });
                    } else {
                        tracing::info!("VCI dividend worker disabled (set VCI_DIVIDEND_WORKER=true to enable)");
                    }
                } else {
                    tracing::info!("VCI workers disabled (set VCI_WORKERS=true to enable)");
                }

                // Spawn Binance data workers if enabled
                let binance_workers_enabled = std::env::var("BINANCE_WORKERS")
                    .map(|v| v == "true" || v == "1")
                    .unwrap_or(false);

                if binance_workers_enabled {
                    tracing::info!("BINANCE_WORKERS=true — spawning daily/hourly/minute crypto workers");

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::binance_bootstrap::run(pool_clone, redis_clone).await;
                    });

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::binance_daily::run(pool_clone, redis_clone).await;
                    });

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::binance_hourly::run(pool_clone, redis_clone).await;
                    });

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::binance_minute::run(pool_clone, redis_clone).await;
                    });
                } else {
                    tracing::info!("BINANCE_WORKERS=false — Binance crypto workers not started");
                }

                // Spawn Yahoo Finance workers if enabled
                let yahoo_workers_enabled = std::env::var("YAHOO_WORKERS")
                    .map(|v| v == "true" || v == "1")
                    .unwrap_or(false);

                if yahoo_workers_enabled {
                    tracing::info!("YAHOO_WORKERS=true — spawning bootstrap/daily/hourly/minute yahoo workers");

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::yahoo_bootstrap::run(pool_clone, redis_clone).await;
                    });

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::yahoo_daily::run(pool_clone, redis_clone).await;
                    });

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::yahoo_hourly::run(pool_clone, redis_clone).await;
                    });

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::yahoo_minute::run(pool_clone, redis_clone).await;
                    });
                } else {
                    tracing::info!("YAHOO_WORKERS=false — Yahoo Finance workers not started");
                }

                // Spawn SJC gold price workers if enabled
                let sjc_workers_enabled = std::env::var("SJC_WORKERS")
                    .map(|v| v == "true" || v == "1")
                    .unwrap_or(false);

                if sjc_workers_enabled {
                    tracing::info!("SJC_WORKERS=true — spawning bootstrap/daily SJC gold workers");

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::sjc_bootstrap::run(pool_clone, redis_clone).await;
                    });

                    let pool_clone = pool.clone();
                    let redis_clone = redis_client.clone();
                    tokio::spawn(async move {
                        crate::workers::sjc_daily::run(pool_clone, redis_clone).await;
                    });
                } else {
                    tracing::info!("SJC_WORKERS=false — SJC gold workers not started");
                }

                // Spawn Redis TS backfill worker if enabled
                let redis_workers_enabled = std::env::var("REDIS_WORKERS")
                    .map(|v| v == "true" || v == "1")
                    .unwrap_or(false);

                if redis_workers_enabled {
                    if let Some(client) = redis_client.clone() {
                        tracing::info!("REDIS_WORKERS=true — spawning Redis ZSET backfill worker (semaphore-gated concurrency)");
                        let pool_clone = pool.clone();
                        tokio::spawn(async move {
                            crate::workers::redis_worker::run(pool_clone, client).await;
                        });
                    } else {
                        tracing::warn!("REDIS_WORKERS=true but Redis is not connected (REDIS_URL not set)");
                    }
                } else {
                    tracing::info!("REDIS_WORKERS=false — Redis ZSET backfill worker not started");
                }

                let (app, health_snapshot) = crate::server::create_app(pool.clone(), redis_client.clone());

                // Spawn health-stats worker (always enabled — lightweight)
                {
                    let pool_clone = pool.clone();
                    tokio::spawn(async move {
                        crate::workers::health::run(pool_clone, health_snapshot).await;
                    });
                }

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

                // Total OHLCV rows (all tickers)
                let total_ohlcv = ohlcv::count_ohlcv(&pool, source, None, None)
                    .await
                    .expect("Failed to count OHLCV");

                tracing::info!("Source: {source} | Tickers: {ticker_count} | OHLCV: {total_ohlcv}");

                // Per-interval totals
                for iv in &["1D", "1h", "1m"] {
                    let ohlcv_count = ohlcv::count_ohlcv(&pool, source, None, Some(iv))
                        .await
                        .expect("Failed to count OHLCV");
                    if ohlcv_count > 0 {
                        tracing::info!("  {iv}: {ohlcv_count} OHLCV");
                    }
                }

                // VNINDEX breakdown
                let vnindex_ohlcv = ohlcv::count_ohlcv(&pool, source, Some("VNINDEX"), None)
                    .await
                    .expect("Failed to count VNINDEX OHLCV");
                tracing::info!("VNINDEX: {vnindex_ohlcv} OHLCV");
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
                let is_index = crate::constants::vci_worker::INDEX_TICKERS
                    .iter()
                    .any(|t| t.eq_ignore_ascii_case(&ticker.as_str()));

                // 2. OHLCV test for each interval
                tracing::info!("{}", "─".repeat(60));
                tracing::info!("OHLCV Test — ticker={}, count_back={}", ticker, count_back);

                for interval in &["1D", "1H", "1m"] {
                    tracing::info!("  Fetching {} ...", interval);
                    match provider.get_history(&ticker, interval, count_back, None).await {
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
        Commands::TestPerf => {
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

                use crate::queries::ohlcv as q;
                use chrono::Utc;
                use std::time::Instant;

                struct BenchResult {
                    label: String,
                    rows: usize,
                    ms: u128,
                }

                let mut all_results: Vec<BenchResult> = Vec::new();
                let separator = "────";
                let slow_threshold_ms: u128 = 2000;

                let print_bench = |results: &[BenchResult], width: usize| {
                    for r in results {
                        let slow = if r.ms >= slow_threshold_ms {
                            format!("  ← SLOW")
                        } else {
                            String::new()
                        };
                        tracing::info!("  {:<width$} | {:>6} rows | {:>6} ms{}", r.label, r.rows, r.ms, slow, width = width);
                    }
                };

                // ── Section 1: VN single ticker (VCB) ──
                {
                    let src = "vn";
                    let tk = "VCB";
                    tracing::info!("{separator} VN: single ticker ({tk}) {separator}");
                    let mut section_results: Vec<BenchResult> = Vec::new();

                    // #1 vn VCB 1D
                    {
                        let label = format!("vn {tk} 1D");
                        let start = Instant::now();
                        match q::get_ohlcv_joined(&pool, src, tk, "1D", Some(100)).await {
                            Ok(rows) => section_results.push(BenchResult { label, rows: rows.len(), ms: start.elapsed().as_millis() }),
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #2 vn VCB 1H
                    {
                        let label = format!("vn {tk} 1H");
                        let start = Instant::now();
                        match q::get_ohlcv_joined(&pool, src, tk, "1H", Some(100)).await {
                            Ok(rows) => section_results.push(BenchResult { label, rows: rows.len(), ms: start.elapsed().as_millis() }),
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #3 vn VCB 1m
                    {
                        let label = format!("vn {tk} 1m");
                        let start = Instant::now();
                        match q::get_ohlcv_joined(&pool, src, tk, "1m", Some(100)).await {
                            Ok(rows) => section_results.push(BenchResult { label, rows: rows.len(), ms: start.elapsed().as_millis() }),
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #4 vn VCB 1m limit=1000
                    {
                        let label = format!("vn {tk} 1m limit=1000");
                        let start = Instant::now();
                        match q::get_ohlcv_joined(&pool, src, tk, "1m", Some(1000)).await {
                            Ok(rows) => section_results.push(BenchResult { label, rows: rows.len(), ms: start.elapsed().as_millis() }),
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }

                    print_bench(&section_results, 28);
                    all_results.extend(section_results);
                }

                // ── Section 2: VN batch (get_ohlcv_joined_batch) ──
                {
                    let src = "vn";
                    tracing::info!("{separator} VN: batch (get_ohlcv_joined_batch) {separator}");
                    let mut section_results: Vec<BenchResult> = Vec::new();

                    // #5 vn batch 1D
                    {
                        let label = "vn batch 1D";
                        let start = Instant::now();
                        match q::get_ohlcv_joined_batch(&pool, src, &["VCB".into()], "1D", Some(100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #6 vn batch 1H
                    {
                        let label = "vn batch 1H";
                        let start = Instant::now();
                        match q::get_ohlcv_joined_batch(&pool, src, &["VCB".into()], "1H", Some(100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #7 vn batch 1m
                    {
                        let label = "vn batch 1m";
                        let start = Instant::now();
                        match q::get_ohlcv_joined_batch(&pool, src, &["VCB".into()], "1m", Some(100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #8 vn batch 1m multi
                    {
                        let label = "vn batch 1m multi";
                        let start = Instant::now();
                        match q::get_ohlcv_joined_batch(&pool, src, &["VCB".into(), "FPT".into()], "1m", Some(100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #9 vn batch 1m start_date
                    {
                        let label = "vn batch 1m start_date";
                        let start_time = Some(Utc::now() - chrono::Duration::days(30));
                        let start = Instant::now();
                        match q::get_ohlcv_joined_batch(&pool, src, &["VCB".into()], "1m", Some(100), start_time, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }

                    print_bench(&section_results, 28);
                    all_results.extend(section_results);
                }

                // ── Section 3: VN aggregated (get_ohlcv_batch_raw) ──
                {
                    let src = "vn";
                    tracing::info!("{separator} VN: aggregated (get_ohlcv_batch_raw from 1m) {separator}");
                    let mut section_results: Vec<BenchResult> = Vec::new();

                    // #10 vn agg 5m
                    {
                        let label = "vn agg 5m";
                        let start = Instant::now();
                        match q::get_ohlcv_batch_raw(&pool, src, &["VCB".into()], "1m", Some(5100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #11 vn agg 15m
                    {
                        let label = "vn agg 15m";
                        let start = Instant::now();
                        match q::get_ohlcv_batch_raw(&pool, src, &["VCB".into()], "1m", Some(5100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #12 vn agg 30m
                    {
                        let label = "vn agg 30m";
                        let start = Instant::now();
                        match q::get_ohlcv_batch_raw(&pool, src, &["VCB".into()], "1m", Some(5100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #13 vn agg 1W
                    {
                        let label = "vn agg 1W";
                        let start = Instant::now();
                        match q::get_ohlcv_batch_raw(&pool, src, &["VCB".into()], "1D", Some(5100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #14 vn agg 1M
                    {
                        let label = "vn agg 1M";
                        let start = Instant::now();
                        match q::get_ohlcv_batch_raw(&pool, src, &["VCB".into()], "1D", Some(5100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }

                    print_bench(&section_results, 28);
                    all_results.extend(section_results);
                }

                // ── Section 4: Crypto single ticker (BTCUSDT) ──
                {
                    let src = "crypto";
                    let tk = "BTCUSDT";
                    tracing::info!("{separator} Crypto: single ticker ({tk}) {separator}");
                    let mut section_results: Vec<BenchResult> = Vec::new();

                    // #15 crypto BTCUSDT 1D
                    {
                        let label = format!("crypto {tk} 1D");
                        let start = Instant::now();
                        match q::get_ohlcv_joined(&pool, src, tk, "1D", Some(100)).await {
                            Ok(rows) => section_results.push(BenchResult { label, rows: rows.len(), ms: start.elapsed().as_millis() }),
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #16 crypto BTCUSDT 1H
                    {
                        let label = format!("crypto {tk} 1H");
                        let start = Instant::now();
                        match q::get_ohlcv_joined(&pool, src, tk, "1H", Some(100)).await {
                            Ok(rows) => section_results.push(BenchResult { label, rows: rows.len(), ms: start.elapsed().as_millis() }),
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #17 crypto BTCUSDT 1m
                    {
                        let label = format!("crypto {tk} 1m");
                        let start = Instant::now();
                        match q::get_ohlcv_joined(&pool, src, tk, "1m", Some(100)).await {
                            Ok(rows) => section_results.push(BenchResult { label, rows: rows.len(), ms: start.elapsed().as_millis() }),
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #18 crypto BTCUSDT 1m limit=1000
                    {
                        let label = format!("crypto {tk} 1m limit=1000");
                        let start = Instant::now();
                        match q::get_ohlcv_joined(&pool, src, tk, "1m", Some(1000)).await {
                            Ok(rows) => section_results.push(BenchResult { label, rows: rows.len(), ms: start.elapsed().as_millis() }),
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }

                    print_bench(&section_results, 28);
                    all_results.extend(section_results);
                }

                // ── Section 5: Crypto batch (get_ohlcv_joined_batch) ──
                {
                    let src = "crypto";
                    tracing::info!("{separator} Crypto: batch (get_ohlcv_joined_batch) {separator}");
                    let mut section_results: Vec<BenchResult> = Vec::new();

                    // #19 crypto batch 1D
                    {
                        let label = "crypto batch 1D";
                        let start = Instant::now();
                        match q::get_ohlcv_joined_batch(&pool, src, &["BTCUSDT".into()], "1D", Some(100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #20 crypto batch 1H
                    {
                        let label = "crypto batch 1H";
                        let start = Instant::now();
                        match q::get_ohlcv_joined_batch(&pool, src, &["BTCUSDT".into()], "1H", Some(100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #21 crypto batch 1m
                    {
                        let label = "crypto batch 1m";
                        let start = Instant::now();
                        match q::get_ohlcv_joined_batch(&pool, src, &["BTCUSDT".into()], "1m", Some(100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #22 crypto batch 1m multi
                    {
                        let label = "crypto batch 1m multi";
                        let start = Instant::now();
                        match q::get_ohlcv_joined_batch(&pool, src, &["BTCUSDT".into(), "ETHUSDT".into()], "1m", Some(100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #23 crypto batch 1m start_date
                    {
                        let label = "crypto batch 1m start_date";
                        let start_time = Some(Utc::now() - chrono::Duration::days(30));
                        let start = Instant::now();
                        match q::get_ohlcv_joined_batch(&pool, src, &["BTCUSDT".into()], "1m", Some(100), start_time, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }

                    print_bench(&section_results, 28);
                    all_results.extend(section_results);
                }

                // ── Section 6: Crypto aggregated (get_ohlcv_batch_raw) ──
                {
                    let src = "crypto";
                    tracing::info!("{separator} Crypto: aggregated (get_ohlcv_batch_raw from 1m) {separator}");
                    let mut section_results: Vec<BenchResult> = Vec::new();

                    // #24 crypto agg 5m
                    {
                        let label = "crypto agg 5m";
                        let start = Instant::now();
                        match q::get_ohlcv_batch_raw(&pool, src, &["BTCUSDT".into()], "1m", Some(5100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #25 crypto agg 15m
                    {
                        let label = "crypto agg 15m";
                        let start = Instant::now();
                        match q::get_ohlcv_batch_raw(&pool, src, &["BTCUSDT".into()], "1m", Some(5100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #26 crypto agg 30m
                    {
                        let label = "crypto agg 30m";
                        let start = Instant::now();
                        match q::get_ohlcv_batch_raw(&pool, src, &["BTCUSDT".into()], "1m", Some(5100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #27 crypto agg 1W
                    {
                        let label = "crypto agg 1W";
                        let start = Instant::now();
                        match q::get_ohlcv_batch_raw(&pool, src, &["BTCUSDT".into()], "1D", Some(5100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }
                    // #28 crypto agg 1M
                    {
                        let label = "crypto agg 1M";
                        let start = Instant::now();
                        match q::get_ohlcv_batch_raw(&pool, src, &["BTCUSDT".into()], "1D", Some(5100), None, None).await {
                            Ok(map) => {
                                let total: usize = map.values().map(|v| v.len()).sum();
                                section_results.push(BenchResult { label: label.into(), rows: total, ms: start.elapsed().as_millis() });
                            }
                            Err(e) => tracing::warn!("  {label} | ERROR: {e}"),
                        }
                    }

                    print_bench(&section_results, 28);
                    all_results.extend(section_results);
                }

                // ── Summary ──
                tracing::info!("{separator} Summary {separator}");
                if all_results.is_empty() {
                    tracing::info!("  No queries executed successfully.");
                    return;
                }
                let max_result = all_results.iter().max_by_key(|r| r.ms).unwrap();
                let avg_ms = all_results.iter().map(|r| r.ms).sum::<u128>() / all_results.len() as u128;
                let slow_count = all_results.iter().filter(|r| r.ms >= slow_threshold_ms).count();
                tracing::info!(
                    "  Queries: {} | Avg: {} ms | Max: {} ms ({}) | Slow (≥{}ms): {}",
                    all_results.len(),
                    avg_ms,
                    max_result.ms,
                    max_result.label,
                    slow_threshold_ms,
                    slow_count,
                );
            });
        }
        Commands::TestBinance {
            ticker,
            interval,
            limit,
            rate_limit,
        } => {
            let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
            rt.block_on(async {
                // 1. Create BinanceProvider
                tracing::info!("Initializing Binance provider (rate_limit={}/min per client)...", rate_limit);
                let provider = match BinanceProvider::new(rate_limit) {
                    Ok(p) => p,
                    Err(e) => {
                        tracing::error!("Failed to create Binance provider: {e}");
                        return;
                    }
                };
                tracing::info!("Connected with {} client(s) (1 vision + {} API)", 1, provider.client_count().saturating_sub(1));

                // 2. Determine intervals to test
                let intervals: Vec<&str> = if interval == "all" {
                    vec!["1d", "1h", "1m"]
                } else {
                    vec![interval.as_str()]
                };

                // 3. Fetch data for each interval
                tracing::info!("{}", "─".repeat(60));
                tracing::info!("OHLCV Test — ticker={}, limit={}", ticker, limit);

                for iv in &intervals {
                    tracing::info!("  Fetching {} ...", iv);
                    match provider.get_history(&ticker, iv, limit).await {
                        Ok(data) => {
                            let count = data.len();
                            if count > 0 {
                                let first = &data[0];
                                let last = &data[count - 1];
                                tracing::info!(
                                    "    ✅ {} | {} records | {} → {}",
                                    iv,
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
                                tracing::info!("    ⚠️  {} | 0 records returned", iv);
                            }
                        }
                        Err(e) => {
                            tracing::error!("    ❌ {} | error: {}", iv, e);
                        }
                    }
                }

                // 4. Summary
                tracing::info!("{}", "─".repeat(60));
                tracing::info!("Test complete — ticker={}, clients={}, rate_limit={}/min", ticker, provider.client_count(), rate_limit);
            });
        }
        Commands::TestYahoo { ticker, rate_limit } => {
            let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
            rt.block_on(async {
                // 1. Create YahooProvider
                tracing::info!("Initializing Yahoo Finance provider (rate_limit={}/min per client)...", rate_limit);
                let provider = match YahooProvider::new(rate_limit) {
                    Ok(p) => p,
                    Err(e) => {
                        tracing::error!("Failed to create Yahoo provider: {e}");
                        return;
                    }
                };
                tracing::info!("Connected with {} client(s)", provider.client_count());

                // 2. OHLCV test for each interval
                tracing::info!("{}", "─".repeat(60));
                tracing::info!("OHLCV Test — ticker={}", ticker);

                for (interval, range) in &[("1d", "1mo"), ("1h", "5d"), ("1m", "1d")] {
                    tracing::info!("  Fetching {} (range={}) ...", interval, range);
                    match provider.get_history(&ticker, interval, range).await {
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
                }

                // 3. Search ticker test
                tracing::info!("{}", "─".repeat(60));
                tracing::info!("Search Test — query=\"{}\"", ticker);
                match provider.search_ticker(&ticker).await {
                    Ok(result) => {
                        tracing::info!("    ✅ {} result(s)", result.count);
                        for item in result.quotes.iter().take(5) {
                            tracing::info!(
                                "       {} | {} | {} | {}",
                                item.symbol,
                                item.exchange,
                                item.short_name,
                                item.quote_type,
                            );
                        }
                        if result.quotes.len() > 5 {
                            tracing::info!("       ... ({} more)", result.quotes.len() - 5);
                        }
                    }
                    Err(e) => {
                        tracing::error!("    ❌ search_ticker error: {}", e);
                    }
                }

                // 4. Summary
                tracing::info!("{}", "─".repeat(60));
                tracing::info!("Test complete — ticker={}, clients={}, rate_limit={}/min", ticker, provider.client_count(), rate_limit);
            });
        }
        Commands::TestProxy => {
            crate::test_proxy::run();
        }
        Commands::TestRedis { ticker } => {
            crate::test_redis::run(ticker);
        }
        Commands::GenerateCompanyInfo { ticker, rate_limit, save } => {
            let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
            rt.block_on(async {
                crate::generate_company_info::run(ticker, rate_limit, save).await;
            });
        }
    }
}
