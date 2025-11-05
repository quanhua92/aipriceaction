/// Legacy GitHub file proxy module
///
/// This module provides a simple proxy to GitHub raw files for backward compatibility.
/// It should be removed in the future once clients migrate to using local data.
///
/// WARNING: This module contains temporary code that should be removed in a future version.

use axum::{
    extract::Path,
    http::{HeaderMap, StatusCode, header::CACHE_CONTROL},
    response::IntoResponse,
};
use tracing::{debug, info, warn, instrument};

const GITHUB_RAW_BASE_URL: &str = "https://raw.githubusercontent.com/quanhua92/aipriceaction-data/refs/heads/main/";

/// GET /raw/*path - Proxy to GitHub raw files
///
/// Simple pass-through proxy with no caching for easy removal.
/// Example: /raw/data/VCB.csv -> https://raw.githubusercontent.com/.../ data/VCB.csv
#[instrument(skip_all, fields(path = %path))]
pub async fn raw_proxy_handler(
    Path(path): Path<String>,
) -> impl IntoResponse {
    debug!(path, "Proxying to GitHub");

    // Build GitHub URL
    let github_url = format!("{}{}", GITHUB_RAW_BASE_URL, path);

    // Fetch from GitHub
    match reqwest::get(&github_url).await {
        Ok(response) => {
            if response.status().is_success() {
                match response.bytes().await {
                    Ok(bytes) => {
                        let content = bytes.to_vec();
                        info!(path, content_size = content.len(), "Proxied from GitHub");

                        // Determine content type from file extension
                        let content_type = get_content_type(&path);

                        let mut headers = HeaderMap::new();
                        headers.insert(CACHE_CONTROL, "max-age=30".parse().unwrap());
                        headers.insert("content-type", content_type.parse().unwrap());

                        (StatusCode::OK, headers, content).into_response()
                    }
                    Err(e) => {
                        warn!(path, error = ?e, "Failed to read response bytes");
                        (StatusCode::INTERNAL_SERVER_ERROR, format!("Failed to read GitHub response: {}", e)).into_response()
                    }
                }
            } else {
                warn!(path, status = %response.status(), "GitHub returned error status");
                (response.status(), format!("GitHub returned error: {}", response.status())).into_response()
            }
        }
        Err(e) => {
            warn!(path, error = ?e, "Failed to fetch from GitHub");
            (StatusCode::BAD_GATEWAY, format!("Failed to fetch from GitHub: {}", e)).into_response()
        }
    }
}

/// Determine content type from file extension
fn get_content_type(path: &str) -> &'static str {
    if path.ends_with(".csv") {
        "text/csv"
    } else if path.ends_with(".json") {
        "application/json"
    } else if path.ends_with(".txt") {
        "text/plain"
    } else {
        "application/octet-stream"
    }
}
