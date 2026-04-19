use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Json, Response};
use axum_extra::extract::Query as AxumQuery;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::sync::Arc;
use uuid::Uuid;

use super::AppState;

// ── Request / Response types ──

#[derive(Debug, Deserialize)]
pub struct SyncPostBody {
    pub secret: String,
    pub value: serde_json::Value,
}

#[derive(Debug, Deserialize)]
pub struct SyncGetQuery {
    pub secret: String,
}

#[derive(Debug, Serialize)]
pub struct SyncResponse {
    pub id: String,
    pub value: serde_json::Value,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(sqlx::FromRow)]
struct SyncRow {
    id: Uuid,
    secret: String,
    value: serde_json::Value,
    created_at: chrono::DateTime<chrono::Utc>,
    updated_at: chrono::DateTime<chrono::Utc>,
}

// ── Helpers ──

fn hash_secret(plaintext: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(plaintext.as_bytes());
    hex::encode(hasher.finalize())
}

fn verify_sync_token(headers: &HeaderMap) -> Result<(), Response> {
    let raw = match std::env::var("SYNC_TOKEN") {
        Ok(v) => v,
        Err(_) => {
            tracing::warn!("SYNC_TOKEN not set — /sync endpoint disabled");
            return Err(error_response(
                StatusCode::FORBIDDEN,
                "Sync endpoint is disabled. Set SYNC_TOKEN environment variable.",
            ));
        }
    };

    let valid_tokens: Vec<&str> = raw.split(',').map(|s| s.trim()).filter(|s| !s.is_empty()).collect();

    if valid_tokens.is_empty() {
        tracing::warn!("SYNC_TOKEN is empty — /sync endpoint disabled");
        return Err(error_response(
            StatusCode::FORBIDDEN,
            "Sync endpoint is disabled. Set SYNC_TOKEN environment variable.",
        ));
    }

    let provided = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    let matched = valid_tokens.iter().any(|token| provided == format!("Bearer {token}"));

    if !matched {
        tracing::warn!("/sync auth failed");
        return Err(error_response(
            StatusCode::UNAUTHORIZED,
            "Invalid or missing authorization token.",
        ));
    }

    Ok(())
}

fn error_response(status: StatusCode, message: &str) -> Response {
    (
        status,
        Json(serde_json::json!({ "success": false, "error": message })),
    )
        .into_response()
}

fn sync_response(row: SyncRow) -> Response {
    (
        StatusCode::OK,
        Json(SyncResponse {
            id: row.id.to_string(),
            value: row.value,
            created_at: row.created_at.to_rfc3339(),
            updated_at: row.updated_at.to_rfc3339(),
        }),
    )
        .into_response()
}

// ── POST /sync/{key} ──

pub async fn sync_post(
    State(state): State<Arc<AppState>>,
    Path(key): Path<String>,
    headers: HeaderMap,
    axum::Json(body): axum::Json<SyncPostBody>,
) -> Response {
    if let Err(e) = verify_sync_token(&headers) {
        return e;
    }

    let uuid = match Uuid::parse_str(&key) {
        Ok(u) => u,
        Err(_) => {
            return error_response(StatusCode::BAD_REQUEST, "Key must be a valid UUID");
        }
    };

    let secret_hash = hash_secret(&body.secret);

    // Check if key already exists — if so, verify secret before allowing update
    let existing = sqlx::query_as::<_, SyncRow>(
        r#"SELECT id, secret, value, created_at, updated_at FROM sync_kv WHERE id = $1"#,
    )
    .bind(uuid)
    .fetch_optional(&state.pool)
    .await;

    match existing {
        Ok(Some(row)) if row.secret != secret_hash => {
            return error_response(StatusCode::FORBIDDEN, "Invalid secret");
        }
        Err(e) => {
            tracing::error!(error = %e, key = %key, "sync_post DB error");
            return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Database error");
        }
        _ => {}
    }

    let result = sqlx::query_as::<_, SyncRow>(
        r#"INSERT INTO sync_kv (id, secret, value, created_at, updated_at)
           VALUES ($1, $2, $3, NOW(), NOW())
           ON CONFLICT (id) DO UPDATE SET value = $3, updated_at = NOW()
           RETURNING id, secret, value, created_at, updated_at"#,
    )
    .bind(uuid)
    .bind(&secret_hash)
    .bind(&body.value)
    .fetch_one(&state.pool)
    .await;

    match result {
        Ok(row) => sync_response(row),
        Err(e) => {
            tracing::error!(error = %e, key = %key, "sync_post DB error");
            error_response(StatusCode::INTERNAL_SERVER_ERROR, "Database error")
        }
    }
}

// ── GET /sync/{key}?secret=... ──

pub async fn sync_get(
    State(state): State<Arc<AppState>>,
    Path(key): Path<String>,
    headers: HeaderMap,
    AxumQuery(query): AxumQuery<SyncGetQuery>,
) -> Response {
    if let Err(e) = verify_sync_token(&headers) {
        return e;
    }

    let uuid = match Uuid::parse_str(&key) {
        Ok(u) => u,
        Err(_) => {
            return error_response(StatusCode::BAD_REQUEST, "Key must be a valid UUID");
        }
    };

    let secret_hash = hash_secret(&query.secret);

    let result = sqlx::query_as::<_, SyncRow>(
        r#"SELECT id, secret, value, created_at, updated_at
           FROM sync_kv WHERE id = $1"#,
    )
    .bind(uuid)
    .fetch_optional(&state.pool)
    .await;

    match result {
        Ok(Some(row)) if row.secret == secret_hash => sync_response(row),
        Ok(Some(_)) => error_response(StatusCode::FORBIDDEN, "Invalid secret"),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "Key not found"),
        Err(e) => {
            tracing::error!(error = %e, key = %key, "sync_get DB error");
            error_response(StatusCode::INTERNAL_SERVER_ERROR, "Database error")
        }
    }
}
