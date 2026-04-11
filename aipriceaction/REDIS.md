# Redis ZSET OHLCV Cache

Redis Sorted Sets (ZSET) act as an optional edge cache for OHLCV data. Workers write crawled data to Redis ZSETs after saving to PostgreSQL. A backfill worker reads all tickers from PostgreSQL and fills ZSETs.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌───────────┐
│  VCI/Binance │────>│  PostgreSQL  │────>│ Redis ZSET│
│  Yahoo/SJC   │     │  (primary)   │     │ (cache)   │
│   workers    │     │              │     │           │
└─────────────┘     └──────────────┘     └───────────┘
                           │                    ▲
                           │    backfill worker   │
                           └────────────────────┘
```

- **Workers**: fire-and-forget writes to Redis after each PG upsert
- **Backfill worker**: full backfill of all ticker/interval groups every 60 minutes, followed by SCAN + trim to enforce retention limits
- **Graceful degradation**: if `REDIS_URL` is not set, everything runs without Redis

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
- **Auth**: `helloaipriceaction`
- **Port**: 6379
- **Healthcheck**: `redis-cli -a helloaipriceaction ping`
- **Volume**: `redis_data` persisted to `/data`

The `aipriceaction` service depends on Redis with `service_healthy` condition.

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `REDIS_URL` | No | — | Redis connection URL. When set, workers write OHLCV to Redis ZSET |
| `REDIS_WORKERS` | No | `false` | Enable the backfill worker (`"true"` or `"1"`) |

**URL format**: `redis://default:helloaipriceaction@localhost:6379/0`

Without `REDIS_URL`, the application starts normally with no Redis-related behavior and no error messages (only a single info log: "REDIS_URL not set, Redis ZSET cache disabled").

## Key Format

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

### Member Format

Each ZSET member is a pipe-delimited string:

```
{ts_ms}|{open}|{high}|{low}|{close}|{volume}|{crawl_ts_ms}
```

**Example**: `1700000000000|1500.5|1510|1490|1505.25|100000|1775870000000`

- **Score**: bar timestamp in milliseconds (integer as f64)
- **Member**: pipe-delimited OHLCV string + crawl timestamp
- **crawl_ts_ms**: `Utc::now().timestamp_millis()` at write time, used for dedup — the latest crawl_ts wins per bar timestamp

### Read Path

Pipelined `ZREVRANGE` — 1 command per ticker, 1 network round-trip for all tickers:

```
ZREVRANGE ohlcv:vn:VCB:1D 0 249     # get last 250 bars
```

Response is an array of member strings, parsed by splitting on `|`. Deduplication by bar timestamp keeps only the entry with the highest `crawl_ts_ms` (most recent write). Backward compatible with old 6-field format (no crawl_ts).

### Write Path

Batch `ZADD` — all OHLCV fields in one call per ticker/interval:

```
ZADD ohlcv:vn:VCB:1D 1700000000000 "1700000000000|1500.5|1510|1490|1505.25|100000|1775870000000" ...
```

Followed by retention trim:

```
ZREMRANGEBYRANK ohlcv:vn:VCB:1D 0 -(MAX+1)    # keep top MAX entries
```

## Retention Policy

| Interval | Max Size | Coverage |
|---|---|---|
| `1D` | 5,000 entries | ~20 years of daily bars |
| `1h` | 20,000 entries | ~2 years of hourly bars |
| `1m` | 10,000 entries | ~7 days of minute bars |

Retention is enforced in two places:
1. **On write**: `write_ohlcv_to_redis()` trims after every `ZADD`
2. **On backfill cycle**: `trim_all_keys()` runs `ZREMRANGEBYRANK` on every discovered key every 60 minutes

## Key Discovery

ZSET keys are discovered via `SCAN`:

```
SCAN 0 MATCH ohlcv:* COUNT 1000
```

The backfill worker parses key components (`source`, `ticker`, `interval`) from the key format.

## Source Files

### `src/redis.rs` — Client Connection

Provides `RedisClient` (type alias for `fred::prelude::Client`) and `connect()`:

- Reads `REDIS_URL` from environment
- Creates a `fred::Client` with `Config::from_url()`
- Calls `client.connect()` (no reconnect policy — fred manages reconnection internally)
- Waits for connection via `client.wait_for_connect()`
- Returns `Option<RedisClient>` — `None` if `REDIS_URL` is unset or connection fails

### `src/workers/redis_worker.rs` — ZSET Helpers & Backfill Worker

**Public helpers** (called from workers):

- `zset_key(source, ticker, interval) -> String` — build the key string
- `max_size(interval) -> usize` — return max ZSET members for the interval
- `format_row_as_member(row) -> String` — format an OhlcvRow as a pipe-delimited member
- `parse_member(member, interval) -> Option<OhlcvRow>` — parse a pipe-delimited member back
- `write_ohlcv_to_redis(client, source, ticker, interval, rows)` — batch-write OHLCV rows via `ZADD`, then trim with `ZREMRANGEBYRANK`. One call per ticker/interval (all 5 fields in one ZADD). No-op if client is `None` or rows is empty.

**Backfill worker** (`run(pool, client)`):

1. Enumerates all tickers from PostgreSQL via `list_all_tickers()`
2. Builds groups: every ticker × 3 intervals (1D, 1h, 1m) with full backfill limits
3. Backfills ALL groups in parallel (concurrency = `BACKFILL_CONCURRENCY`)
4. Discovers all `ohlcv:*` keys via `SCAN`, trims each to retention limit
5. Sleeps 60 minutes, repeats from step 1

### `src/server/redis_reader.rs` — Pipelined ZREVRANGE Read Path

- `batch_read_ohlcv_from_redis()` — pipelines N `ZREVRANGE` calls (1 per ticker), parses pipe-delimited members into `OhlcvRow` structs
- Returns `HashMap<ticker, RedisReadResult>` with PERF tracing

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
REDIS_URL=redis://default:helloaipriceaction@localhost:6379/0 cargo run -- test-redis

# 3. Run server with Redis (workers write to Redis on each crawl)
REDIS_URL=redis://default:helloaipriceaction@localhost:6379/0 cargo run -- serve

# 4. Run server with backfill worker
REDIS_URL=redis://default:helloaipriceaction@localhost:6379/0 REDIS_WORKERS=true cargo run -- serve

# 5. Verify keys exist in Redis
redis-cli -a helloaipriceaction ZCARD ohlcv:vn:VNINDEX:1D
redis-cli -a helloaipriceaction ZREVRANGE ohlcv:vn:VNINDEX:1D 0 2
redis-cli -a helloaipriceaction SCAN 0 MATCH "ohlcv:*" COUNT 100
```
