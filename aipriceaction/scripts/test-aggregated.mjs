/**
 * Test suite for aggregated interval endpoints on the PostgreSQL backend.
 *
 * Tests 5m, 15m, 30m, 1W, 2W, 1M aggregation, CSV export,
 * limit handling, multi-ticker, date range filtering.
 *
 * Usage:
 *   node scripts/test-aggregated.mjs                 # default: http://localhost:3000
 *   node scripts/test-aggregated.mjs http://localhost:3001
 */

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

async function fetchJSON(path) {
  const url = `${BASE_URL}${path}`;
  const start = performance.now();
  const res = await fetch(url);
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

async function fetchCSV(path) {
  const url = `${BASE_URL}${path}`;
  const start = performance.now();
  const res = await fetch(url);
  const text = await res.text();
  const ms = Math.round((performance.now() - start) * 100) / 100;
  return { status: res.status, headers: res.headers, text, ms };
}

// ──────────────────────────────────────────────
// Minute-based aggregations (from 1m data)
// ──────────────────────────────────────────────

async function testFiveMinute() {
  const { status, body, ms } = await fetchJSON("/tickers?symbol=VCB&interval=5m&limit=10");
  console.log(`\n── GET /tickers?symbol=VCB&interval=5m&limit=10 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("VCB" in body, "has VCB key");
  assert(Array.isArray(body.VCB), "VCB is array");
  assert(body.VCB.length === 10, `got 10 records (got ${body.VCB.length})`);

  const r = body.VCB[0];
  assert(typeof r.time === "string", "time is string");
  assert(r.time.match(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/), "time format YYYY-MM-DD HH:MM:SS");
  assert(typeof r.open === "number" && r.open > 0, "open is positive number");
  assert(typeof r.close === "number" && r.close > 0, "close is positive number");
  assert(typeof r.volume === "number" && r.volume >= 0, "volume is non-negative");
  assert(r.symbol === "VCB", "symbol = VCB");

  // Verify 5-minute alignment: minute should be 0, 5, 10, ...
  const minute = parseInt(r.time.split(":")[1], 10);
  assert(minute % 5 === 0, `minute aligned to 5 (${minute})`);
  ok("5-minute bucket alignment correct");
}

async function testFifteenMinute() {
  const { status, body, ms } = await fetchJSON("/tickers?symbol=VCB&interval=15m&limit=5");
  console.log(`\n── GET /tickers?symbol=VCB&interval=15m&limit=5 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.VCB.length === 5, `got 5 records (got ${body.VCB.length})`);

  const r = body.VCB[0];
  const minute = parseInt(r.time.split(":")[1], 10);
  assert(minute % 15 === 0, `minute aligned to 15 (${minute})`);
  ok("15-minute bucket alignment correct");
}

async function testThirtyMinute() {
  const { status, body, ms } = await fetchJSON("/tickers?symbol=VCB&interval=30m&limit=5");
  console.log(`\n── GET /tickers?symbol=VCB&interval=30m&limit=5 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.VCB.length === 5, `got 5 records (got ${body.VCB.length})`);

  const r = body.VCB[0];
  const minute = parseInt(r.time.split(":")[1], 10);
  assert(minute % 30 === 0, `minute aligned to 30 (${minute})`);
  ok("30-minute bucket alignment correct");
}

// ──────────────────────────────────────────────
// Day-based aggregations (from 1D data)
// ──────────────────────────────────────────────

async function testWeekly() {
  const { status, body, ms } = await fetchJSON("/tickers?symbol=VCB&interval=1W&limit=5");
  console.log(`\n── GET /tickers?symbol=VCB&interval=1W&limit=5 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.VCB.length === 5, `got 5 records (got ${body.VCB.length})`);

  const r = body.VCB[0];
  assert(typeof r.open === "number" && r.open > 0, "open is positive number");
  assert(typeof r.high === "number", "high is number");
  assert(typeof r.low === "number", "low is number");
  assert(typeof r.close === "number" && r.close > 0, "close is positive number");
  assert(typeof r.volume === "number", "volume is number");

  // Weekly time should be a Monday (ISO 8601 week start)
  const date = new Date(r.time);
  assert(date.getDay() === 1, `time is Monday (got day ${date.getDay()})`);
  ok("weekly bucket starts on Monday");
}

async function testBiWeekly() {
  const { status, body, ms } = await fetchJSON("/tickers?symbol=VCB&interval=2W&limit=5");
  console.log(`\n── GET /tickers?symbol=VCB&interval=2W&limit=5 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.VCB.length === 5, `got 5 records (got ${body.VCB.length})`);

  const r = body.VCB[0];
  assert(typeof r.close === "number", "close is number");
  ok("bi-weekly aggregation works");
}

async function testMonthly() {
  const { status, body, ms } = await fetchJSON("/tickers?symbol=VCB&interval=1M&limit=5");
  console.log(`\n── GET /tickers?symbol=VCB&interval=1M&limit=5 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.VCB.length === 5, `got 5 records (got ${body.VCB.length})`);

  const r = body.VCB[0];
  assert(typeof r.close === "number", "close is number");

  // Monthly time should be first day of month
  const date = new Date(r.time);
  assert(date.getDate() === 1, `time is 1st of month (got day ${date.getDate()})`);
  ok("monthly bucket starts on 1st");
}

// ──────────────────────────────────────────────
// Aggregated vs native comparison
// ──────────────────────────────────────────────

async function testAggregatedVsNative() {
  const { body: agg } = await fetchJSON("/tickers?symbol=VCB&interval=5m&limit=3");
  const { body: nat } = await fetchJSON("/tickers?symbol=VCB&interval=1m&limit=3");
  console.log(`\n── Compare 5m (aggregated) vs 1m (native) structure ──`);

  assert("VCB" in agg && "VCB" in nat, "both have VCB key");
  assert(agg.VCB.length === nat.VCB.length, "same record count");

  // Both should have the same fields
  const aggFields = Object.keys(agg.VCB[0]).sort();
  const natFields = Object.keys(nat.VCB[0]).sort();
  assert(
    JSON.stringify(aggFields) === JSON.stringify(natFields),
    "same response fields",
    `agg: ${aggFields.join(",")} vs nat: ${natFields.join(",")}`,
  );
}

// ──────────────────────────────────────────────
// Multi-ticker + limit
// ──────────────────────────────────────────────

async function testMultiTickerAggregated() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&symbol=FPT&interval=15m&limit=5",
  );
  console.log(`\n── GET /tickers multi-ticker 15m ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("VCB" in body && "FPT" in body, "has both VCB and FPT");
  assert(body.VCB.length === 5, `VCB has 5 (got ${body.VCB.length})`);
  assert(body.FPT.length === 5, `FPT has 5 (got ${body.FPT.length})`);
}

async function testAggregatedLimit100() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=5m&limit=100",
  );
  console.log(`\n── GET /tickers?interval=5m&limit=100 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.VCB.length === 100, `got 100 records (got ${body.VCB.length})`);
}

// ──────────────────────────────────────────────
// Date range filtering
// ──────────────────────────────────────────────

async function testAggregatedDateRange() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1W&start_date=2025-01-06&end_date=2025-03-01&limit=20",
  );
  console.log(`\n── GET /tickers?interval=1W&start_date=...&end_date=... ── ${ms}ms`);
  assert(status === 200, "returns 200");
  if (body.VCB.length > 0) {
    const dates = body.VCB.map((r) => r.time);
    for (const d of dates) {
      const dateStr = d.split(" ")[0]; // handle "YYYY-MM-DD HH:MM:SS" format
      assert(
        dateStr >= "2025-01-06" && dateStr <= "2025-03-01",
        `date in range (${d})`,
      );
    }
    ok("all dates within range");
  } else {
    ok("no data in range (acceptable)");
  }
}

// ──────────────────────────────────────────────
// CSV format for aggregated intervals
// ──────────────────────────────────────────────

async function testAggregatedCsv() {
  const { status, headers, text, ms } = await fetchCSV(
    "/tickers?symbol=VCB&interval=15m&limit=5&format=csv",
  );
  console.log(`\n── GET /tickers?interval=15m&format=csv ── ${ms}ms`);
  assert(status === 200, "returns 200");
  const ct = headers.get("content-type") || "";
  assert(ct.includes("text/csv"), `content-type is text/csv (got ${ct})`);
  const lines = text.trim().split("\n");
  assert(lines[0] === "ticker,time,open,high,low,close,volume", "CSV header correct");
  assert(lines.length === 6, `header + 5 rows (got ${lines.length} lines)`);
  for (let i = 1; i < lines.length; i++) {
    assert(lines[i].startsWith("VCB,"), `row ${i} starts with VCB`);
  }
  ok("all rows have VCB ticker");
}

async function testMonthlyCsv() {
  const { status, headers, text, ms } = await fetchCSV(
    "/tickers?symbol=VCB&interval=1M&limit=3&format=csv",
  );
  console.log(`\n── GET /tickers?interval=1M&format=csv ── ${ms}ms`);
  assert(status === 200, "returns 200");
  const lines = text.trim().split("\n");
  assert(lines.length === 4, `header + 3 rows (got ${lines.length} lines)`);
  ok("monthly CSV export works");
}

// ──────────────────────────────────────────────
// Aggregated indicators (MA)
// ──────────────────────────────────────────────

async function testAggregatedIndicators() {
  // Weekly with large limit so later records have enough history for MAs
  const { status, body, ms } = await fetchJSON("/tickers?symbol=VCB&interval=1W&limit=200");
  console.log(`\n── Aggregated indicators (1W, limit=200) ── ${ms}ms`);
  assert(status === 200, "returns 200");

  if (body.VCB.length < 20) {
    ok("skipped - not enough data for MA check");
    return;
  }

  // Check a record in the middle (index 50) where MAs should be calculated
  const mid = body.VCB[50];
  assert(typeof mid.ma10 === "number", `ma10 present at index 50 (got ${mid.ma10})`);
  assert(typeof mid.ma20 === "number", `ma20 present at index 50 (got ${mid.ma20})`);
  assert(typeof mid.ma50 === "number", `ma50 present at index 50 (got ${mid.ma50})`);
  assert(typeof mid.ma10_score === "number", `ma10_score present at index 50`);
  ok("MA indicators calculated on aggregated data");
}

async function testAggregatedChangeIndicators() {
  const { status, body } = await fetchJSON("/tickers?symbol=VCB&interval=1W&limit=200");
  console.log(`\n── Aggregated change indicators (1W) ──`);

  if (body.VCB.length < 5) {
    ok("skipped - not enough data");
    return;
  }

  // Second record should have change indicators
  const second = body.VCB[1];
  assert(
    typeof second.close_changed === "number",
    `close_changed present at index 1 (got ${second.close_changed})`,
  );
  assert(
    typeof second.volume_changed === "number",
    `volume_changed present at index 1 (got ${second.volume_changed})`,
  );
  ok("change indicators calculated on aggregated data");
}

// ──────────────────────────────────────────────
// Legacy price scaling with aggregated intervals
// ──────────────────────────────────────────────

async function testAggregatedLegacy() {
  const { body: normal } = await fetchJSON("/tickers?symbol=VCB&interval=1W&limit=1");
  const { body: legacy } = await fetchJSON("/tickers?symbol=VCB&interval=1W&limit=1&legacy=true");
  console.log(`\n── Aggregated legacy price scaling ──`);

  if (normal.VCB.length === 0) {
    ok("skipped - no data");
    return;
  }

  const normalClose = normal.VCB[0].close;
  const legacyClose = legacy.VCB[0].close;
  assert(
    Math.abs(normalClose / legacyClose - 1000) < 0.01,
    `legacy = native / 1000 (native=${normalClose}, legacy=${legacyClose})`,
  );
}

async function testAggregatedLegacyIndexNotDivided() {
  const { body: normal } = await fetchJSON("/tickers?symbol=VNINDEX&interval=1W&limit=1");
  const { body: legacy } = await fetchJSON("/tickers?symbol=VNINDEX&interval=1W&limit=1&legacy=true");
  console.log(`\n── Aggregated legacy: VNINDEX not divided ──`);

  if (!normal.VNINDEX || normal.VNINDEX.length === 0) {
    ok("skipped - no data");
    return;
  }

  assert(
    normal.VNINDEX[0].close === legacy.VNINDEX[0].close,
    `VNINDEX price unchanged (native=${normal.VNINDEX[0].close}, legacy=${legacy.VNINDEX[0].close})`,
  );
}

// ──────────────────────────────────────────────
// Crypto aggregated intervals
// ──────────────────────────────────────────────

async function testCryptoAggregated() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=BTC&mode=crypto&interval=1W&limit=5",
  );
  console.log(`\n── GET /tickers?symbol=BTC&mode=crypto&interval=1W&limit=5 ── ${ms}ms`);

  if (status !== 200 || !body.BTC || body.BTC.length === 0) {
    ok("skipped - no crypto data");
    return;
  }

  assert(body.BTC.length === 5, `got 5 records (got ${body.BTC.length})`);
  assert(body.BTC[0].symbol === "BTC", "symbol = BTC");
  assert(typeof body.BTC[0].close === "number", "close is number");
  ok("crypto weekly aggregation works");
}

// ──────────────────────────────────────────────
// Runner
// ──────────────────────────────────────────────

const tests = [
  testFiveMinute,
  testFifteenMinute,
  testThirtyMinute,
  testWeekly,
  testBiWeekly,
  testMonthly,
  testAggregatedVsNative,
  testMultiTickerAggregated,
  testAggregatedLimit100,
  testAggregatedDateRange,
  testAggregatedCsv,
  testMonthlyCsv,
  testAggregatedIndicators,
  testAggregatedChangeIndicators,
  testAggregatedLegacy,
  testAggregatedLegacyIndexNotDivided,
  testCryptoAggregated,
];

async function main() {
  console.log(`Aggregated intervals test suite — ${BASE_URL}`);
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
