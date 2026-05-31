#!/usr/bin/env node

import { execSync, spawnSync } from "child_process";

const AIPA = process.env.AIPA || "uv run aipa-cli";
const AIPA_ARGS = AIPA.split(" ");
const GREEN = "\x1b[32m";
const RED = "\x1b[31m";
const RESET = "\x1b[0m";

const results = [];
let pass = 0;
let badCount = 0;

function ok(label) {
  results.push(`${GREEN}  PASS${RESET}  ${label}`);
  pass++;
}

function bad(label) {
  results.push(`${RED}  FAIL${RESET}  ${label}`);
  badCount++;
}

function run(...args) {
  try {
    const out = execSync(`${AIPA} ${args.join(" ")}`, {
      encoding: "utf-8",
      timeout: 60_000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return { exit: 0, out };
  } catch (e) {
    return { exit: e.status ?? 1, out: e.stdout ?? "" };
  }
}

function runArgs(...args) {
  const result = spawnSync(AIPA_ARGS[0], [...AIPA_ARGS.slice(1), ...args], {
    encoding: "utf-8",
    timeout: 60_000,
    stdio: ["pipe", "pipe", "pipe"],
  });
  return { exit: result.status ?? 1, out: result.stdout ?? "" };
}

function lines(out) {
  return out.trim().split("\n").filter(Boolean).length;
}

function contains(out, needle) {
  return out.includes(needle);
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

function monthAgo() {
  const d = new Date();
  d.setDate(d.getDate() - 30);
  return d.toISOString().slice(0, 10);
}

console.log("=========================================");
console.log(" AIPriceAction CLI E2E Tests");
console.log(` CLI: ${AIPA}`);
console.log(` Date: ${new Date().toISOString().replace("T", " ").slice(0, 19)}`);
console.log("=========================================\n");

// ===========================
// TCX edge cases (listed 2025-10-21)
// Tests: yearly-only data, pre-IPO dates, multiple intervals
// ===========================

// TCX 1D default — should return all available rows without hanging
{
  const r = run("get-ohlcv-data", "TCX", "--limit", "200", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("TCX 1D limit=200 (exit=0)") : bad(`TCX 1D limit=200 (exit=${r.exit})`);
  lines(r.out) >= 10 ? ok(`TCX 1D returns >=10 rows (${lines(r.out)})`) : bad(`TCX 1D returns >=10 rows (${lines(r.out)})`);
  lines(r.out) <= 200 ? ok(`TCX 1D capped at available data (<=200)`) : bad(`TCX 1D capped at available data (got ${lines(r.out)})`);
}

// TCX 1D with pre-IPO start date — should not hang, returns only post-IPO data
{
  const r = run("get-ohlcv-data", "TCX", "--start-date", "2025-01-01", "--end-date", "2025-12-31", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("TCX 1D pre-IPO start 2025-01-01 (exit=0)") : bad(`TCX 1D pre-IPO start (exit=${r.exit})`);
  lines(r.out) >= 1 ? ok(`TCX 1D pre-IPO returns >=1 row (${lines(r.out)})`) : bad(`TCX 1D pre-IPO returns >=1 row (${lines(r.out)})`);
  // All rows should be on or after 2025-10-21 (IPO date)
  const dataLines = r.out.trim().split("\n").filter(l => l.trim() && !l.startsWith(" "));
  const earliestDate = dataLines.length > 0 ? dataLines[0].split(/\s+/)[0] : "";
  earliestDate >= "2025-10-21"
    ? ok(`TCX 1D earliest row is on/after IPO (${earliestDate})`)
    : bad(`TCX 1D earliest row before IPO (${earliestDate})`);
}

// TCX 1D with far pre-IPO start — should not hang with hundreds of pre-IPO dates
{
  const r = run("get-ohlcv-data", "TCX", "--start-date", "2024-01-01", "--end-date", "2025-12-31", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("TCX 1D far pre-IPO start 2024-01-01 (exit=0)") : bad(`TCX 1D far pre-IPO (exit=${r.exit})`);
  lines(r.out) >= 1 ? ok(`TCX 1D far pre-IPO returns >=1 row (${lines(r.out)})`) : bad(`TCX 1D far pre-IPO returns >=1 row (${lines(r.out)})`);
}

// TCX 1h — intraday data, should handle gracefully
{
  const r = run("get-ohlcv-data", "TCX", "--interval", "1h", "--limit", "50", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("TCX 1h limit=50 (exit=0)") : bad(`TCX 1h limit=50 (exit=${r.exit})`);
  lines(r.out) >= 5 ? ok(`TCX 1h returns >=5 rows (${lines(r.out)})`) : bad(`TCX 1h returns >=5 rows (${lines(r.out)})`);
}

// TCX 1h with pre-IPO start — should not hang
{
  const r = run("get-ohlcv-data", "TCX", "--interval", "1h", "--start-date", "2025-06-01", "--end-date", "2025-12-31", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("TCX 1h pre-IPO start (exit=0)") : bad(`TCX 1h pre-IPO start (exit=${r.exit})`);
}

// TCX 15m — fine-grained intraday, should not hang
{
  const r = run("get-ohlcv-data", "TCX", "--interval", "15m", "--limit", "20", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("TCX 15m limit=20 (exit=0)") : bad(`TCX 15m limit=20 (exit=${r.exit})`);
  lines(r.out) >= 5 ? ok(`TCX 15m returns >=5 rows (${lines(r.out)})`) : bad(`TCX 15m returns >=5 rows (${lines(r.out)})`);
}

// TCX 1m — finest granularity, may have limited history
{
  const r = run("get-ohlcv-data", "TCX", "--interval", "1m", "--limit", "20", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("TCX 1m limit=20 (exit=0)") : bad(`TCX 1m limit=20 (exit=${r.exit})`);
}

// TCX 1W — aggregated weekly, should return from IPO onwards
{
  const r = run("get-ohlcv-data", "TCX", "--interval", "1W", "--limit", "10", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("TCX 1W limit=10 (exit=0)") : bad(`TCX 1W limit=10 (exit=${r.exit})`);
  lines(r.out) >= 3 ? ok(`TCX 1W returns >=3 rows (${lines(r.out)})`) : bad(`TCX 1W returns >=3 rows (${lines(r.out)})`);
}

// TCX with MA — should not crash on insufficient MA buffer data
{
  const r = run("get-ohlcv-data", "TCX", "--limit", "50", "--no-system-prompt");
  r.exit === 0 ? ok("TCX 1D with MA (exit=0)") : bad(`TCX 1D with MA (exit=${r.exit})`);
  contains(r.out, "ma10") ? ok("TCX MA includes ma10") : bad("TCX MA includes ma10");
}

// ===========================
// get-ohlcv-data
// ===========================

// Single ticker default
{
  const r = run("get-ohlcv-data", "VCB", "--limit", "20", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("get-ohlcv-data VCB limit=20 (exit=0)") : bad(`get-ohlcv-data VCB limit=20 (exit=${r.exit})`);
  lines(r.out) >= 10 ? ok(`get-ohlcv-data VCB returns >=10 rows (${lines(r.out)})`) : bad(`get-ohlcv-data VCB returns >=10 rows (${lines(r.out)})`);
}

// Large limit — yearly path
{
  const r = run("get-ohlcv-data", "VCB", "--limit", "200", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("get-ohlcv-data VCB limit=200 (exit=0)") : bad(`get-ohlcv-data VCB limit=200 (exit=${r.exit})`);
  lines(r.out) >= 100 ? ok(`get-ohlcv-data VCB limit=200 returns >=100 rows (${lines(r.out)})`) : bad(`get-ohlcv-data VCB limit=200 returns >=100 rows (${lines(r.out)})`);
}

// Multi-ticker
{
  const r = run("get-ohlcv-data", "VCB", "TCB", "MBB", "--limit", "10", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("get-ohlcv-data multi-ticker VCB TCB MBB (exit=0)") : bad(`get-ohlcv-data multi-ticker VCB TCB MBB (exit=${r.exit})`);
  const outLines = r.out.trim().split("\n").filter(Boolean);
  const symbols = new Set(outLines.slice(1).map(l => l.trim().split(/\s+/).pop()));
  symbols.has("VCB") && symbols.has("TCB") && symbols.has("MBB")
    ? ok("get-ohlcv-data multi-ticker has all 3 symbols")
    : bad(`get-ohlcv-data multi-ticker missing symbols: ${[...symbols]}`);
}

// TCX regression: yearly-only ticker should not return 1 row
{
  const r = run("get-ohlcv-data", "TCX", "--limit", "200", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("get-ohlcv-data TCX yearly-only regression (exit=0)") : bad(`get-ohlcv-data TCX yearly-only regression (exit=${r.exit})`);
  lines(r.out) >= 10 ? ok(`get-ohlcv-data TCX returns >=10 rows (${lines(r.out)})`) : bad(`get-ohlcv-data TCX returns >=10 rows (${lines(r.out)})`);
}

// Explicit date range
{
  const r = run("get-ohlcv-data", "VCB", "--limit", "10", "--no-system-prompt", "--no-ma", "--start-date", "2025-04-28", "--end-date", "2025-04-29");
  r.exit === 0 ? ok("get-ohlcv-data VCB explicit date range (exit=0)") : bad(`get-ohlcv-data VCB explicit date range (exit=${r.exit})`);
  lines(r.out) >= 2 ? ok(`get-ohlcv-data VCB date range returns rows (${lines(r.out)})`) : bad(`get-ohlcv-data VCB date range returns rows (${lines(r.out)})`);
}

// MA indicators present
{
  const r = run("get-ohlcv-data", "VCB", "--limit", "10", "--no-system-prompt");
  r.exit === 0 ? ok("get-ohlcv-data VCB with MA (exit=0)") : bad(`get-ohlcv-data VCB with MA (exit=${r.exit})`);
  contains(r.out, "ma10") ? ok("get-ohlcv-data VCB MA includes ma10 column") : bad("get-ohlcv-data VCB MA includes ma10 column");
  contains(r.out, "ma200") ? ok("get-ohlcv-data VCB MA includes ma200 column") : bad("get-ohlcv-data VCB MA includes ma200 column");
  contains(r.out, "close_changed") ? ok("get-ohlcv-data VCB MA includes close_changed") : bad("get-ohlcv-data VCB MA includes close_changed");
}

// EMA instead of SMA
{
  const r = run("get-ohlcv-data", "VCB", "--limit", "10", "--no-system-prompt", "--ema", "--no-ma");
  r.exit === 0 ? ok("get-ohlcv-data VCB --ema (exit=0)") : bad(`get-ohlcv-data VCB --ema (exit=${r.exit})`);
  lines(r.out) >= 5 ? ok(`get-ohlcv-data VCB --ema returns rows (${lines(r.out)})`) : bad(`get-ohlcv-data VCB --ema returns rows (${lines(r.out)})`);
}

// Crypto 1h interval
{
  const r = run("get-ohlcv-data", "BTCUSDT", "--source", "crypto", "--interval", "1h", "--limit", "10", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("get-ohlcv-data BTCUSDT 1h interval (exit=0)") : bad(`get-ohlcv-data BTCUSDT 1h interval (exit=${r.exit})`);
  lines(r.out) >= 5 ? ok(`get-ohlcv-data BTCUSDT 1h returns >=5 rows (${lines(r.out)})`) : bad(`get-ohlcv-data BTCUSDT 1h returns >=5 rows (${lines(r.out)})`);
}

// Crypto 4h interval
{
  const r = run("get-ohlcv-data", "ETHUSDT", "--source", "crypto", "--interval", "4h", "--limit", "10", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("get-ohlcv-data ETHUSDT 4h interval (exit=0)") : bad(`get-ohlcv-data ETHUSDT 4h interval (exit=${r.exit})`);
  lines(r.out) >= 5 ? ok(`get-ohlcv-data ETHUSDT 4h returns >=5 rows (${lines(r.out)})`) : bad(`get-ohlcv-data ETHUSDT 4h returns >=5 rows (${lines(r.out)})`);
}

// Crypto weekly interval
{
  const r = run("get-ohlcv-data", "BTCUSDT", "--source", "crypto", "--interval", "1W", "--limit", "5", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("get-ohlcv-data BTCUSDT 1W interval (exit=0)") : bad(`get-ohlcv-data BTCUSDT 1W interval (exit=${r.exit})`);
  lines(r.out) >= 3 ? ok(`get-ohlcv-data BTCUSDT 1W returns >=3 rows (${lines(r.out)})`) : bad(`get-ohlcv-data BTCUSDT 1W returns >=3 rows (${lines(r.out)})`);
}

// SJC gold
{
  const r = run("get-ohlcv-data", "SJC-GOLD", "--source", "sjc", "--limit", "5", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("get-ohlcv-data SJC-GOLD (exit=0)") : bad(`get-ohlcv-data SJC-GOLD (exit=${r.exit})`);
  lines(r.out) >= 3 ? ok(`get-ohlcv-data SJC-GOLD returns >=3 rows (${lines(r.out)})`) : bad(`get-ohlcv-data SJC-GOLD returns >=3 rows (${lines(r.out)})`);
}

// VN weekly interval (aggregated)
{
  const r = run("get-ohlcv-data", "FPT", "--interval", "1W", "--limit", "5", "--no-system-prompt", "--no-ma");
  r.exit === 0 ? ok("get-ohlcv-data FPT 1W interval (exit=0)") : bad(`get-ohlcv-data FPT 1W interval (exit=${r.exit})`);
  lines(r.out) >= 3 ? ok(`get-ohlcv-data FPT 1W returns >=3 rows (${lines(r.out)})`) : bad(`get-ohlcv-data FPT 1W returns >=3 rows (${lines(r.out)})`);
}

// ===========================
// live-data
// ===========================

{
  const r = run("live-data", "--top", "5", "--source", "vn");
  r.exit === 0 ? ok("live-data top 5 vn (exit=0)") : bad(`live-data top 5 vn (exit=${r.exit})`);
  lines(r.out) >= 2 ? ok(`live-data returns >=2 rows (${lines(r.out)})`) : bad(`live-data returns >=2 rows (${lines(r.out)})`);
}

{
  const r = run("live-data", "VCB", "TCB");
  r.exit === 0 ? ok("live-data VCB TCB (exit=0)") : bad(`live-data VCB TCB (exit=${r.exit})`);
  lines(r.out) >= 2 ? ok(`live-data VCB TCB returns >=2 rows (${lines(r.out)})`) : bad(`live-data VCB TCB returns >=2 rows (${lines(r.out)})`);
}

{
  const r = run("live-data", "--source", "crypto", "--top", "3");
  r.exit === 0 ? ok("live-data crypto top 3 (exit=0)") : bad(`live-data crypto top 3 (exit=${r.exit})`);
  lines(r.out) >= 2 ? ok(`live-data crypto returns >=2 rows (${lines(r.out)})`) : bad(`live-data crypto returns >=2 rows (${lines(r.out)})`);
}

{
  const r = run("live-data", "--source", "sjc", "--top", "1");
  r.exit === 0 ? ok("live-data sjc (exit=0)") : bad(`live-data sjc (exit=${r.exit})`);
  lines(r.out) >= 1 ? ok(`live-data sjc returns rows (${lines(r.out)})`) : bad(`live-data sjc returns rows (${lines(r.out)})`);
}

// ===========================
// performers
// ===========================

{
  const r = run("performers", "--limit", "5");
  r.exit === 0 ? ok("performers top 5 (exit=0)") : bad(`performers top 5 (exit=${r.exit})`);
  lines(r.out) >= 2 ? ok(`performers returns >=2 rows (${lines(r.out)})`) : bad(`performers returns >=2 rows (${lines(r.out)})`);
}

{
  const r = run("performers", "--sort-by", "value", "--limit", "5");
  r.exit === 0 ? ok("performers sorted by value (exit=0)") : bad(`performers sorted by value (exit=${r.exit})`);
  lines(r.out) >= 2 ? ok(`performers value returns >=2 rows (${lines(r.out)})`) : bad(`performers value returns >=2 rows (${lines(r.out)})`);
}

{
  const r = run("performers", "--sort-by", "ma50_score", "--limit", "5");
  r.exit === 0 ? ok("performers sorted by ma50_score (exit=0)") : bad(`performers sorted by ma50_score (exit=${r.exit})`);
  lines(r.out) >= 2 ? ok(`performers ma50_score returns >=2 rows (${lines(r.out)})`) : bad(`performers ma50_score returns >=2 rows (${lines(r.out)})`);
}

{
  const r = run("performers", "--direction", "asc", "--limit", "5");
  r.exit === 0 ? ok("performers ascending (exit=0)") : bad(`performers ascending (exit=${r.exit})`);
  lines(r.out) >= 2 ? ok(`performers asc returns >=2 rows (${lines(r.out)})`) : bad(`performers asc returns >=2 rows (${lines(r.out)})`);
}

{
  const r = run("performers", "--group", "NGAN_HANG", "--limit", "5");
  r.exit === 0 ? ok("performers banking group (exit=0)") : bad(`performers banking group (exit=${r.exit})`);
  lines(r.out) >= 1 ? ok(`performers banking returns >=1 row (${lines(r.out)})`) : bad(`performers banking returns >=1 row (${lines(r.out)})`);
}

{
  const r = run("performers", "--group", "CHUNG_KHOAN", "--limit", "5");
  r.exit === 0 ? ok("performers securities group (exit=0)") : bad(`performers securities group (exit=${r.exit})`);
  lines(r.out) >= 1 ? ok(`performers securities returns >=1 row (${lines(r.out)})`) : bad(`performers securities returns >=1 row (${lines(r.out)})`);
}

{
  const r = run("performers", "--source", "crypto", "--limit", "5", "--sort-by", "value");
  r.exit === 0 ? ok("performers crypto by value (exit=0)") : bad(`performers crypto by value (exit=${r.exit})`);
  lines(r.out) >= 2 ? ok(`performers crypto value returns >=2 rows (${lines(r.out)})`) : bad(`performers crypto value returns >=2 rows (${lines(r.out)})`);
}

// ===========================
// ticker-list
// ===========================

{
  const r = run("ticker-list", "--source", "vn", "--group", "NGAN_HANG");
  r.exit === 0 ? ok("ticker-list vn banking (exit=0)") : bad(`ticker-list vn banking (exit=${r.exit})`);
  contains(r.out, "VCB") ? ok("ticker-list includes VCB") : bad("ticker-list includes VCB");
  contains(r.out, "TCB") ? ok("ticker-list includes TCB") : bad("ticker-list includes TCB");
}

{
  const r = run("ticker-list", "--source", "crypto", "--compact");
  r.exit === 0 ? ok("ticker-list crypto compact (exit=0)") : bad(`ticker-list crypto compact (exit=${r.exit})`);
  contains(r.out, "BTCUSDT") ? ok("ticker-list crypto includes BTCUSDT") : bad("ticker-list crypto includes BTCUSDT");
  contains(r.out, "ETHUSDT") ? ok("ticker-list crypto includes ETHUSDT") : bad("ticker-list crypto includes ETHUSDT");
}

{
  const r = run("ticker-list", "--source", "vn", "--group", "BAT_DONG_SAN");
  r.exit === 0 ? ok("ticker-list vn real estate (exit=0)") : bad(`ticker-list vn real estate (exit=${r.exit})`);
  contains(r.out, "VIC") ? ok("ticker-list real estate includes VIC") : bad("ticker-list real estate includes VIC");
}

{
  const r = run("ticker-list", "--source", "vn");
  r.exit === 0 ? ok("ticker-list all vn (exit=0)") : bad(`ticker-list all vn (exit=${r.exit})`);
  lines(r.out) >= 100 ? ok(`ticker-list all vn returns >=100 tickers (${lines(r.out)})`) : bad(`ticker-list all vn returns >=100 tickers (${lines(r.out)})`);
}

// ===========================
// volume-profile
// ===========================

{
  const r = run("volume-profile", "VCB", "--start-date", monthAgo(), "--end-date", today());
  r.exit === 0 ? ok("volume-profile VCB multi-day (exit=0)") : bad(`volume-profile VCB multi-day (exit=${r.exit})`);
  lines(r.out) >= 3 ? ok(`volume-profile returns >=3 lines (${lines(r.out)})`) : bad(`volume-profile returns >=3 lines (${lines(r.out)})`);
}

{
  const r = run("volume-profile", "BTCUSDT", "--source", "crypto", "--start-date", monthAgo(), "--end-date", today(), "--bins", "20");
  r.exit === 0 ? ok("volume-profile BTCUSDT crypto (exit=0)") : bad(`volume-profile BTCUSDT crypto (exit=${r.exit})`);
  lines(r.out) >= 3 ? ok(`volume-profile crypto returns >=3 lines (${lines(r.out)})`) : bad(`volume-profile crypto returns >=3 lines (${lines(r.out)})`);
}

{
  const r = run("volume-profile", "FPT", "--start-date", monthAgo(), "--end-date", today(), "--bins", "10", "--value-area-pct", "80");
  r.exit === 0 ? ok("volume-profile FPT with custom bins/VA (exit=0)") : bad(`volume-profile FPT custom params (exit=${r.exit})`);
  lines(r.out) >= 3 ? ok(`volume-profile FPT returns >=3 lines (${lines(r.out)})`) : bad(`volume-profile FPT returns >=3 lines (${lines(r.out)})`);
}

// ===========================
// watchlist
// ===========================

{
  const r = run("watchlist", "ls");
  r.exit === 0 ? ok("watchlist ls (exit=0)") : bad(`watchlist ls (exit=${r.exit})`);
  lines(r.out) >= 2 ? ok(`watchlist ls returns >=2 lines (${lines(r.out)})`) : bad(`watchlist ls returns >=2 lines (${lines(r.out)})`);
}

{
  const r = run("watchlist", "get", "VN30");
  r.exit === 0 ? ok("watchlist get VN30 (exit=0)") : bad(`watchlist get VN30 (exit=${r.exit})`);
  contains(r.out, "VCB") ? ok("watchlist VN30 includes VCB") : bad("watchlist VN30 includes VCB");
  contains(r.out, "FPT") ? ok("watchlist VN30 includes FPT") : bad("watchlist VN30 includes FPT");
  contains(r.out, "HPG") ? ok("watchlist VN30 includes HPG") : bad("watchlist VN30 includes HPG");
}

{
  const r = run("watchlist", "get", "VINGROUP");
  r.exit === 0 ? ok("watchlist get VINGROUP (exit=0)") : bad(`watchlist get VINGROUP (exit=${r.exit})`);
  contains(r.out, "VIC") ? ok("watchlist VINGROUP includes VIC") : bad("watchlist VINGROUP includes VIC");
  contains(r.out, "VHM") ? ok("watchlist VINGROUP includes VHM") : bad("watchlist VINGROUP includes VHM");
}

{
  const r = run("watchlist", "get", "INDEX");
  r.exit === 0 ? ok("watchlist get INDEX (exit=0)") : bad(`watchlist get INDEX (exit=${r.exit})`);
  contains(r.out, "VNINDEX") ? ok("watchlist INDEX includes VNINDEX") : bad("watchlist INDEX includes VNINDEX");
}

// ===========================
// analyze context-only
// ===========================

{
  const r = run("analyze", "VCB", "--limit", "20", "--context-only", "--no-system-prompt");
  r.exit === 0 ? ok("analyze VCB context-only (exit=0)") : bad(`analyze VCB context-only (exit=${r.exit})`);
  lines(r.out) >= 10 ? ok(`analyze VCB context returns >=10 lines (${lines(r.out)})`) : bad(`analyze VCB context returns >=10 lines (${lines(r.out)})`);
  contains(r.out, "time") ? ok("analyze VCB context has OHLCV data") : bad("analyze VCB context has OHLCV data");
  contains(r.out, "VNINDEX") ? ok("analyze VCB context has reference VNINDEX") : bad("analyze VCB context has reference VNINDEX");
}

{
  const r = run("analyze", "VCB", "TCB", "MBB", "--limit", "10", "--context-only", "--no-system-prompt");
  r.exit === 0 ? ok("analyze multi-ticker 3 symbols (exit=0)") : bad(`analyze multi-ticker 3 symbols (exit=${r.exit})`);
  lines(r.out) >= 10 ? ok(`analyze 3-ticker context returns >=10 lines (${lines(r.out)})`) : bad(`analyze 3-ticker context returns >=10 lines (${lines(r.out)})`);
  contains(r.out, "VCB") ? ok("analyze multi-ticker context has VCB") : bad("analyze multi-ticker context has VCB");
  contains(r.out, "TCB") ? ok("analyze multi-ticker context has TCB") : bad("analyze multi-ticker context has TCB");
  contains(r.out, "MBB") ? ok("analyze multi-ticker context has MBB") : bad("analyze multi-ticker context has MBB");
}

{
  const r = run("analyze", "BTCUSDT", "--source", "crypto", "--limit", "10", "--context-only", "--no-system-prompt");
  r.exit === 0 ? ok("analyze BTCUSDT crypto (exit=0)") : bad(`analyze BTCUSDT crypto (exit=${r.exit})`);
  lines(r.out) >= 5 ? ok(`analyze BTCUSDT context returns >=5 lines (${lines(r.out)})`) : bad(`analyze BTCUSDT context returns >=5 lines (${lines(r.out)})`);
  contains(r.out, "BTCUSDT") ? ok("analyze BTCUSDT context has ticker") : bad("analyze BTCUSDT context has ticker");
}

{
  const r = run("analyze", "TCX", "--limit", "50", "--context-only", "--no-system-prompt");
  r.exit === 0 ? ok("analyze TCX yearly-only regression (exit=0)") : bad(`analyze TCX yearly-only regression (exit=${r.exit})`);
  lines(r.out) >= 10 ? ok(`analyze TCX context returns >=10 lines (${lines(r.out)})`) : bad(`analyze TCX context returns >=10 lines (${lines(r.out)})`);
}

{
  const r = run("analyze", "HPG", "--limit", "20", "--interval", "1W", "--context-only", "--no-system-prompt");
  r.exit === 0 ? ok("analyze HPG 1W interval (exit=0)") : bad(`analyze HPG 1W interval (exit=${r.exit})`);
  lines(r.out) >= 5 ? ok(`analyze HPG 1W context returns >=5 lines (${lines(r.out)})`) : bad(`analyze HPG 1W context returns >=5 lines (${lines(r.out)})`);
}

// ===========================
// deep-research snapshot
// ===========================

{
  const r = run("deep-research");
  r.exit === 0 ? ok("deep-research snapshot (exit=0)") : bad(`deep-research snapshot (exit=${r.exit})`);
  lines(r.out) >= 10 ? ok(`deep-research snapshot returns >=10 lines (${lines(r.out)})`) : bad(`deep-research snapshot returns >=10 lines (${lines(r.out)})`);
}

{
  const r = run("deep-research", "--source", "crypto");
  r.exit === 0 ? ok("deep-research crypto snapshot (exit=0)") : bad(`deep-research crypto snapshot (exit=${r.exit})`);
  lines(r.out) >= 5 ? ok(`deep-research crypto snapshot returns >=5 lines (${lines(r.out)})`) : bad(`deep-research crypto snapshot returns >=5 lines (${lines(r.out)})`);
}

// ===========================
// fundamentals
// ===========================

// fundamentals info
{
  const r = run("fundamentals", "info", "ACB");
  r.exit === 0 ? ok("fundamentals info ACB (exit=0)") : bad(`fundamentals info ACB (exit=${r.exit})`);
  contains(r.out, "Ngân hàng") ? ok("fundamentals info ACB has industry") : bad("fundamentals info ACB has industry");
  contains(r.out, "Shareholders") ? ok("fundamentals info ACB has shareholders") : bad("fundamentals info ACB has shareholders");
  contains(r.out, "Officers") ? ok("fundamentals info ACB has officers") : bad("fundamentals info ACB has officers");
}

// fundamentals info — no-data ticker
{
  const r = run("fundamentals", "info", "VNINDEX");
  r.exit !== 0 ? ok("fundamentals info VNINDEX exits non-zero") : bad(`fundamentals info VNINDEX should fail (exit=${r.exit})`);
}

// fundamentals ratios --latest
{
  const r = run("fundamentals", "ratios", "VCB", "--latest");
  r.exit === 0 ? ok("fundamentals ratios VCB --latest (exit=0)") : bad(`fundamentals ratios VCB --latest (exit=${r.exit})`);
  contains(r.out, "Valuation") ? ok("fundamentals ratios has Valuation section") : bad("fundamentals ratios has Valuation section");
  contains(r.out, "PE") ? ok("fundamentals ratios has PE field") : bad("fundamentals ratios has PE field");
  contains(r.out, "period=") ? ok("fundamentals ratios --latest shows period=") : bad("fundamentals ratios --latest shows period=");
}

// fundamentals ratios --yearly (new flag)
{
  const r = run("fundamentals", "ratios", "VCB", "--yearly");
  r.exit === 0 ? ok("fundamentals ratios VCB --yearly (exit=0)") : bad(`fundamentals ratios VCB --yearly (exit=${r.exit})`);
  contains(r.out, "period=") ? ok("fundamentals ratios --yearly shows period=") : bad("fundamentals ratios --yearly shows period=");
}

// fundamentals ratios — default shows all periods (quarterly + yearly)
{
  const r = run("fundamentals", "ratios", "VCB");
  r.exit === 0 ? ok("fundamentals ratios VCB default (exit=0)") : bad(`fundamentals ratios VCB default (exit=${r.exit})`);
  const q1 = /Q1/g;
  const matches = (r.out.match(q1) || []).length;
  matches >= 1 ? ok(`fundamentals ratios default includes quarterly data (${matches} Q1 entries)`) : bad(`fundamentals ratios default includes quarterly data (${matches} Q1 entries)`);
}

// fundamentals ratios --category bank
{
  const r = run("fundamentals", "ratios", "VCB", "--latest", "--category", "bank");
  r.exit === 0 ? ok("fundamentals ratios VCB --category bank (exit=0)") : bad(`fundamentals ratios VCB --category bank (exit=${r.exit})`);
  contains(r.out, "NPL") ? ok("fundamentals ratios bank has NPL") : bad("fundamentals ratios bank has NPL");
  contains(r.out, "CAR") ? ok("fundamentals ratios bank has CAR") : bad("fundamentals ratios bank has CAR");
  !contains(r.out, "Valuation") ? ok("fundamentals ratios bank omits Valuation") : bad("fundamentals ratios bank omits Valuation");
}

// fundamentals ratios --year
{
  const r = run("fundamentals", "ratios", "VCB", "--year", "2024");
  r.exit === 0 ? ok("fundamentals ratios VCB --year 2024 (exit=0)") : bad(`fundamentals ratios VCB --year 2024 (exit=${r.exit})`);
  contains(r.out, "2024") ? ok("fundamentals ratios shows year 2024") : bad("fundamentals ratios shows year 2024");
}

// fundamentals ratios --json
{
  const r = run("fundamentals", "ratios", "VCB", "--latest", "--json");
  r.exit === 0 ? ok("fundamentals ratios VCB --json (exit=0)") : bad(`fundamentals ratios VCB --json (exit=${r.exit})`);
  try {
    const parsed = JSON.parse(r.out);
    parsed.ticker === "VCB" ? ok("fundamentals ratios --json parses with ticker=VCB") : bad("fundamentals ratios --json ticker mismatch");
    Array.isArray(parsed.ratios) ? ok("fundamentals ratios --json has ratios array") : bad("fundamentals ratios --json has ratios array");
  } catch {
    bad("fundamentals ratios --json is valid JSON");
  }
}

// fundamentals rank
{
  const r = run("fundamentals", "rank", "VCB", "BID", "CTG", "TCB", "MBB", "--sort-by", "roe", "--limit", "3");
  r.exit === 0 ? ok("fundamentals rank 5 banks by roe (exit=0)") : bad(`fundamentals rank 5 banks by roe (exit=${r.exit})`);
  lines(r.out) >= 4 ? ok(`fundamentals rank returns >=4 lines (${lines(r.out)})`) : bad(`fundamentals rank returns >=4 lines (${lines(r.out)})`);
  contains(r.out, "roe") ? ok("fundamentals rank has roe column") : bad("fundamentals rank has roe column");
  contains(r.out, "period=") ? ok("fundamentals rank shows period=") : bad("fundamentals rank shows period=");
}

// fundamentals rank --year
{
  const r = run("fundamentals", "rank", "VCB", "BID", "CTG", "--year", "2023", "--sort-by", "roe", "--limit", "3");
  r.exit === 0 ? ok("fundamentals rank --year 2023 (exit=0)") : bad(`fundamentals rank --year 2023 (exit=${r.exit})`);
  contains(r.out, "period=2023") ? ok("fundamentals rank --year shows period=2023") : bad("fundamentals rank --year shows period=2023");
}

// fundamentals screen --year
{
  const r = runArgs("fundamentals", "screen", "--industry", "ngân hàng", "--year", "2024", "--sort-by", "roe", "--limit", "3");
  r.exit === 0 ? ok("fundamentals screen --year 2024 (exit=0)") : bad(`fundamentals screen --year 2024 (exit=${r.exit})`);
  contains(r.out, "period=2024") ? ok("fundamentals screen --year shows period=2024") : bad("fundamentals screen --year shows period=2024");
}

// fundamentals rank --watchlist
{
  const r = run("fundamentals", "rank", "--watchlist", "VN30", "--sort-by", "pe", "--direction", "asc", "--limit", "5");
  r.exit === 0 ? ok("fundamentals rank VN30 by pe asc (exit=0)") : bad(`fundamentals rank VN30 by pe asc (exit=${r.exit})`);
  lines(r.out) >= 3 ? ok(`fundamentals rank VN30 returns >=3 lines (${lines(r.out)})`) : bad(`fundamentals rank VN30 returns >=3 lines (${lines(r.out)})`);
}

// fundamentals screen — value stocks
{
  const r = run("fundamentals", "screen", "--pe-max", "10", "--roe-min", "0.15", "--sort-by", "roe", "--limit", "5");
  r.exit === 0 ? ok("fundamentals screen value stocks (exit=0)") : bad(`fundamentals screen value stocks (exit=${r.exit})`);
  lines(r.out) >= 3 ? ok(`fundamentals screen returns >=3 lines (${lines(r.out)})`) : bad(`fundamentals screen returns >=3 lines (${lines(r.out)})`);
}

// fundamentals screen — industry filter (spawnSync for Unicode args)
{
  const r = runArgs("fundamentals", "screen", "--industry", "ngân hàng", "--roe-min", "0.15", "--sort-by", "roe", "--limit", "5");
  r.exit === 0 ? ok("fundamentals screen banking industry (exit=0)") : bad(`fundamentals screen banking industry (exit=${r.exit})`);
  contains(r.out, "Ngân hàng") ? ok("fundamentals screen has banking entries") : bad("fundamentals screen has banking entries");
  contains(r.out, "period=") ? ok("fundamentals screen shows period=") : bad("fundamentals screen shows period=");
}

// fundamentals screen — with watchlist
{
  const r = run("fundamentals", "screen", "--watchlist", "VN30", "--pe-max", "20", "--roe-min", "0.10", "--sort-by", "roe", "--limit", "5");
  r.exit === 0 ? ok("fundamentals screen VN30 (exit=0)") : bad(`fundamentals screen VN30 (exit=${r.exit})`);
}

// fundamentals no subcommand — usage
{
  const r = run("fundamentals");
  r.exit !== 0 ? ok("fundamentals (no subcommand) exits non-zero") : bad(`fundamentals (no subcommand) should fail (exit=${r.exit})`);
}

// ===========================
// --version
// ===========================

{
  const r = run("--version");
  r.exit === 0 ? ok("aipa --version (exit=0)") : bad(`aipa --version (exit=${r.exit})`);
  /^\d+\.\d+\.\d+$/.test(r.out.trim()) ? ok("aipa --version is semver") : bad(`aipa --version is semver (got: ${r.out.trim().slice(0, 30)})`);
}

// ===========================
// Results
// ===========================

console.log("");
console.log("=========================================");
for (const r of results) {
  console.log(r);
}
console.log("=========================================");
const total = pass + badCount;
console.log(`  Total: ${total}  ${pass} passed  ${badCount} failed`);
console.log("=========================================");

process.exit(badCount === 0 ? 0 : 1);
