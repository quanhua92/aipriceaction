# Redis ZSET OHLCV Cache

Redis Sorted Sets (ZSET) act as an optional edge cache for OHLCV data. Workers write crawled data to Redis ZSETs after saving to PostgreSQL. A backfill worker reads all tickers from PostgreSQL and fills ZSETs.

The server reads from Redis first, falling back to PostgreSQL only when Redis has no data. When PG is down, all `/tickers` and `/analysis/*` endpoints serve from Redis (date-range queries use full ZSET + in-memory filtering).

## Architecture

```
                         READ PATH (Redis-first)
┌──────────┐         ┌───────────┐         ┌──────────────┐
│  Client   │────────>│  Server   │────────>│  Redis ZSET  │──hit──> respond
│  /tickers │         │           │    miss  │  (cache)     │
└──────────┘         │           │────────>│              │
                     │           │         └──────────────┘
                     │           │    miss  ┌──────────────┐
                     └───────────│───────>│  PostgreSQL  │──> respond + write to Redis
                                │         │  (primary)   │
                                │         └──────────────┘
                                │
                         WRITE PATH (PG-first)
┌─────────────┐     ┌──────────────┐     ┌───────────┐
│  VCI/Binance │────>│  PostgreSQL  │────>│ Redis ZSET│
│  Yahoo/SJC   │     │  (primary)   │     │ (cache)   │
│   workers    │     │              │     │           │
└─────────────┘     └──────────────┘     └───────────┘
                           │                    ▲
                           │    backfill worker   │
                           └────────────────────┘
```

- **Read path**: Redis-first for all OHLCV and ticker-list queries (no date range). PG fallback when Redis has no data. PG calls wrapped with 3-5s timeout.
- **Workers**: fire-and-forget writes to Redis after each PG upsert
- **Backfill worker**: full backfill of all ticker/interval groups every 60 minutes, followed by SCAN + trim to enforce retention limits. Caches the ticker list in `meta:ticker_list` at the end of each cycle.
- **Graceful degradation**: if `REDIS_URL` is not set, everything runs without Redis. If PG is down, reads from Redis only (full ZSET read + date-range filtering). If Redis goes down mid-session, reads automatically fall through to PG with 3-5s timeout — no restart needed.

## Dependencies

**`fred` crate v10** (`Cargo.toml`):
- `i-sorted-sets` — Sorted set commands (`zadd`, `zrevrange`, `zremrangebyrank`, `zcard`)
- `i-keys` — key management (`del` for cleanup, `scan_page` for key discovery)
- `enable-rustls` — TLS support for Redis connections

```toml
fred = { version = "10", features = ["i-sorted-sets", "i-keys", "enable-rustls"], default-features = false }
```

## Docker Infrastructure

Redis 8 Alpine service added to `docker-compose.yml` and `docker-compose.local.yml`:

- **Image**: `redis:8-alpine`
- **Memory**: 2 GB max, `allkeys-lru` eviction policy
- **Auth**: `<your-password>`
- **Port**: 6379
- **Healthcheck**: `redis-cli -a <your-password> ping`
- **Volume**: `redis_data` persisted to `/data`

The `aipriceaction` service depends on Redis with `service_healthy` condition.

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `REDIS_URL` | No | — | Redis connection URL. When set, workers write OHLCV to Redis ZSET |
| `REDIS_WORKERS` | No | `false` | Enable the backfill worker (`"true"` or `"1"`) |
| `REDIS_PASSWORD` | No | — | Redis password (auto-configured in Docker via `.env`) |

**URL format**: `redis://default:<your-password>@localhost:6379/0`

Without `REDIS_URL`, the application starts normally with no Redis-related behavior and no error messages (only a single info log: "REDIS_URL not set, Redis ZSET cache disabled").

## Key Format

### OHLCV ZSET keys

One ZSET key per ticker/interval (all 5 OHLCV fields packed into a single member string):

```
ohlcv:{source}:{ticker}:{interval}
```

**Examples**:
```
ohlcv:vn:VCB:1D
ohlcv:crypto:BTCUSDT:1h
ohlcv:yahoo:AAPL:1m
ohlcv:sjc:SJC-GOLD:1D
```

### Ticker list key

A plain string key containing a JSON array of all known tickers (used to resolve ticker lists without PG):

```
meta:ticker_list
```

**Value**: JSON array of `{"source":"vn","ticker":"VCB"}, ...`
**TTL**: 15 minutes (auto-expires, refreshed every backfill cycle)
**Size**: ~1900 tickers × ~30 bytes each ≈ ~57KB

## OHLCV Member Format

Each ZSET member is a pipe-delimited string:

```
{ts_ms}|{open}|{high}|{low}|{close}|{volume}|{crawl_ts_ms}
```

**Example**: `1700000000000|1500.5|1510|1490|1505.25|100000|1775870000000`

- **Score**: bar timestamp in milliseconds (integer as f64)
- **Member**: pipe-delimited OHLCV string + crawl timestamp
- **crawl_ts_ms**: `Utc::now().timestamp_millis()` at write time, used for dedup — the latest crawl_ts wins per bar timestamp

### Read Path (OHLCV)

Pipelined `ZREVRANGE` — 1 command per ticker, 1 network round-trip for all tickers:

```
ZREVRANGE ohlcv:vn:VCB:1D 0 249     # get last 250 bars
```

Response is an array of member strings, parsed by splitting on `|`. Deduplication by bar timestamp keeps only the entry with the highest `crawl_ts_ms` (most recent write). Backward compatible with old 6-field format (no crawl_ts).

### Read Path (Ticker List)

Simple `GET` on `meta:ticker_list`, parsed as JSON array of `{source, ticker}`:

```
GET meta:ticker_list
```

Returns `None` if key doesn't exist (e.g. first boot before backfill completes).

### Write Path (OHLCV)

Batch `ZADD` — all OHLCV fields in one call per ticker/interval:

```
ZADD ohlcv:vn:VCB:1D 1700000000000 "1700000000000|1500.5|1510|1490|1505.25|100000|1775870000000" ...
```

Followed by retention trim:

```
ZREMRANGEBYRANK ohlcv:vn:VCB:1D 0 -(MAX+1)    # keep top MAX entries
```

### Write Path (Ticker List)

`SET` with 15-minute TTL at the end of each backfill cycle:

```
SET meta:ticker_list '{"source":"vn","ticker":"VCB"},...' EX 900
```

## Retention Policy

| Interval | Max Size | Coverage |
|---|---|---|
| `1D` | 5,000 entries | ~20 years of daily bars |
| `1h` | 20,000 entries | ~2 years of hourly bars |
| `1m` | 10,000 entries | ~7 days of minute bars |

| Key | TTL | Refresh |
|---|---|---|
| `meta:ticker_list` | 15 min | Every backfill cycle (60 min) |

Retention is enforced in two places:
1. **On write**: `write_ohlcv_to_redis()` trims after every `ZADD`
2. **On backfill cycle**: `trim_all_keys()` runs `ZREMRANGEBYRANK` on every discovered key every 60 minutes

## Key Discovery

ZSET keys are discovered via `SCAN`:

```
SCAN 0 MATCH ohlcv:* COUNT 1000
```

The backfill worker parses key components (`source`, `ticker`, `interval`) from the key format.

## PG-Outage Resilience

When PostgreSQL is unreachable:

| Endpoint | Behavior |
|---|---|
| `GET /tickers?symbol=BTCUSDT` | Redis-first OHLCV read, instant response |
| `GET /tickers` (no symbol) | Redis ticker list + Redis OHLCV per ticker |
| `GET /tickers?mode=all` | Redis ticker list + Redis OHLCV per source group (incl. sjc merge) |
| `GET /tickers?start_date=..&end_date=..` | Full ZSET read + in-memory date filtering |
| `GET /tickers/group` | File-based (JSON/CSV), no PG needed |
| `GET /tickers/name` | File-based (JSON/CSV), no PG needed |
| `GET /tickers/info` | File-based (CSV), no PG needed |
| `GET /health` | PG health check fails, but endpoint still returns 200 |
| `GET /analysis/rrg` | Redis-first via `try_redis_batch` + `enhance_rows`, per-source fallback |
| `GET /analysis/top-performers` | Redis-first via `try_redis_batch` + `enhance_rows`, per-source fallback |
| `GET /analysis/ma-scores-by-sector` | Redis-first via `try_redis_batch` + `enhance_rows`, per-source fallback |
| `GET /analysis/volume-profile` | Redis-first 1m read with date-range filtering (7-day retention limit) |

PG pool is configured with `acquire_timeout(3s)`. All read-path PG calls are wrapped with `tokio::time::timeout` (3s for ticker queries, 5s for OHLCV batch queries). On timeout or error, the server returns empty data gracefully rather than hanging.

## Source Files

### `src/redis.rs` — Client Connection

Provides `RedisClient` (type alias for `fred::prelude::Client`) and `connect()`:

- Reads `REDIS_URL` from environment
- Creates a `fred::Client` with `Config::from_url()`
- Calls `client.connect()` (no reconnect policy — fred manages reconnection internally)
- Waits for connection via `client.wait_for_connect()` with 3s timeout
- Background health loop pings every 15s, triggers reconnect on failure
- Returns `Option<RedisClient>` — `None` if `REDIS_URL` is unset or connection fails

### `src/workers/redis_worker.rs` — ZSET Helpers & Backfill Worker

**Public types**:

- `TickerInfo` — minimal `{source, ticker}` struct for the ticker list cache

**Public helpers** (called from workers):

- `zset_key(source, ticker, interval) -> String` — build the key string
- `max_size(interval) -> usize` — return max ZSET members for the interval
- `format_row_as_member(row) -> String` — format an OhlcvRow as a pipe-delimited member
- `parse_member(member, interval) -> Option<OhlcvRow>` — parse a pipe-delimited member back
- `write_ohlcv_to_redis(client, source, ticker, interval, rows)` — batch-write OHLCV rows via `ZADD`, then trim with `ZREMRANGEBYRANK`. One call per ticker/interval (all 5 fields in one ZADD). No-op if client is `None` or rows is empty.
- `write_ticker_list(client, tickers)` — serialize `Vec<TickerInfo>` as JSON and store in `meta:ticker_list` with 15min TTL
- `read_ticker_list(client) -> Option<Vec<TickerInfo>>` — read and deserialize `meta:ticker_list` from Redis

**Backfill worker** (`run(pool, client)`):

1. Enumerates all tickers from PostgreSQL via `list_all_tickers()`
2. Builds groups: every ticker × 3 intervals (1D, 1h, 1m) with full backfill limits
3. Backfills ALL groups in parallel (concurrency = `BACKFILL_CONCURRENCY`)
4. Discovers all `ohlcv:*` keys via `SCAN`, trims each to retention limit
5. Caches the ticker list in `meta:ticker_list` for PG-outage resilience
6. Sleeps 60 minutes, repeats from step 1

### `src/server/redis_reader.rs` — Pipelined ZREVRANGE Read Path

- `batch_read_ohlcv_from_redis()` — pipelines N `ZREVRANGE` calls (1 per ticker), parses pipe-delimited members into `OhlcvRow` structs
- `read_ticker_list_from_redis()` — reads `meta:ticker_list` and returns `Option<Vec<TickerInfo>>`
- Returns `HashMap<ticker, RedisReadResult>` with PERF tracing

### `src/server/api/` — Redis-First Read Logic

The server's `/tickers` handler uses Redis-first for all read paths:

- **`fetch.rs::fetch_native_tickers()`**: tries Redis ZSET read first (including merged extra sources like sjc), falls back to PG with 5s timeout
- **`fetch.rs::fetch_aggregated_tickers()`**: same Redis-first logic for aggregated intervals
- **`mod.rs::handle_mode_all()`**: per-source spawned tasks each try Redis first, then PG fallback
- **Ticker list resolution**: tries Redis `meta:ticker_list` first, falls back to PG with 3s timeout
- **`?redis=false`**: skips Redis, goes straight to PG (useful for debugging)

### `src/server/analysis/` — Redis-First Analysis Endpoints

All analysis endpoints use `try_redis_batch()` + `enhance_rows()`:

- **`rrg.rs`**: Redis-first for mascore (no trails), mascore with trails, and JdK algorithm — per-source fallback for `mode=all`
- **`performers.rs`**: Redis-first for top-performers via `try_redis_batch` + `enhance_rows`
- **`ma_scores.rs`**: Redis-first for ma-scores-by-sector via `try_redis_batch` + `enhance_rows`
- **`volume_profile.rs`**: Redis-first 1m read with in-memory date-range filtering

### `src/test_redis.rs` — TestRedis CLI Command

Test suite exercising all Redis ZSET operations:

1. `REDIS_URL` env check + connect
2. **Live data query** — `ZCARD` on real backfilled keys
3. `ZADD` — batch-add 10 synthetic daily bars
4. `ZCARD` — verify count
5. `ZREVRANGE` — read all bars, parse and verify
6. `ZREVRANGE` with limit — read last 5 bars
7. `ZREVRANGE` with scores — verify descending order
8. `ZREMRANGEBYRANK` — trim to 5 entries
9. `ZADD` overwrite — verify last-write-wins (same score)
10. `SCAN` — discover keys matching `ohlcv:*`
11. Cleanup — delete test key
12. Summary — pass/fail counts

```
cargo run -- test-redis          # default ticker: VNINDEX
cargo run -- test-redis --ticker VCB
```

## Constants (`src/constants.rs::redis_ts`)

```rust
pub mod redis_ts {
    pub const DAILY_MAX_SIZE: usize = 5000;      // retention for daily
    pub const HOURLY_MAX_SIZE: usize = 20000;    // retention for hourly
    pub const MINUTE_MAX_SIZE: usize = 10000;    // retention for minute (~7 days)
    pub const DAILY_BACKFILL_LIMIT: i64 = 5000;
    pub const HOURLY_BACKFILL_LIMIT: i64 = 20000;
    pub const MINUTE_BACKFILL_LIMIT: i64 = 10000;
    pub const BACKFILL_LOOP_SECS: u64 = 3600;       // 60 minutes between cycles
    pub const BACKFILL_CONCURRENCY: usize = 2;       // parallel tasks per cycle
    pub const MEMBER_SEP: &str = "|";                // field separator in member string
    pub const TICKER_LIST_KEY: &str = "meta:ticker_list";  // cached ticker list
    pub const TICKER_LIST_TTL_SECS: u64 = 900;       // 15 min TTL for ticker list
}
```

## Performance

Pipelined ZREVRANGE reduces Redis commands to 1 per ticker (vs 5 per ticker with per-field storage):

| Scenario | Redis Commands |
|---|---|
| 1 ticker | 1 ZREVRANGE (pipeline) |
| 3 tickers | 3 ZREVRANGE (pipeline, 1 round-trip) |
| 10 tickers | 10 ZREVRANGE (pipeline, 1 round-trip) |

Typical p50 latencies (vs PostgreSQL):

| Query | Redis | PostgreSQL | Speedup |
|---|---|---|---|
| 1 ticker 15m | 6ms | 11ms | 1.8x |
| 3 tickers 5m | 20ms | 36ms | 1.8x |
| 10 tickers 1D | 86ms | 93ms | 1.1x |

## Verification

```bash
# 1. Start Redis
docker compose up -d

# 2. Run test suite
REDIS_URL=redis://default:<your-password>@localhost:6379/0 cargo run -- test-redis

# 3. Run server with Redis (workers write to Redis on each crawl)
REDIS_URL=redis://default:<your-password>@localhost:6379/0 cargo run -- serve

# 4. Run server with backfill worker
REDIS_URL=redis://default:<your-password>@localhost:6379/0 REDIS_WORKERS=true cargo run -- serve

# 5. Verify keys exist in Redis
redis-cli -a <your-password> ZCARD ohlcv:vn:VNINDEX:1D
redis-cli -a <your-password> ZREVRANGE ohlcv:vn:VNINDEX:1D 0 2
redis-cli -a <your-password> SCAN 0 MATCH "ohlcv:*" COUNT 100

# 6. Verify ticker list cache
redis-cli -a <your-password> GET meta:ticker_list
redis-cli -a <your-password> TTL meta:ticker_list

# 7. Test PG-outage resilience
docker compose stop postgres
curl -s "http://localhost:3000/tickers?symbol=BTCUSDT&interval=1D&limit=2&mode=crypto"
curl -s "http://localhost:3000/tickers?mode=all&interval=1D&limit=1" | python3 -c "import sys,json; print(f'{len(json.load(sys.stdin))} tickers')"
docker compose start postgres
```
