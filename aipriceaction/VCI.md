# VCI Provider Module

The `src/providers/vci.rs` module is a standalone VCI (Vietcap) API client for fetching Vietnamese stock market data. It is independent of the parent branch's `src/services/vci.rs` — simplified with `&self` methods, no mutable state, and a semaphore-based per-client rate limiter.

## CLI

```bash
# Basic test with default VNINDEX ticker
cargo run -- test-vci

# Test with a stock ticker (enables company info + financial ratios)
cargo run -- test-vci --ticker VCB

# Custom rate limit and data points
cargo run -- test-vci --ticker VCB --rate-limit 20 --count-back 500

# With SOCKS5/HTTP proxies
HTTP_PROXIES="socks5h://user:pass@host:port" cargo run -- test-vci
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--ticker` | `VNINDEX` | Ticker symbol to test |
| `--rate-limit` | `30` | Requests per minute per client |
| `--count-back` | `10` | Number of data points to request |

Index tickers (`VNINDEX`, `VN30`, `HNX`, `UPCOM`, `HNX30`, `UPCOMINDEX`) automatically skip company info and financial ratio tests.

## Architecture

### Multi-Client Rotation with Per-Client Rate Limiting

```
┌──────────────────────────────────────────┐
│              VciProvider                 │
│                                          │
│  clients:      [direct, proxy-1, ...]    │
│  rate_limiters: [RL-0,    RL-1,   ...]   │
│                                          │
│  make_request()                          │
│  1. Shuffle client indices               │
│  2. For attempt 1..=5:                   │
│     a. Pick next client (cycle if needed)│
│     b. rate_limiters[i].acquire()        │
│     c. Send request with browser headers │
│     d. Success → return                  │
│     e. Failure → try next client         │
│  3. All failed → return error            │
└──────────────────────────────────────────┘
```

- **1 direct `HttpClient`** always created (unless disabled)
- **Proxy clients** added from `HTTP_PROXIES` env var (comma-separated `socks5h://`, `http://`, `https://`)
- **Each client** gets its own `RateLimiter` (independent token buckets)
- **No proxy connectivity test at startup** (unlike parent branch)
- **`&self`** — fully immutable, no `&mut self` required

### Rate Limiter (Semaphore-Based)

```rust
pub struct RateLimiter {
    semaphore: Arc<Semaphore>,     // N permits = N concurrent requests
    refill_interval_ms: u64,       // 60_000 / requests_per_minute
    refill_handle: JoinHandle<()>, // Background task adds 1 permit per interval
}
```

- Semaphore initialized with `requests_per_minute` permits
- Background tokio task refills +1 permit every `60s / N` milliseconds
- `acquire()` blocks until a permit is available, then consumes it (`forget()`)
- Drop aborts the refill task

### Request Headers

Every request includes full browser-like headers matching Chrome on Windows:

```
Accept: application/json, text/plain, */*
Accept-Language: en-US,en;q=0.9,vi-VN;q=0.8,vi;q=0.7
Content-Type: application/json
Referer: https://trading.vietcap.com.vn/
Origin: https://trading.vietcap.com.vn
User-Agent: <randomly chosen from 5 Chrome/Firefox/Safari/Edge UA strings>
Sec-Fetch-Dest: empty
Sec-Fetch-Mode: cors
Sec-Fetch-Site: same-site
sec-ch-ua: "Not_A Brand";v="8", "Chromium";v="120"...
DNT: 1
```

## API Endpoints

### 1. OHLCV Data — `get_history()`

```rust
pub async fn get_history(&self, symbol: &str, interval: &str, count_back: u32)
    -> Result<Vec<OhlcvData>, VciError>
```

| Field | Description |
|-------|-------------|
| `symbol` | Ticker symbol (e.g. `"VCB"`, `"VNINDEX"`) |
| `interval` | `"1D"`, `"1H"`, `"1m"` |
| `count_back` | Number of data points to request |

**Endpoint:** `POST https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart`

**Payload:**
```json
{
  "timeFrame": "ONE_DAY",
  "symbols": ["VCB"],
  "to": 1743139199,
  "countBack": 500
}
```

**Interval mapping:**

| Input | API Value |
|-------|-----------|
| `1m`, `5m`, `15m`, `30m` | `ONE_MINUTE` |
| `1H` | `ONE_HOUR` |
| `1D`, `1W`, `1M` | `ONE_DAY` |

**Response:** Array of `OhlcvData` sorted chronologically.

```json
[
  {
    "time": "2024-03-25",
    "open": 63087.97,
    "high": 64349.73,
    "low": 62490.3,
    "close": 63552.83,
    "volume": 1370504,
    "symbol": "VCB"
  }
]
```

### 2. Company Info — `company_info()`

```rust
pub async fn company_info(&self, symbol: &str) -> Result<CompanyInfo, VciError>
```

**Endpoint:** `POST https://trading.vietcap.com.vn/data-mt/graphql`

Fetches via GraphQL query: `CompanyListingInfo`, `TickerPriceInfo`, `OrganizationShareHolders`, `OrganizationManagers`.

**Response fields:**

| Field | Source |
|-------|--------|
| `exchange` | `TickerPriceInfo.exchange` |
| `industry` | `CompanyListingInfo.icbName3` |
| `company_profile` | `CompanyListingInfo.companyProfile` |
| `outstanding_shares` | `CompanyListingInfo.issueShare` |
| `current_price` | `TickerPriceInfo.matchPrice` |
| `market_cap` | Calculated: `price * shares` |
| `shareholders` | `OrganizationShareHolders[]` |
| `officers` | `OrganizationManagers[]` |

### 3. Financial Ratios — `financial_ratios()`

```rust
pub async fn financial_ratios(&self, symbol: &str, period: &str)
    -> Result<Vec<HashMap<String, Value>>, VciError>
```

**Endpoint:** `POST https://trading.vietcap.com.vn/data-mt/graphql`

**Period mapping:**

| Input | API Value |
|-------|-----------|
| `"quarter"` | `"Q"` |
| `"year"` | `"Y"` |

**Response:** Array of `HashMap<String, Value>`, each containing ~60 financial fields:

`revenue`, `netProfit`, `pe`, `pb`, `roe`, `roa`, `eps`, `dividend`, `ebitda`, `grossMargin`, `netProfitMargin`, `debtToEquity`, `currentRatio`, `bvps`, `yearReport`, `lengthReport`, and more.

## Error Handling

```rust
pub enum VciError {
    Http(isahc::Error),           // Network/transport errors
    Serialization(serde_json::Error), // JSON parse errors
    InvalidInterval(String),      // Unknown interval string
    InvalidResponse(String),      // API returned unexpected data
    RateLimit,                    // Rate limit exceeded
    NoData,                       // Empty response
}
```

### Retry Logic

`make_request()` performs up to **5 total attempts** across shuffled clients:

- **403 / 429**: Retry immediately on next client
- **5xx**: Retry immediately on next client
- **4xx (other)**: Return error immediately (not retryable)
- **Network error**: Retry immediately on next client
- **No exponential backoff** between client switches (unlike parent branch)
- All attempts exhaust all shuffled client indices before cycling

## Differences from Parent Branch (`src/services/vci.rs` on `main`)

| Feature | Parent (`vci.rs`) | Provider (`providers/vci.rs`) |
|---------|-------------------|-------------------------------|
| Mutability | `&mut self` | `&self` |
| Rate limiter | Sliding window (`Vec<SystemTime>`) | Semaphore (tokio) |
| Shared rate limiter | `Arc<SharedRateLimiter>` | Per-client `RateLimiter` |
| Proxy test at startup | `test_proxy_url()` async | None |
| Batch endpoint | `get_batch_history()` | Not included |
| Resampling | Weekly/monthly | Not included |
| Retry backoff | Exponential + jitter per client | Immediate switch to next client |
| Max retries | 5 per client (N clients x 5) | 5 total across all clients |
| Constructor | `new_async()` tests proxies | `new()` synchronous |

## Workers

Four independent VCI workers run as background tokio tasks: daily, hourly, minute, and dividend recovery.

### Priority Scheduling

Workers do **not** fetch all 383 tickers every loop. Instead, each ticker is assigned a `next_*` timestamp (one per worker) that determines when it's next eligible for processing. Only tickers whose timestamp has passed ("due") are fetched.

**Tier assignment** uses money flow from the **previous** day's daily bar (`close * volume`, `OFFSET 1` to skip today's incomplete bar):

| Tier | Threshold | Tickers | Daily | Hourly | Minute |
|------|-----------|---------|-------|--------|--------|
| T1 | >= 50B VND | VCB, VIC, VNM, FPT... | 15s | 60s | 60s |
| T2 | >= 5B VND | CTG, HPG, MWG... | 30s | 3m | 2m |
| T3 | >= 0.5B VND | mid-cap | 60s | 5m | 5m |
| T4 | < 0.5B VND | illiquid | 2m | 10m | 10m |

**Off-hours multiplier** (x20): No new data arrives outside 9:00-15:00 ICT, so intervals are scaled up. Daily T1 becomes 5min, T4 becomes 40min, etc.

**Per-loop batching**: All due tickers are fetched from the DB, shuffled randomly, and truncated to `DUE_TICKER_BATCH_SIZE` (50). This prevents multiple containers from competing for the same tickers — each container gets a random 50 from the due pool.

**Schedule reset on recovery**: When the dividend worker finishes re-downloading a ticker's full history, all three `next_*` columns are reset to `NOW()` so the ticker is immediately picked up by all three workers.

### Loop Flow (Daily / Hourly / Minute)

```
loop {
    trading = is_trading_hours()

    1. Fetch all due tickers  (WHERE next_1X < NOW())
    2. Shuffle + truncate to 50
    3. Process in chunks of `concurrency` (12 with 4 clients)
       → after each OK: schedule_next_run(NOW() + tier_interval)
    4. Sleep 15-60s (trading) / 60s (off-hours)
}
```

Key constants (`src/constants.rs → vci_worker`):

| Constant | Value | Purpose |
|----------|-------|---------|
| `DAILY_LOOP_TRADE_SECS` | 15 | Daily loop sleep during trading |
| `DAILY_LOOP_OFF_SECS` | 60 | Daily loop sleep off-hours |
| `HOURLY_LOOP_TRADE_SECS` | 60 | Hourly loop sleep during trading |
| `HOURLY_LOOP_OFF_SECS` | 60 | Hourly loop sleep off-hours |
| `MINUTE_LOOP_TRADE_SECS` | 60 | Minute loop sleep during trading |
| `MINUTE_LOOP_OFF_SECS` | 60 | Minute loop sleep off-hours |
| `OFF_HOURS_MULTIPLIER` | 20 | Multiply tier intervals off-hours |
| `DUE_TICKER_BATCH_SIZE` | 50 | Max tickers per loop iteration |
| `RATE_LIMIT_COOLDOWN_SECS` | 60 | Pause when 429 detected in batch |

### DB Schema

```sql
ALTER TABLE tickers
    ADD COLUMN next_1d TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN next_1h TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN next_1m TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Partial indexes for fast due-ticker queries
CREATE INDEX ix_tickers_next_1d ON tickers (next_1d) WHERE source = 'vn' AND status = 'ready';
CREATE INDEX ix_tickers_next_1h ON tickers (next_1h) WHERE source = 'vn' AND status = 'ready';
CREATE INDEX ix_tickers_next_1m ON tickers (next_1m) WHERE source = 'vn' AND status = 'ready';
```

`DEFAULT NOW()` ensures all tickers are immediately due on first run — no bootstrap needed.

### Dividend Worker

The dividend worker handles tickers flagged with `dividend-detected` or `full-download-requested` status:

1. Delete all OHLCV + indicator data for the ticker
2. Re-download full history for all intervals (1D, 1h, 1m) in backward chunks
3. Enhance with technical indicators
4. Set status back to `ready` and reset all `next_*` to `NOW()`

### Multi-Container Deployment

With multiple containers running against the same database:

- The **shuffle + truncate to 50** ensures each container picks a different random subset from the due pool
- The `schedule_next_run` SQL uses `NOW()` server-side, so even if two containers process the same ticker, the later one just re-schedules it
- Rate limit cooldowns (60s on 429) apply per container independently

## Dependencies

Added to `Cargo.toml`:

```toml
isahc = { version = "1.7", features = ["json"] }  # HTTP client with SOCKS5 support
rand = "0.8"                                        # Client index shuffling
```

`isahc` is used instead of `reqwest` because it natively supports SOCKS5 proxies without additional TLS configuration complexity.
