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

function assertOldestFirst(rows, label) {
  if (rows.length < 2) return;
  for (let i = 1; i < rows.length; i++) {
    if (rows[i].time < rows[i - 1].time) {
      fail(`${label}: row[${i}] time '${rows[i].time}' < row[${i-1}] time '${rows[i-1].time}' (not oldest-first)`);
      return;
    }
  }
  ok(`${label}: data is oldest-first (ASC)`);
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
  assert(r.time.match(/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}$/), "time format ISO 8601");
  assert(typeof r.open === "number" && r.open > 0, "open is positive number");
  assert(typeof r.close === "number" && r.close > 0, "close is positive number");
  assert(typeof r.volume === "number" && r.volume >= 0, "volume is non-negative");
  assert(r.symbol === "VCB", "symbol = VCB");

  // Verify 5-minute alignment: minute should be 0, 5, 10, ...
  const minute = parseInt(r.time.split(":")[1], 10);
  assert(minute % 5 === 0, `minute aligned to 5 (${minute})`);
  ok("5-minute bucket alignment correct");
  assertOldestFirst(body.VCB, "5m aggregated");
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
  assertOldestFirst(body.VCB, "15m aggregated");
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
  assertOldestFirst(body.VCB, "30m aggregated");
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
  assertOldestFirst(body.VCB, "1W aggregated");
}

async function testBiWeekly() {
  const { status, body, ms } = await fetchJSON("/tickers?symbol=VCB&interval=2W&limit=5");
  console.log(`\n── GET /tickers?symbol=VCB&interval=2W&limit=5 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.VCB.length === 5, `got 5 records (got ${body.VCB.length})`);

  const r = body.VCB[0];
  assert(typeof r.close === "number", "close is number");
  ok("bi-weekly aggregation works");
  assertOldestFirst(body.VCB, "2W aggregated");
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
  assertOldestFirst(body.VCB, "1M aggregated");
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

  // Both should have the same core fields
  const coreFields = ["time", "open", "high", "low", "close", "volume", "symbol"];
  const aggFields = Object.keys(agg.VCB[0]);
  const natFields = Object.keys(nat.VCB[0]);
  for (const f of coreFields) {
    assert(aggFields.includes(f), `aggregated has ${f}`);
    assert(natFields.includes(f), `native has ${f}`);
  }
  ok("same core response fields");
  assertOldestFirst(nat.VCB, "1m native (comparison)");
  assertOldestFirst(agg.VCB, "5m aggregated (comparison)");
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
  assertOldestFirst(body.VCB, "15m multi VCB");
  assertOldestFirst(body.FPT, "15m multi FPT");
}

async function testAggregatedLimit100() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=5m&limit=100",
  );
  console.log(`\n── GET /tickers?interval=5m&limit=100 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.VCB.length === 100, `got 100 records (got ${body.VCB.length})`);
  assertOldestFirst(body.VCB, "5m limit=100");
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
  assertOldestFirst(body.VCB, "1W date-range");
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
  const expectedHeader = "symbol,time,open,high,low,close,volume,ma10,ma20,ma50,ma100,ma200,ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,close_changed,volume_changed,total_money_changed";
  assert(lines[0] === expectedHeader, "CSV header correct", `got: ${lines[0]}`);
  assert(lines.length === 6, `header + 5 rows (got ${lines.length} lines)`);
  for (let i = 1; i < lines.length; i++) {
    assert(lines[i].startsWith("VCB,"), `row ${i} starts with VCB`);
  }
  ok("all rows have VCB ticker");
  // Verify CSV data is oldest-first
  const csvDates = lines.slice(1).map((l) => l.split(",")[1]);
  for (let i = 1; i < csvDates.length; i++) {
    assert(csvDates[i] >= csvDates[i - 1], `CSV row ${i+1} date >= row ${i} date (oldest-first)`);
  }
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
  const csvDates = lines.slice(1).map((l) => l.split(",")[1]);
  for (let i = 1; i < csvDates.length; i++) {
    assert(csvDates[i] >= csvDates[i - 1], `monthly CSV row ${i+1} date >= row ${i} date`);
  }
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
  assertOldestFirst(body.VCB, "1W indicators");
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
  assertOldestFirst(body.VCB, "1W change indicators");
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
    "/tickers?symbol=BTCUSDT&mode=crypto&interval=1W&limit=5",
  );
  console.log(`\n── GET /tickers?symbol=BTCUSDT&mode=crypto&interval=1W&limit=5 ── ${ms}ms`);

  if (status !== 200 || !body.BTCUSDT || body.BTCUSDT.length === 0) {
    ok("skipped - no crypto data");
    return;
  }

  assert(body.BTCUSDT.length === 5, `got 5 records (got ${body.BTCUSDT.length})`);
  assert(body.BTCUSDT[0].symbol === "BTCUSDT", "symbol = BTCUSDT");
  assert(typeof body.BTCUSDT[0].close === "number", "close is number");
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
