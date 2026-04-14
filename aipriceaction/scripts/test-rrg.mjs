/**
 * Test suite for /analysis/rrg endpoint.
 *
 * Usage:
 *   node scripts/test-rrg.mjs                 # default: http://localhost:3000
 *   node scripts/test-rrg.mjs http://localhost:3001
 */

const BASE_URL = process.argv[2] || "http://localhost:3000";

let passed = 0;
let failed = 0;

function ok(label) {
  passed++;
  console.log(`  \u2705 ${label}`);
}

function fail(label, detail) {
  failed++;
  console.log(`  \u274c ${label}`);
  if (detail) console.log(`     ${detail}`);
}

function assert(cond, label, detail) {
  if (cond) ok(label);
  else fail(label, detail);
}

async function fetchJSON(path, opts = {}) {
  const url = `${BASE_URL}${path}`;
  const start = performance.now();
  const res = await fetch(url, opts);
  const text = await res.text();
  const ms = Math.round((performance.now() - start) * 100) / 100;
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = text;
  }
  return { status: res.status, headers: res.headers, body, ms };
}

// ──────────────────────────────────────────────
// JdK algorithm
// ──────────────────────────────────────────────

async function testJdkBasic() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg");
  console.log(`\n── GET /analysis/rrg (JdK default) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.analysis_type === "rrg", `analysis_type = '${body.analysis_type}'`);
  assert(body.data.algorithm === "jdk", `algorithm = 'jdk'`);
  assert(typeof body.data.benchmark === "string", `benchmark is string (got ${body.data.benchmark})`);
  assert(body.data.period === 10, `period = 10 (got ${body.data.period})`);
  assert(Array.isArray(body.data.tickers), "tickers is array");
  assert(body.data.tickers.length > 0, `tickers not empty (${body.data.tickers.length})`);
}

async function testJdkFields() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?algorithm=jdk");
  console.log(`\n── GET /analysis/rrg?algorithm=jdk (field check) ── ${ms}ms`);
  assert(status === 200, "returns 200");

  const t = body.data.tickers[0];
  assert(typeof t.symbol === "string", "ticker has symbol");
  assert(typeof t.rs_ratio === "number", "ticker has rs_ratio");
  assert(typeof t.rs_momentum === "number", "ticker has rs_momentum");
  assert(typeof t.raw_rs === "number", "ticker has raw_rs");
  assert(typeof t.close === "number", "ticker has close");
  assert(typeof t.volume === "number", "ticker has volume");
  assert(Array.isArray(t.trails), "ticker has trails array (default trails=10)");
  assert(t.trails.length > 0, `trails not empty (${t.trails.length} points)`);

  const trail = t.trails[0];
  assert(typeof trail.date === "string", "trail point has date");
  assert(typeof trail.rs_ratio === "number", "trail point has rs_ratio");
  assert(typeof trail.rs_momentum === "number", "trail point has rs_momentum");
}

async function testJdkBenchmark() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?benchmark=VN30");
  console.log(`\n── GET /analysis/rrg?benchmark=VN30 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.data.benchmark === "VN30", `benchmark = 'VN30'`);
}

async function testJdkPeriod() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?period=20");
  console.log(`\n── GET /analysis/rrg?period=20 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.data.period === 20, `period = 20 (got ${body.data.period})`);
}

async function testJdkNoTrails() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?trails=0");
  console.log(`\n── GET /analysis/rrg?trails=0 ── ${ms}ms`);
  assert(status === 200, "returns 200");

  const t = body.data.tickers[0];
  assert(t.trails === null || t.trails === undefined, `trails is null (got ${JSON.stringify(t.trails)})`);
}

async function testJdkTrailClampMin() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?trails=3");
  console.log(`\n── GET /analysis/rrg?trails=3 (clamped to 3) ── ${ms}ms`);
  assert(status === 200, "returns 200");

  const t = body.data.tickers[0];
  assert(t.trails !== null, "trails is not null");
  assert(t.trails.length === 3, `trail length = 3 (got ${t.trails.length})`);
}

async function testJdkTrailClampMax() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?trails=200");
  console.log(`\n── GET /analysis/rrg?trails=200 (clamped to 120) ── ${ms}ms`);
  assert(status === 200, "returns 200");

  const t = body.data.tickers[0];
  assert(t.trails !== null, "trails is not null");
  assert(t.trails.length <= 120, `trail length <= 120 (got ${t.trails.length})`);
}

async function testJdkMinVolume() {
  const { status: s1, body: b1, ms: m1 } = await fetchJSON("/analysis/rrg");
  const { status: s2, body: b2, ms: m2 } = await fetchJSON("/analysis/rrg?min_volume=10000000");
  console.log(`\n── GET /analysis/rrg?min_volume=10000000 ── ${m2}ms`);
  assert(s1 === 200 && s2 === 200, "both requests return 200");
  assert(b2.data.tickers.length <= b1.data.tickers.length,
    `fewer tickers with high min_volume (${b2.data.tickers.length} <= ${b1.data.tickers.length})`);

  // All returned tickers should meet the volume threshold
  const allAboveThreshold = b2.data.tickers.every((t) => t.volume >= 10000000);
  assert(allAboveThreshold, "all tickers meet min_volume threshold");
}

async function testJdkCrypto() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?mode=crypto&benchmark=BTCUSDT");
  console.log(`\n── GET /analysis/rrg?mode=crypto&benchmark=BTCUSDT ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.data.benchmark === "BTCUSDT", `benchmark = 'BTCUSDT'`);

  if (body.data.tickers.length === 0) {
    ok("skipped - no crypto daily data");
    return;
  }
  assert(body.data.tickers.length > 0, "crypto tickers not empty");
  ok(`crypto RRG returned (${body.data.tickers.length} tickers)`);
}

// ──────────────────────────────────────────────
// MA Score algorithm
// ──────────────────────────────────────────────

async function testMascoreBasic() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?algorithm=mascore");
  console.log(`\n── GET /analysis/rrg?algorithm=mascore ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.analysis_type === "rrg", `analysis_type = '${body.analysis_type}'`);
  assert(body.data.algorithm === "mascore", `algorithm = 'mascore'`);
  assert(body.data.benchmark === null || body.data.benchmark === undefined, `benchmark = null (got ${body.data.benchmark})`);
  assert(body.data.period === null || body.data.period === undefined, `period = null (got ${body.data.period})`);
  assert(body.data.tickers.length > 0, `tickers not empty (${body.data.tickers.length})`);
}

async function testMascoreFields() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?algorithm=mascore&trails=5");
  console.log(`\n── GET /analysis/rrg?algorithm=mascore&trails=5 (field check) ── ${ms}ms`);
  assert(status === 200, "returns 200");

  const t = body.data.tickers[0];
  assert(typeof t.symbol === "string", "ticker has symbol");
  assert(typeof t.rs_ratio === "number", "ticker has rs_ratio (MA20 Score)");
  assert(typeof t.rs_momentum === "number", "ticker has rs_momentum (MA100 Score)");
  assert(t.raw_rs === 0.0, `raw_rs = 0.0 (got ${t.raw_rs})`);
  assert(typeof t.close === "number", "ticker has close");
  assert(typeof t.volume === "number", "ticker has volume");
  assert(t.trails !== null, "trails is not null");
}

async function testMascoreNoTrails() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?algorithm=mascore&trails=0");
  console.log(`\n── GET /analysis/rrg?algorithm=mascore&trails=0 ── ${ms}ms`);
  assert(status === 200, "returns 200");

  const t = body.data.tickers[0];
  assert(t.trails === null || t.trails === undefined, `trails is null (got ${JSON.stringify(t.trails)})`);
}

async function testMascoreMinVolume() {
  const { status: s1, body: b1 } = await fetchJSON("/analysis/rrg?algorithm=mascore");
  const { status: s2, body: b2 } = await fetchJSON("/analysis/rrg?algorithm=mascore&min_volume=10000000");
  console.log(`\n── GET /analysis/rrg?algorithm=mascore&min_volume=10000000`);
  assert(s1 === 200 && s2 === 200, "both requests return 200");
  assert(b2.data.tickers.length <= b1.data.tickers.length,
    `fewer tickers with high min_volume (${b2.data.tickers.length} <= ${b1.data.tickers.length})`);

  const allAboveThreshold = b2.data.tickers.every((t) => t.volume >= 10000000);
  assert(allAboveThreshold, "all tickers meet min_volume threshold");
}

async function testMascoreModeAll() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?algorithm=mascore&mode=all");
  console.log(`\n── GET /analysis/rrg?algorithm=mascore&mode=all ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.data.tickers.length > 0, "tickers not empty");

  const allHaveSource = body.data.tickers.every((t) => typeof t.source === "string" && t.source.length > 0);
  assert(allHaveSource, "all tickers have source field");

  const sources = new Set(body.data.tickers.map((t) => t.source));
  assert(sources.size >= 2, `tickers from multiple sources (${[...sources].join(", ")})`);
}

// ──────────────────────────────────────────────
// Date param tests
// ──────────────────────────────────────────────

async function testJdkWithDate() {
  // Use a date well in the past to ensure data exists
  const date = "2025-01-15";
  const { status, body, ms } = await fetchJSON(`/analysis/rrg?date=${date}&trails=0`);
  console.log(`\n── GET /analysis/rrg?date=${date}&trails=0 (JdK with date) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.analysis_date === date, `analysis_date = '${date}' (got '${body.analysis_date}')`);
  assert(body.data.tickers.length > 0, `tickers not empty (${body.data.tickers.length})`);

  // All trail dates should be <= cutoff (trails=0 means no trails)
  const t = body.data.tickers[0];
  assert(t.trails === null || t.trails === undefined, `trails is null when trails=0 (got ${JSON.stringify(t.trails)})`);
}

async function testJdkWithDateTrails() {
  const date = "2025-01-15";
  const { status, body, ms } = await fetchJSON(`/analysis/rrg?date=${date}&trails=10`);
  console.log(`\n── GET /analysis/rrg?date=${date}&trails=10 (JdK with date + trails) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.analysis_date === date, `analysis_date = '${date}' (got '${body.analysis_date}')`);
  assert(body.data.tickers.length > 0, `tickers not empty (${body.data.tickers.length})`);

  // All trail dates should be <= cutoff date
  const allTrailDatesValid = body.data.tickers.every((t) => {
    if (!t.trails) return true;
    return t.trails.every((tp) => tp.date <= date);
  });
  assert(allTrailDatesValid, "all trail dates <= cutoff date");
}

async function testMascoreWithDate() {
  const date = "2025-01-15";
  const { status, body, ms } = await fetchJSON(`/analysis/rrg?algorithm=mascore&date=${date}&trails=5`);
  console.log(`\n── GET /analysis/rrg?algorithm=mascore&date=${date}&trails=5 (mascore with date) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.analysis_date === date, `analysis_date = '${date}' (got '${body.analysis_date}')`);
  assert(body.data.tickers.length > 0, `tickers not empty (${body.data.tickers.length})`);

  // All trail dates should be <= cutoff date
  const allTrailDatesValid = body.data.tickers.every((t) => {
    if (!t.trails) return true;
    return t.trails.every((tp) => tp.date <= date);
  });
  assert(allTrailDatesValid, "all trail dates <= cutoff date");
}

async function testInvalidDate() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?date=not-a-date");
  console.log(`\n── GET /analysis/rrg?date=not-a-date (invalid date) ── ${ms}ms`);
  assert(status === 400, `returns 400 (got ${status})`);
  assert(body.error && body.error.includes("Invalid date"), `error mentions invalid date (got ${body.error})`);
}

// ──────────────────────────────────────────────
// Sector assignment tests
// ──────────────────────────────────────────────

async function testYahooHasSectors() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?mode=yahoo&algorithm=mascore&trails=0");
  console.log(`\n── GET /analysis/rrg?mode=yahoo (global sectors) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.data.tickers.length > 0, `tickers not empty (${body.data.tickers.length})`);

  const withSector = body.data.tickers.filter((t) => t.sector !== null && t.sector !== undefined);
  assert(withSector.length > 0, `yahoo tickers have sectors (${withSector.length}/${body.data.tickers.length})`);

  const sectors = new Set(withSector.map((t) => t.sector));
  ok(`yahoo sectors: ${[...sectors].join(", ")}`);
}

async function testCryptoHasSectors() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?mode=crypto&algorithm=mascore&trails=0");
  console.log(`\n── GET /analysis/rrg?mode=crypto (crypto sectors) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.data.tickers.length > 0, `tickers not empty (${body.data.tickers.length})`);

  const withSector = body.data.tickers.filter((t) => t.sector !== null && t.sector !== undefined);
  assert(withSector.length > 0, `crypto tickers have sectors (${withSector.length}/${body.data.tickers.length})`);
}

async function testVnHasSectors() {
  const { status, body, ms } = await fetchJSON("/analysis/rrg?mode=vn&algorithm=mascore&trails=0");
  console.log(`\n── GET /analysis/rrg?mode=vn (VN sectors) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.data.tickers.length > 0, `tickers not empty (${body.data.tickers.length})`);

  const withSector = body.data.tickers.filter((t) => t.sector !== null && t.sector !== undefined);
  assert(withSector.length > 0, `VN tickers have sectors (${withSector.length}/${body.data.tickers.length})`);
}

// ──────────────────────────────────────────────
// snap=true/false (Redis snapshot cache)
// ──────────────────────────────────────────────

async function testMascoreSnapPerformance() {
  // Warm up snapshot cache
  await fetchJSON("/analysis/rrg?algorithm=mascore&trails=0&cache=false&snap=true");
  const [cold, warm] = await Promise.all([
    fetchJSON("/analysis/rrg?algorithm=mascore&trails=0&cache=false&snap=false"),
    fetchJSON("/analysis/rrg?algorithm=mascore&trails=0&cache=false&snap=true"),
  ]);
  console.log(`\n── snap perf: mascore trails=0 (vn) ── snap=false: ${cold.ms}ms, snap=true: ${warm.ms}ms`);
  assert(cold.status === 200 && warm.status === 200, "both return 200");
  assert(cold.body.data.tickers.length === warm.body.data.tickers.length,
    `same number of tickers (${cold.body.data.tickers.length} vs ${warm.body.data.tickers.length})`);
}

async function testMascoreSnapModeAll() {
  await fetchJSON("/analysis/rrg?algorithm=mascore&trails=0&mode=all&cache=false&snap=true");
  const [cold, warm] = await Promise.all([
    fetchJSON("/analysis/rrg?algorithm=mascore&trails=0&mode=all&cache=false&snap=false"),
    fetchJSON("/analysis/rrg?algorithm=mascore&trails=0&mode=all&cache=false&snap=true"),
  ]);
  console.log(`\n── snap perf: mascore trails=0 mode=all ── snap=false: ${cold.ms}ms, snap=true: ${warm.ms}ms`);
  assert(cold.status === 200 && warm.status === 200, "both return 200");
  ok(`snap=true ${warm.ms < cold.ms ? "faster" : "similar"} to snap=false for mode=all`);
}

// ──────────────────────────────────────────────
// Runner
// ──────────────────────────────────────────────

const tests = [
  testJdkBasic,
  testJdkFields,
  testJdkBenchmark,
  testJdkPeriod,
  testJdkNoTrails,
  testJdkTrailClampMin,
  testJdkTrailClampMax,
  testJdkMinVolume,
  testJdkCrypto,
  testMascoreBasic,
  testMascoreFields,
  testMascoreNoTrails,
  testMascoreMinVolume,
  testMascoreModeAll,
  testJdkWithDate,
  testJdkWithDateTrails,
  testMascoreWithDate,
  testInvalidDate,
  testYahooHasSectors,
  testCryptoHasSectors,
  testVnHasSectors,
  testMascoreSnapPerformance,
  testMascoreSnapModeAll,
];

async function main() {
  console.log(`RRG API test suite \u2014 ${BASE_URL}`);
  console.log(`${"\u2500".repeat(50)}`);
  const suiteStart = performance.now();

  try {
    await fetchJSON("/health");
  } catch (err) {
    console.error(`\nCannot reach ${BASE_URL} \u2014 is the server running?\n`);
    process.exit(1);
  }

  for (const fn of tests) {
    try {
      await fn();
    } catch (err) {
      failed++;
      console.log(`  \ud83d\udca5 ${fn.name}: ${err.message}`);
    }
  }

  const suiteMs = Math.round((performance.now() - suiteStart) * 100) / 100;
  console.log(`\n${"\u2550".repeat(50)}`);
  console.log(`Total: ${passed + failed} | Passed: ${passed} | Failed: ${failed} | ${suiteMs}ms`);
  console.log(`${"\u2550".repeat(50)}`);

  if (failed > 0) process.exit(1);
}

main();
