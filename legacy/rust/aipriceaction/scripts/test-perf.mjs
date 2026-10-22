/**
 * Performance benchmarks for the Axum API server.
 * Measures response time for each endpoint and asserts SLA thresholds.
 *
 * Usage:
 *   node scripts/test-perf.mjs                 # default: http://localhost:3000
 *   node scripts/test-perf.mjs http://localhost:3001
 */

const BASE_URL = process.argv[2] || "http://localhost:3000";

let passed = 0;
let failed = 0;

function ok(label, ms, threshold) {
  passed++;
  console.log(`  ✅ ${label}  (${ms}ms, threshold ${threshold}ms)`);
}

function fail(label, ms, threshold, detail) {
  failed++;
  console.log(`  ❌ ${label}  (${ms}ms, threshold ${threshold}ms)`);
  if (detail) console.log(`     ${detail}`);
}

async function perf(path, label, thresholdMs) {
  const url = `${BASE_URL}${path}`;
  const start = performance.now();
  const res = await fetch(url);
  const text = await res.text();
  const ms = Math.round((performance.now() - start) * 100) / 100;

  if (res.status === 200) {
    if (ms <= thresholdMs) ok(label, ms, thresholdMs);
    else fail(label, ms, thresholdMs);
  } else {
    failed++;
    console.log(`  ❌ ${label}  (HTTP ${res.status})`);
  }
  return ms;
}

// ──────────────────────────────────────────────
// Tests
// ──────────────────────────────────────────────

async function testNativeDaily() {
  console.log("\n── Native Daily ──");
  await perf("/tickers?symbol=VCB&interval=1D&limit=100", "1D single ticker", 500);
  await perf("/tickers?symbol=VCB&symbol=FPT&symbol=BID&interval=1D&limit=100", "1D 3 tickers", 500);
  await perf("/tickers?symbol=VCB&interval=1D&limit=252&format=csv", "1D CSV export", 500);
}

async function testNativeHourly() {
  console.log("\n── Native Hourly ──");
  await perf("/tickers?symbol=VCB&interval=1h&limit=100", "1h single ticker", 1000);
  await perf("/tickers?symbol=VCB&interval=1h&start_date=2026-03-01&limit=500", "1h with date range", 1000);
}

async function testNativeMinute() {
  console.log("\n── Native Minute ──");
  await perf("/tickers?symbol=VCB&interval=1m&limit=1000", "1m single ticker", 2000);
  await perf("/tickers?symbol=VCB&interval=1m&start_date=2026-03-01&limit=1000", "1m with date range", 2000);
  await perf("/tickers?symbol=VCB&symbol=FPT&interval=1m&limit=500", "1m 2 tickers", 3000);
}

async function testAggregatedMinute() {
  console.log("\n── Aggregated (from 1m) ──");
  await perf("/tickers?symbol=VCB&interval=5m&limit=100", "5m single ticker", 2000);
  await perf("/tickers?symbol=VCB&interval=15m&limit=100", "15m single ticker", 2000);
  await perf("/tickers?symbol=VCB&interval=30m&limit=100", "30m single ticker", 2000);
  await perf("/tickers?symbol=VCB&interval=15m&start_date=2026-03-01&limit=500", "15m with date range", 2000);
  await perf("/tickers?symbol=VCB&symbol=FPT&symbol=BID&interval=15m&limit=100", "15m 3 tickers", 3000);
}

async function testAggregatedDaily() {
  console.log("\n── Aggregated (from 1D) ──");
  await perf("/tickers?symbol=VCB&interval=1W&limit=100", "1W single ticker", 1000);
  await perf("/tickers?symbol=VCB&interval=2W&limit=100", "2W single ticker", 1000);
  await perf("/tickers?symbol=VCB&interval=1M&limit=100", "1M single ticker", 1000);
}

async function testCrypto() {
  console.log("\n── Crypto Mode ──");
  await perf("/tickers?symbol=BTCUSDT&mode=crypto&interval=1D&limit=100", "crypto 1D BTC", 500);
  await perf("/tickers?symbol=ETHUSDT&mode=crypto&interval=1h&limit=100", "crypto 1h ETH", 1000);
  await perf("/tickers?symbol=SOLUSDT&mode=crypto&interval=1m&limit=1000", "crypto 1m SOL", 2000);
}

async function testMultiTickerStress() {
  console.log("\n── Multi-Ticker Stress ──");
  await perf(
    "/tickers?symbol=VCB&symbol=FPT&symbol=BID&symbol=CTG&symbol=MWG&interval=1D&limit=100",
    "5 tickers 1D",
    2000,
  );
  await perf(
    "/tickers?symbol=VCB&symbol=FPT&symbol=BID&interval=15m&limit=100",
    "3 tickers 15m",
    3000,
  );
}

async function testMetadata() {
  console.log("\n── Metadata ──");
  await perf("/tickers/group?mode=vn", "tickers/group vn", 100);
  await perf("/tickers/group?mode=crypto", "tickers/group crypto", 100);
  await perf("/health", "health check", 200);
}

// ──────────────────────────────────────────────
// Runner
// ──────────────────────────────────────────────

const tests = [
  testNativeDaily,
  testNativeHourly,
  testNativeMinute,
  testAggregatedMinute,
  testAggregatedDaily,
  testCrypto,
  testMultiTickerStress,
  testMetadata,
];

async function main() {
  console.log(`Performance test suite — ${BASE_URL}`);
  console.log("─".repeat(50));
  const suiteStart = performance.now();

  // Quick connectivity check
  try {
    await fetch(`${BASE_URL}/health`);
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
  console.log("\n" + "═".repeat(50));
  console.log(`Total: ${passed + failed} | Passed: ${passed} | Failed: ${failed} | ${suiteMs}ms`);
  console.log("═".repeat(50));

  if (failed > 0) process.exit(1);
}

main();
