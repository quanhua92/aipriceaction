pub mod api;
pub mod legacy;
pub mod analysis;
pub mod upload;

use crate::models::Mode;
use crate::services::{SharedDataStore, SharedHealthStats};
use crate::utils::get_public_dir;
use axum::{extract::FromRef, routing::{get, post}, Router};
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;
use tower_http::cors::{CorsLayer, Any, AllowOrigin};
use tower_http::services::ServeDir;
use tower_http::compression::{CompressionLayer, predicate::DefaultPredicate};
use tower_http::timeout::TimeoutLayer;
use tower_http::limit::RequestBodyLimitLayer;
use tower_governor::{
    governor::GovernorConfigBuilder,
    GovernorLayer,
    key_extractor::KeyExtractor,
};
use axum::http::{HeaderValue, HeaderName, header};
use axum::response::Response;
use axum::middleware::{self, Next};
use axum::extract::Request;

/// Custom key extractor that prioritizes CF-Connecting-IP header
/// This ensures rate limiting works correctly behind Cloudflare
#[derive(Clone, Copy, Debug)]
pub struct CloudflareKeyExtractor;

impl KeyExtractor for CloudflareKeyExtractor {
    type Key = String;

    fn extract<T>(&self, req: &axum::http::Request<T>) -> Result<Self::Key, tower_governor::GovernorError> {
        // 1. Try CF-Connecting-IP (Cloudflare's original client IP)
        if let Some(cf_ip) = req.headers().get("cf-connecting-ip") {
            if let Ok(ip_str) = cf_ip.to_str() {
                tracing::debug!("Rate limit key from CF-Connecting-IP: {}", ip_str);
                return Ok(ip_str.to_string());
            }
        }

        // 2. Fallback to X-Forwarded-For (first IP in the chain)
        if let Some(forwarded) = req.headers().get("x-forwarded-for") {
            if let Ok(forwarded_str) = forwarded.to_str() {
                let first_ip = forwarded_str.split(',').next().unwrap_or("").trim();
                if !first_ip.is_empty() {
                    tracing::debug!("Rate limit key from X-Forwarded-For: {}", first_ip);
                    return Ok(first_ip.to_string());
                }
            }
        }

        // 3. Fallback to X-Real-IP
        if let Some(real_ip) = req.headers().get("x-real-ip") {
            if let Ok(ip_str) = real_ip.to_str() {
                tracing::debug!("Rate limit key from X-Real-IP: {}", ip_str);
                return Ok(ip_str.to_string());
            }
        }

        // 4. Final fallback: use connection info (direct IP)
        // This should rarely happen when behind Cloudflare
        tracing::warn!("No client IP headers found, using connection info");
        let ip = req
            .extensions()
            .get::<axum::extract::ConnectInfo<SocketAddr>>()
            .map(|ci| ci.0.ip().to_string())
            .unwrap_or_else(|| "unknown".to_string());

        tracing::debug!("Rate limit key from connection info: {}", ip);
        Ok(ip)
    }
}

/// Application state shared across all handlers
#[derive(Clone)]
pub struct AppState {
    pub data_vn: SharedDataStore,
    pub data_crypto: SharedDataStore,
    pub health_stats: SharedHealthStats,
}

impl AppState {
    /// Get DataStore by mode
    pub fn get_data_store(&self, mode: Mode) -> &SharedDataStore {
        match mode {
            Mode::Vn => &self.data_vn,
            Mode::Crypto => &self.data_crypto,
        }
    }
}

// FromRef implementation for health stats
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
    shared_data_vn: SharedDataStore,
    shared_data_crypto: SharedDataStore,
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
        data_vn: shared_data_vn,
        data_crypto: shared_data_crypto,
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
    tracing::info!("  GET /analysis/volume-profile?symbol=VCB&date=2024-01-15");
    tracing::info!("  POST /upload/markdown?session_id=<uuid>&secret=<secret>");
    tracing::info!("  POST /upload/image?session_id=<uuid>&secret=<secret>");
    tracing::info!("  GET /uploads/{{session_id}}/markdown/{{filename}}");
    tracing::info!("  GET /uploads/{{session_id}}/images/{{filename}}");
    tracing::info!("  DELETE /uploads/{{session_id}}/markdown/{{filename}}?secret=<secret>");
    tracing::info!("  DELETE /uploads/{{session_id}}/images/{{filename}}?secret=<secret>");
    tracing::info!("  DELETE /uploads/{{session_id}}?secret=<secret>");
    tracing::info!("  GET /raw/* (legacy GitHub proxy)");
    tracing::info!("  GET /public/* (static files from {})", public_dir.display());

    // Configure rate limiting: 5000 requests/second per IP, burst up to 10000
    // Uses CF-Connecting-IP header to identify real client behind Cloudflare
    let governor_conf = GovernorConfigBuilder::default()
        .per_second(5000)
        .burst_size(10000)
        .key_extractor(CloudflareKeyExtractor)
        .use_headers()
        .finish()
        .unwrap();

    tracing::info!("Security middleware enabled:");
    tracing::info!("  Rate Limit: 5000 req/s per IP, burst 10000 (using CF-Connecting-IP)");
    tracing::info!("  Request Timeout: 30s");
    tracing::info!("  Body Size Limit: 10MB (upload endpoints), 1MB (other endpoints)");
    tracing::info!("  Security Headers: X-Frame-Options, X-Content-Type-Options, X-XSS-Protection");

    // Build upload routes with 10MB body limit
    let upload_routes = Router::new()
        .route("/upload/markdown", post(upload::upload_markdown_handler))
        .route("/upload/image", post(upload::upload_image_handler))
        .route("/uploads/{session_id}/markdown/{filename}", get(upload::serve_markdown_handler).delete(upload::delete_markdown_handler))
        .route("/uploads/{session_id}/images/{filename}", get(upload::serve_image_handler).delete(upload::delete_image_handler))
        .route("/uploads/{session_id}", axum::routing::delete(upload::delete_session_handler))
        .layer(RequestBodyLimitLayer::new(10 * 1024 * 1024)); // 10MB for uploads

    // Build main routes with 1MB body limit
    let main_routes = Router::new()
        .route("/explorer", get(api::explorer_handler))
        .route("/tickers", get(api::get_tickers_handler))
        .route("/health", get(api::health_handler))
        .route("/tickers/group", get(api::get_ticker_groups_handler))
        .nest("/analysis", analysis_routes())
        .route("/raw/{*path}", get(legacy::raw_proxy_handler))
        .layer(RequestBodyLimitLayer::new(1024 * 1024)); // 1MB for regular API

    // Combine routers
    let app = Router::new()
        .merge(upload_routes)
        .merge(main_routes)
        .nest_service("/public", ServeDir::new(public_dir))
        .with_state(app_state)
        // Apply middleware in order (outer → inner)
        .layer(GovernorLayer::new(Arc::new(governor_conf)))
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
        .route("/volume-profile", get(analysis::volume_profile_handler))
}
