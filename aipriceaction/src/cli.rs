use clap::{Parser, Subcommand};

use crate::db;

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

                let _pool = if !database_url.is_empty() {
                    match db::connect(&database_url).await {
                        Ok(pool) => {
                            tracing::info!("Connected to PostgreSQL, migrations applied");
                            Some(pool)
                        }
                        Err(e) => {
                            tracing::error!("Failed to connect to database: {e}");
                            None
                        }
                    }
                } else {
                    None
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

                match db::connect(&database_url).await {
                    Ok(pool) => {
                        tracing::info!("Connected to PostgreSQL, migrations applied");
                        match db::health_check(&pool).await {
                            Ok(()) => tracing::info!("Database health check: OK"),
                            Err(e) => tracing::error!("Database health check failed: {e}"),
                        }
                    }
                    Err(e) => {
                        tracing::error!("Failed to connect to database: {e}");
                    }
                }
            });
        }
    }
}
