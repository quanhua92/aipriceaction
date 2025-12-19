use clap::{Parser, Subcommand};
use std::path::PathBuf;

use crate::commands;

#[derive(Parser)]
#[command(name = "aipriceaction")]
#[command(about = "AI Price Action CLI", long_about = None)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Subcommand)]
pub enum Commands {
    /// Import legacy data from reference project
    ImportLegacy {
        /// Path to the aipriceaction-data directory
        #[arg(short, long)]
        source: Option<PathBuf>,

        /// Intervals to import: all, daily, hourly, minute (comma-separated)
        #[arg(short, long, default_value = "all")]
        intervals: String,

        /// Force reimport: delete existing files and start from scratch
        #[arg(long)]
        force: bool,
    },
    /// Pull latest data from VCI API
    Pull {
        /// Intervals to sync: all, daily, hourly, minute (comma-separated)
        #[arg(short, long, default_value = "all")]
        intervals: String,

        /// Force full download from start-date (disable resume mode)
        #[arg(long)]
        full: bool,

        /// Number of recent days for resume mode (overrides smart defaults)
        /// Smart defaults: daily=3 days, hourly=5 days, minute=2 days
        #[arg(long)]
        resume_days: Option<u32>,

        /// Start date for historical data (YYYY-MM-DD)
        #[arg(long, default_value = "2015-01-05")]
        start_date: String,

        /// Debug mode: use hardcoded test tickers (VNINDEX, VIC, VCB only)
        #[arg(long)]
        debug: bool,

        /// Skip CSV validation for faster startup (same as serve command)
        #[arg(long)]
        no_validation: bool,

        /// Batch size for API calls (default: 10, try 20-50 for faster sync)
        #[arg(long, default_value = "10")]
        batch_size: usize,
    },
    /// Start the server
    Serve {
        /// Port to listen on
        #[arg(short, long, default_value = "3000")]
        port: u16,
    },
    /// Show current status
    Status,
    /// Run health check on market_data CSV files
    Doctor,
    /// Fetch company information and financial data
    Company {
        /// Specific tickers to process (comma-separated)
        #[arg(short, long)]
        tickers: Option<String>,

        /// Force refresh all data (ignore cache)
        #[arg(long)]
        force: bool,

        /// Number of days before cache expires (default: 7)
        #[arg(long)]
        cache_days: Option<i64>,
    },
    /// Rebuild CSV files with updated technical indicators
    RebuildCsv {
        /// Intervals to rebuild: all, daily, hourly, minute (comma-separated)
        #[arg(short, long, default_value = "all")]
        intervals: String,

        /// Specific tickers to rebuild (comma-separated, rebuild all if not specified)
        #[arg(short, long)]
        tickers: Option<String>,

        /// Data directory: market (default), crypto
        #[arg(long)]
        data_dir: Option<String>,

        /// Verbose output
        #[arg(long)]
        verbose: bool,
    },
    /// Pull cryptocurrency data from CryptoCompare API (Phase 2: BTC daily only)
    CryptoPull {
        /// Cryptocurrency symbol (default: BTC)
        #[arg(short, long)]
        symbol: Option<String>,

        /// Interval: daily (1d) - only daily supported in Phase 2
        #[arg(short, long, default_value = "daily")]
        interval: String,

        /// Force full download from 2010-01-01 (default: resume mode)
        #[arg(long)]
        full: bool,
    },
    /// Get cryptocurrency data with optional filtering
    CryptoGet {
        /// Cryptocurrency ticker symbol (required)
        ticker: String,

        /// Data interval: 1D, 1H, 1m (default: 1D)
        #[arg(short, long, default_value = "1D")]
        interval: String,

        /// Start date filter (YYYY-MM-DD format)
        #[arg(long)]
        start_date: Option<String>,

        /// End date filter (YYYY-MM-DD format)
        #[arg(long)]
        end_date: Option<String>,

        /// Maximum number of records to return (default: 200)
        #[arg(short, long, default_value = "200")]
        limit: u32,
    },
    /// Fix CSV files by removing last N rows (with safety features)
    FixCsv {
        /// Data mode: vn (default) or crypto
        #[arg(long, default_value = "vn")]
        mode: String,

        /// Intervals to process: all, daily, hourly, minute (comma-separated)
        #[arg(short, long, default_value = "all")]
        intervals: String,

        /// Number of rows to remove from end of each file
        #[arg(long)]
        rows: usize,

        /// Specific tickers to process (comma-separated, process all if not specified)
        #[arg(short, long)]
        tickers: Option<String>,

        /// Verbose output
        #[arg(long)]
        verbose: bool,

        /// Execute changes (default is dry-run mode)
        #[arg(long)]
        execute: bool,

        /// Backup original files before modification (to separate backup directory)
        #[arg(long)]
        backup: bool,
    },
}

pub fn run() {
    let cli = Cli::parse();

    match cli.command {
        Commands::ImportLegacy { source, intervals, force } => {
            commands::import_legacy::run(source, intervals, force);
        }
        Commands::Pull {
            intervals,
            full,
            resume_days,
            start_date,
            debug,
            no_validation,
            batch_size,
        } => {
            // resume_days is now Option<u32>, passed directly
            commands::pull::run(intervals, full, resume_days, start_date, debug, no_validation, batch_size);
        }
        Commands::Serve { port } => {
            // Serve needs async runtime - create one just for this command
            let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
            rt.block_on(async {
                commands::serve::run(port).await;
            });
        }
        Commands::Status => {
            commands::status::run();
        }
        Commands::Doctor => {
            commands::doctor::run();
        }
        Commands::Company { tickers, force, cache_days } => {
            let ticker_list = tickers.map(|t| {
                t.split(',')
                    .map(|s| s.trim().to_uppercase())
                    .collect::<Vec<String>>()
            });
            commands::company::run(ticker_list, force, cache_days);
        }
        Commands::RebuildCsv { intervals, tickers, data_dir, verbose } => {
            if let Err(e) = commands::rebuild_csv::run(intervals, tickers, data_dir, verbose) {
                eprintln!("❌ CSV rebuild failed: {}", e);
                std::process::exit(1);
            }
        }
        Commands::CryptoPull { symbol, interval, full } => {
            commands::crypto_pull::run(symbol, interval, full);
        }
        Commands::CryptoGet { ticker, interval, start_date, end_date, limit } => {
            commands::crypto_get::run(ticker, interval, start_date, end_date, limit);
        }
        Commands::FixCsv { mode, intervals, rows, tickers, verbose, execute, backup } => {
            if let Err(e) = commands::fix_csv::run(mode, intervals, rows, tickers, verbose, execute, backup) {
                eprintln!("❌ Fix CSV failed: {}", e);
                std::process::exit(1);
            }
        }
    }
}
