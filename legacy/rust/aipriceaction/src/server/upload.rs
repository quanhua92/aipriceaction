use axum::{
    extract::{Multipart, Path, Query},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use serde::{Deserialize, Serialize};
use std::io::{self, ErrorKind};
use std::path::{Path as FsPath, PathBuf};
use std::sync::OnceLock;
use tokio::fs;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::Mutex;
use tracing::{error, info, instrument, warn};
use uuid::Uuid;

const MAX_MARKDOWN_SIZE: usize = 5 * 1024 * 1024;
const MAX_IMAGE_SIZE: usize = 10 * 1024 * 1024;
const MAX_FILENAME_LENGTH: usize = 200;

const ALLOWED_MARKDOWN_EXTS: &[&str] = &["md", "markdown", "txt"];
const ALLOWED_IMAGE_EXTS: &[&str] = &["jpg", "jpeg", "png", "gif", "webp", "svg"];
const DEFAULT_UPLOADS_MAX_BYTES: u64 = 512 * 1024 * 1024;
const DEFAULT_UPLOADS_MAX_SESSIONS: u64 = 10_000;

static UPLOADS_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

fn uploads_root() -> PathBuf {
    std::env::var_os("UPLOADS_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("uploads"))
}

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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub secret_sha256: Option<String>,
    // Rust's previous uploader wrote the secret in plaintext. Keep reading
    // those records so existing Notes remain fetchable and deletable.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub secret: Option<String>,
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
) -> Response {
    upload_markdown_file_handler(query, multipart).await
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

    if query.secret.chars().count() < 8 {
        return error_response(
            StatusCode::BAD_REQUEST,
            "Secret must be at least 8 characters",
        );
    }

    let metadata_path = uploads_root()
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
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Failed to create session",
            );
        }
        info!(session_id = %session_id, "Created new session with metadata");
    }

    let mut uploaded_files = Vec::new();

    while let Ok(Some(field)) = multipart.next_field().await {
        let filename = match field.file_name() {
            Some(name) => name.to_string(),
            None => {
                warn!("Field without filename, skipping");
                continue;
            }
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
                return error_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Failed to read file data",
                );
            }
        };

        if data.len() > file_type.max_size() {
            return error_response(
                StatusCode::PAYLOAD_TOO_LARGE,
                &format!(
                    "File size exceeds {}MB limit",
                    file_type.max_size() / (1024 * 1024)
                ),
            );
        }

        if let Err(e) = validate_mime_type(&data, file_type) {
            return error_response(StatusCode::UNSUPPORTED_MEDIA_TYPE, &e);
        }

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
        if file_path.exists() {
            return error_response(
                StatusCode::CONFLICT,
                &format!("File already exists: {}", sanitized),
            );
        }

        let temp_path = file_path.with_extension("tmp");
        if let Err(e) = fs::write(&temp_path, &data).await {
            error!(error = %e, path = %temp_path.display(), "Failed to write temp file");
            return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to save file");
        }

        match fs::rename(&temp_path, &file_path).await {
            Ok(_) => {
                info!(path = %file_path.display(), size = data.len(), "File saved successfully")
            }
            Err(e) => {
                error!(error = %e, "Failed to rename temp file");
                let _ = fs::remove_file(&temp_path).await;
                return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to save file");
            }
        }

        let content_type = detect_content_type(&data, file_type);
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

    (
        StatusCode::OK,
        axum::Json(UploadResponse {
            success: true,
            session_id: Some(session_id.to_string()),
            files: Some(uploaded_files),
            error: None,
        }),
    )
        .into_response()
}

async fn upload_markdown_file_handler(query: UploadQuery, mut multipart: Multipart) -> Response {
    let session_id = match validate_session_id(&query.session_id) {
        Ok(id) => id,
        Err(e) => return error_response(StatusCode::BAD_REQUEST, &e),
    };
    if query.secret.chars().count() < 8 {
        return error_response(
            StatusCode::BAD_REQUEST,
            "Secret must be at least 8 characters",
        );
    }

    // Parse and validate the whole multipart form before creating a session
    // or writing any file. Python accepts exactly one file named content.md.
    let mut files = Vec::new();
    loop {
        let field = match multipart.next_field().await {
            Ok(Some(field)) => field,
            Ok(None) => break,
            Err(_) => return error_response(StatusCode::BAD_REQUEST, "Invalid multipart form"),
        };
        if field.name() != Some("file") {
            continue;
        }
        let Some(filename) = field.file_name().map(str::to_owned) else {
            continue;
        };
        let data = match field.bytes().await {
            Ok(bytes) => bytes.to_vec(),
            Err(_) => return error_response(StatusCode::BAD_REQUEST, "Invalid multipart form"),
        };
        files.push((filename, data));
    }

    if files.is_empty() {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            axum::Json(serde_json::json!({"error": "Field required"})),
        )
            .into_response();
    }
    if files.len() != 1 {
        return error_response(
            StatusCode::BAD_REQUEST,
            "Exactly one markdown file is required",
        );
    }
    let (original_name, data) = files.pop().expect("one file was checked");
    if sanitize_filename(&original_name) != "content.md" {
        return error_response(StatusCode::BAD_REQUEST, "Expected filename: content.md");
    }
    if data.len() > MAX_MARKDOWN_SIZE {
        return error_response(StatusCode::PAYLOAD_TOO_LARGE, "File size exceeds 5MB limit");
    }
    if std::str::from_utf8(&data).is_err() {
        return error_response(
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            "Invalid file type. Expected markdown/text content",
        );
    }

    // The global lock keeps quota reservations and same-name writes atomic
    // within this server process. The API normally runs as one process per
    // shared uploads volume.
    let _guard = UPLOADS_LOCK.get_or_init(|| Mutex::new(())).lock().await;
    let root = uploads_root();
    let session_id_text = session_id.to_string();
    let session_dir = match safe_upload_directory(&root, &[&session_id_text], false) {
        Ok(path) => path,
        Err(e) => {
            error!(error = %e, "Invalid session upload path");
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Invalid upload storage path",
            );
        }
    };
    let metadata_path = session_dir.join("metadata.json");
    let metadata_exists = match std::fs::symlink_metadata(&metadata_path) {
        Ok(metadata) if metadata.is_file() => true,
        Ok(_) => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Failed to read session metadata",
            );
        }
        Err(e) if e.kind() == ErrorKind::NotFound => false,
        Err(_) => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Failed to read session metadata",
            );
        }
    };
    if metadata_exists {
        let metadata = match load_metadata(&session_id).await {
            Ok(metadata) => metadata,
            Err(_) => {
                return error_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Failed to read session metadata",
                );
            }
        };
        if !metadata_matches_secret(&metadata, &query.secret) {
            return error_response(StatusCode::FORBIDDEN, "Invalid secret for this session");
        }
        let markdown_dir =
            match safe_upload_directory(&root, &[&session_id_text, "markdown"], false) {
                Ok(path) => path,
                Err(_) => {
                    return error_response(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        "Invalid upload storage path",
                    );
                }
            };
        if markdown_dir.exists() {
            match fs::read_dir(&markdown_dir).await {
                Ok(mut entries) => {
                    if entries.next_entry().await.ok().flatten().is_some() {
                        return error_response(
                            StatusCode::CONFLICT,
                            "File already exists: content.md",
                        );
                    }
                }
                Err(_) => {
                    return error_response(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        "Invalid upload storage path",
                    );
                }
            }
        }
    }

    let (current_bytes, session_count) = match tokio::task::spawn_blocking({
        let root = root.clone();
        move || scan_upload_usage(&root)
    })
    .await
    {
        Ok(Ok(usage)) => usage,
        _ => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Failed to inspect upload storage",
            );
        }
    };
    if current_bytes.saturating_add(data.len() as u64) > uploads_max_bytes() {
        return error_response(
            StatusCode::INSUFFICIENT_STORAGE,
            "Notes storage quota exceeded",
        );
    }
    if !metadata_exists && session_count >= uploads_max_sessions() {
        return error_response(
            StatusCode::INSUFFICIENT_STORAGE,
            "Notes session limit reached",
        );
    }

    let markdown_dir = match safe_upload_directory(&root, &[&session_id_text, "markdown"], true) {
        Ok(path) => path,
        Err(e) => {
            error!(error = %e, "Invalid markdown upload path");
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Invalid upload storage path",
            );
        }
    };
    let file_path = markdown_dir.join("content.md");
    let temp_path = markdown_dir.join(format!(".upload-{}", Uuid::new_v4()));
    if let Err(e) = write_new_file_nofollow(&temp_path, &data).await {
        error!(error = %e, "Failed to write markdown temporary file");
        let _ = fs::remove_file(&temp_path).await;
        return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to save file");
    }
    if let Err(e) = fs::hard_link(&temp_path, &file_path).await {
        let _ = fs::remove_file(&temp_path).await;
        if e.kind() == std::io::ErrorKind::AlreadyExists {
            return error_response(StatusCode::CONFLICT, "File already exists: content.md");
        }
        error!(error = %e, "Failed to publish markdown file");
        return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to save file");
    }
    let _ = fs::remove_file(&temp_path).await;

    if !metadata_exists {
        if let Err(e) = save_metadata(&session_id, &query.secret).await {
            let _ = fs::remove_file(&file_path).await;
            let _ = fs::remove_dir_all(&session_dir).await;
            error!(error = %e, "Failed to create session metadata");
            return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Failed to save file");
        }
    }

    let content_type = FileType::Markdown.default_content_type().to_string();
    let response = UploadResponse {
        success: true,
        session_id: Some(session_id.to_string()),
        files: Some(vec![FileInfo {
            original_name,
            stored_name: "content.md".to_string(),
            size_bytes: data.len(),
            content_type,
            url: format!("/uploads/{}/markdown/content.md", session_id),
        }]),
        error: None,
    };
    (StatusCode::OK, axum::Json(response)).into_response()
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

    let is_markdown = matches!(file_type, FileType::Markdown);
    if is_markdown {
        if filename.is_empty()
            || filename != sanitize_filename(&filename)
            || filename.starts_with('.')
            || filename.contains("..")
        {
            return error_response(
                StatusCode::BAD_REQUEST,
                "Invalid filename: path traversal detected",
            );
        }
    } else if let Err(e) = validate_path_component(&filename) {
        return error_response(StatusCode::BAD_REQUEST, &e);
    }

    let _guard = UPLOADS_LOCK.get_or_init(|| Mutex::new(())).lock().await;
    let root = uploads_root();
    let session_id_text = session_uuid.to_string();
    let _session_dir = match safe_upload_directory(&root, &[&session_id_text], false) {
        Ok(path) => path,
        Err(e) => {
            error!(error = %e, "Invalid session upload path");
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Invalid upload storage path",
            );
        }
    };
    let file_dir =
        match safe_upload_directory(&root, &[&session_id_text, file_type.subdirectory()], false) {
            Ok(path) => path,
            Err(e) => {
                error!(error = %e, "Invalid upload file directory");
                return error_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Invalid upload storage path",
                );
            }
        };

    if is_markdown {
        let metadata = match load_metadata(&session_uuid).await {
            Ok(metadata) => metadata,
            Err(_) => return error_response(StatusCode::NOT_FOUND, "Session does not exist"),
        };
        // The mounted Python Notes route serves public sessions and returns
        // Access denied for private sessions, regardless of a query secret.
        if !metadata.is_public {
            return error_response(StatusCode::FORBIDDEN, "Access denied");
        }
    } else {
        match check_read_access(&session_uuid, secret.as_deref()).await {
            Ok(true) => info!(session_id = %session_uuid, "Read access granted"),
            Ok(false) => {
                warn!(session_id = %session_uuid, "Read access denied");
                return error_response(StatusCode::FORBIDDEN, "Access denied");
            }
            Err(e) => {
                warn!(session_id = %session_uuid, error = %e, "Read access check failed");
                return error_response(StatusCode::FORBIDDEN, &e);
            }
        }
    }

    let file_path = file_dir.join(&filename);
    let data = match read_regular_file_nofollow(&file_path).await {
        Ok(data) => data,
        Err(e) if e.kind() == ErrorKind::NotFound => {
            return error_response(StatusCode::NOT_FOUND, "File not found");
        }
        Err(e) if is_markdown => {
            error!(error = %e, path = %file_path.display(), "Invalid Notes file path");
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Invalid upload storage path",
            );
        }
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
        return Err(format!(
            "Invalid file extension. Expected: {}",
            file_type.allowed_extensions().join(", ")
        ));
    }
    Ok(())
}

fn validate_mime_type(data: &[u8], file_type: FileType) -> Result<(), String> {
    match file_type {
        FileType::Markdown => {
            std::str::from_utf8(data)
                .map_err(|_| "Invalid file type. Expected markdown/text content".to_string())?;
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

/// Resolve a path below the configured uploads root without accepting symlink
/// components. The root itself is trusted and resolved as Python's storage
/// layer does; session and subdirectory entries must be real directories.
fn safe_upload_directory(root: &FsPath, parts: &[&str], create: bool) -> io::Result<PathBuf> {
    std::fs::create_dir_all(root)?;
    let anchor = std::fs::canonicalize(root)?;
    let mut current = anchor.clone();

    for part in parts {
        let next = current.join(part);
        if create {
            match std::fs::create_dir(&next) {
                Ok(()) => {}
                Err(e) if e.kind() == ErrorKind::AlreadyExists => {}
                Err(e) => return Err(e),
            }
        }

        let info = match std::fs::symlink_metadata(&next) {
            Ok(info) => info,
            Err(e) if e.kind() == ErrorKind::NotFound => return Ok(next),
            Err(e) => return Err(e),
        };
        if !info.file_type().is_dir() {
            return Err(io::Error::new(
                ErrorKind::InvalidInput,
                "upload storage path is not a real directory",
            ));
        }

        let resolved = std::fs::canonicalize(&next)?;
        if !resolved.starts_with(&anchor) {
            return Err(io::Error::new(
                ErrorKind::InvalidInput,
                "upload storage path escapes its root",
            ));
        }
        current = resolved;
    }
    Ok(current)
}

async fn read_regular_file_nofollow(path: &FsPath) -> io::Result<Vec<u8>> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW);

    let mut file = options.open(path).await?;
    if !file.metadata().await?.is_file() {
        return Err(io::Error::new(
            ErrorKind::InvalidInput,
            "upload storage path is not a regular file",
        ));
    }
    let mut content = Vec::new();
    file.read_to_end(&mut content).await?;
    Ok(content)
}

async fn write_new_file_nofollow(path: &FsPath, content: &[u8]) -> io::Result<()> {
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW);

    let mut file = options.open(path).await?;
    file.write_all(content).await?;
    file.sync_all().await
}

fn count_regular_files(directory: &FsPath) -> usize {
    let Ok(entries) = std::fs::read_dir(directory) else {
        return 0;
    };
    entries
        .filter_map(Result::ok)
        .filter(|entry| {
            std::fs::symlink_metadata(entry.path())
                .map(|metadata| metadata.file_type().is_file())
                .unwrap_or(false)
        })
        .count()
}

async fn ensure_session_dir(session_id: &Uuid, file_type: FileType) -> std::io::Result<PathBuf> {
    let base = uploads_root();
    let session_id_text = session_id.to_string();
    safe_upload_directory(&base, &[&session_id_text, file_type.subdirectory()], true)
}

fn detect_content_type(data: &[u8], file_type: FileType) -> String {
    if matches!(file_type, FileType::Markdown) {
        return file_type.default_content_type().to_string();
    }
    match infer::get(data) {
        Some(kind) => kind.mime_type().to_string(),
        None => file_type.default_content_type().to_string(),
    }
}

async fn load_metadata(session_id: &Uuid) -> Result<SessionMetadata, String> {
    let root = uploads_root();
    let session_id_text = session_id.to_string();
    let session_dir = safe_upload_directory(&root, &[&session_id_text], false)
        .map_err(|e| format!("Invalid upload storage path: {e}"))?;
    let metadata_path = session_dir.join("metadata.json");
    let content = read_regular_file_nofollow(&metadata_path)
        .await
        .map_err(|e| format!("Failed to read metadata: {e}"))?;
    serde_json::from_slice(&content).map_err(|e| format!("Failed to parse metadata: {e}"))
}

async fn save_metadata(session_id: &Uuid, secret: &str) -> Result<(), String> {
    let root = uploads_root();
    let session_id_text = session_id.to_string();
    let session_dir = safe_upload_directory(&root, &[&session_id_text], true)
        .map_err(|e| format!("Failed to create session directory: {e}"))?;

    let metadata = SessionMetadata {
        session_id: session_id.to_string(),
        secret_sha256: Some(secret_digest(secret)),
        secret: None,
        is_public: true,
        created_at: chrono::Utc::now().to_rfc3339(),
    };

    let metadata_path = session_dir.join("metadata.json");
    let temp_path = session_dir.join(format!(".metadata-{}", Uuid::new_v4()));
    let json = serde_json::to_vec(&metadata)
        .map_err(|e| format!("Failed to serialize metadata: {}", e))?;
    if let Err(e) = write_new_file_nofollow(&temp_path, &json).await {
        let _ = fs::remove_file(&temp_path).await;
        return Err(format!("Failed to write metadata: {e}"));
    }
    if let Err(e) = fs::hard_link(&temp_path, &metadata_path).await {
        let _ = fs::remove_file(&temp_path).await;
        return Err(format!("Failed to publish metadata: {e}"));
    }
    fs::remove_file(&temp_path)
        .await
        .map_err(|e| format!("Failed to remove temporary metadata: {}", e))?;
    Ok(())
}

async fn validate_secret(
    session_id: &Uuid,
    provided_secret: &str,
) -> Result<SessionMetadata, String> {
    let metadata = load_metadata(session_id).await?;
    if !metadata_matches_secret(&metadata, provided_secret) {
        return Err("Invalid secret".to_string());
    }
    Ok(metadata)
}

fn metadata_matches_secret(metadata: &SessionMetadata, provided_secret: &str) -> bool {
    if let Some(expected) = metadata.secret_sha256.as_deref() {
        expected == secret_digest(provided_secret)
    } else {
        metadata.secret.as_deref() == Some(provided_secret)
    }
}

fn secret_digest(secret: &str) -> String {
    use sha2::{Digest, Sha256};

    hex::encode(Sha256::digest(secret.as_bytes()))
}

async fn check_read_access(
    session_id: &Uuid,
    provided_secret: Option<&str>,
) -> Result<bool, String> {
    let metadata = load_metadata(session_id).await?;
    if metadata.is_public {
        return Ok(true);
    }
    match provided_secret {
        Some(secret) if metadata_matches_secret(&metadata, secret) => Ok(true),
        Some(_) => Err("Invalid secret".to_string()),
        None => Err("Secret required for private session".to_string()),
    }
}

fn uploads_max_bytes() -> u64 {
    configured_upload_limit("UPLOADS_MAX_BYTES", DEFAULT_UPLOADS_MAX_BYTES)
}

fn uploads_max_sessions() -> u64 {
    configured_upload_limit("UPLOADS_MAX_SESSIONS", DEFAULT_UPLOADS_MAX_SESSIONS)
}

fn configured_upload_limit(name: &str, default: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(default)
}

fn scan_upload_usage(root: &FsPath) -> std::io::Result<(u64, u64)> {
    if !root.exists() {
        return Ok((0, 0));
    }
    let mut total_bytes = 0_u64;
    let mut session_count = 0_u64;
    for entry in std::fs::read_dir(root)? {
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => continue,
        };
        let name = entry.file_name();
        if name.to_string_lossy().starts_with('.') {
            continue;
        }
        let file_type = match entry.file_type() {
            Ok(file_type) => file_type,
            Err(_) => continue,
        };
        if !file_type.is_dir() {
            continue;
        }
        let session_path = entry.path();
        if std::fs::metadata(session_path.join("metadata.json"))
            .map(|metadata| metadata.is_file())
            .unwrap_or(false)
        {
            session_count = session_count.saturating_add(1);
        }
        sum_regular_files(&session_path, &mut total_bytes)?;
    }
    Ok((total_bytes, session_count))
}

fn sum_regular_files(path: &FsPath, total_bytes: &mut u64) -> std::io::Result<()> {
    for entry in std::fs::read_dir(path)? {
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => continue,
        };
        let file_type = match entry.file_type() {
            Ok(file_type) => file_type,
            Err(_) => continue,
        };
        if file_type.is_dir() {
            sum_regular_files(&entry.path(), total_bytes)?;
        } else if file_type.is_file() && entry.file_name() != "metadata.json" {
            if let Ok(metadata) = entry.metadata() {
                *total_bytes = total_bytes.saturating_add(metadata.len());
            }
        }
    }
    Ok(())
}

fn error_response(status: StatusCode, message: &str) -> Response {
    (
        status,
        axum::Json(UploadResponse {
            success: false,
            session_id: None,
            files: None,
            error: Some(message.to_string()),
        }),
    )
        .into_response()
}

#[instrument(skip(query))]
pub async fn delete_markdown_handler(
    Path((session_id, filename)): Path<(String, String)>,
    Query(query): Query<DeleteQuery>,
) -> Response {
    info!(
        "DELETE markdown file: session={}, file={}",
        session_id, filename
    );
    delete_file_handler(session_id, filename, "markdown", &query.secret).await
}

#[instrument(skip(query))]
pub async fn delete_image_handler(
    Path((session_id, filename)): Path<(String, String)>,
    Query(query): Query<DeleteQuery>,
) -> Response {
    info!(
        "DELETE image file: session={}, file={}",
        session_id, filename
    );
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
        Err(_) => {
            return delete_error_response(StatusCode::BAD_REQUEST, "Invalid session_id format");
        }
    };

    if secret.chars().count() < 8 {
        return delete_error_response(
            StatusCode::BAD_REQUEST,
            "Secret must be at least 8 characters",
        );
    }

    let sanitized = sanitize_filename(&filename);
    if sanitized.is_empty() || sanitized.contains("..") || sanitized.contains('/') {
        return delete_error_response(StatusCode::BAD_REQUEST, "Invalid filename");
    }

    let session_dir = uploads_root().join(session_uuid.to_string());
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

    (
        StatusCode::OK,
        axum::Json(DeleteResponse {
            success: true,
            message: Some("File deleted successfully".to_string()),
            file: Some(sanitized),
            session_id: None,
            files_deleted: None,
            error: None,
        }),
    )
        .into_response()
}

#[instrument(skip(query))]
pub async fn delete_session_handler(
    Path(session_id): Path<String>,
    Query(query): Query<DeleteQuery>,
) -> Response {
    info!("DELETE session: {}", session_id);

    let session_uuid = match Uuid::parse_str(&session_id) {
        Ok(uuid) => uuid,
        Err(_) => {
            return delete_error_response(StatusCode::BAD_REQUEST, "Invalid session_id format");
        }
    };

    if query.secret.chars().count() < 8 {
        return delete_error_response(
            StatusCode::BAD_REQUEST,
            "Secret must be at least 8 characters",
        );
    }

    let _guard = UPLOADS_LOCK.get_or_init(|| Mutex::new(())).lock().await;
    let root = uploads_root();
    let session_id_text = session_uuid.to_string();
    let session_dir = match safe_upload_directory(&root, &[&session_id_text], false) {
        Ok(path) => path,
        Err(e) => {
            error!(error = %e, "Invalid session upload path");
            return delete_error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Invalid upload storage path",
            );
        }
    };

    let metadata = match load_metadata(&session_uuid).await {
        Ok(metadata) => metadata,
        Err(_) => return delete_error_response(StatusCode::NOT_FOUND, "Session does not exist"),
    };
    if !metadata_matches_secret(&metadata, &query.secret) {
        warn!("Invalid secret for session DELETE");
        return delete_error_response(StatusCode::FORBIDDEN, "Invalid secret");
    }

    let markdown_dir = safe_upload_directory(&root, &[&session_id_text, "markdown"], false);
    let images_dir = safe_upload_directory(&root, &[&session_id_text, "images"], false);
    let (markdown_count, images_count) = match (markdown_dir, images_dir) {
        (Ok(markdown), Ok(images)) => {
            (count_regular_files(&markdown), count_regular_files(&images))
        }
        // Python treats either unsafe child directory as an invalid storage
        // path for accounting, resets both counts, then removes the session.
        _ => (0, 0),
    };

    if let Err(e) = fs::remove_dir_all(&session_dir).await {
        error!("Failed to delete session: {}", e);
        return delete_error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Failed to delete session",
        );
    }

    info!(
        "Session deleted successfully: {} (markdown: {}, images: {})",
        session_id, markdown_count, images_count
    );

    (
        StatusCode::OK,
        axum::Json(DeleteResponse {
            success: true,
            message: Some("Session deleted successfully".to_string()),
            file: None,
            session_id: Some(session_uuid.to_string()),
            files_deleted: Some(FilesDeleted {
                markdown: markdown_count,
                images: images_count,
            }),
            error: None,
        }),
    )
        .into_response()
}

fn delete_error_response(status: StatusCode, message: &str) -> Response {
    (
        status,
        axum::Json(DeleteResponse {
            success: false,
            message: None,
            file: None,
            session_id: None,
            files_deleted: None,
            error: Some(message.to_string()),
        }),
    )
        .into_response()
}
