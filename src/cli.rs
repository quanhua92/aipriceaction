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
    },
    /// Pull latest data from VCI API
    Pull {
        /// Intervals to sync: all, daily, hourly, minute (comma-separated)
        #[arg(short, long, default_value = "all")]
        intervals: String,

        /// Force full download from start-date (disable resume mode)
        #[arg(long)]
        full: bool,

        /// Number of recent days for resume mode
        #[arg(long, default_value = "30")]
        resume_days: u32,

        /// Start date for historical data (YYYY-MM-DD)
        #[arg(long, default_value = "2015-01-05")]
        start_date: String,
    },
    /// Start the server
    Serve,
    /// Show current status
    Status,
}

pub fn run() {
    let cli = Cli::parse();

    match cli.command {
        Commands::ImportLegacy { source } => {
            commands::import_legacy::run(source);
        }
        Commands::Pull {
            intervals,
            full,
            resume_days,
            start_date,
        } => {
            commands::pull::run(intervals, full, resume_days, start_date);
        }
        Commands::Serve => {
            commands::serve::run();
        }
        Commands::Status => {
            commands::status::run();
        }
    }
}
