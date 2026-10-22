# Latest validation evidence

This page summarizes recorded local candidate checks. It is not production
telemetry; the production cutover remains pending in the last recorded baseline.

## Home-page request burst

The retained machine-readable report is
[`reports/homepage-19-2026-10-21.json`](reports/homepage-19-2026-10-21.json),
captured 2026-10-21 UTC against the local Vite proxy and the checkout at
`1438e04`. It covers local SQLite/archive data whose sampled latest candle date
was 2026-10-21; the report includes response hashes and per-request row counts.
This measured run used the 19 core requests concurrently and bypassed ticker
response, Redis, and snapshot caches (`cache=false&redis=false&snap=false`).
The archive object cache remained enabled.

Reproduce from a running local API and web proxy with:

```sh
cd aipriceaction-api
.venv/bin/python scripts/benchmark_homepage.py \
  --base-url http://127.0.0.1:5173/aipriceaction-api \
  --core-home-page-only --concurrency 19 --bypass-response-cache \
  --report /private/tmp/aipa-homepage-19.json
```

The temporary output path keeps new benchmark artifacts out of the live
`data/` directory. The retained report is a historical result, not a promise
that a repeat run will have identical latency, rows, or hashes.

| Measure | Result |
| --- | ---: |
| Requests | 19 |
| HTTP success | 19 / 19 |
| Total page-batch wall time | 550.51 ms |
| p50 request latency | 351.13 ms |
| p95 / maximum request latency | 517.23 ms |
| Requests over 1 second | 0 |
| Latest candle date in sampled ticker responses | 2026-10-21 |

This is a local measurement on the current data and hardware. It is not a
production SLA or proof that all symbols/intervals are fresh.

## Tests

- Latest focused post-push API set: 86 passed, 1 warning.
- Latest full API run: 1,738 passed, 3 skipped, 2 failures. Both failures were
  legacy assertions expecting SQLite `user_version=2` while current storage
  reports version 3; resolve or update those tests before claiming a fully green
  suite.
- Ruff and Compose configuration checks passed for the same code state.

Historical provider captures, repair workflows, and older benchmark results are
preserved in [`legacy/2026-10-22/VALIDATION.md`](legacy/2026-10-22/VALIDATION.md).
