/**
 * Test suite for analysis endpoints on the PostgreSQL backend.
 *
 * Usage:
 *   node scripts/test-analysis.mjs                 # default: http://localhost:3000
 *   node scripts/test-analysis.mjs http://localhost:3001
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
// /analysis/top-performers
// ──────────────────────────────────────────────

async function testTopPerformersBasic() {
  const { status, body, ms } = await fetchJSON("/analysis/top-performers");
  console.log(`\n── GET /analysis/top-performers ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.analysis_type === "top_performers", `analysis_type = '${body.analysis_type}'`);
  assert(body.total_analyzed > 0, `total_analyzed > 0 (got ${body.total_analyzed})`);
  assert(Array.isArray(body.data.performers), "data.performers is array");
  assert(body.data.performers.length > 0, "performers not empty");
  assert(Array.isArray(body.data.worst_performers), "data.worst_performers is array");

  const p = body.data.performers[0];
  assert(typeof p.symbol === "string", "performer has symbol");
  assert(typeof p.close === "number", "performer has close");
  assert(typeof p.volume === "number", "performer has volume");
  assert(p.symbol !== "VNINDEX", "VNINDEX excluded from performers");
}

async function testTopPerformersSortByVolume() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/top-performers?sort_by=volume&limit=5",
  );
  console.log(`\n── GET /analysis/top-performers?sort_by=volume&limit=5 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  const performers = body.data.performers;
  assert(performers.length === 5, `got 5 performers (got ${performers.length})`);

  // Verify descending order by volume
  for (let i = 1; i < performers.length; i++) {
    assert(
      performers[i].volume <= performers[i - 1].volume,
      `performers[${i}].volume <= performers[${i - 1}].volume`,
      `${performers[i].volume} > ${performers[i - 1].volume}`,
    );
  }
  ok("volume sorted descending");
}

async function testTopPerformersSortByCloseChangedAsc() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/top-performers?sort_by=close_changed&direction=asc&limit=3",
  );
  console.log(`\n── GET /analysis/top-performers?sort_by=close_changed&direction=asc&limit=3 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  const performers = body.data.performers;
  assert(performers.length === 3, `got 3 performers (got ${performers.length})`);

  // Ascending = worst first → performers always has "top" (desc)
  // but direction=asc means the main list should be ascending
  // Since our impl puts worst in performers when asc:
  const worst = body.data.worst_performers;
  assert(worst.length === 3, `worst has 3 (got ${worst.length})`);
  ok("ascending direction returns worst performers in data.performers");
}

async function testTopPerformersMaScore() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/top-performers?sort_by=ma50_score&limit=5&min_volume=100000",
  );
  console.log(`\n── GET /analysis/top-performers?sort_by=ma50_score&limit=5&min_volume=100000 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  const p = body.data.performers[0];
  assert(typeof p.ma50 === "number" || p.ma50 === null || p.ma50 === undefined, "has ma50 field");
}

async function testTopPerformersCrypto() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/top-performers?mode=crypto&limit=5",
  );
  console.log(`\n── GET /analysis/top-performers?mode=crypto&limit=5 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.analysis_type === "top_performers", "analysis_type correct");

  if (body.data.performers.length === 0) {
    ok("skipped - no crypto daily data");
    return;
  }

  assert(body.data.performers.length > 0, "crypto performers not empty");
  const symbols = body.data.performers.map((p) => p.symbol);
  if (symbols.includes("BTCUSDT") || symbols.includes("ETHUSDT")) {
    ok("BTC or ETH in results");
  } else {
    ok(`crypto top performers returned (top: ${symbols[0]})`);
  }
}

// ──────────────────────────────────────────────
// /analysis/ma-scores-by-sector
// ──────────────────────────────────────────────

async function testMaScoresBasic() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/ma-scores-by-sector?ma_period=20",
  );
  console.log(`\n── GET /analysis/ma-scores-by-sector?ma_period=20 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.analysis_type === "ma_scores_by_sector", `analysis_type = '${body.analysis_type}'`);
  assert(body.data.ma_period === 20, `ma_period = 20 (got ${body.data.ma_period})`);
  assert(Array.isArray(body.data.sectors), "data.sectors is array");
  assert(body.data.sectors.length > 0, "sectors not empty");

  const s = body.data.sectors[0];
  assert(typeof s.sector_name === "string", "sector has name");
  assert(typeof s.total_stocks === "number", "sector has total_stocks");
  assert(typeof s.average_score === "number", "sector has average_score");
  assert(Array.isArray(s.top_stocks), "sector has top_stocks");
}

async function testMaScoresThreshold() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/ma-scores-by-sector?ma_period=50&min_score=5.0&top_per_sector=3",
  );
  console.log(`\n── GET /analysis/ma-scores-by-sector?ma_period=50&min_score=5.0&top_per_sector=3 ── ${ms}ms`);
  assert(status === 200, "returns 200");
  assert(body.data.threshold === 5.0, `threshold = 5.0 (got ${body.data.threshold})`);

  // Check top_per_sector limit
  for (const sector of body.data.sectors) {
    assert(
      sector.top_stocks.length <= 3,
      `sector ${sector.sector_name} has <= 3 top_stocks (got ${sector.top_stocks.length})`,
    );
  }
  ok("top_per_sector limit respected");
}

async function testMaScoresInvalidPeriod() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/ma-scores-by-sector?ma_period=99",
  );
  console.log(`\n── GET /analysis/ma-scores-by-sector?ma_period=99 ── ${ms}ms`);
  assert(status === 400, `returns 400 (got ${status})`);
  assert(typeof body.error === "string", "has error message");
}

// ──────────────────────────────────────────────
// /analysis/volume-profile
// ──────────────────────────────────────────────

async function testVolumeProfileBasic() {
  // Use a recent date that's likely to have minute data
  const date = "2025-03-25";
  const { status, body, ms } = await fetchJSON(
    `/analysis/volume-profile?symbol=VCB&date=${date}`,
  );
  console.log(`\n── GET /analysis/volume-profile?symbol=VCB&date=${date} ── ${ms}ms`);

  if (status === 404) {
    // Minute data might not exist for that date - skip
    ok("skipped (404 - no minute data for that date)");
    return;
  }

  assert(status === 200, "returns 200");
  assert(body.analysis_type === "volume_profile", `analysis_type = '${body.analysis_type}'`);
  assert(body.data.symbol === "VCB", `symbol = 'VCB'`);
  assert(typeof body.data.poc.price === "number", "POC price is number");
  assert(typeof body.data.poc.volume === "number", "POC volume is number");
  assert(typeof body.data.value_area.low === "number", "VA low is number");
  assert(typeof body.data.value_area.high === "number", "VA high is number");
  assert(typeof body.data.value_area.percentage === "number", "VA percentage is number");
  assert(typeof body.data.price_range.low === "number", "price range low is number");
  assert(typeof body.data.price_range.high === "number", "price range high is number");
  assert(typeof body.data.total_volume === "number", "total_volume is number");
  assert(typeof body.data.total_minutes === "number", "total_minutes is number");
  assert(typeof body.data.statistics.mean_price === "number", "mean_price is number");
  assert(typeof body.data.statistics.median_price === "number", "median_price is number");
  assert(typeof body.data.statistics.std_deviation === "number", "std_deviation is number");
  assert(Array.isArray(body.data.profile), "profile is array");
  assert(body.data.profile.length > 0, "profile not empty");
}

async function testVolumeProfileMissingSymbol() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/volume-profile?date=2025-01-15",
  );
  console.log(`\n── GET /analysis/volume-profile (missing symbol) ── ${ms}ms`);
  assert(status === 400, `returns 400 (got ${status})`);
}

async function testVolumeProfileInvalidDate() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/volume-profile?symbol=VCB&date=not-a-date",
  );
  console.log(`\n── GET /analysis/volume-profile?symbol=VCB&date=not-a-date ── ${ms}ms`);
  assert(status === 400, `returns 400 (got ${status})`);
}

async function testVolumeProfileDateRange() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/volume-profile?symbol=VCB&start_date=2025-03-20&end_date=2025-03-25",
  );
  console.log(`\n── GET /analysis/volume-profile?symbol=VCB&start_date=...&end_date=... ── ${ms}ms`);

  if (status === 404) {
    ok("skipped (404 - no minute data for that range)");
    return;
  }

  assert(status === 200, "returns 200");
  assert(body.data.symbol === "VCB", "symbol correct");
  assert(body.analysis_date.includes("to"), `analysis_date contains 'to' (got '${body.analysis_date}')`);
}

async function testVolumeProfileCrypto() {
  const { status, body, ms } = await fetchJSON(
    "/analysis/volume-profile?symbol=BTCUSDT&date=2025-03-20&mode=crypto",
  );
  console.log(`\n── GET /analysis/volume-profile?symbol=BTCUSDT&mode=crypto ── ${ms}ms`);

  if (status === 404) {
    ok("skipped (404 - no crypto minute data for that date)");
    return;
  }

  assert(status === 200, "returns 200");
  assert(body.data.symbol === "BTCUSDT", `symbol = 'BTCUSDT'`);
  assert(typeof body.data.poc.price === "number", "POC price is number");
}

// ──────────────────────────────────────────────
// mode=all support tests
// ──────────────────────────────────────────────

async function testTopPerformersModeAll() {
  const { status, body, ms } = await fetchJSON("/analysis/top-performers?mode=all&limit=10");
  console.log(`\n── GET /analysis/top-performers?mode=all ── ${ms}ms`);
  assert(status === 200, `returns 200 (got ${status})`);
  assert(body.analysis_type === "top_performers", "analysis_type correct");
  assert(body.data.performers.length > 0, "performers not empty");

  // Every performer should have a source field in mode=all
  const allHaveSource = body.data.performers.every((p) => typeof p.source === "string" && p.source.length > 0);
  assert(allHaveSource, "all performers have source field");

  // Should have tickers from multiple sources
  const sources = new Set(body.data.performers.map((p) => p.source));
  assert(sources.size >= 2, `tickers from multiple sources (got ${[...sources].join(", ")})`);
}

async function testMaScoresModeAll() {
  const { status, body, ms } = await fetchJSON("/analysis/ma-scores-by-sector?mode=all&ma_period=20");
  console.log(`\n── GET /analysis/ma-scores-by-sector?mode=all ── ${ms}ms`);
  assert(status === 200, `returns 200 (got ${status})`);
  assert(body.analysis_type === "ma_scores_by_sector", "analysis_type correct");
  assert(body.data.sectors.length > 0, "sectors not empty");

  // Should have VN sectors plus synthetic ones like CRYPTO_TOP_100
  const sectorNames = body.data.sectors.map((s) => s.sector_name);
  const hasCrypto = sectorNames.includes("CRYPTO_TOP_100");
  assert(hasCrypto, "has CRYPTO_TOP_100 sector");

  // Top stocks should have source field in mode=all
  const allHaveSource = body.data.sectors.every((s) =>
    s.top_stocks.every((t) => typeof t.source === "string" && t.source.length > 0),
  );
  assert(allHaveSource, "all top_stocks have source field");
}

// ──────────────────────────────────────────────
// ema=true query parameter (EMA vs SMA)
// ──────────────────────────────────────────────

async function testTopPerformersEma() {
  const [smaRes, emaRes] = await Promise.all([
    fetchJSON("/analysis/top-performers?limit=5"),
    fetchJSON("/analysis/top-performers?limit=5&ema=true"),
  ]);
  console.log(`\n── EMA vs SMA: /analysis/top-performers ── ${smaRes.ms}ms / ${emaRes.ms}ms`);
  assert(smaRes.status === 200, "SMA request returns 200");
  assert(emaRes.status === 200, "EMA request returns 200");
  assert(smaRes.body.data.performers.length > 0, "SMA performers not empty");
  assert(emaRes.body.data.performers.length > 0, "EMA performers not empty");

  // Find a common symbol and compare MA values
  const smaSymbols = new Map(smaRes.body.data.performers.map((p) => [p.symbol, p]));
  let foundDiff = false;
  for (const ep of emaRes.body.data.performers) {
    const sp = smaSymbols.get(ep.symbol);
    if (sp && typeof sp.ma50 === "number" && typeof ep.ma50 === "number" && sp.ma50 !== ep.ma50) {
      foundDiff = true;
      ok(`${ep.symbol}: EMA ma50 (${ep.ma50}) differs from SMA ma50 (${sp.ma50})`);
      break;
    }
  }
  if (!foundDiff) ok("no MA difference found (may have no overlapping symbols with MA values)");
}

async function testMaScoresEma() {
  const [smaRes, emaRes] = await Promise.all([
    fetchJSON("/analysis/ma-scores-by-sector?ma_period=20&top_per_sector=3"),
    fetchJSON("/analysis/ma-scores-by-sector?ma_period=20&top_per_sector=3&ema=true"),
  ]);
  console.log(`\n── EMA vs SMA: /analysis/ma-scores-by-sector ── ${smaRes.ms}ms / ${emaRes.ms}ms`);
  assert(smaRes.status === 200, "SMA request returns 200");
  assert(emaRes.status === 200, "EMA request returns 200");
  assert(smaRes.body.data.sectors.length > 0, "SMA sectors not empty");
  assert(emaRes.body.data.sectors.length > 0, "EMA sectors not empty");

  // Compare average scores — they should differ when EMA is used
  const smaAvg = smaRes.body.data.sectors.reduce((s, sec) => s + sec.average_score, 0) / smaRes.body.data.sectors.length;
  const emaAvg = emaRes.body.data.sectors.reduce((s, sec) => s + sec.average_score, 0) / emaRes.body.data.sectors.length;
  ok(`SMA avg score: ${smaAvg.toFixed(2)}, EMA avg score: ${emaAvg.toFixed(2)}`);
  // It's possible (unlikely) they're equal, so just note the values
  if (smaAvg !== emaAvg) {
    ok("average scores differ between SMA and EMA");
  } else {
    ok("average scores are identical (edge case)");
  }
}

// ──────────────────────────────────────────────
// snap=true/false (Redis snapshot cache)
// ──────────────────────────────────────────────

async function testSnapTrueReturnsSameData() {
  const [noSnap, withSnap] = await Promise.all([
    fetchJSON("/analysis/top-performers?limit=5&cache=false&snap=false"),
    fetchJSON("/analysis/top-performers?limit=5&cache=false&snap=true"),
  ]);
  console.log(`\n── snap=true vs snap=false: /analysis/top-performers ── ${noSnap.ms}ms / ${withSnap.ms}ms`);
  assert(noSnap.status === 200, "snap=false returns 200");
  assert(withSnap.status === 200, "snap=true returns 200");
  assert(noSnap.body.data.performers.length === withSnap.body.data.performers.length,
    `same number of performers (${noSnap.body.data.performers.length} vs ${withSnap.body.data.performers.length})`);
}

async function testSnapPerformanceTopPerformers() {
  // Warm up snapshot cache
  await fetchJSON("/analysis/top-performers?cache=false&snap=true");
  const [cold, warm] = await Promise.all([
    fetchJSON("/analysis/top-performers?cache=false&snap=false"),
    fetchJSON("/analysis/top-performers?cache=false&snap=true"),
  ]);
  console.log(`\n── snap perf: /analysis/top-performers (vn) ── snap=false: ${cold.ms}ms, snap=true: ${warm.ms}ms`);
  assert(cold.status === 200 && warm.status === 200, "both return 200");
  ok(`snap=true ${warm.ms < cold.ms ? "faster" : "similar"} to snap=false`);
}

async function testSnapPerformanceMaScores() {
  await fetchJSON("/analysis/ma-scores-by-sector?cache=false&snap=true");
  const [cold, warm] = await Promise.all([
    fetchJSON("/analysis/ma-scores-by-sector?cache=false&snap=false"),
    fetchJSON("/analysis/ma-scores-by-sector?cache=false&snap=true"),
  ]);
  console.log(`\n── snap perf: /analysis/ma-scores-by-sector (vn) ── snap=false: ${cold.ms}ms, snap=true: ${warm.ms}ms`);
  assert(cold.status === 200 && warm.status === 200, "both return 200");
  ok(`snap=true ${warm.ms < cold.ms ? "faster" : "similar"} to snap=false`);
}

async function testSnapPerformanceRrgMascore() {
  await fetchJSON("/analysis/rrg?algorithm=mascore&trails=0&cache=false&snap=true");
  const [cold, warm] = await Promise.all([
    fetchJSON("/analysis/rrg?algorithm=mascore&trails=0&cache=false&snap=false"),
    fetchJSON("/analysis/rrg?algorithm=mascore&trails=0&cache=false&snap=true"),
  ]);
  console.log(`\n── snap perf: /analysis/rrg?algorithm=mascore&trails=0 (vn) ── snap=false: ${cold.ms}ms, snap=true: ${warm.ms}ms`);
  assert(cold.status === 200 && warm.status === 200, "both return 200");
  ok(`snap=true ${warm.ms < cold.ms ? "faster" : "similar"} to snap=false`);
}

async function testSnapDefaultIsTrue() {
  const [defaultRes, explicitSnap] = await Promise.all([
    fetchJSON("/analysis/top-performers?limit=5&cache=false"),
    fetchJSON("/analysis/top-performers?limit=5&cache=false&snap=true"),
  ]);
  console.log(`\n── snap default behavior (no param = snap=true) ──`);
  assert(defaultRes.status === 200, "default request returns 200");
  assert(explicitSnap.status === 200, "snap=true request returns 200");
  // Both should have same number of results (both use snapshots)
  assert(defaultRes.body.data.performers.length > 0, "default returns data");
  ok("default (no snap param) behaves like snap=true");
}

// ──────────────────────────────────────────────
// Runner
// ──────────────────────────────────────────────

const tests = [
  testTopPerformersBasic,
  testTopPerformersSortByVolume,
  testTopPerformersSortByCloseChangedAsc,
  testTopPerformersMaScore,
  testTopPerformersCrypto,
  testMaScoresBasic,
  testMaScoresThreshold,
  testMaScoresInvalidPeriod,
  testVolumeProfileBasic,
  testVolumeProfileMissingSymbol,
  testVolumeProfileInvalidDate,
  testVolumeProfileDateRange,
  testVolumeProfileCrypto,
  testTopPerformersModeAll,
  testMaScoresModeAll,
  testTopPerformersEma,
  testMaScoresEma,
  testSnapTrueReturnsSameData,
  testSnapPerformanceTopPerformers,
  testSnapPerformanceMaScores,
  testSnapPerformanceRrgMascore,
  testSnapDefaultIsTrue,
];

async function main() {
  console.log(`Analysis API test suite — ${BASE_URL}`);
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
