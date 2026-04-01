use axum::{
    extract::{Multipart, Path, Query},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use tokio::fs;
use tracing::{info, warn, instrument, error};
use uuid::Uuid;

const MAX_MARKDOWN_SIZE: usize = 5 * 1024 * 1024;
const MAX_IMAGE_SIZE: usize = 10 * 1024 * 1024;
const MAX_FILENAME_LENGTH: usize = 200;

const ALLOWED_MARKDOWN_EXTS: &[&str] = &["md", "markdown", "txt"];
const ALLOWED_IMAGE_EXTS: &[&str] = &["jpg", "jpeg", "png", "gif", "webp", "svg"];

#[derive(Debug, Deserialize)]
pub struct UploadQuery {
    pub session_id: String,
    pub secret: String,
}

#[derive(Debug, Deserialize)]
pub struct RetrieveQuery {
    pub secret: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct DeleteQuery {
    pub secret: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SessionMetadata {
    pub session_id: String,
    pub secret: String,
    pub is_public: bool,
    pub created_at: String,
}

#[derive(Debug, Serialize)]
pub struct DeleteResponse {
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub files_deleted: Option<FilesDeleted>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct FilesDeleted {
    pub markdown: usize,
    pub images: usize,
}

#[derive(Debug, Serialize)]
pub struct UploadResponse {
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub files: Option<Vec<FileInfo>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct FileInfo {
    pub original_name: String,
    pub stored_name: String,
    pub size_bytes: usize,
    pub content_type: String,
    pub url: String,
}

#[derive(Debug, Clone, Copy)]
enum FileType {
    Markdown,
    Image,
}

impl FileType {
    fn subdirectory(&self) -> &'static str {
        match self {
            FileType::Markdown => "markdown",
            FileType::Image => "images",
        }
    }

    fn allowed_extensions(&self) -> &'static [&'static str] {
        match self {
            FileType::Markdown => ALLOWED_MARKDOWN_EXTS,
            FileType::Image => ALLOWED_IMAGE_EXTS,
        }
    }

    fn max_size(&self) -> usize {
        match self {
            FileType::Markdown => MAX_MARKDOWN_SIZE,
            FileType::Image => MAX_IMAGE_SIZE,
        }
    }

    fn default_content_type(&self) -> &'static str {
        match self {
            FileType::Markdown => "text/markdown; charset=utf-8",
            FileType::Image => "application/octet-stream",
        }
    }
}

#[instrument(skip(multipart))]
pub async fn upload_markdown_handler(
    Query(query): Query<UploadQuery>,
    multipart: Multipart,
) -> impl IntoResponse {
    upload_file_handler(query, multipart, FileType::Markdown).await
}

#[instrument(skip(multipart))]
pub async fn upload_image_handler(
    Query(query): Query<UploadQuery>,
    multipart: Multipart,
) -> impl IntoResponse {
    upload_file_handler(query, multipart, FileType::Image).await
}

async fn upload_file_handler(
    query: UploadQuery,
    mut multipart: Multipart,
    file_type: FileType,
) -> Response {
    let session_id = match validate_session_id(&query.session_id) {
        Ok(id) => id,
        Err(e) => return error_response(StatusCode::BAD_REQUEST, &e),
    };

    if query.secret.len() < 8 {
        return error_response(StatusCode::BAD_REQUEST, "Secret must be at least 8 characters");
    }

    let metadata_path = PathBuf::from("uploads")
        .join(session_id.to_string())
        .join("metadata.json");

    if metadata_path.exists() {
        match validate_secret(&session_id, &query.secret).await {
            Ok(_) => info!(session_id = %session_id, "Secret validated for existing session"),
            Err(e) => {
                warn!(session_id = %session_id, error = %e, "Secret validation failed");
                return error_response(StatusCode::FORBIDDEN, "Invalid secret for this session");
            }
        }
    } else {
        if let Err(e) = save_metadata(&session_id, &query.secret).await {
            error!(session_id = %session_id, error = %e, "Failed to create session metadata");
            return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to create session");
        }
        info!(session_id = %session_id, "Created new session with metadata");
    }

    let mut uploaded_files = Vec::new();

    while let Ok(Some(field)) = multipart.next_field().await {
        let filename = match field.file_name() {
            Some(name) => name.to_string(),
            None => { warn!("Field without filename, skipping"); continue; }
        };

        info!(filename = %filename, "Processing upload");

        let sanitized = sanitize_filename(&filename);
        if sanitized.is_empty() {
            return error_response(StatusCode::BAD_REQUEST, "Invalid filename");
        }

        if let Err(e) = validate_file_extension(&sanitized, file_type) {
            return error_response(StatusCode::BAD_REQUEST, &e);
        }

        let data = match field.bytes().await {
            Ok(bytes) => bytes,
            Err(e) => {
                error!(error = %e, "Failed to read file data");
                return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to read file data");
            }
        };

        if data.len() > file_type.max_size() {
            return error_response(StatusCode::PAYLOAD_TOO_LARGE,
                &format!("File size exceeds {}MB limit", file_type.max_size() / (1024 * 1024)));
        }

        if let Err(e) = validate_mime_type(&data, file_type) {
            return error_response(StatusCode::UNSUPPORTED_MEDIA_TYPE, &e);
        }

        let file_dir = match ensure_session_dir(&session_id, file_type).await {
            Ok(dir) => dir,
            Err(e) => {
                error!(error = %e, "Failed to create session directory");
                return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to create upload directory");
            }
        };

        let file_path = file_dir.join(&sanitized);
        if file_path.exists() {
            return error_response(StatusCode::CONFLICT, &format!("File already exists: {}", sanitized));
        }

        let temp_path = file_path.with_extension("tmp");
        if let Err(e) = fs::write(&temp_path, &data).await {
            error!(error = %e, path = %temp_path.display(), "Failed to write temp file");
            return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to save file");
        }

        match fs::rename(&temp_path, &file_path).await {
            Ok(_) => info!(path = %file_path.display(), size = data.len(), "File saved successfully"),
            Err(e) => {
                error!(error = %e, "Failed to rename temp file");
                let _ = fs::remove_file(&temp_path).await;
                return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to save file");
            }
        }

        let content_type = detect_content_type(&data, file_type);
        let url = format!("/uploads/{}/{}/{}", session_id, file_type.subdirectory(), sanitized);

        uploaded_files.push(FileInfo {
            original_name: filename,
            stored_name: sanitized,
            size_bytes: data.len(),
            content_type,
            url,
        });
    }

    if uploaded_files.is_empty() {
        return error_response(StatusCode::BAD_REQUEST, "No file provided");
    }

    (StatusCode::OK, axum::Json(UploadResponse {
        success: true,
        session_id: Some(session_id.to_string()),
        files: Some(uploaded_files),
        error: None,
    })).into_response()
}

#[instrument]
pub async fn serve_markdown_handler(
    Path((session_id, filename)): Path<(String, String)>,
    Query(query): Query<RetrieveQuery>,
) -> impl IntoResponse {
    serve_file_handler(session_id, filename, query.secret, FileType::Markdown).await
}

#[instrument]
pub async fn serve_image_handler(
    Path((session_id, filename)): Path<(String, String)>,
    Query(query): Query<RetrieveQuery>,
) -> impl IntoResponse {
    serve_file_handler(session_id, filename, query.secret, FileType::Image).await
}

async fn serve_file_handler(
    session_id: String,
    filename: String,
    secret: Option<String>,
    file_type: FileType,
) -> Response {
    let session_uuid = match validate_session_id(&session_id) {
        Ok(id) => id,
        Err(e) => return error_response(StatusCode::BAD_REQUEST, &e),
    };

    let session_dir = PathBuf::from("uploads").join(session_uuid.to_string());
    if !session_dir.exists() {
        return error_response(StatusCode::NOT_FOUND, "Session does not exist");
    }

    if let Err(e) = validate_path_component(&filename) {
        return error_response(StatusCode::BAD_REQUEST, &e);
    }

    match check_read_access(&session_uuid, secret.as_deref()).await {
        Ok(true) => {
            info!(session_id = %session_uuid, "Read access granted");
        }
        Ok(false) => {
            warn!(session_id = %session_uuid, "Read access denied");
            return error_response(StatusCode::FORBIDDEN, "Access denied");
        }
        Err(e) => {
            warn!(session_id = %session_uuid, error = %e, "Read access check failed");
            return error_response(StatusCode::FORBIDDEN, &e);
        }
    };

    let file_path = PathBuf::from("uploads")
        .join(session_uuid.to_string())
        .join(file_type.subdirectory())
        .join(&filename);

    if !file_path.exists() {
        return error_response(StatusCode::NOT_FOUND, "File not found");
    }

    let metadata = match fs::metadata(&file_path).await {
        Ok(m) => m,
        Err(_) => return error_response(StatusCode::NOT_FOUND, "File not found"),
    };

    if !metadata.is_file() {
        return error_response(StatusCode::BAD_REQUEST, "Not a regular file");
    }

    let data = match fs::read(&file_path).await {
        Ok(d) => d,
        Err(e) => {
            error!(error = %e, path = %file_path.display(), "Failed to read file");
            return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to read file");
        }
    };

    let content_type = detect_content_type(&data, file_type);

    (
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, content_type.as_str()),
            (header::CONTENT_DISPOSITION, &format!("inline; filename=\"{}\"", filename)),
            (header::CACHE_CONTROL, "public, max-age=3600"),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
        ],
        data,
    ).into_response()
}

fn sanitize_filename(name: &str) -> String {
    name.chars()
        .map(|c| match c {
            '/' | '\\' | '\0' => '_',
            '<' | '>' | ':' | '"' | '|' | '?' | '*' => '_',
            c if c.is_control() => '_',
            c => c,
        })
        .collect::<String>()
        .trim_start_matches('.')
        .chars()
        .take(MAX_FILENAME_LENGTH)
        .collect()
}

fn validate_session_id(id: &str) -> Result<Uuid, String> {
    if id.is_empty() {
        return Err("Missing required parameter: session_id".to_string());
    }
    if id.contains("..") || id.contains('/') || id.contains('\\') {
        return Err("Invalid session_id format".to_string());
    }
    Uuid::parse_str(id).map_err(|_| "Invalid session_id format".to_string())
}

fn validate_file_extension(filename: &str, file_type: FileType) -> Result<(), String> {
    let extension = filename.rsplit('.').next().unwrap_or("").to_lowercase();
    if extension.is_empty() {
        return Err("File must have an extension".to_string());
    }
    if !file_type.allowed_extensions().contains(&extension.as_str()) {
        return Err(format!("Invalid file extension. Expected: {}", file_type.allowed_extensions().join(", ")));
    }
    Ok(())
}

fn validate_mime_type(data: &[u8], file_type: FileType) -> Result<(), String> {
    match file_type {
        FileType::Markdown => {
            std::str::from_utf8(data).map_err(|_| "Invalid file type. Expected markdown/text content".to_string())?;
            Ok(())
        }
        FileType::Image => {
            let kind = infer::get(data).ok_or_else(|| "Unable to detect file type".to_string())?;
            if !kind.mime_type().starts_with("image/") {
                return Err("Invalid file type. Expected image content".to_string());
            }
            if kind.mime_type() == "image/svg+xml" {
                if data.windows(7).any(|w| w.eq_ignore_ascii_case(b"<script")) {
                    return Err("SVG files with scripts are not allowed".to_string());
                }
            }
            Ok(())
        }
    }
}

fn validate_path_component(component: &str) -> Result<(), String> {
    if component.is_empty() {
        return Err("Filename cannot be empty".to_string());
    }
    if component.contains("..") || component.contains('/') || component.contains('\\') {
        return Err("Invalid filename: path traversal detected".to_string());
    }
    if component.starts_with('.') {
        return Err("Invalid filename: cannot start with dot".to_string());
    }
    Ok(())
}

async fn ensure_session_dir(session_id: &Uuid, file_type: FileType) -> std::io::Result<PathBuf> {
    let base = PathBuf::from("uploads");
    let session = base.join(session_id.to_string());
    let type_dir = session.join(file_type.subdirectory());
    fs::create_dir_all(&type_dir).await?;
    Ok(type_dir)
}

fn detect_content_type(data: &[u8], file_type: FileType) -> String {
    match infer::get(data) {
        Some(kind) => kind.mime_type().to_string(),
        None => file_type.default_content_type().to_string(),
    }
}

async fn load_metadata(session_id: &Uuid) -> Result<SessionMetadata, String> {
    let metadata_path = PathBuf::from("uploads")
        .join(session_id.to_string())
        .join("metadata.json");

    if !metadata_path.exists() {
        return Err("Session does not exist".to_string());
    }

    let content = fs::read_to_string(&metadata_path)
        .await
        .map_err(|e| format!("Failed to read metadata: {}", e))?;

    serde_json::from_str(&content).map_err(|e| format!("Failed to parse metadata: {}", e))
}

async fn save_metadata(session_id: &Uuid, secret: &str) -> Result<(), String> {
    let session_dir = PathBuf::from("uploads").join(session_id.to_string());
    fs::create_dir_all(&session_dir).await.map_err(|e| format!("Failed to create session directory: {}", e))?;

    let metadata = SessionMetadata {
        session_id: session_id.to_string(),
        secret: secret.to_string(),
        is_public: true,
        created_at: chrono::Utc::now().to_rfc3339(),
    };

    let metadata_path = session_dir.join("metadata.json");
    let json = serde_json::to_string_pretty(&metadata).map_err(|e| format!("Failed to serialize metadata: {}", e))?;
    fs::write(&metadata_path, json).await.map_err(|e| format!("Failed to write metadata: {}", e))?;
    Ok(())
}

async fn validate_secret(session_id: &Uuid, provided_secret: &str) -> Result<SessionMetadata, String> {
    let metadata = load_metadata(session_id).await?;
    if metadata.secret != provided_secret {
        return Err("Invalid secret".to_string());
    }
    Ok(metadata)
}

async fn check_read_access(session_id: &Uuid, provided_secret: Option<&str>) -> Result<bool, String> {
    let metadata = load_metadata(session_id).await?;
    if metadata.is_public {
        return Ok(true);
    }
    match provided_secret {
        Some(secret) if metadata.secret == secret => Ok(true),
        Some(_) => Err("Invalid secret".to_string()),
        None => Err("Secret required for private session".to_string()),
    }
}

fn error_response(status: StatusCode, message: &str) -> Response {
    (status, axum::Json(UploadResponse {
        success: false,
        session_id: None,
        files: None,
        error: Some(message.to_string()),
    })).into_response()
}

#[instrument(skip(query))]
pub async fn delete_markdown_handler(
    Path((session_id, filename)): Path<(String, String)>,
    Query(query): Query<DeleteQuery>,
) -> Response {
    info!("DELETE markdown file: session={}, file={}", session_id, filename);
    delete_file_handler(session_id, filename, "markdown", &query.secret).await
}

#[instrument(skip(query))]
pub async fn delete_image_handler(
    Path((session_id, filename)): Path<(String, String)>,
    Query(query): Query<DeleteQuery>,
) -> Response {
    info!("DELETE image file: session={}, file={}", session_id, filename);
    delete_file_handler(session_id, filename, "images", &query.secret).await
}

async fn delete_file_handler(
    session_id: String,
    filename: String,
    subdir: &str,
    secret: &str,
) -> Response {
    let session_uuid = match Uuid::parse_str(&session_id) {
        Ok(uuid) => uuid,
        Err(_) => return delete_error_response(StatusCode::BAD_REQUEST, "Invalid session_id format"),
    };

    if secret.len() < 8 {
        return delete_error_response(StatusCode::BAD_REQUEST, "Secret must be at least 8 characters");
    }

    let sanitized = sanitize_filename(&filename);
    if sanitized.is_empty() || sanitized.contains("..") || sanitized.contains('/') {
        return delete_error_response(StatusCode::BAD_REQUEST, "Invalid filename");
    }

    let session_dir = PathBuf::from("uploads").join(session_uuid.to_string());
    if !session_dir.exists() {
        return delete_error_response(StatusCode::NOT_FOUND, "Session does not exist");
    }

    if let Err(e) = validate_secret(&session_uuid, secret).await {
        warn!("Invalid secret for DELETE: {}", e);
        return delete_error_response(StatusCode::FORBIDDEN, &e);
    }

    let file_path = session_dir.join(subdir).join(&sanitized);
    if !file_path.exists() {
        return delete_error_response(StatusCode::NOT_FOUND, "File not found");
    }

    if let Err(e) = fs::remove_file(&file_path).await {
        error!("Failed to delete file: {}", e);
        return delete_error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to delete file");
    }

    info!("File deleted successfully: {}", sanitized);

    (StatusCode::OK, axum::Json(DeleteResponse {
        success: true,
        message: Some("File deleted successfully".to_string()),
        file: Some(sanitized),
        session_id: None,
        files_deleted: None,
        error: None,
    })).into_response()
}

#[instrument(skip(query))]
pub async fn delete_session_handler(
    Path(session_id): Path<String>,
    Query(query): Query<DeleteQuery>,
) -> Response {
    info!("DELETE session: {}", session_id);

    let session_uuid = match Uuid::parse_str(&session_id) {
        Ok(uuid) => uuid,
        Err(_) => return delete_error_response(StatusCode::BAD_REQUEST, "Invalid session_id format"),
    };

    if query.secret.len() < 8 {
        return delete_error_response(StatusCode::BAD_REQUEST, "Secret must be at least 8 characters");
    }

    let session_dir = PathBuf::from("uploads").join(session_uuid.to_string());
    if !session_dir.exists() {
        return delete_error_response(StatusCode::NOT_FOUND, "Session does not exist");
    }

    if let Err(e) = validate_secret(&session_uuid, &query.secret).await {
        warn!("Invalid secret for session DELETE: {}", e);
        return delete_error_response(StatusCode::FORBIDDEN, &e);
    }

    let mut markdown_count = 0;
    let mut images_count = 0;

    let markdown_dir = session_dir.join("markdown");
    if markdown_dir.exists() {
        if let Ok(mut entries) = fs::read_dir(&markdown_dir).await {
            while let Ok(Some(_)) = entries.next_entry().await { markdown_count += 1; }
        }
    }

    let images_dir = session_dir.join("images");
    if images_dir.exists() {
        if let Ok(mut entries) = fs::read_dir(&images_dir).await {
            while let Ok(Some(_)) = entries.next_entry().await { images_count += 1; }
        }
    }

    if let Err(e) = fs::remove_dir_all(&session_dir).await {
        error!("Failed to delete session: {}", e);
        return delete_error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to delete session");
    }

    info!("Session deleted successfully: {} (markdown: {}, images: {})", session_id, markdown_count, images_count);

    (StatusCode::OK, axum::Json(DeleteResponse {
        success: true,
        message: Some("Session deleted successfully".to_string()),
        file: None,
        session_id: Some(session_id),
        files_deleted: Some(FilesDeleted { markdown: markdown_count, images: images_count }),
        error: None,
    })).into_response()
}

fn delete_error_response(status: StatusCode, message: &str) -> Response {
    (status, axum::Json(DeleteResponse {
        success: false,
        message: None,
        file: None,
        session_id: None,
        files_deleted: None,
        error: Some(message.to_string()),
    })).into_response()
}
