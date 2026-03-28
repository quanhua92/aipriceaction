/**
 * Test suite for upload endpoints on the PostgreSQL backend.
 *
 * Tests file upload, retrieval, deletion, error handling.
 *
 * Usage:
 *   node scripts/test-upload.mjs                 # default: http://localhost:3000
 *   node scripts/test-upload.mjs http://localhost:3001
 */

import { randomUUID } from "node:crypto";

const BASE_URL = process.argv[2] || "http://localhost:3000";

let passed = 0;
let failed = 0;

function ok(label) {
  passed++;
  console.log(`  ✅ ${label}`);
}

function fail(label, detail) {
  failed++;
  console.log(`  ❌ ${label}`);
  if (detail) console.log(`     ${detail}`);
}

function assert(cond, label, detail) {
  if (cond) ok(label);
  else fail(label, detail);
}

function uuid() {
  return randomUUID();
}

async function fetchJSON(path, opts = {}) {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, opts);
  const text = await res.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = text;
  }
  return { status: res.status, headers: res.headers, body };
}

async function fetchRaw(path, opts = {}) {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, opts);
  const buffer = await res.arrayBuffer();
  return { status: res.status, headers: res.headers, body: Buffer.from(buffer) };
}

async function uploadFile(type, sessionId, secret, filename, content) {
  const url = `${BASE_URL}/upload/${type}?session_id=${sessionId}&secret=${secret}`;
  const blob = new Blob([content], { type: "text/plain" });
  const formData = new FormData();
  formData.append("file", blob, filename);
  const res = await fetch(url, { method: "POST", body: formData });
  const text = await res.text();
  let body;
  try { body = JSON.parse(text); } catch { body = text; }
  return { status: res.status, headers: res.headers, body };
}

// ──────────────────────────────────────────────
// Upload / Retrieve / Delete markdown
// ──────────────────────────────────────────────

async function testUploadMarkdown() {
  const sid = uuid();
  const secret = uuid();
  const content = "# Test\n\nHello world.";

  const { status, body, ms } = await uploadFile("markdown", sid, secret, "test.md", content);
  console.log(`\n── POST /upload/markdown (new session) ──`);
  assert(status === 200, `returns 200 (got ${status})`);
  assert(body.success === true, "success = true");
  assert(body.session_id === sid, "session_id echoed");
  assert(Array.isArray(body.files), "files is array");
  assert(body.files.length === 1, `1 file (got ${body.files?.length})`);
  assert(body.files[0].original_name === "test.md", `filename = 'test.md'`);
  assert(typeof body.files[0].url === "string", "has url");
}

async function testRetrieveMarkdown() {
  const sid = uuid();
  const secret = uuid();
  const content = "# Retrieve Test\n\nBody content here.";

  await uploadFile("markdown", sid, secret, "doc.md", content);

  const { status, body, ms } = await fetchRaw(
    `/uploads/${sid}/markdown/doc.md`,
  );
  console.log(`\n── GET /uploads/{sid}/markdown/doc.md ──`);
  assert(status === 200, `returns 200 (got ${status})`);
  const text = body.toString("utf-8");
  assert(text.includes("# Retrieve Test"), "content matches");
  assert(text.includes("Body content here."), "body content present");
}

async function testDuplicateMarkdown() {
  const sid = uuid();
  const secret = uuid();
  const content = "dup";

  await uploadFile("markdown", sid, secret, "dup.md", content);

  const { status, body } = await uploadFile("markdown", sid, secret, "dup.md", content);
  console.log(`\n── POST /upload/markdown (duplicate) ──`);
  assert(status === 409, `returns 409 Conflict (got ${status})`);
  assert(body.success === false, "success = false");
}

async function testUploadTextFile() {
  const sid = uuid();
  const secret = uuid();
  const content = "Plain text content";

  const { status, body } = await uploadFile("markdown", sid, secret, "notes.txt", content);
  console.log(`\n── POST /upload/markdown (txt file) ──`);
  assert(status === 200, `returns 200 (got ${status})`);
  assert(body.files[0].original_name === "notes.txt", "txt file accepted");
}

async function testDeleteMarkdown() {
  const sid = uuid();
  const secret = uuid();
  await uploadFile("markdown", sid, secret, "to-delete.md", "bye");

  const { status, body } = await fetchJSON(
    `/uploads/${sid}/markdown/to-delete.md?secret=${secret}`,
    { method: "DELETE" },
  );
  console.log(`\n── DELETE /uploads/{sid}/markdown/to-delete.md ──`);
  assert(status === 200, `returns 200 (got ${status})`);
  assert(body.success === true, "success = true");
  assert(body.file === "to-delete.md", `deleted file = 'to-delete.md'`);

  // Verify it's gone
  const verify = await fetchRaw(`/uploads/${sid}/markdown/to-delete.md`);
  assert(verify.status === 404, `file gone after delete (got ${verify.status})`);
}

async function testDeleteWrongSecret() {
  const sid = uuid();
  const secret = uuid();
  await uploadFile("markdown", sid, secret, "protected.md", "secret");

  const { status } = await fetchJSON(
    `/uploads/${sid}/markdown/protected.md?secret=wrong-secret-12345`,
    { method: "DELETE" },
  );
  console.log(`\n── DELETE with wrong secret ──`);
  assert(status === 403, `returns 403 (got ${status})`);
}

// ──────────────────────────────────────────────
// Upload / Retrieve / Delete image
// ──────────────────────────────────────────────

async function testUploadImage() {
  const sid = uuid();
  const secret = uuid();

  // Minimal valid 1x1 PNG
  const pngHex =
    "89504e470d0a1a0a0000000d49484452000000010000000010802000000907753de0000000c494441540" +
    "8d763f8cfc000000301000005180dd8db40000000049454e44ae426082";
  const pngBuffer = Buffer.from(pngHex, "hex");

  const url = `${BASE_URL}/upload/image?session_id=${sid}&secret=${secret}`;
  const formData = new FormData();
  formData.append("file", new Blob([pngBuffer], { type: "image/png" }), "pixel.png");

  const res = await fetch(url, { method: "POST", body: formData });
  const text = await res.text();
  let body;
  try { body = JSON.parse(text); } catch { body = text; }

  console.log(`\n── POST /upload/image (1x1 PNG) ──`);
  assert(res.status === 200, `returns 200 (got ${res.status})`);
  assert(body.success === true, "success = true");
  assert(body.files[0].original_name === "pixel.png", "filename = 'pixel.png'");
}

async function testRetrieveImage() {
  const sid = uuid();
  const secret = uuid();

  const pngHex =
    "89504e470d0a1a0a0000000d49484452000000010000000010802000000907753de0000000c494441540" +
    "8d763f8cfc000000301000005180dd8db40000000049454e44ae426082";
  const pngBuffer = Buffer.from(pngHex, "hex");

  const url = `${BASE_URL}/upload/image?session_id=${sid}&secret=${secret}`;
  const formData = new FormData();
  formData.append("file", new Blob([pngBuffer], { type: "image/png" }), "logo.png");
  await fetch(url, { method: "POST", body: formData });

  const { status, headers, body } = await fetchRaw(
    `/uploads/${sid}/images/logo.png`,
  );
  console.log(`\n── GET /uploads/{sid}/images/logo.png ──`);
  assert(status === 200, `returns 200 (got ${status})`);
  const ct = headers.get("content-type") || "";
  assert(ct.includes("image/") || ct.includes("octet"), `content-type is image/* (got ${ct})`);
}

async function testDeleteImage() {
  const sid = uuid();
  const secret = uuid();

  const pngHex =
    "89504e470d0a1a0a0000000d49484452000000010000000010802000000907753de0000000c494441540" +
    "8d763f8cfc000000301000005180dd8db40000000049454e44ae426082";
  const pngBuffer = Buffer.from(pngHex, "hex");

  const url = `${BASE_URL}/upload/image?session_id=${sid}&secret=${secret}`;
  const formData = new FormData();
  formData.append("file", new Blob([pngBuffer], { type: "image/png" }), "del.png");
  await fetch(url, { method: "POST", body: formData });

  const { status, body } = await fetchJSON(
    `/uploads/${sid}/images/del.png?secret=${secret}`,
    { method: "DELETE" },
  );
  console.log(`\n── DELETE /uploads/{sid}/images/del.png ──`);
  assert(status === 200, `returns 200 (got ${status})`);
  assert(body.success === true, "success = true");
}

// ──────────────────────────────────────────────
// Session deletion
// ──────────────────────────────────────────────

async function testDeleteSession() {
  const sid = uuid();
  const secret = uuid();
  await uploadFile("markdown", sid, secret, "a.md", "aaa");
  await uploadFile("markdown", sid, secret, "b.md", "bbb");

  const { status, body } = await fetchJSON(
    `/uploads/${sid}?secret=${secret}`,
    { method: "DELETE" },
  );
  console.log(`\n── DELETE /uploads/{sid} (entire session) ──`);
  assert(status === 200, `returns 200 (got ${status})`);
  assert(body.success === true, "success = true");
  assert(body.session_id === sid, "session_id echoed");
  assert(typeof body.files_deleted === "object", "has files_deleted");
  assert(body.files_deleted.markdown >= 2, `${body.files_deleted.markdown} markdown files deleted`);

  // Verify session is gone
  const verify = await fetchRaw(`/uploads/${sid}/markdown/a.md`);
  assert(verify.status === 404, `session gone after delete (got ${verify.status})`);
}

// ──────────────────────────────────────────────
// Error cases
// ──────────────────────────────────────────────

async function testMissingSessionId() {
  const { status } = await uploadFile("markdown", "", "12345678", "x.md", "x");
  console.log(`\n── POST /upload/markdown (missing session_id) ──`);
  assert(status === 400, `returns 400 (got ${status})`);
}

async function testShortSecret() {
  const { status } = await uploadFile("markdown", uuid(), "abc", "x.md", "x");
  console.log(`\n── POST /upload/markdown (secret < 8 chars) ──`);
  assert(status === 400, `returns 400 (got ${status})`);
}

async function testInvalidSessionId() {
  const { status } = await uploadFile("markdown", "not-a-uuid", "12345678", "x.md", "x");
  console.log(`\n── POST /upload/markdown (invalid session_id) ──`);
  assert(status === 400, `returns 400 (got ${status})`);
}

async function testWrongSecretUpload() {
  const sid = uuid();
  const secret = uuid();
  await uploadFile("markdown", sid, secret, "secure.md", "data");

  const { status } = await uploadFile("markdown", sid, "wrong-secret-99", "other.md", "x");
  console.log(`\n── POST /upload/markdown (wrong secret for existing session) ──`);
  assert(status === 403, `returns 403 (got ${status})`);
}

async function testBinaryToMarkdownEndpoint() {
  const sid = uuid();
  const secret = uuid();
  // Send random binary data to the markdown endpoint
  const binaryContent = Buffer.alloc(100, 0xff);

  const url = `${BASE_URL}/upload/markdown?session_id=${sid}&secret=${secret}`;
  const formData = new FormData();
  formData.append("file", new Blob([binaryContent], { type: "application/octet-stream" }), "bad.md");

  const res = await fetch(url, { method: "POST", body: formData });
  const text = await res.text();
  let body;
  try { body = JSON.parse(text); } catch { body = text; }

  console.log(`\n── POST /upload/markdown (binary content) ──`);
  assert(res.status === 415 || res.status === 400, `returns 415/400 (got ${res.status})`);
}

async function testPathTraversal() {
  const { status } = await fetchRaw("/uploads/00000000-0000-0000-0000-000000000000/markdown/../../etc/passwd");
  console.log(`\n── GET path traversal attempt ──`);
  assert(status === 400 || status === 404, `returns 400/404 (got ${status})`);
}

async function testNonexistentFile() {
  const sid = uuid();
  const { status } = await fetchRaw(`/uploads/${sid}/markdown/nope.md`);
  console.log(`\n── GET nonexistent session/file ──`);
  assert(status === 404, `returns 404 (got ${status})`);
}

async function testDeleteNonexistentFile() {
  const sid = uuid();
  const secret = uuid();
  const { status } = await fetchJSON(
    `/uploads/${sid}/markdown/nope.md?secret=${secret}`,
    { method: "DELETE" },
  );
  console.log(`\n── DELETE nonexistent file ──`);
  assert(status === 404 || status === 400, `returns 404/400 (got ${status})`);
}

async function testDeleteNonexistentSession() {
  const sid = uuid();
  const { status } = await fetchJSON(
    `/uploads/${sid}?secret=${uuid()}`,
    { method: "DELETE" },
  );
  console.log(`\n── DELETE nonexistent session ──`);
  assert(status === 404, `returns 404 (got ${status})`);
}

// ──────────────────────────────────────────────
// Runner
// ──────────────────────────────────────────────

const tests = [
  testUploadMarkdown,
  testRetrieveMarkdown,
  testDuplicateMarkdown,
  testUploadTextFile,
  testDeleteMarkdown,
  testDeleteWrongSecret,
  testUploadImage,
  testRetrieveImage,
  testDeleteImage,
  testDeleteSession,
  testMissingSessionId,
  testShortSecret,
  testInvalidSessionId,
  testWrongSecretUpload,
  testBinaryToMarkdownEndpoint,
  testPathTraversal,
  testNonexistentFile,
  testDeleteNonexistentFile,
  testDeleteNonexistentSession,
];

async function main() {
  console.log(`Upload API test suite — ${BASE_URL}`);
  console.log(`${"─".repeat(50)}`);
  const suiteStart = performance.now();

  try {
    await fetchJSON("/health");
  } catch (err) {
    console.error(`\nCannot reach ${BASE_URL} — is the server running?\n`);
    process.exit(1);
  }

  for (const fn of tests) {
    try {
      await fn();
    } catch (err) {
      failed++;
      console.log(`  💥 ${fn.name}: ${err.message}`);
    }
  }

  const suiteMs = Math.round((performance.now() - suiteStart) * 100) / 100;
  console.log(`\n${"═".repeat(50)}`);
  console.log(`Total: ${passed + failed} | Passed: ${passed} | Failed: ${failed} | ${suiteMs}ms`);
  console.log(`${"═".repeat(50)}`);

  if (failed > 0) process.exit(1);
}

main();
