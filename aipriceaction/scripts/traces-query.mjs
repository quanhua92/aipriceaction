#!/usr/bin/env node
/**
 * Query OpenObserve for OpenTelemetry traces from aipriceaction.
 *
 * Usage:
 *   node scripts/traces-query.mjs                          # recent traces
 *   node scripts/traces-query.mjs --summary                # summary counts
 *   node scripts/traces-query.mjs --errors                 # 5xx server errors only
 *   node scripts/traces-query.mjs --404                    # 404 not found (hack probes)
 *   node scripts/traces-query.mjs --5xx                    # 5xx server errors only
 *   node scripts/traces-query.mjs --ip                     # traces with client IP
 *   node scripts/traces-query.mjs --uri /tickers           # filter by URI pattern
 *   node scripts/traces-query.mjs --method POST            # filter by HTTP method
 *   node scripts/traces-query.mjs --minutes 60             # last 60 minutes (default: 30)
 *
 * Environment variables (or reads from .env):
 *   OO_URL       OpenObserve URL (default: http://localhost:5080)
 *   OO_USER      OpenObserve username (default: root@example.com)
 *   OO_PASS      OpenObserve password (default: Complexpass#123)
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// --- Config ---
const args = process.argv.slice(2);
const minutes = parseInt(argValue("--minutes") || "30", 10);
const mode = args.includes("--summary") ? "summary"
  : args.includes("--errors") || args.includes("--5xx") ? "errors"
  : args.includes("--404") ? "404"
  : args.includes("--ip") ? "ip"
  : "recent";
const uriFilter = argValue("--uri");
const methodFilter = argValue("--method");

function argValue(flag) {
  const idx = args.indexOf(flag);
  return idx >= 0 && args[idx + 1] ? args[idx + 1] : null;
}

// Load .env
let OO_URL = process.env.OO_URL;
let OO_USER = process.env.OO_USER;
let OO_PASS = process.env.OO_PASS;

if (!OO_URL || !OO_USER || !OO_PASS) {
  try {
    const envPath = resolve(import.meta.dirname, "..", ".env");
    const envContent = readFileSync(envPath, "utf-8");
    for (const line of envContent.split("\n")) {
      const m = line.match(/^((?:ZO_ROOT_USER|OO_)...)=(.+)$/);
      if (m) {
        const [, key, val] = m;
        if (key === "ZO_ROOT_USER_EMAIL" && !OO_USER) OO_USER = val;
        else if (key === "ZO_ROOT_USER_PASSWORD" && !OO_PASS) OO_PASS = val;
        else if (key === "OO_URL" && !OO_URL) OO_URL = val;
        else if (key === "OO_USER" && !OO_USER) OO_USER = val;
        else if (key === "OO_PASS" && !OO_PASS) OO_PASS = val;
      }
    }
  } catch {}
}

OO_URL = OO_URL || "http://localhost:5080";
OO_USER = OO_USER || "root@example.com";
OO_PASS = OO_PASS || "Complexpass#123";

const CREDS = Buffer.from(`${OO_USER}:${OO_PASS}`).toString("base64");

// --- Time range (microseconds) ---
const nowUs = BigInt(Date.now()) * 1000n;
const startUs = nowUs - BigInt(minutes) * 60n * 1_000_000n;

// --- Query builder ---
function buildQuery(mode) {
  const conditions = [`operation_name='http_request'`];
  let select = "trace_id, span_id, method, uri, client_ip, http_status, latency_ms, duration, start_time";

  if (mode === "errors") {
    conditions.push("(http_status >= 500 OR span_status='ERROR')");
  } else if (mode === "404") {
    conditions.push("http_status = 404");
  } else if (mode === "ip") {
    conditions.push("client_ip != '-'");
  }

  if (uriFilter) {
    conditions.push(`uri LIKE '%${uriFilter}%'`);
  }
  if (methodFilter) {
    conditions.push(`method = '${methodFilter.toUpperCase()}'`);
  }

  const where = conditions.join(" AND ");
  const limit = mode === "summary" ? 10000 : 50;

  if (mode === "summary") {
    return `SELECT method, uri, http_status, count(*) as cnt, avg(CAST(latency_ms as double)) as avg_latency_ms, max(CAST(latency_ms as double)) as max_latency_ms FROM default WHERE ${where} GROUP BY method, uri, http_status ORDER BY cnt DESC LIMIT 100`;
  }

  return `SELECT ${select} FROM default WHERE ${where} ORDER BY start_time DESC LIMIT ${limit}`;
}

async function search(sql) {
  const body = JSON.stringify({
    query: {
      sql,
      start_time: Number(startUs),
      end_time: Number(nowUs),
    },
    sql_mode: "full",
  });

  const resp = await fetch(`${OO_URL}/api/default/_search?type=traces`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Basic ${CREDS}`,
    },
    body,
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`OpenObserve ${resp.status}: ${text}`);
  }

  return resp.json();
}

// --- Renderers ---
function renderRecent(hits) {
  if (!hits.length) { console.log("  No traces found."); return; }
  console.log(`  ${"Method".padEnd(7)} ${"Status".padEnd(7)} ${"Latency".padEnd(10)} ${"IP".padEnd(16)} URI`);
  console.log(`  ${"-".repeat(7)} ${"-".repeat(7)} ${"-".repeat(10)} ${"-".repeat(16)} ${"-".repeat(40)}`);
  for (const h of hits) {
    const method = (h.method || "").padEnd(7);
    const status = String(h.http_status ?? "?").padEnd(7);
    const latency = `${h.latency_ms ?? "?"}ms`.padEnd(10);
    const ip = (h.client_ip || "-").padEnd(16);
    const uri = (h.uri || "").substring(0, 60);
    console.log(`  ${method} ${status} ${latency} ${ip} ${uri}`);
  }
  console.log(`\n  Showing ${hits.length} most recent traces (last ${minutes} min)`);
}

function renderSummary(hits) {
  if (!hits.length) { console.log("  No traces found."); return; }
  console.log(`  ${"Method".padEnd(7)} ${"Status".padEnd(7)} ${"Count".padEnd(8)} ${"Avg ms".padEnd(10)} ${"Max ms".padEnd(10)} URI`);
  console.log(`  ${"-".repeat(7)} ${"-".repeat(7)} ${"-".repeat(8)} ${"-".repeat(10)} ${"-".repeat(10)} ${"-".repeat(40)}`);
  for (const h of hits) {
    const method = (h.method || "").padEnd(7);
    const status = String(h.http_status ?? "?").padEnd(7);
    const cnt = String(h.cnt).padEnd(8);
    const avg = String(Math.round(Number(h.avg_latency_ms || 0))).padEnd(10);
    const max = String(Math.round(Number(h.max_latency_ms || 0))).padEnd(10);
    const uri = (h.uri || "").substring(0, 50);
    console.log(`  ${method} ${status} ${cnt} ${avg} ${max} ${uri}`);
  }
  console.log(`\n  Total: ${hits.length} unique (method, uri, status) groups`);
}

// --- Main ---
async function main() {
  const sql = buildQuery(mode);
  console.log(`\nOpenTelemetry Traces (${mode}) — last ${minutes} min\n`);
  console.log(`  Query: ${sql.substring(0, 120)}...\n`);

  try {
    const result = await search(sql);
    const hits = result.hits || [];

    switch (mode) {
      case "summary": renderSummary(hits); break;
      case "errors": renderRecent(hits); break;
      case "404": renderRecent(hits); break;
      case "ip": renderRecent(hits); break;
      default: renderRecent(hits); break;
    }

    // Show trace count from stream stats
    if (mode === "recent") {
      console.log(`  Total hits in time range: ${hits.length}`);
    }
  } catch (err) {
    console.error(`  Error: ${err.message}`);
    process.exit(1);
  }
}

main();
