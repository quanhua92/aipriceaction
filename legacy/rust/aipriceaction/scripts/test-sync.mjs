/**
 * Integration tests for the /sync KV-store endpoint.
 *
 * Tests CRUD operations, auth (SYNC_TOKEN + per-entry secret), key rotation.
 *
 * Usage:
 *   SYNC_TOKEN="key1,key2" node scripts/test-sync.mjs                 # default: http://localhost:3000
 *   SYNC_TOKEN="key1,key2" node scripts/test-sync.mjs http://localhost:3001
 *
 * Requires SYNC_TOKEN env var to be set (comma-separated for key rotation).
 */

import { randomUUID } from "node:crypto";

const SYNC_TOKEN = process.env.SYNC_TOKEN;
const BASE_URL = process.argv[2] || "http://localhost:3000";

// Split token into array for rotation tests
const TOKENS = SYNC_TOKEN ? SYNC_TOKEN.split(",").map((t) => t.trim()) : [];
const TOKEN_1 = TOKENS[0] || "missing-token-1";
const TOKEN_2 = TOKENS[1] || "missing-token-2";

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

function authHeader(token = TOKEN_1) {
  return { Authorization: `Bearer ${token}` };
}

// ──────────────────────────────────────────────
// POST /sync/{key} — create
// ──────────────────────────────────────────────

async function testPostCreate() {
  const key = uuid();
  const { status, body } = await fetchJSON(`/sync/${key}`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({
      secret: "my-password",
      value: { bookmarks: ["BTCUSDT", "VCB", "FPT"], theme: "dark" },
    }),
  });

  console.log(`\n── POST /sync/{key} (create) ──`);
  assert(status === 200, `returns 200 (got ${status})`);
  assert(body.id === key, `id echoed (got ${body.id})`);
  assert(body.success !== false, "success not false");
  assert(Array.isArray(body.value?.bookmarks), "value has bookmarks");
  assert(body.value?.theme === "dark", `theme = 'dark' (got ${body.value?.theme})`);
  assert(typeof body.created_at === "string", "has created_at");
  assert(typeof body.updated_at === "string", "has updated_at");
}

// ──────────────────────────────────────────────
// GET /sync/{key} — retrieve
// ──────────────────────────────────────────────

async function testGetRetrieve() {
  const key = uuid();
  const value = { notes: "hello world", count: 42 };

  await fetchJSON(`/sync/${key}`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ secret: "get-secret", value }),
  });

  const { status, body } = await fetchJSON(`/sync/${key}?secret=get-secret`, {
    headers: authHeader(),
  });

  console.log(`\n── GET /sync/{key} (retrieve) ──`);
  assert(status === 200, `returns 200 (got ${status})`);
  assert(body.id === key, `id matches`);
  assert(body.value?.notes === "hello world", `value.notes matches`);
  assert(body.value?.count === 42, `value.count = 42`);
}

// ──────────────────────────────────────────────
// POST /sync/{key} — upsert (update existing)
// ──────────────────────────────────────────────

async function testPostUpsert() {
  const key = uuid();

  // Create
  await fetchJSON(`/sync/${key}`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ secret: "upsert-secret", value: { v: 1 } }),
  });

  // Update with new value
  const { status, body } = await fetchJSON(`/sync/${key}`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ secret: "upsert-secret", value: { v: 2, updated: true } }),
  });

  console.log(`\n── POST /sync/{key} (upsert) ──`);
  assert(status === 200, `returns 200 (got ${status})`);
  assert(body.value?.v === 2, `value updated to v=2 (got ${body.value?.v})`);
  assert(body.value?.updated === true, `value.updated = true`);
}

// ──────────────────────────────────────────────
// Auth tests
// ──────────────────────────────────────────────

async function testGetWrongSecret() {
  const key = uuid();
  await fetchJSON(`/sync/${key}`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ secret: "correct-secret", value: {} }),
  });

  const { status } = await fetchJSON(`/sync/${key}?secret=wrong-secret`, {
    headers: authHeader(),
  });

  console.log(`\n── GET /sync/{key} (wrong secret) ──`);
  assert(status === 403, `returns 403 (got ${status})`);
}

async function testPostWrongSecret() {
  const key = uuid();
  await fetchJSON(`/sync/${key}`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ secret: "correct-secret", value: { v: 1 } }),
  });

  // Try to overwrite with wrong secret — should be rejected
  const { status: postStatus } = await fetchJSON(`/sync/${key}`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ secret: "wrong-secret", value: { v: 2 } }),
  });

  // Original secret should still work
  const { status: getStatus, body } = await fetchJSON(`/sync/${key}?secret=correct-secret`, {
    headers: authHeader(),
  });

  console.log(`\n── POST /sync/{key} (wrong secret rejected) ──`);
  assert(postStatus === 403, `POST with wrong secret returns 403 (got ${postStatus})`);
  assert(getStatus === 200, `original secret still works (got ${getStatus})`);
  assert(body.value?.v === 1, `value unchanged (got ${body.value?.v})`);
}

async function testNoToken() {
  const key = uuid();
  const { status } = await fetchJSON(`/sync/${key}?secret=test`, {});

  console.log(`\n── GET /sync/{key} (no auth token) ──`);
  assert(status === 401, `returns 401 (got ${status})`);
}

async function testRotatedToken() {
  if (!TOKENS[1]) {
    console.log(`\n── GET /sync/{key} (rotated token) ──`);
    console.log("  ⏭️  Skipped (only one SYNC_TOKEN provided)");
    return;
  }

  const key = uuid();
  await fetchJSON(`/sync/${key}`, {
    method: "POST",
    headers: { ...authHeader(TOKEN_1), "Content-Type": "application/json" },
    body: JSON.stringify({ secret: "rotate-secret", value: { rotated: true } }),
  });

  // Read using second (rotated) token
  const { status, body } = await fetchJSON(`/sync/${key}?secret=rotate-secret`, {
    headers: authHeader(TOKEN_2),
  });

  console.log(`\n── GET /sync/{key} (rotated token) ──`);
  assert(status === 200, `returns 200 with rotated token (got ${status})`);
  assert(body.value?.rotated === true, `value accessible`);
}

// ──────────────────────────────────────────────
// Error cases
// ──────────────────────────────────────────────

async function testInvalidKeyFormat() {
  const { status } = await fetchJSON(`/sync/not-a-uuid?secret=test`, {
    headers: authHeader(),
  });

  console.log(`\n── GET /sync/{key} (invalid key format) ──`);
  assert(status === 400, `returns 400 (got ${status})`);
}

async function testGetNonexistent() {
  const key = uuid();
  const { status } = await fetchJSON(`/sync/${key}?secret=test`, {
    headers: authHeader(),
  });

  console.log(`\n── GET /sync/{key} (nonexistent key) ──`);
  assert(status === 404, `returns 404 (got ${status})`);
}

async function testPostMissingSecret() {
  const { status } = await fetchJSON(`/sync/${uuid()}`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ value: {} }),
  });

  console.log(`\n── POST /sync/{key} (missing secret) ──`);
  assert(status === 422 || status === 400, `returns 4xx (got ${status})`);
}

async function testPostMissingValue() {
  const { status } = await fetchJSON(`/sync/${uuid()}`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ secret: "test" }),
  });

  console.log(`\n── POST /sync/{key} (missing value) ──`);
  assert(status === 422 || status === 400, `returns 4xx (got ${status})`);
}

async function testPostInvalidKeyFormat() {
  const { status } = await fetchJSON(`/sync/not-a-uuid`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ secret: "test", value: {} }),
  });

  console.log(`\n── POST /sync/{key} (invalid key format) ──`);
  assert(status === 400, `returns 400 (got ${status})`);
}

// ──────────────────────────────────────────────
// Run
// ──────────────────────────────────────────────

async function main() {
  console.log(`Sync API test suite — ${BASE_URL}`);
  console.log(`Tokens: ${TOKENS.length} (${TOKENS.map((t) => t.slice(0, 4) + "...").join(", ")})`);
  console.log(`${"─".repeat(50)}`);

  if (!SYNC_TOKEN) {
    console.error("\n⚠️  SYNC_TOKEN env var not set. Tests will use placeholder tokens.");
    console.error("   Set SYNC_TOKEN env var for accurate auth tests.\n");
  }

  try {
    await fetchJSON("/health");
  } catch {
    console.error(`\nCannot reach ${BASE_URL} — is the server running?\n`);
    process.exit(1);
  }

  const suiteStart = performance.now();

  await testPostCreate();
  await testGetRetrieve();
  await testPostUpsert();
  await testGetWrongSecret();
  await testPostWrongSecret();
  await testNoToken();
  await testRotatedToken();
  await testInvalidKeyFormat();
  await testGetNonexistent();
  await testPostMissingSecret();
  await testPostMissingValue();
  await testPostInvalidKeyFormat();

  const suiteMs = Math.round((performance.now() - suiteStart) * 100) / 100;
  console.log(`\n${"═".repeat(50)}`);
  console.log(`Total: ${passed + failed} | Passed: ${passed} | Failed: ${failed} | ${suiteMs}ms`);
  console.log(`${"═".repeat(50)}`);

  if (failed > 0) process.exit(1);
}

main();
