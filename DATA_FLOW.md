# Data Flow

Visual guide to how the Python SDK fetches historical data from S3, live data from the REST API, and merges them into a single DataFrame.

---

## The Two-Tier Architecture

```
  User calls get_ohlcv("VCB", interval="1D", start_date="2024-01-01")

                          ┌─────────────────────────────┐
                          │         get_ohlcv()          │
                          └──────────┬──────────────────┘
                                     │
                          ┌──────────┴──────────┐
                          v                      v
                   ┌─────────────┐        ┌──────────────┐
                   │  S3 Archive  │        │  Live REST   │
                   │ (historical) │        │   API        │
                   └──────┬──────┘        └──────┬───────┘
                          │                       │
                          v                       v
                   Daily + yearly CSVs       /tickers?interval=1D
                   cached locally            &ma=true
                          │                       │
                          └───────────┬───────────┘
                                      v
                              ┌───────────────┐
                              │  Merge live   │
                              │  on top of S3 │
                              └───────┬───────┘
                                      v
                              Final DataFrame
```

- **S3 archive**: complete historical data, stored as CSV files, cached on disk
- **Live API**: today's data (and recent bars), computed in real-time by the Rust backend

---

## S3 Archive — How Historical Data is Stored

### File structure on S3

```
s3://aipriceaction/
  ohlcv/
    vn/
      VCB/
        1D/
          VCB-1D-2024-01-15.csv       <- daily file
          VCB-1D-2024-01-16.csv
          ...
        1m/
          VCB-1m-2024-01-15.csv
          ...
        yearly/
          VCB-1D-2024.csv              <- yearly aggregate
          VCB-1h-2024.csv
    crypto/
      BTCUSDT/
        1D/
          BTCUSDT-1D-2024-01-15.csv
        yearly/
          BTCUSDT-1D-2024.csv
```

### Daily CSV format

No header row. Columns: `time, open, high, low, close, volume`

```
2024-01-15, 80500, 81200, 80300, 80900, 12500000
2024-01-16, 80900, 81500, 80700, 81200, 11800000
```

### Local disk cache

Every CSV fetched from S3 is cached locally to avoid re-downloading.
Default location: `$TMPDIR/aipriceaction-s3-cache/` (customizable via `cache_dir` parameter).

```
$TMPDIR/aipriceaction-s3-cache/
  vn/
    VCB/
      1D/
        VCB-1D-2024-01-15.csv         <- cached copy
        VCB-1D-2024-01-15.csv.hash    <- server content hash
      yearly/
        VCB-1D-2024.csv
        VCB-1D-2024.csv.hash
```

### Cache freshness check

Each cached CSV has a `.hash` sidecar file storing the S3 object's content hash.
Before re-downloading, the SDK checks if the cache is still fresh:

```
  Is cached file fresh?

  ┌─────────────────────────────────────────────────┐
  │ Within TTL (default 5 min)?                     │
  │   YES -> return True (skip HEAD request)         │
  │   NO  -> continue to server check                │
  └─────────────────┬───────────────────────────────┘
                    v
  ┌─────────────────────────────────────────────────┐
  │ HEAD request to S3: get x-amz-meta-content-hash │
  │ Compare with local .hash file                    │
  │                                                  │
  │   Hash matches -> update TTL timestamp, fresh!   │
  │   Hash differs  -> stale, need to re-download    │
  │   HEAD fails    -> trust cache conservatively    │
  │   No .hash file -> write it, trust cache         │
  └─────────────────────────────────────────────────┘
```

This avoids downloading the full CSV on every call — a lightweight HEAD request (checking the hash header) is enough to know if the file changed.

---

## Fetch Strategy — Smart Yearly + Daily Fallback

The SDK doesn't blindly fetch one file per day. It uses a smarter strategy for `1D` and `1h` intervals.

### For 1D and 1h intervals

```
  Request: VCB, 1D, 2023-06-01 to 2024-01-15

  Step 1: Try yearly files first
  ┌────────────────────────────────────────────────────┐
  │  VCB-1D-2024.csv   <- covers Jan-Dec 2024         │
  │  VCB-1D-2023.csv   <- covers Jan-Dec 2023         │
  └────────────────────────────────────────────────────┘

  Step 2: Check if yearly files cover the full range
  ┌─────────────────────────────────────────────────────┐
  │  Yearly files cover up to 2024-01-14               │
  │  But today is 2024-01-15 and it's not in yearly yet │
  └─────────────────────────────────────────────────────┘

  Step 3: Fetch only the uncovered tail as daily files
  ┌─────────────────────────────────────────────────────┐
  │  VCB-1D-2024-01-15.csv  <- just today              │
  └─────────────────────────────────────────────────────┘

  Step 4: Concat + deduplicate + sort
  ┌─────────────────────────────────────────────────────┐
  │  2023.csv + 2024.csv + 2024-01-15.csv              │
  │  drop_duplicates(time) -> sort by time             │
  └─────────────────────────────────────────────────────┘
```

This means requesting a 2-year range makes **3 requests** (2 yearly + 1 daily), not **500+**.

### For 1m and other intervals

Minute data is too granular for yearly files. The SDK fetches daily CSVs **backwards** from the newest date, stopping as soon as it has enough rows:

```
  Request: VCB, 1m, need 200 rows

  Day 2024-01-15  <- fetch (got 225 rows) ... enough! stop.

  vs. naive approach:

  Day 2024-01-15  <- fetch
  Day 2024-01-14  <- fetch
  Day 2024-01-13  <- fetch  (waste — already have enough)
  ...
```

It also stops after a streak of consecutive 404s (weekends, holidays):

```
  VN stocks:    stop after 7 consecutive misses (weekends + holidays)
  Crypto (24/7): stop after 14 consecutive misses
```

---

## Parallel Fetching

When requesting multiple tickers, the SDK fetches them concurrently:

```
  get_ohlcv(["VCB", "FPT", "MWG"], interval="1D")

  Thread 1:  VCB  ──> S3 fetch ──> DataFrame
  Thread 2:  FPT  ──> S3 fetch ──> DataFrame    <- all in parallel
  Thread 3:  MWG  ──> S3 fetch ──> DataFrame
  Thread pool: 8 workers

  Result: pd.concat([VCB_df, FPT_df, MWG_df])
```

---

## Live REST API — Real-Time Data

### How it works

```
  Client                        Rust Backend
  ┌──────────┐                  ┌──────────────────────────────────┐
  │ fetch_   │  GET /tickers    │ Workers continuously sync        │
  │ live_    │ ───────────────> │ VCI/Binance data into PostgreSQL │
  │ data()   │  <---------------│                                  │
  │          │  JSON response   │ Query latest candles per ticker  │
  └──────────┘                  └──────────────────────────────────┘
```

### In-memory cache (no disk persistence)

The live response is cached in a Python dict for **120 seconds** to avoid hammering the API.
Unlike S3 data (which persists to disk with `.hash` sidecars), live data is **not written to disk** — a process restart means a full re-fetch.

```
  1st call:  fetch from API, store in self._live_cache dict, return
  2nd call:  (within 2 min) return from dict, no network call
  3rd call:  (after 2 min)  fetch fresh data, update dict
  Process restarts -> cache gone, next call hits the API again
```

### Limits per interval

The `limit` query parameter controls how many candles per ticker the API returns. Different intervals use different limits:

```
  Interval   limit   Why
  ────────   ─────   ──────────────────────────────
  1D         1       Only today's daily candle
  1h         5       Last 5 hourly candles
  1m         60      Last 60 one-minute candles
  5m         12      12 x 5min = 1 hour
  15m        4       4 x 15min = 1 hour
  30m        2       2 x 30min = 1 hour
  4h         6       6 x 4h = 24 hours
  1W         4       Last 4 weekly candles
  2W         2       Last 2 bi-weekly candles
```

Daily data only needs 1 candle (today). Finer intervals need more to cover recent activity.

### The `ma` parameter

The live API accepts `ma=true` or `ma=false`:

- **`ma=true`** (default): response includes MA scores, changes, and money flow — used by `context.py` for analysis
- **`ma=false`**: response returns only OHLCV columns — used by `get_ohlcv()` during the merge step (extra fields would be stripped anyway)

### Response format

```json
{
  "VCB": [
    {"time": "2024-01-15", "close": 80900, "volume": 12500000, "ma10": 79500, "ma10_score": 1.3, ...}
  ],
  "FPT": [
    {"time": "2024-01-15", "close": 152000, "volume": 8200000, "ma10": 149000, "ma10_score": 2.0, ...}
  ]
}
```

When `ma=true`, the response includes extra fields (MA lines, MA scores, changes, money flow) that S3 CSVs don't have.
The merge process (`_merge_live_data`) strips these down to pure OHLCV before combining with historical data.

---

## The Merge — S3 + Live

This is where the two data sources combine. Live data always wins — it's fresher.

### Merge algorithm

```
  For each ticker:

  S3 data (historical):           Live data (today):

  Jan 10: 80,000                  Jan 14: 81,500   <- today's close updated
  Jan 11: 80,500                  Jan 15: 82,000   <- brand new candle
  Jan 12: 79,800
  Jan 13: 81,000
  Jan 14: 81,200                  <- stale (will be replaced)

  Step 1: Find overlapping times
  ┌──────────────────────────────────────────────┐
  │  Overlap: Jan 14 exists in both S3 and live  │
  └──────────────────────────────────────────────┘

  Step 2: Remove S3 rows that overlap with live
  ┌──────────────────────────────────────────────┐
  │  S3 Jan 14 (81,200) -> DROPPED               │
  │  S3 Jan 10, 11, 12, 13 -> KEPT               │
  └──────────────────────────────────────────────┘

  Step 3: Drop live candles older than oldest S3 row
  ┌──────────────────────────────────────────────┐
  │  Live Jan 14 and Jan 15 are >= Jan 10 -> KEEP│
  └──────────────────────────────────────────────┘

  Step 4: Concat and sort
  ┌──────────────────────────────────────────────┐
  │  Jan 10: 80,000  (S3)                        │
  │  Jan 11: 80,500  (S3)                        │
  │  Jan 12: 79,800  (S3)                        │
  │  Jan 13: 81,000  (S3)                        │
  │  Jan 14: 81,500  (LIVE)  <- replaced         │
  │  Jan 15: 82,000  (LIVE)  <- new              │
  └──────────────────────────────────────────────┘
```

### Time normalization

S3 and live use slightly different time formats. Before comparing, both get normalized:

```
  S3 format:    "2024-01-15 00:00:00"    -> "2024-01-15"
  S3 format:    "2024-01-15T14:00:00"    -> "2024-01-15 14:00:00"
  Live format:  "2024-01-15"             -> "2024-01-15"
  Live format:  "2024-01-15T14:00:00"    -> "2024-01-15 14:00:00"
```

Stripping " 00:00:00" ensures a 1D candle from S3 matches the same day from live.

### Edge case: no S3 data

If the ticker has no historical data on S3, the result is built purely from live data:

```
  S3: empty
  Live: [Jan 14, Jan 15]

  Result: just Jan 14 and Jan 15 from live
```

### Edge case: no live data

If the live API is down or the ticker isn't tracked, S3 data passes through unchanged:

```
  S3: [Jan 10, Jan 11, Jan 12]
  Live: empty or timeout

  Result: just S3 data, no merge needed
```

---

## Full Flow: get_ohlcv()

```
  get_ohlcv("VCB", interval="1D", start_date="2024-01-01", ma=True)

  ┌─────────────────────────────────────────────────────┐
  │ 1. Resolve ticker -> (source, symbol)               │
  │    "VCB" -> ("vn", "VCB")                           │
  │                                                     │
  │ 2. Expand start_date for MA buffer                  │
  │    User wants from Jan 1                             │
  │    MA200 needs ~280 days before that                │
  │    Actual fetch starts ~Apr 2023                    │
  │                                                     │
  │ 3. Generate list of dates to fetch                  │
  │    Apr 2023 ... Jan 15 2024                         │
  │                                                     │
  │ 4. Fetch S3 data (yearly + daily fallback)          │
  │    VCB-1D-2023.csv + VCB-1D-2024.csv + today.csv   │
  │                                                     │
  │ 5. Aggregate if needed                              │
  │    e.g. 1m -> 5m, 15m, 1h, etc.                    │
  │                                                     │
  │ 6. Fetch live data (if use_live=True)               │
  │    GET /tickers?interval=1D&ma=true                 │
  │                                                     │
  │ 7. Merge live on top of S3                          │
  │    Overwrite overlapping times, append new candles  │
  │                                                     │
  │ 8. Compute MA indicators if ma=True                 │
  │    Add ma10..ma200, ma10_score..ma200_score columns │
  │                                                     │
  │ 9. Trim to user's original date range               │
  │    Drop the MA buffer rows before Jan 1             │
  │                                                     │
  │ 10. Return DataFrame                                │
  └─────────────────────────────────────────────────────┘
```

---

## Aggregation — Derived Intervals

Some intervals don't exist on S3. They're computed on-the-fly from base intervals:

```
  Base intervals (stored on S3):   1m, 1h, 1D
  Derived intervals (aggregated):  5m, 15m, 30m, 2h, 4h, 1W, 2W, 1M

  Example: 1m -> 15m aggregation

  1m candles:                                       15m candle:
  09:00  O=100 H=102 L=99  C=101 V=5000            O=100
  09:01  O=101 H=103 L=100 C=102 V=4500            H=105  (max of all highs)
  ...                                               L=98   (min of all lows)
  09:14  O=103 H=105 L=101 C=104 V=6200            C=104  (last close)
                                                    V=78000 (sum of all volumes)
```

Aggregation happens **after** S3 fetch, **before** live merge. So even derived intervals get live data overlayed.

---

## Usage

```python
from aipriceaction import AIPriceAction

client = AIPriceAction()

# Historical only (S3)
df = client.get_ohlcv("VCB", interval="1D", start_date="2024-01-01")

# Historical + live (default)
df = client.get_ohlcv("VCB", interval="1D", start_date="2024-01-01")

# Multiple tickers at once
df = client.get_ohlcv(["VCB", "FPT", "MWG"], interval="1D")

# Derived interval (aggregated from 1m)
df = client.get_ohlcv("VCB", interval="15m", start_date="2024-01-15")

# With MA indicators
df = client.get_ohlcv("VCB", interval="1D", ma=True)
```

---

## File Reference

| File | Purpose |
|---|---|
| `sdk/aipriceaction-python/src/aipriceaction/client.py` | `get_ohlcv()`, `fetch_live_data()`, `_merge_live_data()`, S3 fetch logic |
| `sdk/aipriceaction-python/src/aipriceaction/aggregator.py` | Client-side OHLCV aggregation for derived intervals |
| `sdk/aipriceaction-python/src/aipriceaction/indicators.py` | MA and change metric computation |
| `src/server/api.rs` | Rust REST API serving live data |
| `src/workers/` | Rust background workers syncing data from VCI/Binance into PostgreSQL |
