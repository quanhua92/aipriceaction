mod api;
pub mod types;

use sqlx::PgPool;
use std::sync::Arc;
use tower_http::compression::CompressionLayer;
use tower_http::cors::CorsLayer;
use tower_http::timeout::TimeoutLayer;

pub struct AppState {
    pub pool: PgPool,
}

#[allow(deprecated)]
pub fn create_app(pool: PgPool) -> axum::Router {
    let state = Arc::new(AppState { pool });

    axum::Router::new()
        .route("/health", axum::routing::get(api::health))
        .route("/tickers", axum::routing::get(api::tickers))
        .route("/tickers/group", axum::routing::get(api::tickers_group))
        .layer(CompressionLayer::new())
        .layer(CorsLayer::permissive())
        .layer(TimeoutLayer::new(std::time::Duration::from_secs(30)))
        .with_state(state)
}
