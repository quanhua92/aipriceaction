/**
 * Integration tests for the Axum API server (/health, /tickers, /tickers/group).
 *
 * Usage:
 *   node scripts/test-api.mjs                 # default: http://localhost:3001
 *   node scripts/test-api.mjs http://localhost:3000
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
// Tests
// ──────────────────────────────────────────────

async function testHealth() {
  const { status, body, ms } = await fetchJSON("/health");
  console.log(`\n── GET /health ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.status === "ok", "status is 'ok'", `got: ${body.status}`);
  assert(body.storage === "postgresql", "storage is 'postgresql'");
}

async function testSingleTicker() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1D&limit=2",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=1D&limit=2 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(typeof body === "object" && "VCB" in body, "has VCB key");
  assert(Array.isArray(body.VCB), "VCB value is array");
  assert(body.VCB.length === 2, `got 2 rows (got ${body.VCB.length})`);

  const r = body.VCB[0];
  assert(typeof r.time === "string", "time is string");
  assert(r.time.match(/^\d{4}-\d{2}-\d{2}$/), "daily time format YYYY-MM-DD");
  assert(typeof r.close === "number", "close is number");
  assert(typeof r.volume === "number", "volume is number");
  assert(r.symbol === "VCB", `symbol is 'VCB'`);
  assert(typeof r.ma10 === "number" || r.ma10 === undefined, "ma10 present or undefined");
  assert(typeof r.ma10_score === "number" || r.ma10_score === undefined, "ma10_score present or undefined");
  assert(typeof r.close_changed === "number" || r.close_changed === undefined, "close_changed present or undefined");
}

async function testMultipleTickers() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&symbol=FPT&interval=1m&limit=3",
  );
  console.log(`\n── GET /tickers?symbol=VCB&symbol=FPT&interval=1m&limit=3 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("VCB" in body && "FPT" in body, "has both VCB and FPT keys");
  assert(body.VCB.length === 3, `VCB has 3 rows (got ${body.VCB.length})`);
  assert(body.FPT.length === 3, `FPT has 3 rows (got ${body.FPT.length})`);
}

async function testCsvFormat() {
  const url = `${BASE_URL}/tickers?symbol=VCB&interval=1D&limit=2&format=csv`;
  const start = performance.now();
  const res = await fetch(url);
  const ct = res.headers.get("content-type") || "";
  const text = await res.text();
  const ms = Math.round((performance.now() - start) * 100) / 100;
  console.log(`\n── GET /tickers?symbol=VCB&interval=1D&limit=2&format=csv ── ${ms}ms`);
  assert(res.status === 200, "returns 200");
  assert(ct.includes("text/csv"), `content-type is text/csv (got ${ct})`);
  const lines = text.trim().split("\n");
  assert(lines[0] === "ticker,time,open,high,low,close,volume", "CSV header matches");
  assert(lines.length === 3, `header + 2 rows (got ${lines.length} lines)`);
  for (let i = 1; i < lines.length; i++) {
    assert(lines[i].startsWith("VCB,"), `row ${i} starts with VCB`);
  }
}

async function testLegacyPrices() {
  const { body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1D&limit=1&legacy=true",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=1D&limit=1&legacy=true ── ${ms}ms`);
  const close = body.VCB[0].close;
  assert(close < 200, `VCB price divided by 1000 (got ${close})`);
}

async function testLegacyIndexNotDivided() {
  const { body, ms } = await fetchJSON(
    "/tickers?symbol=VNINDEX&interval=1D&limit=1&legacy=true",
  );
  console.log(`\n── GET /tickers?symbol=VNINDEX&interval=1D&limit=1&legacy=true ── ${ms}ms`);
  const close = body.VNINDEX[0].close;
  assert(close > 1000, `VNINDEX price NOT divided (got ${close})`);
}

async function testDateRange() {
  const { body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1D&start_date=2025-01-01&end_date=2025-03-01&limit=5",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=1D&start_date=...&end_date=...&limit=5 ── ${ms}ms`);
  const rows = body.VCB;
  assert(rows.length > 0, `got rows (got ${rows.length})`);
  const dates = rows.map((r) => r.time);
  for (const d of dates) {
    assert(d >= "2025-01-01" && d <= "2025-03-01", `date in range (${d})`);
  }
}

async function testHourlyTimeFormat() {
  const { body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1H&limit=2",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=1H&limit=2 ── ${ms}ms`);
  const r = body.VCB[0];
  assert(
    r.time.match(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/),
    `hourly time format YYYY-MM-DD HH:MM:SS (got '${r.time}')`,
  );
}

async function testMinuteTimeFormat() {
  const { body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1m&limit=2",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=1m&limit=2 ── ${ms}ms`);
  const r = body.VCB[0];
  assert(
    r.time.match(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/),
    `minute time format YYYY-MM-DD HH:MM:SS (got '${r.time}')`,
  );
}

async function testNoSymbols() {
  const { status, body, ms } = await fetchJSON("/tickers?interval=1D");
  console.log(`\n── GET /tickers (no symbols) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(
    typeof body === "object" && Object.keys(body).length === 0,
    "empty object",
  );
}

async function testInvalidInterval() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=invalid",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=invalid ── ${ms}ms`);
  assert(status === 400, `returns 400 (got ${status})`);
  assert(typeof body.error === "string", "has error message");
}

async function testVnTickerGroups() {
  const { status, body, ms } = await fetchJSON("/tickers/group");
  console.log(`\n── GET /tickers/group (VN) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(typeof body === "object", "is an object");
  const keys = Object.keys(body);
  assert(keys.length > 0, `has groups (got ${keys.length})`);
  assert(keys.includes("NGAN_HANG"), `has NGAN_HANG group`);
  const sample = body[keys[0]];
  assert(Array.isArray(sample), "group values are arrays");
  assert(sample.length > 0, "group has tickers");
}

async function testCryptoTickerGroups() {
  const { status, body, ms } = await fetchJSON("/tickers/group?mode=crypto");
  console.log(`\n── GET /tickers/group?mode=crypto ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("CRYPTO_TOP_100" in body, "has CRYPTO_TOP_100 key");
  const symbols = body.CRYPTO_TOP_100;
  assert(Array.isArray(symbols), "is array");
  assert(symbols.length > 0, `has symbols (got ${symbols.length})`);
  assert(symbols.includes("BTC"), "includes BTC");
  assert(symbols.includes("ETH"), "includes ETH");
}

async function testModeAliases() {
  const { status: s1, ms: ms1 } = await fetchJSON("/tickers/group?mode=stock");
  const { status: s2, body: b2, ms: ms2 } = await fetchJSON("/tickers/group?mode=cryptos");
  console.log(`\n── Mode aliases (stock, cryptos) ── ${ms1}ms / ${ms2}ms`);
  assert(s1 === 200, "mode=stock → 200");
  assert(s2 === 200, "mode=cryptos → 200");
  assert("CRYPTO_TOP_100" in b2, "mode=cryptos has CRYPTO_TOP_100");
}

async function testIntervalLowercase() {
  const { status: s1, body: b1, ms: ms1 } = await fetchJSON("/tickers?symbol=VCB&interval=1d&limit=1");
  const { status: s2, ms: ms2 } = await fetchJSON("/tickers?symbol=VCB&interval=1h&limit=1");
  const { status: s3, ms: ms3 } = await fetchJSON("/tickers?symbol=VCB&interval=1M&limit=1");
  console.log(`\n── Lowercase interval aliases (1d, 1h, 1M) ── ${ms1}ms / ${ms2}ms / ${ms3}ms`);
  assert(s1 === 200, "interval=1d → 200");
  assert(b1.VCB?.length === 1, "1d returns rows");
  assert(s2 === 200, "interval=1h → 200");
  assert(s3 === 200, "interval=1M → 200");
}

async function testIndicatorsPresent() {
  const { body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1D&limit=1",
  );
  console.log(`\n── Technical indicators present ── ${ms}ms`);
  const r = body.VCB[0];
  assert(typeof r.ma10 === "number", "ma10 is number");
  assert(typeof r.ma20 === "number", "ma20 is number");
  assert(typeof r.ma50 === "number", "ma50 is number");
  assert(typeof r.ma100 === "number", "ma100 is number");
  assert(typeof r.ma200 === "number", "ma200 is number");
  assert(typeof r.ma10_score === "number", "ma10_score is number");
  assert(typeof r.close_changed === "number", "close_changed is number");
  assert(typeof r.volume_changed === "number", "volume_changed is number");
  assert(typeof r.total_money_changed === "number", "total_money_changed is number");
}

// ──────────────────────────────────────────────
// Runner
// ──────────────────────────────────────────────

const tests = [
  testHealth,
  testSingleTicker,
  testMultipleTickers,
  testCsvFormat,
  testLegacyPrices,
  testLegacyIndexNotDivided,
  testDateRange,
  testHourlyTimeFormat,
  testMinuteTimeFormat,
  testNoSymbols,
  testInvalidInterval,
  testVnTickerGroups,
  testCryptoTickerGroups,
  testModeAliases,
  testIntervalLowercase,
  testIndicatorsPresent,
];

async function main() {
  console.log(`API test suite — ${BASE_URL}`);
  console.log(`${"─".repeat(50)}`);
  const suiteStart = performance.now();

  // Quick connectivity check
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
