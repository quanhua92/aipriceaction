pub mod api;
pub mod legacy;
pub mod analysis;

use crate::services::{SharedDataStore, SharedHealthStats};
use crate::utils::get_public_dir;
use axum::{extract::FromRef, routing::get, Router};
use std::net::SocketAddr;
use tower_http::cors::{CorsLayer, Any, AllowOrigin};
use tower_http::services::ServeDir;
use axum::http::HeaderValue;

/// Application state shared across all handlers
#[derive(Clone)]
pub struct AppState {
    pub data: SharedDataStore,
    pub health_stats: SharedHealthStats,
}

// FromRef implementations to extract specific state components
impl FromRef<AppState> for SharedDataStore {
    fn from_ref(app_state: &AppState) -> SharedDataStore {
        app_state.data.clone()
    }
}

impl FromRef<AppState> for SharedHealthStats {
    fn from_ref(app_state: &AppState) -> SharedHealthStats {
        app_state.health_stats.clone()
    }
}

/// Start the axum server
pub async fn serve(
    shared_data: SharedDataStore,
    shared_health_stats: SharedHealthStats,
    port: u16,
) -> Result<(), Box<dyn std::error::Error>> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"))
        )
        .with_target(false)
        .init();

    tracing::info!("Starting aipriceaction server");

    let app_state = AppState {
        data: shared_data,
        health_stats: shared_health_stats,
    };

    // Configure CORS - allow all subdomains of aipriceaction.com plus localhost
    let cors = CorsLayer::new()
        .allow_origin(AllowOrigin::predicate(
            |origin: &HeaderValue, _request_parts: &_| {
                if let Ok(origin_str) = origin.to_str() {
                    // Allow all subdomains of aipriceaction.com (including the main domain)
                    if origin_str.ends_with(".aipriceaction.com")
                        || origin_str == "https://aipriceaction.com"
                        || origin_str == "http://aipriceaction.com" {
                        return true;
                    }

                    // Allow localhost and local network for development
                    if origin_str.starts_with("http://localhost:")
                        || origin_str.starts_with("http://127.0.0.1:")
                        || origin_str.starts_with("http://100.121.116.69:")
                        || origin_str.starts_with("http://192.168.1.13:") {
                        return true;
                    }
                }
                false
            }
        ))
        .allow_methods([
            axum::http::Method::GET,
            axum::http::Method::POST,
            axum::http::Method::OPTIONS,
        ])
        .allow_headers(Any);

    let public_dir = get_public_dir();
    tracing::info!("Using public directory: {}", public_dir.display());

    tracing::info!("Registering routes:");
    tracing::info!("  GET /explorer (API Explorer UI)");
    tracing::info!("  GET /tickers?symbol=VCB&interval=1D&start_date=2024-01-01");
    tracing::info!("  GET /health");
    tracing::info!("  GET /tickers/group");
    tracing::info!("  GET /analysis/top-performers?sort_by=close_change_percent&limit=10");
    tracing::info!("  GET /analysis/ma-scores-by-sector?ma_period=20");
    tracing::info!("  GET /raw/* (legacy GitHub proxy)");
    tracing::info!("  GET /public/* (static files from {})", public_dir.display());

    // Build router with routes
    let app = Router::new()
        .route("/explorer", get(api::explorer_handler))
        .route("/tickers", get(api::get_tickers_handler))
        .route("/health", get(api::health_handler))
        .route("/tickers/group", get(api::get_ticker_groups_handler))
        .nest("/analysis", analysis_routes())
        .route("/raw/{*path}", get(legacy::raw_proxy_handler))
        .nest_service("/public", ServeDir::new(public_dir))
        .layer(cors)
        .with_state(app_state);

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    tracing::info!(%addr, "Server listening");

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await?;

    Ok(())
}

/// Analysis routes configuration
fn analysis_routes() -> Router<AppState> {
    Router::new()
        .route("/top-performers", get(analysis::top_performers_handler))
        .route("/ma-scores-by-sector", get(analysis::ma_scores_by_sector_handler))
}
