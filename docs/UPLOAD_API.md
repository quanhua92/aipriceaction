# Upload API Documentation

## Overview

The Upload API provides session-based file storage for markdown documents and images. Files are organized by UUID session identifiers, allowing isolated storage contexts for different users or workflows.

## Directory Structure

```
uploads/
└── {session-uuid}/
    ├── markdown/
    │   ├── notes.md
    │   └── draft.md
    └── images/
        ├── screenshot.png
        └── diagram.jpg
```

## Authentication

Each session is protected by a secret key that must be provided by the client for write operations (uploads). The secret and session settings are stored in `metadata.json`.

**Security Model:**
- **Write operations (POST) always require secret** - Upload markdown/images
- **Read operations (GET) are public by default** - Anyone with the URL can download files
- First upload: Creates session with client-provided secret, stores in `metadata.json`
- Subsequent uploads: Validates provided secret against stored secret
- Wrong/missing secret on upload: Returns `403 Forbidden`
- Secret is stored in `uploads/{session-uuid}/metadata.json`

**Session Configuration (metadata.json):**
```json
{
  "session_id": "018c9f8e-7a2b-7890-a456-123456789abc",
  "secret": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
  "is_public": true,
  "created_at": "2025-11-24T14:30:00Z"
}
```

- `is_public: true` (default): Files can be read without secret (public sharing)
- `is_public: false`: Files require secret to read (private session)
- **Note:** Currently `is_public` always defaults to `true` (future: client can set via parameter)

**Recommended Secret Generation:**
- Use UUID v4 or similar cryptographically secure random string
- Minimum 32 characters recommended
- Client should store the secret securely (e.g., localStorage, secure storage)

## Endpoints

### 1. Upload Markdown File

**Endpoint:** `POST /upload/markdown`

**Query Parameters:**
- `session_id` (required): UUID string identifying the session (any UUID version accepted)
- `secret` (required): Session secret key (client-generated, min 32 characters recommended)

**Request:**
- Content-Type: `multipart/form-data`
- Form field name: `file`
- Max file size: 5MB
- Allowed extensions: `.md`, `.markdown`, `.txt`

**Example:**
```bash
# Generate session ID and secret (client-side)
SESSION_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
SECRET=$(uuidgen | tr '[:upper:]' '[:lower:]')

echo "Session ID: $SESSION_ID"
echo "Secret: $SECRET"
echo "IMPORTANT: Store these securely!"

# First upload - creates session with secret
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@notes.md"

# Subsequent uploads - same secret required
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@more-notes.md"

# Wrong secret - returns 403 Forbidden
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID&secret=wrong-secret" \
  -F "file=@another.md"
```

**Success Response (200 OK):**
```json
{
  "success": true,
  "session_id": "018c9f8e-7a2b-7890-a456-123456789abc",
  "files": [
    {
      "original_name": "notes.md",
      "stored_name": "notes.md",
      "size_bytes": 1024,
      "content_type": "text/markdown; charset=utf-8",
      "url": "/uploads/018c9f8e-7a2b-7890-a456-123456789abc/markdown/notes.md"
    }
  ]
}
```

**Error Responses:**
- `400 Bad Request`: Missing session_id/secret, invalid UUID, no file provided, invalid extension, invalid secret format
- `403 Forbidden`: Wrong secret for existing session, or missing secret
- `409 Conflict`: File already exists (duplicate filename)
- `413 Payload Too Large`: File exceeds 5MB (may return 500 for very large files)
- `415 Unsupported Media Type`: File is not valid markdown/text
- `500 Internal Server Error`: Server error (disk write failed, or request body parsing failed for very large files)

---

### 2. Upload Image File

**Endpoint:** `POST /upload/image`

**Query Parameters:**
- `session_id` (required): UUID string identifying the session (any UUID version accepted)
- `secret` (required): Session secret key (client-generated, min 32 characters recommended)

**Request:**
- Content-Type: `multipart/form-data`
- Form field name: `file`
- Max file size: 10MB
- Allowed extensions: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.svg`

**Example:**
```bash
# Use same session ID and secret from markdown upload
SESSION_ID="018c9f8e-7a2b-7890-a456-123456789abc"
SECRET="a1b2c3d4-e5f6-7890-1234-567890abcdef"

# Upload an image to existing session
curl -X POST "http://localhost:3000/upload/image?session_id=$SESSION_ID&secret=$SECRET" \
  -F "file=@screenshot.png"
```

**Success Response (200 OK):**
```json
{
  "success": true,
  "session_id": "018c9f8e-7a2b-7890-a456-123456789abc",
  "files": [
    {
      "original_name": "screenshot.png",
      "stored_name": "screenshot.png",
      "size_bytes": 524288,
      "content_type": "image/png",
      "url": "/uploads/018c9f8e-7a2b-7890-a456-123456789abc/images/screenshot.png"
    }
  ]
}
```

**Error Responses:**
- `400 Bad Request`: Missing session_id/secret, invalid UUID, no file provided, invalid extension, invalid secret format
- `403 Forbidden`: Wrong secret for existing session, or missing secret
- `409 Conflict`: File already exists (duplicate filename)
- `413 Payload Too Large`: File exceeds 10MB (may return 500 for very large files)
- `415 Unsupported Media Type`: File is not a valid image
- `500 Internal Server Error`: Server error (disk write failed, or request body parsing failed for very large files)

---

### 3. Retrieve Markdown File

**Endpoint:** `GET /uploads/{session_id}/markdown/{filename}`

**Path Parameters:**
- `session_id`: UUID identifying the session
- `filename`: Name of the markdown file

**Query Parameters:**
- `secret` (optional): Session secret key - only required if `is_public: false` in metadata.json

**Example:**
```bash
SESSION_ID="018c9f8e-7a2b-7890-a456-123456789abc"

# Public session (is_public: true) - no secret needed
curl "http://localhost:3000/uploads/$SESSION_ID/markdown/notes.md"

# Private session (is_public: false) - secret required
SECRET="a1b2c3d4-e5f6-7890-1234-567890abcdef"
curl "http://localhost:3000/uploads/$SESSION_ID/markdown/notes.md?secret=$SECRET"
```

**Success Response (200 OK):**
- Content-Type: `text/markdown; charset=utf-8`
- Body: Raw file contents

**Response Headers:**
```
Content-Type: text/markdown; charset=utf-8
Content-Disposition: inline; filename="notes.md"
Cache-Control: public, max-age=3600
X-Content-Type-Options: nosniff
```

**Error Responses:**
- `400 Bad Request`: Invalid session_id or filename (path traversal detected)
- `403 Forbidden`: Session is private (`is_public: false`) and secret is missing or wrong
- `404 Not Found`: Session or file doesn't exist
- `500 Internal Server Error`: File read error

---

### 4. Retrieve Image File

**Endpoint:** `GET /uploads/{session_id}/images/{filename}`

**Path Parameters:**
- `session_id`: UUID identifying the session
- `filename`: Name of the image file

**Query Parameters:**
- `secret` (optional): Session secret key - only required if `is_public: false` in metadata.json

**Example:**
```bash
SESSION_ID="018c9f8e-7a2b-7890-a456-123456789abc"

# Public session - download image (no secret needed)
curl -O "http://localhost:3000/uploads/$SESSION_ID/images/screenshot.png"

# Use in HTML (public session)
<img src="http://localhost:3000/uploads/$SESSION_ID/images/screenshot.png" />

# Private session - secret required
SECRET="a1b2c3d4-e5f6-7890-1234-567890abcdef"
curl "http://localhost:3000/uploads/$SESSION_ID/images/screenshot.png?secret=$SECRET"
```

**Success Response (200 OK):**
- Content-Type: `image/png`, `image/jpeg`, etc. (based on file type)
- Body: Raw image bytes

**Response Headers:**
```
Content-Type: image/png
Content-Disposition: inline; filename="screenshot.png"
Cache-Control: public, max-age=3600
X-Content-Type-Options: nosniff
```

**Error Responses:**
- `400 Bad Request`: Invalid session_id or filename (path traversal detected)
- `403 Forbidden`: Session is private (`is_public: false`) and secret is missing or wrong
- `404 Not Found`: Session or file doesn't exist
- `500 Internal Server Error`: File read error

---

## Security & Validation

### Filename Sanitization
Uploaded filenames are automatically sanitized:
- Path separators (`/`, `\`, `\0`) replaced with `_`
- Dangerous characters (`<`, `>`, `:`, `"`, `|`, `?`, `*`) replaced with `_`
- Control characters replaced with `_`
- Leading dots stripped (prevents hidden files)
- Truncated to 200 characters

**Example:**
- Input: `../../../etc/passwd.md`
- Sanitized: `.._.._..etc_passwd.md`

### Session ID Validation
- Must be a valid UUID (any version accepted)
- Path traversal attempts rejected
- Empty or malformed IDs return `400 Bad Request`

### File Type Validation
The API validates files using multiple methods:
1. **Extension check**: Ensures extension matches endpoint
2. **MIME type detection**: Analyzes file bytes to detect actual type
3. **Content validation**:
   - Markdown: Must be valid UTF-8 text
   - Images: Must have valid image MIME type
   - SVG: Rejected if contains `<script>` tags (XSS prevention)

### Path Traversal Prevention
All file operations validate paths:
- Reject filenames containing `..`, `/`, `\`
- Reject filenames starting with `.`
- Ensure all paths resolve within session directory

### Rate Limiting
- 5000 requests/second per IP address (burst: 10000)
- Applies to all API endpoints via middleware

### File Size Limits
- Markdown files: 5MB maximum
- Image files: 10MB maximum
- Enforced by middleware before file processing

---

## Error Handling

### Error Response Format
All errors return JSON with consistent structure:

```json
{
  "success": false,
  "error": "Error message description"
}
```

### Common Error Codes

**400 Bad Request**
```json
{
  "success": false,
  "error": "Missing required parameter: session_id"
}
```
```json
{
  "success": false,
  "error": "Invalid session_id format"
}
```
```json
{
  "success": false,
  "error": "Invalid file extension. Expected: .md, .markdown, .txt"
}
```

**409 Conflict**
```json
{
  "success": false,
  "error": "File already exists: notes.md"
}
```

**413 Payload Too Large**
```json
{
  "success": false,
  "error": "File size exceeds 5MB limit"
}
```

**415 Unsupported Media Type**
```json
{
  "success": false,
  "error": "Invalid file type. Expected markdown/text content"
}
```

**500 Internal Server Error**
```json
{
  "success": false,
  "error": "Failed to save file: disk write error"
}
```

---

## Use Cases

### 1. Store User Notes
```bash
SESSION_ID="018c9f8e-7a2b-7890-a456-123456789abc"

# Create a note
echo "# My Notes\n\nSome content here" > notes.md

# Upload
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID" \
  -F "file=@notes.md"

# Retrieve later
curl "http://localhost:3000/uploads/$SESSION_ID/markdown/notes.md"
```

### 2. Upload Screenshots
```bash
SESSION_ID="018c9f8e-7a2b-7890-a456-123456789abc"

# Upload screenshot
curl -X POST "http://localhost:3000/upload/image?session_id=$SESSION_ID" \
  -F "file=@screenshot.png"

# Get image URL for embedding
URL="http://localhost:3000/uploads/$SESSION_ID/images/screenshot.png"
echo "<img src=\"$URL\" />"
```

### 3. Multiple Files in Same Session
```bash
SESSION_ID="018c9f8e-7a2b-7890-a456-123456789abc"

# Upload multiple documents
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID" \
  -F "file=@chapter1.md"
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID" \
  -F "file=@chapter2.md"

# Upload related images
curl -X POST "http://localhost:3000/upload/image?session_id=$SESSION_ID" \
  -F "file=@diagram1.png"
curl -X POST "http://localhost:3000/upload/image?session_id=$SESSION_ID" \
  -F "file=@diagram2.png"
```

---

## Edge Cases

### Duplicate Filenames
If you upload a file with the same name twice:
```bash
# First upload - succeeds
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID" \
  -F "file=@notes.md"

# Second upload with same filename - fails with 409
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID" \
  -F "file=@notes.md"
# Returns: {"success": false, "error": "File already exists: notes.md"}
```

**Workaround:** Rename the file before uploading:
```bash
cp notes.md notes_v2.md
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID" \
  -F "file=@notes_v2.md"
```

### Special Characters in Filenames
Files with special characters are automatically sanitized:
```bash
# Upload file with special characters
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID" \
  -F "file=@my:special<file>.md"

# Stored as: my_special_file_.md
# Retrieve using sanitized name:
curl "http://localhost:3000/uploads/$SESSION_ID/markdown/my_special_file_.md"
```

### Very Long Filenames
Filenames are truncated to 200 characters:
```bash
# 300-character filename gets truncated to 200 characters
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID" \
  -F "file=@$(python3 -c 'print("a" * 300)').md"
```

### Non-UTF-8 Content for Markdown
Binary data uploaded to markdown endpoint is rejected:
```bash
# Upload binary file to markdown endpoint - fails with 415
curl -X POST "http://localhost:3000/upload/markdown?session_id=$SESSION_ID" \
  -F "file=@image.png"
# Returns: {"success": false, "error": "Invalid file type. Expected markdown/text content"}
```

---

## Integration Examples

### JavaScript/TypeScript
```typescript
async function uploadMarkdown(sessionId: string, file: File): Promise<void> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(
    `http://localhost:3000/upload/markdown?session_id=${sessionId}`,
    {
      method: 'POST',
      body: formData,
    }
  );

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error);
  }

  const result = await response.json();
  console.log('Uploaded:', result.files[0].url);
}

// Usage
const sessionId = '018c9f8e-7a2b-7890-a456-123456789abc';
const file = new File(['# Hello World'], 'hello.md', { type: 'text/markdown' });
await uploadMarkdown(sessionId, file);
```

### Python
```python
import requests

def upload_markdown(session_id: str, file_path: str) -> dict:
    with open(file_path, 'rb') as f:
        files = {'file': f}
        response = requests.post(
            f'http://localhost:3000/upload/markdown',
            params={'session_id': session_id},
            files=files
        )
        response.raise_for_status()
        return response.json()

# Usage
session_id = '018c9f8e-7a2b-7890-a456-123456789abc'
result = upload_markdown(session_id, 'notes.md')
print(f"Uploaded: {result['files'][0]['url']}")
```

---

## Docker Deployment

The uploads directory is mounted as a volume for persistence:

**docker-compose.yml:**
```yaml
services:
  aipriceaction:
    volumes:
      - ./uploads:/app/uploads
```

**docker-compose.local.yml:**
```yaml
services:
  aipriceaction:
    volumes:
      - /Volumes/data/workspace/aipriceaction/uploads:/app/uploads
```

Files uploaded to the API persist across container restarts.

---

## Future Enhancements

### Planned Features
- **Authentication**: JWT/API key validation per session
- **Session Management**: List all files in a session
- **Batch Operations**: Upload multiple files in single request
- **Markdown Rendering**: Convert .md to HTML on-the-fly
- **File Deletion**: Delete individual files or entire sessions
- **Cleanup Worker**: Auto-delete sessions older than 30 days
- **Storage Backend**: S3/R2 integration for production scale

### Not Planned
- ~~File versioning~~ (use different filenames: `notes_v1.md`, `notes_v2.md`)
- ~~Auto-rename on duplicate~~ (client should handle renaming)
- ~~File compression~~ (client can compress before upload if needed)
- ~~Folder structure within session~~ (keep flat structure for simplicity)

---

## Troubleshooting

### Upload Fails with 500 Error
Check disk space:
```bash
df -h
```

Check uploads directory permissions:
```bash
ls -la uploads/
# Should be writable by user running the server
```

### File Not Found After Upload
Verify the file was actually saved:
```bash
ls -la uploads/{session-id}/markdown/
```

Check you're using the correct session ID:
```bash
# The session ID in upload and retrieval must match exactly
```

### Docker: Permission Denied
Ensure uploads directory has correct ownership:
```bash
sudo chown -R 1000:1000 uploads/
chmod 755 uploads/
```

### Large File Upload Fails
Check body size limit in server configuration:
```rust
// In src/server/mod.rs
.layer(RequestBodyLimitLayer::new(10 * 1024 * 1024)) // 10MB
```

---

## API Testing

See `scripts/test-upload.sh` for comprehensive test suite covering:
- ✅ Successful markdown upload
- ✅ Successful image upload
- ✅ File retrieval
- ✅ Duplicate file detection (409 error)
- ✅ Invalid session ID (400 error)
- ✅ Path traversal prevention (400 error)
- ✅ Wrong file type (415 error)
- ✅ Missing parameters (400 error)
- ✅ File too large (413 error)

```bash
# Run all tests
./scripts/test-upload.sh

# Test against production
./scripts/test-upload.sh https://api.aipriceaction.com
```
