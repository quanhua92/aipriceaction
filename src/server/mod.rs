pub mod api;
pub mod legacy;
pub mod analysis;

use crate::services::{SharedDataStore, SharedHealthStats};
use crate::utils::get_public_dir;
use axum::{extract::FromRef, routing::get, Router};
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;
use tower_http::cors::{CorsLayer, Any, AllowOrigin};
use tower_http::services::ServeDir;
use tower_http::compression::{CompressionLayer, predicate::DefaultPredicate};
use tower_http::timeout::TimeoutLayer;
use tower_http::limit::RequestBodyLimitLayer;
use tower_governor::{governor::GovernorConfigBuilder, GovernorLayer};
use axum::http::{HeaderValue, HeaderName, header};
use axum::response::Response;
use axum::middleware::{self, Next};
use axum::extract::Request;

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

/// Middleware to add security headers to all responses
async fn add_security_headers(request: Request, next: Next) -> Response {
    let mut response = next.run(request).await;
    let headers = response.headers_mut();

    // Prevent clickjacking
    headers.insert(
        header::HeaderName::from_static("x-frame-options"),
        header::HeaderValue::from_static("SAMEORIGIN")
    );

    // Prevent MIME type sniffing
    headers.insert(
        header::HeaderName::from_static("x-content-type-options"),
        header::HeaderValue::from_static("nosniff")
    );

    // XSS protection
    headers.insert(
        header::HeaderName::from_static("x-xss-protection"),
        header::HeaderValue::from_static("1; mode=block")
    );

    response
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
        .allow_headers(Any)
        .expose_headers([
            HeaderName::from_static("cf-cache-status"),
            HeaderName::from_static("cf-ray"),
            HeaderName::from_static("x-ratelimit-limit"),
            HeaderName::from_static("x-ratelimit-remaining"),
            HeaderName::from_static("x-ratelimit-reset"),
        ]);

    let public_dir = get_public_dir();
    tracing::info!("Using public directory: {}", public_dir.display());

    tracing::info!("Registering routes:");
    tracing::info!("  GET /explorer (API Explorer UI)");
    tracing::info!("  GET /tickers?symbol=VCB&interval=1D&start_date=2024-01-01");
    tracing::info!("  GET /health");
    tracing::info!("  GET /tickers/group");
    tracing::info!("  GET /analysis/top-performers?sort_by=close_changed&limit=10");
    tracing::info!("  GET /analysis/ma-scores-by-sector?ma_period=20");
    tracing::info!("  GET /raw/* (legacy GitHub proxy)");
    tracing::info!("  GET /public/* (static files from {})", public_dir.display());

    // Configure rate limiting: 500 requests/second, burst up to 1000
    let governor_conf = GovernorConfigBuilder::default()
        .per_second(500)
        .burst_size(1000)
        .use_headers()
        .finish()
        .unwrap();

    tracing::info!("Security middleware enabled:");
    tracing::info!("  Rate Limit: 500 req/s, burst 1000");
    tracing::info!("  Request Timeout: 30s");
    tracing::info!("  Body Size Limit: 1MB");
    tracing::info!("  Security Headers: X-Frame-Options, X-Content-Type-Options, X-XSS-Protection");

    // Build router with routes
    let app = Router::new()
        .route("/explorer", get(api::explorer_handler))
        .route("/tickers", get(api::get_tickers_handler))
        .route("/health", get(api::health_handler))
        .route("/tickers/group", get(api::get_ticker_groups_handler))
        .nest("/analysis", analysis_routes())
        .route("/raw/{*path}", get(legacy::raw_proxy_handler))
        .nest_service("/public", ServeDir::new(public_dir))
        .with_state(app_state)
        // Apply middleware in order (outer → inner)
        .layer(GovernorLayer::new(Arc::new(governor_conf)))
        .layer(RequestBodyLimitLayer::new(1024 * 1024))
        .layer(TimeoutLayer::new(Duration::from_secs(30)))
        .layer(middleware::from_fn(add_security_headers))
        .layer(
            CompressionLayer::new()
                .gzip(true)
                .deflate(true)
                .br(true)
                .compress_when(DefaultPredicate::new())
        )
        .layer(cors);

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
