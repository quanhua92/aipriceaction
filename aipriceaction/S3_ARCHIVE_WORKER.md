# S3 Archive Worker

Exports OHLCV data from PostgreSQL to S3 as daily CSV files, and exposes ticker
metadata as a static JSON file. A companion Python SDK reads data via plain HTTP
(no S3 protocol, no boto3, no credentials needed).

## Architecture

```
PostgreSQL (source of truth)
  |
  | [s3_archive worker, concurrent uploads, every 1 hour]
  v
S3 Bucket (rustfs / AWS S3) — public-read
  |-- meta/tickers.json            -- ticker metadata
  |-- ohlcv/{source}/{ticker}/{interval}/{ticker}-{interval}-{YYYY}-{MM}-{DD}.csv
  |
  | [aipriceaction Python SDK reads via HTTP GET/HEAD]
  v
Python notebooks / scripts
```

## S3 Key Scheme

One file per ticker, per interval, per day.

```
ohlcv/{source}/{ticker}/{interval}/{ticker}-{interval}-{YYYY}-{MM}-{DD}.csv
meta/tickers.json
```

Examples:

```
ohlcv/vn/VCB/1D/VCB-1D-2025-04-01.csv
ohlcv/vn/VCB/1D/VCB-1D-2025-04-02.csv
ohlcv/vn/VCB/1h/VCB-1h-2025-04-01.csv
ohlcv/vn/VCB/1m/VCB-1m-2025-04-01.csv
ohlcv/crypto/BTCUSDT/1D/BTCUSDT-1D-2025-04-01.csv
ohlcv/yahoo/^GSPC/1D/^GSPC-1D-2025-04-01.csv
ohlcv/sjc/SJC-GOLD/1D/SJC-GOLD-1D-2025-04-01.csv
meta/tickers.json
```

## CSV Format

Each file contains OHLCV data for one ticker, one interval, one day.

Header: `time,open,high,low,close,volume`

Daily interval example (1 row per file):
```csv
time,open,high,low,close,volume
2025-04-01 00:00:00,75200.0,75800.0,75100.0,75600.0,12345678
```

Hourly interval example (6-8 rows per file for VN stocks, 24 for crypto):
```csv
time,open,high,low,close,volume
2025-04-01 09:00:00,75200.0,75400.0,75100.0,75300.0,500000
2025-04-01 10:00:00,75300.0,75800.0,75200.0,75600.0,800000
2025-04-01 11:00:00,75600.0,75900.0,75500.0,75800.0,600000
```

Minute interval example (up to 390 rows per file for VN stocks, 1440 for crypto):
```csv
time,open,high,low,close,volume
2025-04-01 09:00:00,75200.0,75250.0,75180.0,75230.0,10000
2025-04-01 09:01:00,75230.0,75280.0,75200.0,75250.0,12000
```

- `time` — ISO-ish timestamp: `YYYY-MM-DD HH:MM:SS` (UTC)
- `open`, `high`, `low`, `close` — f64 prices
- `volume` — i64 (trading volume)
- Rows sorted chronologically (oldest first)

## tickers.json Format

Static metadata for all tickers, uploaded to `meta/tickers.json`. Built by combining
data from multiple source files at startup:

| Source | Source file | Fields contributed |
|---|---|---|
| VN stocks | `vn.csv` | `source=vn`, `ticker`, `name` (from `organ_name`), `exchange`, `type` |
| Crypto | `binance_tickers.json` → `data[]` | `source=crypto`, `ticker` (from `symbol`), `name` |
| Global | `global_tickers.json` → `data[]` | `source=yahoo`, `ticker` (from `symbol`), `name`, `category` |
| SJC gold | `sjc_tickers.json` → `data[]` | `source=sjc`, `ticker` (from `symbol`), `name`, `category` |
| Groups | `ticker_group.json` | `group` field — maps ticker symbols to sector names |

```json
[
  {
    "source": "vn",
    "ticker": "VCB",
    "name": "Ngân hàng TMCP Kỹ thương Việt Nam",
    "exchange": "HOSE",
    "type": "stock",
    "group": "NGAN_HANG"
  },
  {
    "source": "crypto",
    "ticker": "BTCUSDT",
    "name": "Bitcoin",
    "group": "CRYPTO_TOP_100"
  },
  {
    "source": "yahoo",
    "ticker": "^GSPC",
    "name": "S&P 500",
    "category": "US Index"
  },
  {
    "source": "sjc",
    "ticker": "SJC-GOLD",
    "name": "SJC Gold Bar",
    "category": "Commodity"
  }
]
```

The `group` field is populated by reverse-looking up each ticker in `ticker_group.json`
categories (VN stocks get sector groups, crypto gets "CRYPTO_TOP_100", yahoo/sjc
get their `category` field as group).

## Worker Logic

### Startup scan (on start + every 24 hours)

On startup and every `STARTUP_SCAN_INTERVAL_SECS` (default 24h), the worker performs a full historical scan:

1. Upload `meta/tickers.json` (skip if SHA-256 of JSON bytes matches S3 metadata)
2. Query data ranges: `SELECT ticker_id, interval, MIN(time), MAX(time) FROM ohlcv GROUP BY ticker_id, interval`
3. For each ticker + interval:
   - Iterate from earliest date to current date (day by day)
   - For each day: check fingerprint → skip if S3 hash matches, else upload
4. When scan completes, enter incremental loop

The daily rescan catches new tickers added to the DB and ensures their full history
gets uploaded (not just the recent `LOOKBACK_DAYS`).

All uploads run concurrently via `tokio::spawn` + `Semaphore(UPLOAD_CONCURRENCY=4)`.
This is efficient because:
- If all files already exist in S3, each day is just a HEAD request + fingerprint comparison → skip
- Only new or changed files trigger actual CSV generation and upload
- Dividend-adjusted data is automatically detected via the fingerprint and re-uploaded
- Can be safely interrupted (Ctrl+C / container stop) — partial progress is preserved

### Incremental loop (every 1 hour)

1. Upload `meta/tickers.json` (skip if SHA-256 of JSON bytes matches S3 metadata)
2. For each ticker in DB:
   For each interval (`1D`, `1h`, `1m`):
     For today + `LOOKBACK_DAYS - 1` previous days:
       a. `get_ohlcv_day_fingerprint()` → `(count, max_time, sum_close_scaled, sum_volume)`
       b. If no data for this day → skip
       c. `fingerprint = SHA-256("{count}:{max_time}:{sum_close_scaled}:{sum_volume}")`
       d. HEAD S3 object, read `x-amz-meta-content-hash`
       e. If hash matches → **SKIP** (no upload)
       f. Else → fetch all rows for the day, build CSV, PUT to S3 with metadata
   All day-tasks run concurrently (bounded by `UPLOAD_CONCURRENCY`)
3. Sleep `LOOP_SECS` (default 3600 seconds = 1 hour, override via `S3_ARCHIVE_INTERVAL_SECS`)

### Skip-if-unchanged (fingerprint)

Avoids re-uploading unchanged files. Uses a 4-column aggregate fingerprint
computed from a single cheap SQL query:

```sql
SELECT
    COUNT(*),
    MAX(time),
    SUM((close * 10000)::bigint),
    SUM(volume)
FROM ohlcv
WHERE ticker_id = $1 AND interval = $2 AND time >= $3 AND time < $4;
```

Fingerprint: `SHA-256("{count}:{max_time_rfc3339}:{sum_close_scaled}:{sum_volume}")`

Detection matrix:

| Change type              | COUNT | MAX(time) | SUM(close) | SUM(volume) | Detected? |
|--------------------------|-------|-----------|------------|-------------|-----------|
| New bar arrives          | +1    | changes   | changes    | changes     | Yes       |
| Intraday close/vol update| same  | same      | changes    | changes     | Yes       |
| Dividend price adjustment| same  | same      | **changes**| same        | Yes       |
| Volume correction        | same  | same      | same       | **changes** | Yes       |
| No change                | same  | same      | same       | same        | No (skip) |

The `×10000` multiplier preserves 4 decimal places before casting to `bigint`,
avoiding floating-point precision issues.

Each S3 PUT sets custom metadata:
- `x-amz-meta-content-hash`: the SHA-256 fingerprint string

The Python SDK reads this via a standard HTTP HEAD request (`requests.head()`) — no S3
protocol or boto3 needed. The `x-amz-meta-content-hash` header is returned in any HTTP
HEAD/GET response for public objects, whether from AWS S3 or rustfs.

## Constants

```rust
pub mod s3_archive {
    /// Worker loop interval (seconds). Override via `S3_ARCHIVE_INTERVAL_SECS` env var.
    /// Default: 3600 (1 hour).
    pub const LOOP_SECS: u64 = 3600;

    /// Number of days to check in incremental mode (today + N-1 previous)
    pub const LOOKBACK_DAYS: u32 = 7;

    /// How often to re-run the full historical scan (catches new tickers)
    pub const STARTUP_SCAN_INTERVAL_SECS: u64 = 86400; // 24 hours

    /// Max parallel S3 uploads
    pub const UPLOAD_CONCURRENCY: usize = 4;

    /// Content-Type headers
    pub const CSV_CONTENT_TYPE: &str = "text/csv";
    pub const JSON_CONTENT_TYPE: &str = "application/json";
}
```

## Query Functions

Defined in `src/queries/s3_archive.rs`.

### `get_ohlcv_for_day(pool, ticker_id, interval, date) -> Vec<OhlcvRow>`

Fetches all raw OHLCV rows for a specific ticker, interval, and date.
Returns rows sorted by `time ASC` for CSV output.

```sql
SELECT time, open, high, low, close, volume
FROM ohlcv
WHERE ticker_id = $1 AND interval = $2 AND time >= $3 AND time < $4
ORDER BY time ASC
```

### `get_ohlcv_day_fingerprint(pool, ticker_id, interval, date) -> Option<DayFingerprint>`

Returns the 4-column fingerprint components, or `None` if no data exists for that day.

```sql
SELECT
    COUNT(*),
    MAX(time),
    SUM((close * 10000)::bigint),
    SUM(volume)
FROM ohlcv
WHERE ticker_id = $1 AND interval = $2 AND time >= $3 AND time < $4;
```

### `get_all_tickers_for_archive(pool) -> Vec<ArchiveTicker>`

Returns all tickers with enriched metadata for the `meta/tickers.json` export.
Combines data from the `tickers` DB table with local JSON/CSV source files
(`vn.csv`, `binance_tickers.json`, `global_tickers.json`, `sjc_tickers.json`,
`ticker_group.json`).

```sql
SELECT source, ticker, name
FROM tickers
ORDER BY source, ticker
```

Then enriched with:
- `exchange`, `type` from `vn.csv` (for `source=vn` tickers)
- `category` from `global_tickers.json` / `binance_tickers.json` / `sjc_tickers.json`
- `group` from `ticker_group.json` (reverse lookup: find which category key contains the ticker symbol)

### `get_data_ranges(pool) -> Vec<DataRange>`

Returns `(ticker_id, interval, earliest_time, latest_time)` for each ticker+interval.
Used during bootstrap to determine the date range to iterate.

```sql
SELECT ticker_id, interval, MIN(time) as earliest, MAX(time) as latest
FROM ohlcv
GROUP BY ticker_id, interval
```

## Data Structures

```rust
// Enriched ticker info for archive metadata (tickers.json)
#[derive(serde::Serialize)]
pub struct ArchiveTicker {
    pub source: String,
    pub ticker: String,
    pub name: Option<String>,
    pub exchange: Option<String>,  // HOSE, HNX, UPCOM (vn only)
    #[serde(rename = "type")]
    pub ticker_type: Option<String>, // stock, index, etc.
    pub category: Option<String>,  // Commodity, US Index, etc. (yahoo/crypto/sjc)
    pub group: Option<String>,     // NGAN_HANG, CONG_NGHE, etc.
}

// Fingerprint components for skip-if-unchanged (per-day)
pub struct DayFingerprint {
    pub count: i64,
    pub max_time: DateTime<Utc>,
    pub sum_close_scaled: i64,  // SUM((close * 10000)::bigint)
    pub sum_volume: i64,
}

// Data range per ticker+interval for startup scan
pub struct DataRange {
    pub ticker_id: i32,
    pub interval: String,
    pub earliest: DateTime<Utc>,
    pub latest: DateTime<Utc>,
}
```

## S3 Client Initialization

Uses `rust-s3` (crate name `rust-s3`, library name `s3`). Supports custom endpoints
(rustfs) via `Region::Custom` when `S3_ENDPOINT` env var is set.

```rust
use awscreds::Credentials;
use s3::{Bucket, BucketConfiguration, Region};

fn create_s3_bucket() -> Result<Bucket, Box<dyn std::error::Error>> {
    let bucket_name = std::env::var("S3_BUCKET")?;
    let region_str = std::env::var("S3_REGION").unwrap_or_else(|_| "us-east-1".into());

    let creds = if let Ok(key) = std::env::var("AWS_ACCESS_KEY_ID") {
        let secret = std::env::var("AWS_SECRET_ACCESS_KEY").unwrap_or_default();
        Credentials::new(
            Some(key.as_str()),
            Some(secret.as_str()),
            None, None, None,
        )?
    } else {
        Credentials::default()?
    };

    // If S3_ENDPOINT is set, use Region::Custom for S3-compatible storage (rustfs)
    let region = if let Ok(endpoint) = std::env::var("S3_ENDPOINT") {
        Region::Custom {
            region: region_str,
            endpoint,
        }
    } else {
        region_str.parse::<Region>()?
    };

    let bucket = Bucket::new(&bucket_name, region, creds)?;

    // Path-style URLs for S3-compatible storage (rustfs, MinIO, etc.)
    Ok(*bucket.with_path_style())
}
```

Key API patterns:
- `Bucket::new(name, region, credentials) -> Result<Box<Bucket>>`
- `bucket.exists().await -> Result<bool>` — check if bucket exists
- `Bucket::create_with_path_style(name, region, creds, config).await` — create bucket
- `bucket.head_object(key).await -> Result<(HeadObjectResult, u16)>` — HEAD object
- `HeadObjectResult.metadata: Option<HashMap<String, String>>` — custom metadata (stripped of `x-amz-meta-` prefix)
- `bucket.put_object_with_content_type_and_headers(key, content, content_type, Some(HeaderMap)).await` — PUT with custom metadata
- `bucket.credentials().await -> Result<Credentials>` — get current credentials

## Worker Spawn

Follows the existing `spawn_worker` pattern in `src/cli.rs`. Controlled by
`S3_ARCHIVE_WORKER` env var (default: `false`).

```rust
let s3_archive_enabled = std::env::var("S3_ARCHIVE_WORKER")
    .map(|v| v == "true" || v == "1")
    .unwrap_or(false);

if s3_archive_enabled {
    if std::env::var("S3_BUCKET").is_ok() {
        spawn_worker(&pool, &redis_client, crate::workers::s3_archive::run);
    } else {
        tracing::warn!("S3_ARCHIVE_WORKER=true but S3_BUCKET not set");
    }
}
```

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `S3_ARCHIVE_WORKER` | No | `false` | Enable the S3 archive worker |
| `S3_ARCHIVE_INTERVAL_SECS` | No | `3600` | Worker loop interval in seconds |
| `S3_BUCKET` | Yes (if enabled) | — | S3 bucket name (must be public-read for SDK) |
| `S3_REGION` | No | `us-east-1` | AWS region |
| `S3_ENDPOINT` | No | — | Custom endpoint (for rustfs) |
| `AWS_ACCESS_KEY_ID` | No | — | AWS credentials (only needed for upload, not SDK reads) |
| `AWS_SECRET_ACCESS_KEY` | No | — | AWS credentials (only needed for upload, not SDK reads) |

The bucket must be configured for **public-read** access so the Python SDK can fetch
data without credentials. For AWS S3, use a bucket policy. For rustfs, set the bucket
access policy to public after creation.

## Docker Compose: rustfs Service

rustfs is a high-performance, S3-compatible object storage built in Rust (Apache 2.0).
Used for local testing.

```yaml
  rustfs:
    image: rustfs/rustfs:latest
    container_name: aipriceaction-rustfs
    ports:
      - "9000:9000"   # S3 API
      - "9001:9001"   # Web console
    environment:
      - RUSTFS_ADDRESS=0.0.0.0:9000
      - RUSTFS_CONSOLE_ADDRESS=0.0.0.0:9001
      - RUSTFS_CONSOLE_ENABLE=true
      - RUSTFS_ACCESS_KEY=rustfsadmin
      - RUSTFS_SECRET_KEY=rustfsadmin
      - RUSTFS_UNSAFE_BYPASS_DISK_CHECK=true
    volumes:
      - rustfs_data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "sh", "-c",
        "curl -f http://127.0.0.1:9000/health && curl -f http://127.0.0.1:9001/rustfs/console/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  rustfs-permfix:
    image: alpine
    volumes:
      - rustfs_data:/data
    command: sh -c "chown -R 10001:10001 /data && echo 'Permissions fixed' && exit 0"
    restart: "no"
```

The `aipriceaction` service adds S3 env vars and `depends_on: rustfs: condition: service_healthy`.

## Dependencies

```toml
# Cargo.toml
rust-s3 = { version = "0.37.1", default-features = false, features = ["tokio-rustls-tls", "with-tokio"] }
```

Uses `rust-s3` with `tokio-rustls-tls` — lightweight S3 client with rustls TLS,
consistent with the rest of the project. No native-tls/OpenSSL dependency.

Both `sha2` and `hex` are already in Cargo.toml (used by other modules).

## Files

### Created

| File | Purpose |
|---|---|
| `src/workers/s3_archive.rs` | Worker loop: bootstrap + incremental upload |
| `src/queries/s3_archive.rs` | Query functions: daily OHLCV, fingerprint, data ranges, tickers |

### Modified

| File | Change |
|---|---|
| `Cargo.toml` | Add `rust-s3`, `aws-creds`, `http` |
| `src/constants.rs` | Add `s3_archive` config module |
| `src/queries/mod.rs` | Add `pub mod s3_archive;` |
| `src/workers/mod.rs` | Add `pub mod s3_archive;` |
| `src/cli.rs` | Spawn worker when `S3_ARCHIVE_WORKER=true` |
| `.env.example` | Add S3_ARCHIVE_WORKER, S3_BUCKET, S3_REGION, S3_ENDPOINT, AWS creds |
| `docker-compose.yml` | Add rustfs service + permfix, update aipriceaction env/depends |

---

# Python SDK (`sdk/aipriceaction-python/`)

Reads archived data via plain HTTP. No S3 protocol, no boto3, no credentials needed.
The S3 bucket is configured for public-read access — any HTTP client works.

## How it works

The Rust worker uploads CSV files to S3. The bucket is set to public-read, so all
objects are accessible via plain HTTP GET/HEAD URLs. The Python SDK only needs a
**base URL** — no AWS credentials, no boto3, no S3 protocol knowledge.

```bash
# rustfs (local)
http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-01.csv

# AWS S3 (production)
https://aipriceaction-archive.s3.us-east-1.amazonaws.com/ohlcv/vn/VCB/1D/VCB-1D-2025-04-01.csv
```

The `x-amz-meta-content-hash` header is included in HTTP HEAD/GET responses — the
SDK uses `requests.head()` to check if data has changed without downloading.

## Project Structure

```
sdk/aipriceaction-python/
  pyproject.toml
  src/
    aipriceaction/
      __init__.py
      client.py        # Main AIPriceAction class
      models.py        # TickerInfo dataclass
      exceptions.py    # AIPriceActionError
      indicators.py    # SMA/EMA calculation (ported from Rust backend)
  tests/
    conftest.py       # Fixtures (mock_s3, mock_s3_ma)
    test_client.py    # 44 tests
```

## Public API

`get_ohlcv()` mirrors the `/tickers` REST API endpoint parameters:

```python
from aipriceaction import AIPriceAction

# base_url + optional cache_dir for local disk caching
client = AIPriceAction(
    "http://localhost:9000/aipriceaction-archive",
    cache_dir="./cache",
)

# Ticker metadata (HTTP GET meta/tickers.json, cached in memory + disk)
tickers = client.get_tickers()
tickers = client.get_tickers(source="vn")

# OHLCV data — mirrors /tickers endpoint
df = client.get_ohlcv("VCB", interval="1D")                        # single ticker, last 365 days
df = client.get_ohlcv(tickers=["VCB", "FPT"], interval="1D")        # multiple tickers
df = client.get_ohlcv(ticker="VCB", interval="1D", limit=100)       # limit rows per ticker
df = client.get_ohlcv(ticker="VCB", start_date="2025-01-01", end_date="2025-04-30")
df = client.get_ohlcv(ticker=None, interval="1D", source="crypto")  # all crypto tickers

# MA indicators (ma=True is default, matching /tickers endpoint)
df = client.get_ohlcv("VCB", interval="1D", ma=True)                # SMA (default)
df = client.get_ohlcv("VCB", interval="1D", ema=True)               # EMA instead of SMA
df = client.get_ohlcv("VCB", interval="1D", ma=False)               # no indicators

# Download CSVs to local folder
paths = client.download_csv("VCB", interval="1D",
    start_date="2025-04-01", end_date="2025-04-30", output_dir="./data")

# Check if data changed without downloading (HTTP HEAD)
hash = client.get_content_hash("VCB", "1D", "2025-04-01")
# -> "a1b2c3..." or None if file doesn't exist
```

### `get_ohlcv` parameters

| Param | Type | Default | Description |
|---|---|---|---|
| `ticker` | `str` or `None` | `None` | Single symbol, or `None` for all tickers |
| `tickers` | `list[str]` or `None` | `None` | Multiple symbols (mutually exclusive with `ticker`) |
| `interval` | `str` | `"1D"` | `"1D"`, `"1h"`, `"1m"` (native intervals stored in S3) |
| `limit` | `int` or `None` | `None` | Max rows per ticker (applied after fetching) |
| `start_date` | `str`/`date`/`datetime` | 365 days ago | Start date (inclusive) |
| `end_date` | `str`/`date`/`datetime` | today | End date (inclusive) |
| `source` | `str` or `None` | `None` | Override auto-detection (`"vn"`, `"yahoo"`, `"crypto"`, `"sjc"`) |
| `ma` | `bool` | `True` | Calculate MA indicators and scores (fetches 400 extra days for buffer) |
| `ema` | `bool` | `False` | Use EMA instead of SMA when `ma=True` |

### MA indicator columns (when `ma=True`)

When `ma=True`, the returned DataFrame includes these additional columns (matching
the Rust `/tickers` response): `ma10`, `ma20`, `ma50`, `ma100`, `ma200`,
`ma10_score`, `ma20_score`, `ma50_score`, `ma100_score`, `ma200_score`,
`close_changed`, `volume_changed`, `total_money_changed`.

MA score formula: `((close - ma) / ma) * 100`

SMA/EMA calculations are ported from the Rust backend (`src/models/indicators.rs`)
and produce identical results.

## URL Building

The SDK builds public URLs for date ranges:
```
{base_url}/ohlcv/{source}/{ticker}/{interval}/{ticker}-{interval}-{YYYY}-{MM}-{DD}.csv
```

Source auto-detection uses `tickers.json` to resolve ticker symbols. Priority:
vn > yahoo > sjc > crypto (matches Rust's `resolve_ticker_sources`).

Missing days (HTTP 404) are silently skipped.

## TickerInfo Model

```python
@dataclass
class TickerInfo:
    source: str
    ticker: str
    name: Optional[str] = None
    exchange: Optional[str] = None   # HOSE, HNX, UPCOM (vn only)
    type: Optional[str] = None       # stock, index, etc.
    category: Optional[str] = None   # Commodity, US Index, etc.
    group: Optional[str] = None      # NGAN_HANG, CONG_NGHE, etc.
```

## Behaviors

- **Source auto-detection**: If `source` not specified, auto-detects from `tickers.json`.
  Priority: vn > yahoo > sjc > crypto.
- **Default date range**: `start_date` defaults to 365 days ago, `end_date` to today.
- **MA buffer**: When `ma=True`, fetches 400 extra days before `start_date` to warm the
  MA-200 buffer, then trims results to the user's requested range.
- **Ticker metadata caching**: First `get_tickers()` fetches via HTTP, cached in memory
  and on disk. Pass `use_cache=False` to force re-fetch.
- **404 handling**: Silently skips days that don't have data yet.
- **Content-hash check**: `get_content_hash()` uses HTTP HEAD to read `x-amz-meta-content-hash`
  header — zero bytes downloaded.
- **Aggregated intervals**: `5m`, `15m`, `30m`, `4h`, `1W`, `2W`, `1M` are not stored in S3
  and will raise `ValueError`. Only native intervals `1D`, `1h`, `1m` are available.

## Config

```python
# Local rustfs
client = AIPriceAction(
    "http://localhost:9000/aipriceaction-archive",
    cache_dir="./cache",  # optional, defaults to temp dir
)

# Production AWS S3
client = AIPriceAction(
    "https://aipriceaction-archive.s3.us-east-1.amazonaws.com",
)
```

No environment variables required. No credentials. Just a URL.

## Dependencies

```toml
[project]
dependencies = [
    "requests>=2.28",
    "pandas>=2.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "responses>=0.25",
]
```

Minimal dependencies — `requests` for HTTP, `pandas` for DataFrame output.
No boto3, no botocore, no AWS SDK.
