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
    /// Pull latest data
    Pull,
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
        Commands::Pull => {
            commands::pull::run();
        }
        Commands::Serve => {
            commands::serve::run();
        }
        Commands::Status => {
            commands::status::run();
        }
    }
}
