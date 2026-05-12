#!/usr/bin/env node
/**
 * Generate HTTP traffic to produce OpenTelemetry traces in OpenObserve.
 *
 * Usage:
 *   node scripts/traces-generate.mjs                          # default: http://localhost:3000
 *   node scripts/traces-generate.mjs http://localhost:3000 5   # custom base URL, 5 rounds
 *
 * Sends a mix of real endpoints, 404s, and simulated hack probes
 * so you can verify traces in OpenObserve with varied status codes and client IPs.
 */

const BASE_URL = process.argv[2] || "http://localhost:3000";
const ROUNDS = parseInt(process.argv[3] || "2", 10);

const requests = [
  { path: "/health", label: "health check" },
  { path: "/tickers?source=vn&interval=1D&limit=5", label: "tickers VN daily" },
  { path: "/tickers?source=crypto&interval=1h&limit=3", label: "tickers crypto hourly" },
  { path: "/tickers/group", label: "tickers group" },
  { path: "/tickers/name", label: "tickers name" },
  { path: "/tickers/info", label: "tickers info" },
  { path: "/analysis/top-performers?source=vn&interval=1D", label: "top performers" },
  { path: "/analysis/ma-scores-by-sector?source=vn", label: "MA scores by sector" },
  { path: "/analysis/volume-profile?ticker=VCB&source=vn&interval=1D", label: "volume profile" },
  { path: "/explorer", label: "explorer" },
];

const probes404 = [
  "/wp-admin",
  "/.env",
  "/admin/login",
  "/api/v1/users",
  "/favicon.ico",
  "/nonexistent",
  "/robots.txt",
];

const clientIPs = [
  "203.0.113.42",
  "198.51.100.7",
  "192.0.2.99",
  "10.0.0.1",
];

let totalSent = 0;

async function sendGet(path, { label, ip } = {}) {
  const headers = {};
  if (ip) headers["X-Forwarded-For"] = ip;

  try {
    const resp = await fetch(`${BASE_URL}${path}`, { headers });
    const status = resp.status;
    // drain body
    await resp.text();
    totalSent++;
    console.log(`  [${status}] ${label || path}${ip ? ` (ip=${ip})` : ""}`);
  } catch (err) {
    totalSent++;
    console.log(`  [ERR] ${label || path}: ${err.message}`);
  }
}

async function main() {
  console.log(`Generating trace traffic: ${ROUNDS} round(s) against ${BASE_URL}\n`);

  for (let round = 1; round <= ROUNDS; round++) {
    console.log(`--- Round ${round}/${ROUNDS} ---`);

    // Real endpoints
    for (const req of requests) {
      const ip = clientIPs[Math.floor(Math.random() * clientIPs.length)];
      await sendGet(req.path, { label: req.label, ip });
    }

    // 404 probes
    for (const path of probes404) {
      const ip = clientIPs[Math.floor(Math.random() * clientIPs.length)];
      await sendGet(path, { label: `404 probe: ${path}`, ip });
    }

    console.log("");
  }

  console.log(`Done. Total requests sent: ${totalSent}`);
  console.log(`\nCheck traces with:`);
  console.log(`  node scripts/traces-query.mjs`);
}

main();
