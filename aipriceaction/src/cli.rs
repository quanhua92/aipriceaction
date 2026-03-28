use clap::{Parser, Subcommand};

use crate::db;
use crate::models::interval::Interval;
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
                // TODO: wire up actual server with pool
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
                        // ── Q1: Single ticker, limit only ──
                        let label = "limit-only";
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

                        // ── Q2: Date range (last 30 days) ──
                        let label = "30d-range";
                        let now = chrono::Utc::now();
                        let start_time = now - chrono::Duration::days(30);
                        let start = Instant::now();
                        match q::get_ohlcv_joined_range(
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

                        // ── Q3: Date range (last 1 year) ──
                        let label = "1y-range";
                        let start_time = now - chrono::Duration::days(365);
                        let start = Instant::now();
                        match q::get_ohlcv_joined_range(
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

                        // ── Q4: Full history (no limit) ──
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

                // ── Q5: Multi-ticker simulation (sequential) ──
                tracing::info!("{}", "─".repeat(80));
                let label = "multi-ticker";
                for interval in &interval_list {
                    let start = Instant::now();
                    let mut total_rows = 0usize;
                    for ticker in &ticker_list {
                        match q::get_ohlcv_joined(&pool, &source, ticker, interval, Some(limit)).await {
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
    }
}
