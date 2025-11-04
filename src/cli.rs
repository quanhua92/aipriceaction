use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "aipriceaction")]
#[command(about = "AI Price Action CLI", long_about = None)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Subcommand)]
pub enum Commands {
    /// Import legacy data
    ImportLegacy,
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
        Commands::ImportLegacy => {
            println!("Running import-legacy command...");
        }
        Commands::Pull => {
            println!("Running pull command...");
        }
        Commands::Serve => {
            println!("Running serve command...");
        }
        Commands::Status => {
            println!("Running status command...");
        }
    }
}
