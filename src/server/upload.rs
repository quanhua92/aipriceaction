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

// Constants
const MAX_MARKDOWN_SIZE: usize = 5 * 1024 * 1024; // 5MB
const MAX_IMAGE_SIZE: usize = 10 * 1024 * 1024; // 10MB
const MAX_FILENAME_LENGTH: usize = 200;

const ALLOWED_MARKDOWN_EXTS: &[&str] = &["md", "markdown", "txt"];
const ALLOWED_IMAGE_EXTS: &[&str] = &["jpg", "jpeg", "png", "gif", "webp", "svg"];

/// Query parameters for upload endpoints
#[derive(Debug, Deserialize)]
pub struct UploadQuery {
    pub session_id: String,
    pub secret: String,
}

/// Query parameters for retrieval endpoints
#[derive(Debug, Deserialize)]
pub struct RetrieveQuery {
    pub secret: Option<String>,
}

/// Query parameters for delete endpoints
#[derive(Debug, Deserialize)]
pub struct DeleteQuery {
    pub secret: String,
}

/// Session metadata stored in metadata.json
#[derive(Debug, Serialize, Deserialize)]
pub struct SessionMetadata {
    pub session_id: String,
    pub secret: String,
    pub is_public: bool,
    pub created_at: String,
}

/// Response structure for delete operations
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

/// Files deleted count in session deletion
#[derive(Debug, Serialize)]
pub struct FilesDeleted {
    pub markdown: usize,
    pub images: usize,
}

/// Response structure for successful uploads
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

/// Information about an uploaded file
#[derive(Debug, Serialize)]
pub struct FileInfo {
    pub original_name: String,
    pub stored_name: String,
    pub size_bytes: usize,
    pub content_type: String,
    pub url: String,
}

/// File type enum for validation
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

/// POST /upload/markdown - Upload markdown files
#[instrument(skip(multipart))]
pub async fn upload_markdown_handler(
    Query(query): Query<UploadQuery>,
    multipart: Multipart,
) -> impl IntoResponse {
    upload_file_handler(query, multipart, FileType::Markdown).await
}

/// POST /upload/image - Upload image files
#[instrument(skip(multipart))]
pub async fn upload_image_handler(
    Query(query): Query<UploadQuery>,
    multipart: Multipart,
) -> impl IntoResponse {
    upload_file_handler(query, multipart, FileType::Image).await
}

/// Generic file upload handler
async fn upload_file_handler(
    query: UploadQuery,
    mut multipart: Multipart,
    file_type: FileType,
) -> Response {
    // Validate session ID
    let session_id = match validate_session_id(&query.session_id) {
        Ok(id) => id,
        Err(e) => {
            return error_response(StatusCode::BAD_REQUEST, &e);
        }
    };

    // Validate secret length (min 32 characters recommended)
    if query.secret.len() < 8 {
        return error_response(StatusCode::BAD_REQUEST, "Secret must be at least 8 characters");
    }

    // Check if session exists and validate secret
    let metadata_path = PathBuf::from("uploads")
        .join(session_id.to_string())
        .join("metadata.json");

    if metadata_path.exists() {
        // Existing session - validate secret
        match validate_secret(&session_id, &query.secret).await {
            Ok(_) => {
                info!(session_id = %session_id, "Secret validated for existing session");
            }
            Err(e) => {
                warn!(session_id = %session_id, error = %e, "Secret validation failed");
                return error_response(StatusCode::FORBIDDEN, "Invalid secret for this session");
            }
        }
    } else {
        // New session - create metadata with provided secret
        if let Err(e) = save_metadata(&session_id, &query.secret).await {
            error!(session_id = %session_id, error = %e, "Failed to create session metadata");
            return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to create session");
        }
        info!(session_id = %session_id, "Created new session with metadata");
    }

    let mut uploaded_files = Vec::new();

    // Process multipart fields
    while let Ok(Some(field)) = multipart.next_field().await {
        let filename = match field.file_name() {
            Some(name) => name.to_string(),
            None => {
                warn!("Field without filename, skipping");
                continue;
            }
        };

        info!(filename = %filename, "Processing upload");

        // Sanitize filename
        let sanitized = sanitize_filename(&filename);
        if sanitized.is_empty() {
            return error_response(StatusCode::BAD_REQUEST, "Invalid filename");
        }

        // Validate file extension
        if let Err(e) = validate_file_extension(&sanitized, file_type) {
            return error_response(StatusCode::BAD_REQUEST, &e);
        }

        // Read file data
        let data = match field.bytes().await {
            Ok(bytes) => bytes,
            Err(e) => {
                error!(error = %e, "Failed to read file data");
                return error_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Failed to read file data",
                );
            }
        };

        // Check file size
        if data.len() > file_type.max_size() {
            return error_response(
                StatusCode::PAYLOAD_TOO_LARGE,
                &format!(
                    "File size exceeds {}MB limit",
                    file_type.max_size() / (1024 * 1024)
                ),
            );
        }

        // Validate MIME type
        if let Err(e) = validate_mime_type(&data, file_type) {
            return error_response(StatusCode::UNSUPPORTED_MEDIA_TYPE, &e);
        }

        // Ensure session directory exists
        let file_dir = match ensure_session_dir(&session_id, file_type).await {
            Ok(dir) => dir,
            Err(e) => {
                error!(error = %e, "Failed to create session directory");
                return error_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Failed to create upload directory",
                );
            }
        };

        let file_path = file_dir.join(&sanitized);

        // Check if file already exists
        if file_path.exists() {
            return error_response(
                StatusCode::CONFLICT,
                &format!("File already exists: {}", sanitized),
            );
        }

        // Write file atomically (write to temp, then rename)
        let temp_path = file_path.with_extension("tmp");
        match fs::write(&temp_path, &data).await {
            Ok(_) => {}
            Err(e) => {
                error!(error = %e, path = %temp_path.display(), "Failed to write temp file");
                return error_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Failed to save file",
                );
            }
        }

        match fs::rename(&temp_path, &file_path).await {
            Ok(_) => {
                info!(
                    path = %file_path.display(),
                    size = data.len(),
                    "File saved successfully"
                );
            }
            Err(e) => {
                error!(error = %e, "Failed to rename temp file");
                // Clean up temp file
                let _ = fs::remove_file(&temp_path).await;
                return error_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Failed to save file",
                );
            }
        }

        // Detect content type for response
        let content_type = detect_content_type(&data, file_type);

        // Build file URL
        let url = format!(
            "/uploads/{}/{}/{}",
            session_id,
            file_type.subdirectory(),
            sanitized
        );

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

    // Success response
    let response = UploadResponse {
        success: true,
        session_id: Some(session_id.to_string()),
        files: Some(uploaded_files),
        error: None,
    };

    (StatusCode::OK, axum::Json(response)).into_response()
}

/// GET /uploads/{session_id}/markdown/{filename} - Serve markdown files
#[instrument]
pub async fn serve_markdown_handler(
    Path((session_id, filename)): Path<(String, String)>,
    Query(query): Query<RetrieveQuery>,
) -> impl IntoResponse {
    serve_file_handler(session_id, filename, query.secret, FileType::Markdown).await
}

/// GET /uploads/{session_id}/images/{filename} - Serve image files
#[instrument]
pub async fn serve_image_handler(
    Path((session_id, filename)): Path<(String, String)>,
    Query(query): Query<RetrieveQuery>,
) -> impl IntoResponse {
    serve_file_handler(session_id, filename, query.secret, FileType::Image).await
}

/// Generic file serving handler
async fn serve_file_handler(
    session_id: String,
    filename: String,
    secret: Option<String>,
    file_type: FileType,
) -> Response {
    // Validate session ID
    let session_uuid = match validate_session_id(&session_id) {
        Ok(id) => id,
        Err(e) => {
            return error_response(StatusCode::BAD_REQUEST, &e);
        }
    };

    // Check if session exists BEFORE checking access
    let session_dir = PathBuf::from("uploads").join(session_uuid.to_string());
    if !session_dir.exists() {
        return error_response(StatusCode::NOT_FOUND, "Session does not exist");
    }

    // Validate filename (prevent path traversal)
    if let Err(e) = validate_path_component(&filename) {
        return error_response(StatusCode::BAD_REQUEST, &e);
    }

    // Check read access (is_public or valid secret)
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
    }

    // Build file path
    let file_path = PathBuf::from("uploads")
        .join(session_uuid.to_string())
        .join(file_type.subdirectory())
        .join(&filename);

    // Check if file exists
    if !file_path.exists() {
        return error_response(StatusCode::NOT_FOUND, "File not found");
    }

    // Read file metadata
    let metadata = match fs::metadata(&file_path).await {
        Ok(m) => m,
        Err(e) => {
            error!(error = %e, path = %file_path.display(), "Failed to read file metadata");
            return error_response(StatusCode::NOT_FOUND, "File not found");
        }
    };

    // Ensure it's a regular file (not symlink or directory)
    if !metadata.is_file() {
        return error_response(StatusCode::BAD_REQUEST, "Not a regular file");
    }

    // Read file contents
    let data = match fs::read(&file_path).await {
        Ok(d) => d,
        Err(e) => {
            error!(error = %e, path = %file_path.display(), "Failed to read file");
            return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to read file");
        }
    };

    info!(
        path = %file_path.display(),
        size = data.len(),
        "Serving file"
    );

    // Detect content type
    let content_type = detect_content_type(&data, file_type);

    // Build response with appropriate headers
    (
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, content_type.as_str()),
            (
                header::CONTENT_DISPOSITION,
                &format!("inline; filename=\"{}\"", filename),
            ),
            (header::CACHE_CONTROL, "public, max-age=3600"),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
        ],
        data,
    )
        .into_response()
}

/// Sanitize filename to prevent security issues
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

/// Validate session ID format
fn validate_session_id(id: &str) -> Result<Uuid, String> {
    if id.is_empty() {
        return Err("Missing required parameter: session_id".to_string());
    }

    // Check for path traversal attempts
    if id.contains("..") || id.contains('/') || id.contains('\\') {
        return Err("Invalid session_id format".to_string());
    }

    // Parse as UUID
    Uuid::parse_str(id).map_err(|_| "Invalid session_id format".to_string())
}

/// Validate file extension matches expected type
fn validate_file_extension(filename: &str, file_type: FileType) -> Result<(), String> {
    let extension = filename
        .rsplit('.')
        .next()
        .unwrap_or("")
        .to_lowercase();

    if extension.is_empty() {
        return Err("File must have an extension".to_string());
    }

    if !file_type.allowed_extensions().contains(&extension.as_str()) {
        return Err(format!(
            "Invalid file extension. Expected: {}",
            file_type.allowed_extensions().join(", ")
        ));
    }

    Ok(())
}

/// Validate MIME type matches expected file type
fn validate_mime_type(data: &[u8], file_type: FileType) -> Result<(), String> {
    match file_type {
        FileType::Markdown => {
            // For markdown/text, verify it's valid UTF-8
            std::str::from_utf8(data)
                .map_err(|_| "Invalid file type. Expected markdown/text content".to_string())?;
            Ok(())
        }
        FileType::Image => {
            // Use infer to detect actual MIME type
            let kind = infer::get(data)
                .ok_or_else(|| "Unable to detect file type".to_string())?;

            if !kind.mime_type().starts_with("image/") {
                return Err("Invalid file type. Expected image content".to_string());
            }

            // Special handling for SVG: check for script tags (XSS prevention)
            if kind.mime_type() == "image/svg+xml" {
                if data.windows(7).any(|w| w.eq_ignore_ascii_case(b"<script")) {
                    return Err("SVG files with scripts are not allowed".to_string());
                }
            }

            Ok(())
        }
    }
}

/// Validate path component to prevent path traversal
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

/// Ensure session directory exists, create if needed
async fn ensure_session_dir(session_id: &Uuid, file_type: FileType) -> std::io::Result<PathBuf> {
    let base = PathBuf::from("uploads");
    let session = base.join(session_id.to_string());
    let type_dir = session.join(file_type.subdirectory());

    // create_dir_all is atomic and handles concurrent calls gracefully
    fs::create_dir_all(&type_dir).await?;

    Ok(type_dir)
}

/// Detect content type from file data
fn detect_content_type(data: &[u8], file_type: FileType) -> String {
    match infer::get(data) {
        Some(kind) => kind.mime_type().to_string(),
        None => file_type.default_content_type().to_string(),
    }
}

/// Load session metadata from metadata.json
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

    serde_json::from_str(&content)
        .map_err(|e| format!("Failed to parse metadata: {}", e))
}

/// Save session metadata to metadata.json
async fn save_metadata(session_id: &Uuid, secret: &str) -> Result<(), String> {
    let session_dir = PathBuf::from("uploads").join(session_id.to_string());

    // Create session directory if it doesn't exist
    fs::create_dir_all(&session_dir)
        .await
        .map_err(|e| format!("Failed to create session directory: {}", e))?;

    let metadata = SessionMetadata {
        session_id: session_id.to_string(),
        secret: secret.to_string(),
        is_public: true, // Default to public
        created_at: chrono::Utc::now().to_rfc3339(),
    };

    let metadata_path = session_dir.join("metadata.json");
    let json = serde_json::to_string_pretty(&metadata)
        .map_err(|e| format!("Failed to serialize metadata: {}", e))?;

    fs::write(&metadata_path, json)
        .await
        .map_err(|e| format!("Failed to write metadata: {}", e))?;

    Ok(())
}

/// Validate secret against stored metadata
async fn validate_secret(session_id: &Uuid, provided_secret: &str) -> Result<SessionMetadata, String> {
    let metadata = load_metadata(session_id).await?;

    if metadata.secret != provided_secret {
        return Err("Invalid secret".to_string());
    }

    Ok(metadata)
}

/// Check if session allows public read access
async fn check_read_access(session_id: &Uuid, provided_secret: Option<&str>) -> Result<bool, String> {
    let metadata = load_metadata(session_id).await?;

    // If session is public, allow access without secret
    if metadata.is_public {
        return Ok(true);
    }

    // If session is private, require secret
    match provided_secret {
        Some(secret) => {
            if metadata.secret == secret {
                Ok(true)
            } else {
                Err("Invalid secret".to_string())
            }
        }
        None => Err("Secret required for private session".to_string()),
    }
}

/// Build error response
fn error_response(status: StatusCode, message: &str) -> Response {
    let response = UploadResponse {
        success: false,
        session_id: None,
        files: None,
        error: Some(message.to_string()),
    };

    (status, axum::Json(response)).into_response()
}
/// DELETE /uploads/{session_id}/markdown/{filename}?secret={secret}
/// Delete a specific markdown file
#[instrument(skip(query))]
pub async fn delete_markdown_handler(
    Path((session_id, filename)): Path<(String, String)>,
    Query(query): Query<DeleteQuery>,
) -> Response {
    info!("DELETE markdown file: session={}, file={}", session_id, filename);

    // Validate session_id
    let session_uuid = match Uuid::parse_str(&session_id) {
        Ok(uuid) => uuid,
        Err(_) => return delete_error_response(StatusCode::BAD_REQUEST, "Invalid session_id format"),
    };

    // Validate secret format (min 8 chars)
    if query.secret.len() < 8 {
        return delete_error_response(StatusCode::BAD_REQUEST, "Secret must be at least 8 characters");
    }

    // Sanitize and validate filename
    let sanitized = sanitize_filename(&filename);
    if sanitized.is_empty() || sanitized.contains("..") || sanitized.contains('/') {
        return delete_error_response(StatusCode::BAD_REQUEST, "Invalid filename");
    }

    // Build file path
    let session_dir = PathBuf::from("uploads").join(session_uuid.to_string());
    let file_path = session_dir.join("markdown").join(&sanitized);

    // Check if session exists
    if !session_dir.exists() {
        return delete_error_response(StatusCode::NOT_FOUND, "Session does not exist");
    }

    // Validate secret
    match validate_secret(&session_uuid, &query.secret).await {
        Ok(_) => info!("Secret validated for DELETE"),
        Err(e) => {
            warn!("Invalid secret for DELETE: {}", e);
            return delete_error_response(StatusCode::FORBIDDEN, &e);
        }
    }

    // Check if file exists
    if !file_path.exists() {
        return delete_error_response(StatusCode::NOT_FOUND, "File not found");
    }

    // Delete the file
    if let Err(e) = fs::remove_file(&file_path).await {
        error!("Failed to delete file: {}", e);
        return delete_error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to delete file");
    }

    info!("File deleted successfully: {}", sanitized);

    let response = DeleteResponse {
        success: true,
        message: Some("File deleted successfully".to_string()),
        file: Some(sanitized),
        session_id: None,
        files_deleted: None,
        error: None,
    };

    (StatusCode::OK, axum::Json(response)).into_response()
}

/// DELETE /uploads/{session_id}/images/{filename}?secret={secret}
/// Delete a specific image file
#[instrument(skip(query))]
pub async fn delete_image_handler(
    Path((session_id, filename)): Path<(String, String)>,
    Query(query): Query<DeleteQuery>,
) -> Response {
    info!("DELETE image file: session={}, file={}", session_id, filename);

    // Validate session_id
    let session_uuid = match Uuid::parse_str(&session_id) {
        Ok(uuid) => uuid,
        Err(_) => return delete_error_response(StatusCode::BAD_REQUEST, "Invalid session_id format"),
    };

    // Validate secret format (min 8 chars)
    if query.secret.len() < 8 {
        return delete_error_response(StatusCode::BAD_REQUEST, "Secret must be at least 8 characters");
    }

    // Sanitize and validate filename
    let sanitized = sanitize_filename(&filename);
    if sanitized.is_empty() || sanitized.contains("..") || sanitized.contains('/') {
        return delete_error_response(StatusCode::BAD_REQUEST, "Invalid filename");
    }

    // Build file path
    let session_dir = PathBuf::from("uploads").join(session_uuid.to_string());
    let file_path = session_dir.join("images").join(&sanitized);

    // Check if session exists
    if !session_dir.exists() {
        return delete_error_response(StatusCode::NOT_FOUND, "Session does not exist");
    }

    // Validate secret
    match validate_secret(&session_uuid, &query.secret).await {
        Ok(_) => info!("Secret validated for DELETE"),
        Err(e) => {
            warn!("Invalid secret for DELETE: {}", e);
            return delete_error_response(StatusCode::FORBIDDEN, &e);
        }
    }

    // Check if file exists
    if !file_path.exists() {
        return delete_error_response(StatusCode::NOT_FOUND, "File not found");
    }

    // Delete the file
    if let Err(e) = fs::remove_file(&file_path).await {
        error!("Failed to delete file: {}", e);
        return delete_error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to delete file");
    }

    info!("File deleted successfully: {}", sanitized);

    let response = DeleteResponse {
        success: true,
        message: Some("File deleted successfully".to_string()),
        file: Some(sanitized),
        session_id: None,
        files_deleted: None,
        error: None,
    };

    (StatusCode::OK, axum::Json(response)).into_response()
}

/// DELETE /uploads/{session_id}?secret={secret}
/// Delete entire session (all files + metadata)
#[instrument(skip(query))]
pub async fn delete_session_handler(
    Path(session_id): Path<String>,
    Query(query): Query<DeleteQuery>,
) -> Response {
    info!("DELETE session: {}", session_id);

    // Validate session_id
    let session_uuid = match Uuid::parse_str(&session_id) {
        Ok(uuid) => uuid,
        Err(_) => return delete_error_response(StatusCode::BAD_REQUEST, "Invalid session_id format"),
    };

    // Validate secret format (min 8 chars)
    if query.secret.len() < 8 {
        return delete_error_response(StatusCode::BAD_REQUEST, "Secret must be at least 8 characters");
    }

    // Build session directory path
    let session_dir = PathBuf::from("uploads").join(session_uuid.to_string());

    // Check if session exists
    if !session_dir.exists() {
        return delete_error_response(StatusCode::NOT_FOUND, "Session does not exist");
    }

    // Validate secret
    match validate_secret(&session_uuid, &query.secret).await {
        Ok(_) => info!("Secret validated for session DELETE"),
        Err(e) => {
            warn!("Invalid secret for session DELETE: {}", e);
            return delete_error_response(StatusCode::FORBIDDEN, &e);
        }
    }

    // Count files before deletion
    let markdown_dir = session_dir.join("markdown");
    let images_dir = session_dir.join("images");

    let mut markdown_count = 0;
    let mut images_count = 0;

    if markdown_dir.exists() {
        if let Ok(mut entries) = fs::read_dir(&markdown_dir).await {
            while let Ok(Some(_)) = entries.next_entry().await {
                markdown_count += 1;
            }
        }
    }

    if images_dir.exists() {
        if let Ok(mut entries) = fs::read_dir(&images_dir).await {
            while let Ok(Some(_)) = entries.next_entry().await {
                images_count += 1;
            }
        }
    }

    // Delete entire session directory
    if let Err(e) = fs::remove_dir_all(&session_dir).await {
        error!("Failed to delete session: {}", e);
        return delete_error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to delete session");
    }

    info!("Session deleted successfully: {} (markdown: {}, images: {})",
          session_id, markdown_count, images_count);

    let response = DeleteResponse {
        success: true,
        message: Some("Session deleted successfully".to_string()),
        file: None,
        session_id: Some(session_id),
        files_deleted: Some(FilesDeleted {
            markdown: markdown_count,
            images: images_count,
        }),
        error: None,
    };

    (StatusCode::OK, axum::Json(response)).into_response()
}

/// Build delete error response
fn delete_error_response(status: StatusCode, message: &str) -> Response {
    let response = DeleteResponse {
        success: false,
        message: None,
        file: None,
        session_id: None,
        files_deleted: None,
        error: Some(message.to_string()),
    };

    (status, axum::Json(response)).into_response()
}
