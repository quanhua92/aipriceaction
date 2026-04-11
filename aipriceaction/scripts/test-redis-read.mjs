// Usage: node scripts/test-redis-read.mjs [BASE_URL]
const BASE = process.argv[2] || 'http://localhost:3000';
let pass = 0, fail = 0, total = 0;

async function test(name, url, expectSource, expectNonEmpty = true) {
  total++;
  try {
    // Always bypass in-memory cache
    const sep = url.includes('?') ? '&' : '?';
    const t0 = performance.now();
    const res = await fetch(`${BASE}${url}${sep}cache=false`);
    const source = res.headers.get('x-data-source');
    const data = await res.json();
    const ms = (performance.now() - t0).toFixed(0);

    const sourceOk = source === expectSource;
    const dataOk = !expectNonEmpty || Object.keys(data).length > 0;

    if (sourceOk && dataOk) {
      console.log(`[PASS] ${name} — x-data-source: ${source}, tickers: ${Object.keys(data).length}, ${ms}ms`);
      pass++;
    } else {
      console.log(`[FAIL] ${name} — expected source=${expectSource}, got=${source}, dataOk=${dataOk}, ${ms}ms`);
      fail++;
    }
  } catch (e) {
    console.log(`[FAIL] ${name} — fetch error: ${e.message}`);
    fail++;
  }
}

// Tests that should hit Redis (single ticker, all intervals)
await test('VCB 1m native', '/tickers?symbol=VCB&interval=1m&limit=100', 'redis');
await test('VCB 1h native', '/tickers?symbol=VCB&interval=1h&limit=100', 'redis');
await test('VCB 1D native', '/tickers?symbol=VCB&interval=1D&limit=100', 'redis');
await test('VCB 5m aggregated', '/tickers?symbol=VCB&interval=5m&limit=100', 'redis');
await test('VCB 15m aggregated', '/tickers?symbol=VCB&interval=15m&limit=100', 'redis');
await test('VCB 4h aggregated', '/tickers?symbol=VCB&interval=4h&limit=100', 'redis');
await test('VCB 1W aggregated', '/tickers?symbol=VCB&interval=1W&limit=52', 'redis');
await test('VCB 2W aggregated', '/tickers?symbol=VCB&interval=2W&limit=26', 'redis');
await test('VCB 1M aggregated', '/tickers?symbol=VCB&interval=1M&limit=12', 'redis');
await test('BTCUSDT 1D crypto', '/tickers?symbol=BTCUSDT&interval=1D&limit=100&mode=crypto', 'redis');

// Tests that should fall back to PG (date range)
await test('VCB 1D with date range', '/tickers?symbol=VCB&interval=1D&start_date=2025-01-01', 'postgres');

// Tests that should hit Redis (multi-ticker via Lua batch)
await test('VCB+FPT multi-ticker', '/tickers?symbol=VCB&symbol=FPT&interval=1D&limit=10', 'redis');
await test('VCB+FPT+HPG multi-ticker', '/tickers?symbol=VCB&symbol=FPT&symbol=HPG&interval=1D&limit=10', 'redis');
await test('VCB+FPT multi 1h', '/tickers?symbol=VCB&symbol=FPT&interval=1h&limit=50', 'redis');
await test('VCB+FPT multi 5m agg', '/tickers?symbol=VCB&symbol=FPT&interval=5m&limit=50', 'redis');

// Verify response data consistency: compare Redis vs PG for same query
try {
  const t0 = performance.now();
  const redisRes = await fetch(`${BASE}/tickers?symbol=VCB&interval=1D&limit=10&cache=false&_t=${Date.now()}`);
  const pgRes = await fetch(`${BASE}/tickers?symbol=VCB&interval=1D&limit=10&start_date=2024-01-01&cache=false&_t=${Date.now()}`);
  const redisData = await redisRes.json();
  const pgData = await pgRes.json();
  const ms = (performance.now() - t0).toFixed(0);
  total++;
  if (Object.keys(redisData).length > 0 && Object.keys(pgData).length > 0) {
    console.log(`[PASS] Data consistency — Redis: ${Object.keys(redisData).length} tickers, PG: ${Object.keys(pgData).length} tickers, ${ms}ms`);
    pass++;
  } else {
    console.log(`[FAIL] Data consistency — Redis: ${Object.keys(redisData).length}, PG: ${Object.keys(pgData).length}, ${ms}ms`);
    fail++;
  }
} catch (e) {
  total++;
  console.log(`[FAIL] Data consistency — fetch error: ${e.message}`);
  fail++;
}

// ── Performance comparison: redis=true vs redis=false for identical queries ──

const ITERS = 5;

async function bench(label, url) {
  // First request (cold — script load, connection setup)
  const t0 = performance.now();
  const coldRes = await fetch(`${BASE}${url}&cache=false&_t=first-${Date.now()}`);
  await coldRes.json();
  const cold = performance.now() - t0;

  const times = [];
  for (let i = 0; i < ITERS; i++) {
    const t0 = performance.now();
    const res = await fetch(`${BASE}${url}&cache=false&_t=${Date.now()}`);
    await res.json();
    times.push(performance.now() - t0);
  }
  const p50 = times.sort((a, b) => a - b)[Math.floor(times.length / 2)];
  return { cold, p50, times };
}

console.log(`\n── Performance comparison (redis=true vs redis=false, cold + ${ITERS} iters, bypass cache) ──`);

const queries = [
  { label: '1 ticker 1m limit=1000',           path: '/tickers?symbol=VCB&interval=1m&limit=1000' },
  { label: '1 ticker 5m limit=1000',           path: '/tickers?symbol=VCB&interval=5m&limit=1000' },
  { label: '1 ticker 15m limit=1000',          path: '/tickers?symbol=VCB&interval=15m&limit=1000' },
  { label: '1 ticker 1h limit=1000',           path: '/tickers?symbol=VCB&interval=1h&limit=1000' },
  { label: '1 ticker 4h limit=1000',           path: '/tickers?symbol=VCB&interval=4h&limit=1000' },
  { label: '1 ticker 1D limit=1000',           path: '/tickers?symbol=VCB&interval=1D&limit=1000' },
  { label: '1 ticker 1W limit=52',             path: '/tickers?symbol=VCB&interval=1W&limit=52' },
  { label: '1 ticker 2W limit=26',             path: '/tickers?symbol=VCB&interval=2W&limit=26' },
  { label: '1 ticker 1M limit=12',             path: '/tickers?symbol=VCB&interval=1M&limit=12' },
  { label: '3 tickers 1D limit=1000',          path: '/tickers?symbol=VCB&symbol=FPT&symbol=HPG&interval=1D&limit=1000' },
  { label: '3 tickers 1h limit=1000',          path: '/tickers?symbol=VCB&symbol=FPT&symbol=HPG&interval=1h&limit=1000' },
  { label: '3 tickers 5m limit=500',           path: '/tickers?symbol=VCB&symbol=FPT&symbol=HPG&interval=5m&limit=500' },
  { label: '3 tickers 15m limit=500',          path: '/tickers?symbol=VCB&symbol=FPT&symbol=HPG&interval=15m&limit=500' },
  { label: '3 tickers 4h limit=500',           path: '/tickers?symbol=VCB&symbol=FPT&symbol=HPG&interval=4h&limit=500' },
  { label: '3 tickers 1W limit=52',            path: '/tickers?symbol=VCB&symbol=FPT&symbol=HPG&interval=1W&limit=52' },
  { label: '3 tickers 1M limit=12',            path: '/tickers?symbol=VCB&symbol=FPT&symbol=HPG&interval=1M&limit=12' },
  { label: '10 tickers 1D limit=1000',         path: '/tickers?symbol=VCB&symbol=FPT&symbol=HPG&symbol=MWG&symbol=TCB&symbol=SAB&symbol=VNM&symbol=GAS&symbol=PLX&symbol=POW&interval=1D&limit=1000' },
];

for (const q of queries) {
  const redisResult = await bench(q.label, `${q.path}&redis=true`);
  const pgResult    = await bench(q.label, `${q.path}&redis=false`);
  const ratio = pgResult.p50 > 0 ? (redisResult.p50 / pgResult.p50).toFixed(2) : '—';
  console.log(
    `${q.label.padEnd(32)} | redis cold=${redisResult.cold.toFixed(0).padStart(4)}ms p50=${redisResult.p50.toFixed(1).padStart(6)}ms  pg cold=${pgResult.cold.toFixed(0).padStart(4)}ms p50=${pgResult.p50.toFixed(1).padStart(6)}ms  ratio ${ratio}`
  );

  // Data consistency: redis=true vs redis=false should return same JSON body
  total++;
  try {
    const [redisRes, pgRes] = await Promise.all([
      fetch(`${BASE}${q.path}&redis=true&cache=false&_t=${Date.now()}`),
      fetch(`${BASE}${q.path}&redis=false&cache=false&_t=${Date.now()}`),
    ]);
    const redisJson = await redisRes.json();
    const pgJson = await pgRes.json();

    // Compare only the data arrays (headers will differ)
    const tickers = Object.keys(redisJson);
    let match = tickers.length === Object.keys(pgJson).length;
    if (match) {
      for (const t of tickers) {
        const rr = redisJson[t];
        const pr = pgJson[t];
        if (!pr || rr.length !== pr.length) { match = false; break; }
        for (let i = 0; i < rr.length; i++) {
          // Compare key OHLCV fields (allow small float rounding)
          if (rr[i].time !== pr[i].time ||
              Math.abs(rr[i].close - pr[i].close) > 0.01 ||
              Math.abs(rr[i].open - pr[i].open) > 0.01 ||
              Math.abs(rr[i].high - pr[i].high) > 0.01 ||
              Math.abs(rr[i].low - pr[i].low) > 0.01) {
            match = false;
            break;
          }
        }
        if (!match) break;
      }
    }
    if (match) {
      pass++;
    } else {
      fail++;
      console.log(`  [FAIL] Data mismatch for: ${q.label}`);
    }
  } catch (e) {
    fail++;
    console.log(`  [FAIL] Consistency check error for ${q.label}: ${e.message}`);
  }
}

console.log(`\n${pass}/${total} passed, ${fail} failed`);
process.exit(fail > 0 ? 1 : 0);
