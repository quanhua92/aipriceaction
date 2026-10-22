> **Archived Rust documentation (2026-10-22).** The source paths and runtime claims below describe the deprecated Rust implementation. For current Python API behavior, see [`aipriceaction-api/README.md`](../../../../aipriceaction-api/README.md) from the repository root.

# TODO — Bugs & Vulnerabilities

## Critical

- [ ] **SQL injection via string interpolation in `count_ohlcv()`**
  - File: `src/queries/ohlcv.rs:763-777`
  - Multiple branches build SQL with `format!()` instead of parameterized queries (`$1`, `$2`, ...).
  - Not exploitable today (all callers use internal/hardcoded values), but poor hygiene if an API endpoint is added later.
  - Fix: rewrite to use `sqlx::query_scalar().bind()` for all parameters.

- [ ] **SSRF via GitHub raw proxy — remove endpoint**
  - File: `src/server/legacy.rs`
  - User-supplied `path` concatenated with GitHub raw URL. Scoped to own repo so damage is bounded.
  - Fix: remove the legacy proxy module and its route entirely.

- [x] ~~No rate limiting on public API~~ (handled by nginx proxy)

## High

- [ ] **Production panics from `.unwrap()` / `.expect()` calls**
  - `src/providers/vci.rs:132` — `semaphore.acquire().await.expect("rate limiter closed")` panics worker
  - `src/providers/vci.rs:324` — `choose(&mut rand::thread_rng()).unwrap()` panics if `user_agents` is empty
  - `src/workers/redis_worker.rs:308` — `permit.acquire().await.unwrap()` panics if semaphore closed
  - `src/server/analysis/rrg.rs:639,664` — `.last().unwrap()` on empty Vec
  - `src/server/upload.rs:30-31` — `.parse().unwrap()` on header values
  - Fix: replace all with proper error handling (`match`, `ok()`, `?`, or early return).

- [x] ~~`std::sync::Mutex` in async context (deadlock risk)~~ — only locked in `Drop` impl (sync destructor), never in async paths. Safe as-is.

- [ ] **Intentional memory leak in Redis connection handle**
  - File: `src/redis.rs:72`
  - `std::mem::forget(handle)` prevents clean shutdown of the Redis reconnection task.
  - Fix: store the handle in a long-lived struct (e.g., `AppState`) instead of leaking it.

## Medium

- [ ] **Silent error swallowing**
  - `src/services/import.rs:43` — `.filter_map(|e| e.ok())` silently drops corrupted CSV rows
  - `src/workers/vci_shared.rs:159` — fire-and-forget `tokio::spawn` for Redis writes with no error handling
  - `src/redis.rs:64` — `let _ = health_client.init().await` ignores reconnection errors
  - Fix: log errors at minimum; propagate where possible.

- [ ] **Cache TTL too high — reduce from 10s to 3-5s**
  - File: `src/server/cache.rs`
  - In-memory cache (TTL 10s) only evicts by TTL; workers updating OHLCV data don't invalidate cached entries.
  - Stale financial data can be served for up to 10 seconds.
  - Fix: reduce TTL to 3-5s. Explicit invalidation would defeat the cache since workers write constantly.

- [x] ~~No concurrent upload limit (DoS vector)~~ — nginx already handles connection limits.

- [ ] **Sensitive error details exposed to clients**
  - File: `src/server/api/mod.rs:393-415`
  - Database errors (schema, table names, internal state) returned directly as `e.to_string()`.
  - Fix: return generic error messages to clients; log full details server-side.

- [ ] **Unbounded query results for single ticker — cap at 10k**
  - File: `src/server/api/mod.rs:114-121`
  - Single-ticker queries bypass `max_limit()`, allowing requests like `?ticker=VCB&interval=1m&limit=999999999`.
  - Fix: apply a 10k cap for single-ticker queries. 10k bars covers ~7 days of 1m data or ~40 years of daily data.

## Low

- [ ] **Dead code compiler warnings — remove all**
  - Unused functions: `save_ohlcv`, `get_ticker`, `list_tickers`, `get_ohlcv`, `load_groups_from_json`
  - Unused fields: `Ticker.next_1h`, `Ticker.next_1m`, `RateLimiter.refill_interval_ms`, `TopPerformersQuery.sector`, `TopPerformersQuery.with_hour`, `LegacyQuery.interval`
  - Unused enum variants: `BinanceError::RateLimit`, `BinanceError::NoData`
  - Unused method: `RateLimiter.requests_per_minute`
  - Unused variable assignment: `param_idx` in `src/queries/ohlcv.rs:608`
  - Fix: remove all dead code.

- [x] ~~No holiday calendar for Vietnamese trading hours~~ — workers just get empty responses on holidays. Minor API waste but no functional impact.

- [x] ~~Integer overflow in interval calculations~~ — max product is ~43M, far below `i64::MAX` (~9.2 quintillion). Impossible in practice.
