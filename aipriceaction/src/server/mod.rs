mod api;
mod cache;
pub mod types;
pub mod analysis;
pub mod legacy;
pub mod upload;

use sqlx::PgPool;
use std::sync::Arc;
use tower_http::compression::CompressionLayer;
use tower_http::cors::CorsLayer;
use tower_http::services::ServeDir;
use tower_http::timeout::TimeoutLayer;
use tower_http::limit::RequestBodyLimitLayer;
use axum::http::{HeaderValue, HeaderName, Method};
use axum::response::Response;
use axum::middleware::{self, Next};
use axum::extract::Request;
use std::time::Duration;

pub struct AppState {
    pub pool: PgPool,
    pub started_at: std::time::Instant,
    pub tickers_cache: Arc<tokio::sync::RwLock<cache::TickersCache>>,
}

#[derive(sqlx::FromRow)]
pub struct HealthRow {
    pub source: String,
    pub ticker_count: i64,
    pub active_tickers: i64,
    pub daily_records: i64,
    pub hourly_records: i64,
    pub minute_records: i64,
}

/// Middleware to add security headers to all responses
async fn add_security_headers(request: Request, next: Next) -> Response {
    let mut response = next.run(request).await;
    let headers = response.headers_mut();

    headers.insert(
        HeaderName::from_static("x-frame-options"),
        HeaderValue::from_static("SAMEORIGIN"),
    );
    headers.insert(
        HeaderName::from_static("x-content-type-options"),
        HeaderValue::from_static("nosniff"),
    );
    headers.insert(
        HeaderName::from_static("x-xss-protection"),
        HeaderValue::from_static("1; mode=block"),
    );

    response
}

/// Middleware to add cache control headers to static files
async fn add_cache_headers(request: Request, next: Next) -> Response {
    let path = request.uri().path().to_string();
    let mut response = next.run(request).await;

    let cache_control = if path.ends_with(".js") {
        Some("max-age=300, public")
    } else if path.ends_with(".css") {
        Some("max-age=3600, public")
    } else if path.ends_with(".html") {
        Some("no-cache, no-store, must-revalidate")
    } else if path.ends_with(".png") || path.ends_with(".jpg") || path.ends_with(".jpeg")
           || path.ends_with(".gif") || path.ends_with(".webp") || path.ends_with(".svg") {
        Some("max-age=86400, public")
    } else if path.ends_with(".ico") {
        Some("max-age=86400, public")
    } else {
        Some("max-age=3600, public")
    };

    if let Some(cache_value) = cache_control {
        response.headers_mut().insert(
            HeaderName::from_static("cache-control"),
            HeaderValue::from_str(cache_value).unwrap(),
        );
    }

    response
}

#[allow(deprecated)]
pub fn create_app(pool: PgPool) -> axum::Router {
    let tickers_cache = cache::TickersCache::new(
        crate::constants::api::CACHE_MAX_ENTRIES,
        Duration::from_secs(crate::constants::api::CACHE_TTL_SECS),
    );
    let tickers_cache = Arc::new(tokio::sync::RwLock::new(tickers_cache));

    cache::TickersCache::spawn_sweep_task(
        tickers_cache.clone(),
        Duration::from_secs(crate::constants::api::CACHE_TTL_SECS),
    );

    let state = Arc::new(AppState {
        pool,
        started_at: std::time::Instant::now(),
        tickers_cache,
    });

    // Upload routes with 10MB body limit
    let upload_routes = axum::Router::new()
        .route("/upload/markdown", axum::routing::post(upload::upload_markdown_handler))
        .route("/upload/image", axum::routing::post(upload::upload_image_handler))
        .route(
            "/uploads/{session_id}/markdown/{filename}",
            axum::routing::get(upload::serve_markdown_handler)
                .delete(upload::delete_markdown_handler),
        )
        .route(
            "/uploads/{session_id}/images/{filename}",
            axum::routing::get(upload::serve_image_handler)
                .delete(upload::delete_image_handler),
        )
        .route(
            "/uploads/{session_id}",
            axum::routing::delete(upload::delete_session_handler),
        )
        .layer(RequestBodyLimitLayer::new(10 * 1024 * 1024));

    // Main routes with 1MB body limit
    let main_routes = axum::Router::new()
        .route("/explorer", axum::routing::get(api::explorer_handler))
        .route("/tickers", axum::routing::get(api::tickers))
        .route("/health", axum::routing::get(api::health))
        .route("/tickers/group", axum::routing::get(api::tickers_group))
        .route("/tickers/name", axum::routing::get(api::tickers_name))
        .route("/tickers/info", axum::routing::get(api::tickers_info))
        .nest("/analysis", analysis_routes())
        .route("/raw/{*path}", axum::routing::get(legacy::raw_proxy_handler))
        .layer(RequestBodyLimitLayer::new(1024 * 1024));

    // Public static files with cache headers
    let public_dir = std::path::Path::new("public");
    let public_routes = axum::Router::new()
        .nest_service("/public", ServeDir::new(public_dir).precompressed_br())
        .layer(middleware::from_fn(add_cache_headers));

    axum::Router::new()
        .merge(upload_routes)
        .merge(main_routes)
        .merge(public_routes)
        .with_state(state)
        .layer(TimeoutLayer::new(Duration::from_secs(180)))
        .layer(middleware::from_fn(add_security_headers))
        .layer(CompressionLayer::new())
        .layer(build_cors_layer())
}

fn build_cors_layer() -> CorsLayer {
    let origins_str = std::env::var("CORS_ORIGINS").unwrap_or_else(|_| "https://aipriceaction.com".to_string());

    if origins_str.trim() == "*" {
        tracing::info!("CORS: permissive mode (all origins allowed)");
        return CorsLayer::permissive();
    }

    let origins: Vec<HeaderValue> = origins_str
        .split(',')
        .filter_map(|s| HeaderValue::from_str(s.trim()).ok())
        .collect();

    if origins.is_empty() {
        tracing::info!("CORS: no valid origins parsed, falling back to permissive mode");
        return CorsLayer::permissive();
    }

    tracing::info!("CORS: allowed origins = {:?}", origins_str);
    CorsLayer::new()
        .allow_origin(origins)
        .allow_methods([Method::GET, Method::POST, Method::DELETE, Method::OPTIONS])
        .allow_headers([
            HeaderName::from_static("content-type"),
            HeaderName::from_static("authorization"),
        ])
}

fn analysis_routes() -> axum::Router<Arc<AppState>> {
    axum::Router::new()
        .route("/top-performers", axum::routing::get(analysis::top_performers_handler))
        .route("/ma-scores-by-sector", axum::routing::get(analysis::ma_scores_by_sector_handler))
        .route("/volume-profile", axum::routing::get(analysis::volume_profile_handler))
}
