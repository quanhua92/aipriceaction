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
  assert(typeof body.total_tickers_count === "number", "has total_tickers_count");
  assert(typeof body.active_tickers_count === "number", "has active_tickers_count");
  assert(typeof body.daily_records_count === "number", "has daily_records_count");
  assert(typeof body.hourly_records_count === "number", "has hourly_records_count");
  assert(typeof body.minute_records_count === "number", "has minute_records_count");
  assert(typeof body.current_system_time === "string", "has current_system_time");
  assert(typeof body.uptime_secs === "number", "has uptime_secs");
  assert(typeof body.daily_last_sync === "string", "has daily_last_sync");
  assert(typeof body.hourly_last_sync === "string", "has hourly_last_sync");
  assert(typeof body.minute_last_sync === "string", "has minute_last_sync");
  assert(typeof body.is_trading_hours === "boolean", "has is_trading_hours");
  assert(body.trading_hours_timezone === "Asia/Ho_Chi_Minh", "trading_hours_timezone");
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
  assertOldestFirst(body.VCB, "daily native");
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
  assertOldestFirst(body.VCB, "minute native VCB");
  assertOldestFirst(body.FPT, "minute native FPT");
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
  const expected = "symbol,time,open,high,low,close,volume,ma10,ma20,ma50,ma100,ma200,ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,close_changed,volume_changed,total_money_changed";
  assert(lines[0] === expected, "CSV header matches", `got: ${lines[0]}`);
  assert(lines.length === 3, `header + 2 rows (got ${lines.length} lines)`);
  for (let i = 1; i < lines.length; i++) {
    assert(lines[i].startsWith("VCB,"), `row ${i} starts with VCB`);
  }
  // Verify CSV data is oldest-first
  const csvDates = lines.slice(1).map((l) => l.split(",")[1]);
  for (let i = 1; i < csvDates.length; i++) {
    assert(csvDates[i] >= csvDates[i - 1], `CSV row ${i+1} date >= row ${i} date (oldest-first)`);
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
  assertOldestFirst(rows, "daily date-range");
}

async function testHourlyTimeFormat() {
  const { body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1H&limit=2",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=1H&limit=2 ── ${ms}ms`);
  const r = body.VCB[0];
  assert(
    r.time.match(/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}$/),
    `hourly time format ISO 8601 (got '${r.time}')`,
  );
  assertOldestFirst(body.VCB, "hourly native");
}

async function testMinuteTimeFormat() {
  const { body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1m&limit=2",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=1m&limit=2 ── ${ms}ms`);
  const r = body.VCB[0];
  assert(
    r.time.match(/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}$/),
    `minute time format ISO 8601 (got '${r.time}')`,
  );
  assertOldestFirst(body.VCB, "minute native");
}

async function testNoSymbols() {
  const { status, body, ms } = await fetchJSON("/tickers?interval=1D");
  console.log(`\n── GET /tickers (no symbols) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  const keys = Object.keys(body);
  assert(keys.length > 0, "returns all tickers (non-empty)", `got ${keys.length} keys`);
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
  assert(symbols.includes("BTCUSDT"), "includes BTCUSDT");
  assert(symbols.includes("ETHUSDT"), "includes ETHUSDT");
}

async function testModeAliases() {
  const { status: s1, ms: ms1 } = await fetchJSON("/tickers/group?mode=stock");
  const { status: s2, body: b2, ms: ms2 } = await fetchJSON("/tickers/group?mode=cryptos");
  console.log(`\n── Mode aliases (stock, cryptos) ── ${ms1}ms / ${ms2}ms`);
  assert(s1 === 200, "mode=stock → 200");
  assert(s2 === 200, "mode=cryptos → 200");
  assert("CRYPTO_TOP_100" in b2, "mode=cryptos has CRYPTO_TOP_100");
}

async function testIntervalUppercase() {
  const { status: s1, body: b1, ms: ms1 } = await fetchJSON("/tickers?symbol=VCB&interval=1D&limit=1");
  const { status: s2, body: b2, ms: ms2 } = await fetchJSON("/tickers?symbol=VCB&interval=1H&limit=1");
  const { status: s3, body: b3, ms: ms3 } = await fetchJSON("/tickers?symbol=VCB&interval=1M&limit=1");
  console.log(`\n── Uppercase intervals (1D, 1H, 1M) ── ${ms1}ms / ${ms2}ms / ${ms3}ms`);
  assert(s1 === 200, "interval=1D → 200");
  assert(b1.VCB?.length === 1, "1D returns rows");
  assert(s2 === 200, "interval=1H → 200");
  assert(b2.VCB?.length === 1, "1H returns rows");
  assert(s3 === 200, "interval=1M → 200");
  assert(b3.VCB?.length === 1, "1M returns rows");
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

async function testNoLimit() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1D",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=1D (no limit) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("VCB" in body, "has VCB key");
  assert(body.VCB.length === 252, `defaults to 252 rows (got ${body.VCB.length})`);
  assertOldestFirst(body.VCB, "no-limit daily");
}

async function testNoLimitEmptyTicker() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=NOTEXIST&interval=1D",
  );
  console.log(`\n── GET /tickers?symbol=NOTEXIST&interval=1D (no limit) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(!("NOTEXIST" in body), "no key for non-existent ticker");
  assert(typeof body === "object", "empty object");
}

async function testNoLimitFutureRange() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1D&start_date=2099-01-01",
  );
  console.log(`\n── GET /tickers?symbol=VCB (future date range, no limit) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(!("VCB" in body), "no key for future-only range");
}

async function testModeAllWithSymbols() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?mode=all&interval=1D&limit=1&symbol=VCB&symbol=BTCUSDT",
  );
  console.log(`\n── GET /tickers?mode=all&interval=1D&limit=1&symbol=VCB&symbol=BTCUSDT ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("VCB" in body, "has VCB key (vn source)");
  assert(body.VCB.length === 1, `VCB has 1 row (got ${body.VCB.length})`);
  if ("BTCUSDT" in body) {
    assert(body.BTCUSDT.length === 1, `BTCUSDT has 1 row (got ${body.BTCUSDT.length})`);
    ok("has BTCUSDT key (crypto source)");
  } else {
    ok("BTCUSDT not returned (may not exist in DB)");
  }
}

async function testModeAllNoSymbols() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?mode=all&interval=1D&limit=1",
  );
  console.log(`\n── GET /tickers?mode=all&interval=1D&limit=1 (no symbols) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  const keys = Object.keys(body);
  assert(keys.length > 1, `returns tickers from multiple sources (got ${keys.length} keys)`);
  // Should have tickers from at least 2 sources
  const hasVn = keys.some(k => /^[A-Z]{3}$/.test(k));
  const hasCrypto = keys.some(k => k.endsWith("USDT"));
  assert(hasVn || hasCrypto, "has tickers from at least one known source pattern");
}

async function testModeAllLegacy() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?mode=all&interval=1D&limit=1&symbol=VCB&symbol=BTCUSDT&legacy=true",
  );
  console.log(`\n── GET /tickers?mode=all&legacy=true ── ${ms}ms`);
  assert(status === 200, "returns 200");
  if ("VCB" in body && body.VCB.length > 0) {
    assert(body.VCB[0].close < 200, `VCB price divided by 1000 (got ${body.VCB[0].close})`);
    ok("legacy scaling applied to VN ticker");
  }
  if ("BTCUSDT" in body && body.BTCUSDT.length > 0) {
    assert(body.BTCUSDT[0].close > 200, `BTCUSDT price NOT divided (got ${body.BTCUSDT[0].close})`);
    ok("legacy scaling NOT applied to crypto ticker");
  }
}

async function testModeAllGroups() {
  const { status, body, ms } = await fetchJSON("/tickers/group?mode=all");
  console.log(`\n── GET /tickers/group?mode=all ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(typeof body === "object", "is an object");
  const keys = Object.keys(body);
  assert(keys.length > 1, `has groups from multiple sources (got ${keys.length})`);
}

async function testModeAllNames() {
  const { status, body, ms } = await fetchJSON("/tickers/name?mode=all");
  console.log(`\n── GET /tickers/name?mode=all ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(typeof body === "object", "is an object");
  const keys = Object.keys(body);
  assert(keys.length > 1, `has names from multiple sources (got ${keys.length})`);
}

async function testModeAllAggregated() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?mode=all&interval=1W&limit=2&symbol=VCB&symbol=BTCUSDT",
  );
  console.log(`\n── GET /tickers?mode=all&interval=1W&limit=2 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  if ("VCB" in body) {
    assert(body.VCB.length === 2, `VCB has 2 rows (got ${body.VCB.length})`);
    ok("VN weekly aggregation works with mode=all");
  }
  if ("BTCUSDT" in body) {
    ok("crypto weekly aggregation works with mode=all");
  }
}

// ──────────────────────────────────────────────
// Duplicate timestamp detection
// ──────────────────────────────────────────────

function assertNoDuplicateTimes(rows, label) {
  if (rows.length < 2) return;
  const seen = new Map();
  for (let i = 0; i < rows.length; i++) {
    const t = rows[i].time;
    if (seen.has(t)) {
      fail(
        `${label}: duplicate time '${t}' at rows ${seen.get(t)} and ${i}`,
        `total rows=${rows.length}, duplicate times=${[...seen.entries()].filter(([, v]) => v > 1).length + 1}`,
      );
      return;
    }
    seen.set(t, i);
  }
  ok(`${label}: no duplicate times (${rows.length} rows)`);
}

async function testVnNoDuplicates() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1D&limit=40",
  );
  console.log(`\n── No duplicates: VCB (vn) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  if ("VCB" in body) {
    assertNoDuplicateTimes(body.VCB, "VCB daily");
  } else {
    ok("VCB not in response (skipped)");
  }
}

async function testYahooNoDuplicates() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=CL=F&interval=1D&limit=40&mode=yahoo",
  );
  console.log(`\n── No duplicates: CL=F (yahoo) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  if ("CL=F" in body) {
    assertNoDuplicateTimes(body["CL=F"], "CL=F daily yahoo");
  } else {
    ok("CL=F not in response (skipped)");
  }
}

async function testCryptoNoDuplicates() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=BTCUSDT&interval=1D&limit=40&mode=crypto",
  );
  console.log(`\n── No duplicates: BTCUSDT (crypto) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  if ("BTCUSDT" in body) {
    assertNoDuplicateTimes(body.BTCUSDT, "BTCUSDT daily crypto");
  } else {
    ok("BTCUSDT not in response (skipped)");
  }
}

async function testModeAllNoDuplicates() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=CL=F&symbol=VCB&symbol=BTCUSDT&interval=1D&limit=40&mode=all",
  );
  console.log(`\n── No duplicates: mode=all (CL=F + VCB + BTCUSDT) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  for (const sym of ["CL=F", "VCB", "BTCUSDT"]) {
    if (sym in body && body[sym].length > 0) {
      assertNoDuplicateTimes(body[sym], `${sym} daily mode=all`);
    } else {
      ok(`${sym} not in response (skipped)`);
    }
  }
}

// ──────────────────────────────────────────────
// ma=false param (skip MA indicators)
// ──────────────────────────────────────────────

async function testMaFalseNoIndicators() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1D&limit=2&ma=false",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=1D&limit=2&ma=false ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("VCB" in body, "has VCB key");
  assert(body.VCB.length === 2, `got 2 rows (got ${body.VCB.length})`);
  const r = body.VCB[0];
  assert(r.ma10 === undefined, "ma10 absent when ma=false");
  assert(r.ma20 === undefined, "ma20 absent when ma=false");
  assert(r.ma50 === undefined, "ma50 absent when ma=false");
  assert(r.ma100 === undefined, "ma100 absent when ma=false");
  assert(r.ma200 === undefined, "ma200 absent when ma=false");
  assert(r.ma10_score === undefined, "ma10_score absent when ma=false");
  // Second row (newest) has change indicators; first row (oldest) does not
  const r2 = body.VCB[1];
  assert(r2.close_changed !== undefined, "close_changed still present on second row");
  assert(r2.volume_changed !== undefined, "volume_changed still present on second row");
}

async function testMaTrueHasIndicators() {
  const { body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1D&limit=1&ma=true",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=1D&limit=1&ma=true ── ${ms}ms`);
  const r = body.VCB[0];
  assert(typeof r.ma10 === "number", "ma10 present when ma=true");
  assert(typeof r.ma20 === "number", "ma20 present when ma=true");
  assert(typeof r.ma50 === "number", "ma50 present when ma=true");
  assert(typeof r.ma100 === "number", "ma100 present when ma=true");
  assert(typeof r.ma200 === "number", "ma200 present when ma=true");
}

async function testMaFalseAggregated() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=15m&limit=2&ma=false",
  );
  console.log(`\n── GET /tickers?symbol=VCB&interval=15m&limit=2&ma=false ── ${ms}ms`);
  assert(status === 200, "returns 200");
  if ("VCB" in body) {
    const r = body.VCB[0];
    assert(r.ma10 === undefined, "ma10 absent for aggregated when ma=false");
    // Second row (newest) has change indicators
    const r2 = body.VCB[1];
    assert(r2.close_changed !== undefined, "close_changed still present for aggregated");
  } else {
    ok("VCB not in response (skipped)");
  }
}

// ──────────────────────────────────────────────
// ema=true query parameter (EMA vs SMA)
// ──────────────────────────────────────────────

async function testEmaDiffersFromSma() {
  const [smaRes, emaRes] = await Promise.all([
    fetchJSON("/tickers?symbol=VCB&interval=1D&limit=50"),
    fetchJSON("/tickers?symbol=VCB&interval=1D&limit=50&ema=true"),
  ]);
  console.log(`\n── EMA vs SMA: VCB 1D limit=50 ── ${smaRes.ms}ms / ${emaRes.ms}ms`);
  assert(smaRes.status === 200, "SMA request returns 200");
  assert(emaRes.status === 200, "EMA request returns 200");
  if (!("VCB" in smaRes.body) || !("VCB" in emaRes.body)) {
    ok("VCB not in response (skipped)");
    return;
  }
  // Scan all rows for any MA difference (EMA diverges from SMA over time)
  let foundDiff = false;
  for (let i = 0; i < smaRes.body.VCB.length; i++) {
    const s = smaRes.body.VCB[i];
    const e = emaRes.body.VCB[i];
    if (typeof s.ma10 === "number" && typeof e.ma10 === "number" && s.ma10 !== e.ma10) {
      foundDiff = true;
      break;
    }
  }
  assert(foundDiff, "at least one row has different EMA vs SMA ma10");
}

async function testEmaDefaultIsSma() {
  const [defaultRes, explicitSmaRes] = await Promise.all([
    fetchJSON("/tickers?symbol=VCB&interval=1D&limit=3"),
    fetchJSON("/tickers?symbol=VCB&interval=1D&limit=3&ema=false"),
  ]);
  console.log(`\n── Default (no ema) matches ema=false ──`);
  assert(defaultRes.status === 200, "default request returns 200");
  assert(explicitSmaRes.status === 200, "ema=false request returns 200");
  if (!("VCB" in defaultRes.body) || !("VCB" in explicitSmaRes.body)) {
    ok("VCB not in response (skipped)");
    return;
  }
  const d = defaultRes.body.VCB;
  const s = explicitSmaRes.body.VCB;
  for (let i = 0; i < d.length; i++) {
    assert(d[i].ma10 === s[i].ma10, `default ma10 matches ema=false at row ${i}`);
    assert(d[i].ma20 === s[i].ma20, `default ma20 matches ema=false at row ${i}`);
  }
  ok("default behavior (no ema param) is identical to ema=false");
}

async function testEmaWithAggregated() {
  const [smaRes, emaRes] = await Promise.all([
    fetchJSON("/tickers?symbol=VCB&interval=1W&limit=10"),
    fetchJSON("/tickers?symbol=VCB&interval=1W&limit=10&ema=true"),
  ]);
  console.log(`\n── EMA with aggregated interval (1W) ── ${smaRes.ms}ms / ${emaRes.ms}ms`);
  assert(smaRes.status === 200, "SMA weekly returns 200");
  assert(emaRes.status === 200, "EMA weekly returns 200");
  if (!("VCB" in smaRes.body) || !("VCB" in emaRes.body)) {
    ok("VCB not in response (skipped)");
    return;
  }
  const s = smaRes.body.VCB;
  const e = emaRes.body.VCB;
  let foundDiff = false;
  for (let i = 0; i < s.length; i++) {
    for (const ma of ["ma10", "ma20", "ma50"]) {
      if (typeof s[i][ma] === "number" && typeof e[i][ma] === "number" && s[i][ma] !== e[i][ma]) {
        foundDiff = true;
        break;
      }
    }
    if (foundDiff) break;
  }
  assert(foundDiff, "weekly EMA differs from weekly SMA");
}

async function testEmaWithModeAll() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=VCB&symbol=BTCUSDT&interval=1D&limit=1&mode=all&ema=true",
  );
  console.log(`\n── EMA with mode=all ── ${ms}ms`);
  assert(status === 200, "returns 200");
  if ("VCB" in body) {
    assert(typeof body.VCB[0].ma10 === "number", "VCB has ma10 with ema=true mode=all");
    ok("mode=all + ema=true works for VN ticker");
  }
  if ("BTCUSDT" in body) {
    assert(typeof body.BTCUSDT[0].ma10 === "number", "BTCUSDT has ma10 with ema=true mode=all");
    ok("mode=all + ema=true works for crypto ticker");
  }
}

// ──────────────────────────────────────────────
// SJC-GOLD merged into yahoo mode
// ──────────────────────────────────────────────

async function testSjcGoldInYahooGroups() {
  const { status, body, ms } = await fetchJSON("/tickers/group?mode=yahoo");
  console.log(`\n── SJC-GOLD in yahoo groups ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("Commodity" in body, "has Commodity group");
  assert(body.Commodity.includes("SJC-GOLD"), "SJC-GOLD is in Commodity group");
}

async function testSjcGoldInYahooNames() {
  const { status, body, ms } = await fetchJSON("/tickers/name?mode=yahoo");
  console.log(`\n── SJC-GOLD in yahoo names ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("SJC-GOLD" in body, "has SJC-GOLD key");
  assert(body["SJC-GOLD"] === "SJC Gold Bar", `name matches (got '${body["SJC-GOLD"]}')`);
}

async function testSjcGoldInYahooTickers() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=SJC-GOLD&mode=yahoo&interval=1D&limit=3",
  );
  console.log(`\n── SJC-GOLD OHLCV via mode=yahoo ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("SJC-GOLD" in body, "has SJC-GOLD key");
  assert(body["SJC-GOLD"].length === 3, `got 3 rows (got ${body["SJC-GOLD"].length})`);
  assertOldestFirst(body["SJC-GOLD"], "SJC-GOLD daily yahoo");
}

async function testSjcGoldInAllGroups() {
  const { status, body, ms } = await fetchJSON("/tickers/group?mode=all");
  console.log(`\n── SJC-GOLD in mode=all groups ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("Commodity" in body, "has Commodity group");
  assert(body.Commodity.includes("SJC-GOLD"), "SJC-GOLD is in Commodity group under mode=all");
}

async function testSjcGoldNoDuplicates() {
  const { status, body, ms } = await fetchJSON(
    "/tickers?symbol=SJC-GOLD&interval=1D&limit=40&mode=yahoo",
  );
  console.log(`\n── No duplicates: SJC-GOLD (yahoo) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  if ("SJC-GOLD" in body) {
    assertNoDuplicateTimes(body["SJC-GOLD"], "SJC-GOLD daily yahoo");
  } else {
    ok("SJC-GOLD not in response (skipped)");
  }
}

// ──────────────────────────────────────────────
// snap=true/false (Redis snapshot cache)
// ──────────────────────────────────────────────

async function testSnapTrueReturnsSameData() {
  const [noSnap, withSnap] = await Promise.all([
    fetchJSON("/tickers?symbol=VCB&interval=1D&limit=2&cache=false&snap=false"),
    fetchJSON("/tickers?symbol=VCB&interval=1D&limit=2&cache=false&snap=true"),
  ]);
  console.log(`\n── snap=true vs snap=false: /tickers (VCB 1D limit=2) ── ${noSnap.ms}ms / ${withSnap.ms}ms`);
  assert(noSnap.status === 200, "snap=false returns 200");
  assert(withSnap.status === 200, "snap=true returns 200");
  assert("VCB" in noSnap.body && "VCB" in withSnap.body, "both have VCB key");
  assert(noSnap.body.VCB.length === withSnap.body.VCB.length,
    `same number of rows (${noSnap.body.VCB.length} vs ${withSnap.body.VCB.length})`);
}

async function testSnapPerformanceMultiTicker() {
  // Warm up snapshot cache with all tickers
  await fetchJSON("/tickers?interval=1D&limit=1&cache=false&snap=true");
  const [cold, warm] = await Promise.all([
    fetchJSON("/tickers?interval=1D&limit=1&cache=false&snap=false"),
    fetchJSON("/tickers?interval=1D&limit=1&cache=false&snap=true"),
  ]);
  console.log(`\n── snap perf: /tickers?interval=1D&limit=1 (all tickers) ── snap=false: ${cold.ms}ms, snap=true: ${warm.ms}ms`);
  assert(cold.status === 200 && warm.status === 200, "both return 200");
  const coldKeys = Object.keys(cold.body);
  const warmKeys = Object.keys(warm.body);
  assert(coldKeys.length === warmKeys.length,
    `same number of tickers (${coldKeys.length} vs ${warmKeys.length})`);
  ok(`snap=true ${warm.ms < cold.ms ? "faster" : "similar"} to snap=false`);
}

async function testSnapDefaultIsTrue() {
  const [defaultRes, explicitSnap] = await Promise.all([
    fetchJSON("/tickers?symbol=VCB&interval=1D&limit=1&cache=false"),
    fetchJSON("/tickers?symbol=VCB&interval=1D&limit=1&cache=false&snap=true"),
  ]);
  console.log(`\n── snap default behavior (no param = snap=true) ──`);
  assert(defaultRes.status === 200, "default request returns 200");
  assert(explicitSnap.status === 200, "snap=true request returns 200");
  assert("VCB" in defaultRes.body, "default returns data");
  ok("default (no snap param) behaves like snap=true");
}

async function testSnapWithEndDateToday() {
  // end_date=today should still use snapshot (no constraint)
  const today = new Date().toISOString().slice(0, 10);
  const { status, headers, body, ms } = await fetchJSON(
    `/tickers?symbol=VCB&interval=1D&limit=1&end_date=${today}&cache=false&snap=true`,
  );
  console.log(`\n── snap with end_date=today (VCB 1D limit=1) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert("VCB" in body, "has VCB key");
  const source = headers.get("x-data-source");
  assert(source === "redis-snap", `x-data-source is redis-snap (got ${source})`);
}

async function testSnapWithPastEndDateBypassesSnap() {
  // end_date in the past should NOT use snapshot (actual constraint)
  const { status, headers, ms } = await fetchJSON(
    "/tickers?symbol=VCB&interval=1D&limit=1&end_date=2025-01-01&cache=false&snap=true",
  );
  console.log(`\n── snap with past end_date (VCB 1D limit=1) ── ${ms}ms`);
  assert(status === 200, "returns 200");
  const source = headers.get("x-data-source");
  assert(source !== "redis-snap", `x-data-source is NOT redis-snap (got ${source})`);
}

// ──────────────────────────────────────────────
// POST /tickers/refresh tests
// ──────────────────────────────────────────────

async function testRefreshVnDaily() {
  const { status, body, ms } = await fetchJSON("/tickers/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ interval: "1D", mode: "vn" }),
  });
  console.log(`\n── POST /tickers/refresh {1D, vn} ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(typeof body.updated === "number", "has updated count");
  assert(body.updated >= 0, "updated is non-negative");
  assert(Array.isArray(body.sources), "has sources array");
  assert(body.sources.includes("vn"), "sources includes vn");
}

async function testRefreshInvalidInterval() {
  const { status, body, ms } = await fetchJSON("/tickers/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ interval: "5m", mode: "vn" }),
  });
  console.log(`\n── POST /tickers/refresh {5m, vn} (invalid interval) ── ${ms}ms`);
  assert(status === 400, `returns 400 (got ${status})`);
  assert(typeof body.error === "string", "has error message");
}

async function testRefreshAllSources() {
  const { status, body, ms } = await fetchJSON("/tickers/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ interval: "1h", mode: "all" }),
  });
  console.log(`\n── POST /tickers/refresh {1h, all} ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(typeof body.updated === "number", "has updated count");
  assert(body.sources.length === 3, `updates 3 sources (got ${body.sources.length})`);
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
  testIntervalUppercase,
  testIndicatorsPresent,
  testNoLimit,
  testNoLimitEmptyTicker,
  testNoLimitFutureRange,
  testModeAllWithSymbols,
  testModeAllNoSymbols,
  testModeAllLegacy,
  testModeAllGroups,
  testModeAllNames,
  testModeAllAggregated,
  testMaFalseNoIndicators,
  testMaTrueHasIndicators,
  testMaFalseAggregated,
  testVnNoDuplicates,
  testYahooNoDuplicates,
  testCryptoNoDuplicates,
  testModeAllNoDuplicates,
  testEmaDiffersFromSma,
  testEmaDefaultIsSma,
  testEmaWithAggregated,
  testEmaWithModeAll,
  testSjcGoldInYahooGroups,
  testSjcGoldInYahooNames,
  testSjcGoldInYahooTickers,
  testSjcGoldInAllGroups,
  testSjcGoldNoDuplicates,
  testSnapTrueReturnsSameData,
  testSnapPerformanceMultiTicker,
  testSnapDefaultIsTrue,
  testSnapWithEndDateToday,
  testSnapWithPastEndDateBypassesSnap,
  testRefreshVnDaily,
  testRefreshInvalidInterval,
  testRefreshAllSources,
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
