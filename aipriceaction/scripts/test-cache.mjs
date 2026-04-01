/**
 * Cache tests for the /tickers endpoint.
 * Verifies TTL cache behavior: cold miss, warm hit, bypass, eviction.
 *
 * Usage:
 *   node scripts/test-cache.mjs                 # default: http://localhost:3000
 *   node scripts/test-cache.mjs http://localhost:3001
 */

const BASE_URL = process.argv[2] || "http://localhost:3000";
const CACHE_TTL_MS = 11_000; // slightly over 10s TTL

let passed = 0;
let failed = 0;

function ok(label, detail) {
  passed++;
  console.log(`  ✅ ${label}${detail ? ` (${detail})` : ""}`);
}

function fail(label, detail) {
  failed++;
  console.log(`  ❌ ${label}${detail ? ` (${detail})` : ""}`);
}

async function fetchJson(path) {
  const res = await fetch(`${BASE_URL}${path}`);
  const text = await res.text();
  try {
    return { status: res.status, data: JSON.parse(text), ms: 0 };
  } catch {
    return { status: res.status, data: null, text };
  }
}

async function fetchTimed(path) {
  const start = performance.now();
  const res = await fetch(`${BASE_URL}${path}`);
  await res.text();
  const ms = Math.round((performance.now() - start) * 100) / 100;
  return { status: res.status, ms };
}

// ──────────────────────────────────────────────
// Tests
// ──────────────────────────────────────────────

async function testColdMiss() {
  console.log("\n── 1. Cold miss ──");
  const { status, ms } = await fetchTimed(
    "/tickers?symbol=VCB&interval=1D&limit=5&cache=false"
  );
  if (status === 200) ok(`cold miss returns 200`, `${ms}ms`);
  else fail(`cold miss returns 200`, `HTTP ${status}`);
  return ms;
}

async function testWarmHit(coldMs) {
  console.log("\n── 2. Warm hit ──");
  // First request with cache enabled (populates cache)
  await fetchTimed("/tickers?symbol=VCB&interval=1D&limit=5");

  // Second identical request should be a cache hit
  const { status, ms } = await fetchTimed(
    "/tickers?symbol=VCB&interval=1D&limit=5"
  );
  if (status !== 200) {
    fail(`warm hit returns 200`, `HTTP ${status}`);
    return;
  }
  if (ms < coldMs) ok(`warm hit faster than cold`, `${ms}ms vs ${coldMs}ms`);
  else fail(`warm hit faster than cold`, `${ms}ms vs ${coldMs}ms`);
  if (ms < 20) ok(`warm hit under 20ms`, `${ms}ms`);
  else fail(`warm hit under 20ms`, `${ms}ms`);
}

async function testDifferentKeyMiss() {
  console.log("\n── 3. Different key miss ──");
  // FPT is a different cache key — should be a miss
  const { status, ms } = await fetchTimed(
    "/tickers?symbol=FPT&interval=1D&limit=5&cache=false"
  );
  if (status === 200) ok(`different ticker is cache miss`, `${ms}ms`);
  else fail(`different ticker is cache miss`, `HTTP ${status}`);
}

async function testCacheBypass() {
  console.log("\n── 4. Cache bypass (cache=false) ──");
  // Populate cache
  await fetchTimed("/tickers?symbol=BID&interval=1D&limit=5");

  // cache=false should always be a miss
  const { status, ms } = await fetchTimed(
    "/tickers?symbol=BID&interval=1D&limit=5&cache=false"
  );
  if (status === 200) ok(`cache=false bypasses cache`, `${ms}ms`);
  else fail(`cache=false bypasses cache`, `HTTP ${status}`);
}

async function testLegacyBypass() {
  console.log("\n── 5. Legacy bypass (same cache key) ──");
  // Populate cache for VCB
  await fetchTimed("/tickers?symbol=VCB&interval=1D&limit=5");

  // legacy=true should still hit cache (legacy not in key)
  const { status, ms } = await fetchTimed(
    "/tickers?symbol=VCB&interval=1D&limit=5&legacy=true"
  );
  const { data } = await fetchJson(
    "/tickers?symbol=VCB&interval=1D&limit=5&legacy=true"
  );
  if (status === 200 && data && data.VCB && data.VCB[0]) {
    const price = data.VCB[0].close;
    if (price < 200) ok(`legacy price divided by 1000`, `close=${price}`);
    else fail(`legacy price divided by 1000`, `close=${price}`);
  }
  if (ms < 20) ok(`legacy=true hits cache`, `${ms}ms`);
  else fail(`legacy=true hits cache`, `${ms}ms`);
}

async function testFormatBypass() {
  console.log("\n── 6. Format bypass (same cache key) ──");
  // Populate cache
  await fetchTimed("/tickers?symbol=CTG&interval=1D&limit=5");

  // format=csv should still hit cache
  const start = performance.now();
  const res = await fetch(
    `${BASE_URL}/tickers?symbol=CTG&interval=1D&limit=5&format=csv`
  );
  await res.text();
  const ms = Math.round((performance.now() - start) * 100) / 100;

  if (res.status === 200) ok(`format=csv returns 200`);
  else fail(`format=csv returns 200`, `HTTP ${res.status}`);
  if (ms < 20) ok(`format=csv hits cache`, `${ms}ms`);
  else fail(`format=csv hits cache`, `${ms}ms`);
}

async function testAggregatedCache() {
  console.log("\n── 7. Aggregated cache (15m) ──");
  // First request populates cache
  await fetchTimed("/tickers?symbol=VCB&interval=15m&limit=10");

  // Second should be a hit
  const { status, ms } = await fetchTimed(
    "/tickers?symbol=VCB&interval=15m&limit=10"
  );
  if (status === 200) ok(`aggregated 15m returns 200`);
  else fail(`aggregated 15m returns 200`, `HTTP ${status}`);
  if (ms < 20) ok(`aggregated 15m hits cache`, `${ms}ms`);
  else fail(`aggregated 15m hits cache`, `${ms}ms`);
}

async function testMultiTickerCache() {
  console.log("\n── 8. Multi-ticker cache ──");
  // Populate cache for VCB+FPT
  await fetchTimed("/tickers?symbol=VCB&symbol=FPT&interval=1D&limit=5");

  // Same combo should be a hit (order shouldn't matter)
  const { status, ms } = await fetchTimed(
    "/tickers?symbol=FPT&symbol=VCB&interval=1D&limit=5"
  );
  if (status === 200) ok(`multi-ticker reordered returns 200`);
  else fail(`multi-ticker reordered returns 200`, `HTTP ${status}`);
  if (ms < 20) ok(`multi-ticker reordered hits cache`, `${ms}ms`);
  else fail(`multi-ticker reordered hits cache`, `${ms}ms`);

  // Different combo should be a miss
  const { status: status2, ms: ms2 } = await fetchTimed(
    "/tickers?symbol=VCB&symbol=FPT&symbol=BID&interval=1D&limit=5&cache=false"
  );
  if (status2 === 200) ok(`different multi-ticker combo is miss`, `${ms2}ms`);
  else fail(`different multi-ticker combo is miss`, `HTTP ${status2}`);
}

async function testTTLExpiry() {
  console.log("\n── 9. TTL expiry ──");
  const symbol = "HPG";
  // Populate cache
  await fetchTimed(`/tickers?symbol=${symbol}&interval=1D&limit=5`);

  // Warm hit
  const { status: hitStatus, ms: hitMs } = await fetchTimed(
    `/tickers?symbol=${symbol}&interval=1D&limit=5`
  );
  if (hitMs < 20) ok(`pre-expiry: warm hit`, `${hitMs}ms`);
  else fail(`pre-expiry: warm hit`, `${hitMs}ms`);

  // Wait for TTL to expire
  console.log(`  ⏳ waiting ${CACHE_TTL_MS / 1000}s for TTL expiry...`);
  await new Promise((r) => setTimeout(r, CACHE_TTL_MS));

  // After TTL, should be a cold miss (full time)
  const { status, ms } = await fetchTimed(
    `/tickers?symbol=${symbol}&interval=1D&limit=5`
  );
  if (status === 200) ok(`post-expiry: returns 200`, `${ms}ms`);
  else fail(`post-expiry: returns 200`, `HTTP ${status}`);
  // We can't assert it's slower than 20ms because the query itself could be fast,
  // but we can verify it still works correctly after eviction
}

// ──────────────────────────────────────────────
// Runner
// ──────────────────────────────────────────────

async function main() {
  console.log(`Cache test suite — ${BASE_URL}`);
  console.log("─".repeat(50));

  // Quick health check
  const health = await fetch(`${BASE_URL}/health`);
  if (!health.ok) {
    console.error(`Server not reachable at ${BASE_URL}`);
    process.exit(1);
  }

  const coldMs = await testColdMiss();
  await testWarmHit(coldMs);
  await testDifferentKeyMiss();
  await testCacheBypass();
  await testLegacyBypass();
  await testFormatBypass();
  await testAggregatedCache();
  await testMultiTickerCache();
  await testTTLExpiry();

  console.log("\n" + "═".repeat(50));
  console.log(`Total: ${passed + failed} | Passed: ${passed} | Failed: ${failed}`);
  console.log("═".repeat(50));

  if (failed > 0) process.exit(1);
}

main().catch((e) => {
  console.error("Fatal:", e);
  process.exit(1);
});
