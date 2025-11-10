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

        /// Verbose output
        #[arg(long)]
        verbose: bool,
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
            batch_size,
        } => {
            // resume_days is now Option<u32>, passed directly
            commands::pull::run(intervals, full, resume_days, start_date, debug, batch_size);
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
        Commands::RebuildCsv { intervals, tickers, verbose } => {
            if let Err(e) = commands::rebuild_csv::run(intervals, tickers, verbose) {
                eprintln!("❌ CSV rebuild failed: {}", e);
                std::process::exit(1);
            }
        }
    }
}
