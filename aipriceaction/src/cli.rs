use chrono::{TimeZone, Utc};
use clap::{Parser, Subcommand};

use crate::db;
use crate::models::ohlcv::{IndicatorRow, OhlcvRow};
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

                // Exercise the upsert pipeline with sample data
                let ticker_id = ohlcv::ensure_ticker(&pool, "vn_example", "VCB")
                    .await
                    .expect("Failed to upsert ticker");
                tracing::info!("Ticker VCB (vn_example) → id={ticker_id}");

                let ohlcv_rows = vec![
                    OhlcvRow {
                        ticker_id,
                        interval: "1D".into(),
                        time: Utc.with_ymd_and_hms(2025, 3, 25, 0, 0, 0).unwrap(),
                        open: 60500.0,
                        high: 61200.0,
                        low: 60100.0,
                        close: 60800.0,
                        volume: 1_500_000,
                    },
                    OhlcvRow {
                        ticker_id,
                        interval: "1D".into(),
                        time: Utc.with_ymd_and_hms(2025, 3, 26, 0, 0, 0).unwrap(),
                        open: 60800.0,
                        high: 61500.0,
                        low: 60500.0,
                        close: 61200.0,
                        volume: 1_800_000,
                    },
                    OhlcvRow {
                        ticker_id,
                        interval: "1D".into(),
                        time: Utc.with_ymd_and_hms(2025, 3, 27, 0, 0, 0).unwrap(),
                        open: 61200.0,
                        high: 62000.0,
                        low: 61000.0,
                        close: 61800.0,
                        volume: 2_100_000,
                    },
                ];
                ohlcv::save_ohlcv(&pool, &ohlcv_rows)
                    .await
                    .expect("Failed to save OHLCV");
                tracing::info!("Saved {} OHLCV rows", ohlcv_rows.len());

                let indicator_rows = vec![
                    IndicatorRow {
                        ticker_id,
                        interval: "1D".into(),
                        time: Utc.with_ymd_and_hms(2025, 3, 25, 0, 0, 0).unwrap(),
                        ma10: Some(60400.0),
                        ma20: Some(60000.0),
                        ma50: None,
                        ma100: None,
                        ma200: None,
                        ma10_score: Some(0.66),
                        ma20_score: Some(1.33),
                        ma50_score: None,
                        ma100_score: None,
                        ma200_score: None,
                        close_changed: Some(0.5),
                        volume_changed: Some(10.2),
                        total_money_changed: Some(15000000.0),
                    },
                    IndicatorRow {
                        ticker_id,
                        interval: "1D".into(),
                        time: Utc.with_ymd_and_hms(2025, 3, 26, 0, 0, 0).unwrap(),
                        ma10: Some(60600.0),
                        ma20: Some(60200.0),
                        ma50: None,
                        ma100: None,
                        ma200: None,
                        ma10_score: Some(0.99),
                        ma20_score: Some(1.66),
                        ma50_score: None,
                        ma100_score: None,
                        ma200_score: None,
                        close_changed: Some(0.66),
                        volume_changed: Some(20.0),
                        total_money_changed: Some(36000000.0),
                    },
                    IndicatorRow {
                        ticker_id,
                        interval: "1D".into(),
                        time: Utc.with_ymd_and_hms(2025, 3, 27, 0, 0, 0).unwrap(),
                        ma10: Some(60933.0),
                        ma20: Some(60400.0),
                        ma50: None,
                        ma100: None,
                        ma200: None,
                        ma10_score: Some(1.42),
                        ma20_score: Some(2.32),
                        ma50_score: None,
                        ma100_score: None,
                        ma200_score: None,
                        close_changed: Some(0.98),
                        volume_changed: Some(16.67),
                        total_money_changed: Some(12600000.0),
                    },
                ];
                ohlcv::save_indicators(&pool, &indicator_rows)
                    .await
                    .expect("Failed to save indicators");
                tracing::info!("Saved {} indicator rows", indicator_rows.len());

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

                // List tickers
                let tickers = ohlcv::list_tickers(&pool, "vn_example")
                    .await
                    .expect("Failed to list tickers");
                tracing::info!("Tickers (vn_example): {} found", tickers.len());
                for t in &tickers {
                    tracing::info!("  {t}");
                }

                // Read OHLCV for first ticker
                if let Some(ticker) = tickers.first() {
                    let ohlcv_rows = ohlcv::get_ohlcv(&pool, ticker.id, "1D", Some(10))
                        .await
                        .expect("Failed to get OHLCV");
                    tracing::info!("OHLCV rows for {} (1D): {} rows", ticker.ticker, ohlcv_rows.len());
                    for row in &ohlcv_rows {
                        tracing::info!("  {row}");
                    }

                    let indicators = ohlcv::get_indicators(&pool, ticker.id, "1D", Some(10))
                        .await
                        .expect("Failed to get indicators");
                    tracing::info!("Indicators for {} (1D): {} rows", ticker.ticker, indicators.len());
                    for row in &indicators {
                        tracing::info!("  {row}");
                    }
                }

                // Joined query (20-column CSV format)
                let joined = ohlcv::get_ohlcv_joined(&pool, "vn_example", "VCB", "1D", Some(10))
                    .await
                    .expect("Failed to get joined data");
                tracing::info!("Joined rows (VCB 1D): {} rows", joined.len());
                for row in &joined {
                    tracing::info!("  {row}");
                }
            });
        }
    }
}
