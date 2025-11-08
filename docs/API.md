# API Documentation

Complete API reference for the aipriceaction server.

## Table of Contents

- [Overview](#overview)
- [Base URL](#base-url)
- [Endpoints](#endpoints)
  - [GET /tickers](#get-tickers)
  - [GET /health](#get-health)
  - [GET /tickers/group](#get-tickersgroup)
  - [GET /raw/*](#get-raw)
- [Data Formats](#data-formats)
- [Examples](#examples)

---

## Overview

The aipriceaction API provides access to Vietnamese stock market data with technical indicators. All data is served from an in-memory cache updated periodically.

**Features:**
- Real-time stock data with technical indicators
- Multiple time intervals (daily, hourly, minute)
- VCI-compatible time format
- Optional legacy price format for backward compatibility
- JSON and CSV output formats

---

## Base URL

```
http://localhost:3000
```

---

## Endpoints

### GET /tickers

Query stock data with optional filters.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `symbol` | string[] | No | All tickers | Ticker symbols to query. Can be repeated: `symbol=VCB&symbol=FPT` |
| `interval` | string | No | `1D` | Time interval: **Base intervals:** `1D` (daily), `1H` (hourly), `1m` (minute). **Aggregated intervals:** `5m`, `15m`, `30m` (minute aggregations), `1W` (weekly), `2W` (bi-weekly), `1M` (monthly) |
| `start_date` | string | No | Yesterday | Start date filter (YYYY-MM-DD format) |
| `end_date` | string | No | Today | End date filter (YYYY-MM-DD format) |
| `limit` | number | No | None | Limit number of records to return (works with `end_date` to get N rows back in history). Ignored if `start_date` is provided |
| `legacy` | boolean | No | `false` | If true, divides stock prices by 1000 (old proxy compatibility) |
| `format` | string | No | `json` | Response format: `json` or `csv` |
| `cache` | boolean | No | `true` | Use memory cache (default). Set to `false` to force disk read and bypass TTL cache |

#### Default Behavior

**When no dates are specified:**
- Returns only **today's data** (last trading day)
- This matches the production API's default behavior for drop-in compatibility

**When dates are specified:**
- Returns data for the specified date range

#### Response Format (JSON)

```json
{
  "TICKER": [
    {
      "time": "2025-11-05",           // YYYY-MM-DD for daily
      "open": 60000.0,                 // Full VND prices (unless legacy=true)
      "high": 61100.0,
      "low": 59900.0,
      "close": 60800.0,
      "volume": 4601700,
      "symbol": "VCB",

      // Technical Indicators (optional, when available)
      "ma10": 59960.0,                 // 10-period moving average
      "ma20": 60970.0,                 // 20-period moving average
      "ma50": 62971.0,                 // 50-period moving average
      "ma10_score": 1.4009,            // ((close - ma10) / ma10) * 100
      "ma20_score": -0.2788,           // ((close - ma20) / ma20) * 100
      "ma50_score": -3.4476,           // ((close - ma50) / ma50) * 100
      "close_changed": 1.5234,         // Percentage change from previous close
      "volume_changed": -10.2341       // Percentage change from previous volume
    }
  ]
}
```

#### Time Format by Interval

| Interval | Time Format | Example |
|----------|-------------|---------|
| `1D` (daily) | `YYYY-MM-DD` | `2025-11-05` |
| `1H` (hourly) | `YYYY-MM-DD HH:MM:SS` | `2025-11-05 09:30:00` |
| `1m` (minute) | `YYYY-MM-DD HH:MM:SS` | `2025-11-05 09:30:00` |
| `5m` (5-minute) | `YYYY-MM-DD HH:MM:SS` | `2025-11-05 09:30:00` |
| `15m` (15-minute) | `YYYY-MM-DD HH:MM:SS` | `2025-11-05 09:30:00` |
| `30m` (30-minute) | `YYYY-MM-DD HH:MM:SS` | `2025-11-05 09:30:00` |
| `1W` (weekly) | `YYYY-MM-DD` | `2025-11-04` (Monday) |
| `2W` (bi-weekly) | `YYYY-MM-DD` | `2025-11-04` (Monday) |
| `1M` (monthly) | `YYYY-MM-DD` | `2025-11-01` (1st day) |

**Note:** All times are in UTC timezone.

#### Aggregated Intervals

Aggregated intervals provide OHLCV data computed from base intervals:

**Minute-based aggregations** (computed from 1m data):
- `5m`: 5-minute candles (5 × 1m records per candle)
- `15m`: 15-minute candles (15 × 1m records per candle)
- `30m`: 30-minute candles (30 × 1m records per candle)

**Day-based aggregations** (computed from 1D data):
- `1W`: Weekly candles (Monday to Sunday, ~5-7 trading days)
- `2W`: Bi-weekly candles (even/odd week grouping)
- `1M`: Monthly candles (calendar month boundaries)

**Aggregation logic:**
- **Time**: Bucket start time (5m/15m/30m boundaries, Monday for weeks, 1st for months)
- **Open**: First record's open price in the bucket
- **High**: Maximum high across all records
- **Low**: Minimum low across all records
- **Close**: Last record's close price in the bucket
- **Volume**: Sum of volumes across all records
- **MA indicators**: Last record's values (end-of-period state)
- **MA scores**: Last record's values
- **close_changed / volume_changed**: Set to `null` (not applicable for aggregated data)

#### Legacy Price Format

When `legacy=true`:
- **Stock tickers** (VCB, FPT, etc.): Prices divided by 1000
  - Example: `60800.0` → `60.8`
- **Market indices** (VNINDEX, VN30): Prices unchanged
  - Example: `1250.5` → `1250.5`
- **MA values**: Divided by 1000 for stocks, unchanged for indices
- **MA scores**: Unchanged (always percentages)

#### CSV Format

When `format=csv`, returns CSV with the following structure:

```csv
symbol,time,open,high,low,close,volume,ma10,ma20,ma50,ma10_score,ma20_score,ma50_score,close_changed,volume_changed
VCB,2025-11-05,60000.0,61100.0,59900.0,60800.0,4601700,59960.0,60970.0,62971.0,1.4009,-0.2788,-3.4476,1.5234,-10.2341
```

**Headers:**
- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="tickers_1D.csv"`

#### Examples

**Get today's VCB stock data (default):**
```bash
curl "http://localhost:3000/tickers?symbol=VCB"
```

**Get hourly data for multiple stocks:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&symbol=FPT&interval=1H"
```

**Get historical daily data:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&start_date=2025-01-01&end_date=2025-12-31"
```

**Get last 5 trading days before a specific date:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&end_date=2024-06-15&limit=5"
```

**Get last 10 trading days (using limit with today's date):**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&limit=10"
```

**Get 5-minute candles:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&interval=5m&limit=20"
```

**Get weekly candles (last 10 weeks):**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&interval=1W&limit=10"
```

**Get monthly candles for 2024:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&interval=1M&start_date=2024-01-01&end_date=2024-12-31"
```

**Get data in legacy price format:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&legacy=true"
```

**Get all tickers for last day:**
```bash
curl "http://localhost:3000/tickers"
```

**Export to CSV:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&format=csv" -o VCB.csv
```

**Force disk read (bypass memory cache):**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&cache=false"
```

---

### GET /health

Health check endpoint with system statistics.

#### Response Format

```json
{
  // Worker statistics
  "daily_last_sync": "2025-11-05T13:06:31.605383+00:00",
  "hourly_last_sync": null,
  "minute_last_sync": null,
  "daily_iteration_count": 1,
  "slow_iteration_count": 0,

  // Trading hours info
  "is_trading_hours": false,
  "trading_hours_timezone": "Asia/Ho_Chi_Minh",

  // Memory statistics
  "memory_usage_bytes": 2912724120,
  "memory_usage_mb": 2777.79,
  "memory_limit_mb": 4096,
  "memory_usage_percent": 67.82,

  // Ticker statistics
  "total_tickers_count": 282,
  "active_tickers_count": 282,
  "daily_records_count": 69702,
  "hourly_records_count": 314313,
  "minute_records_count": 8748154,

  // Disk cache statistics (for hourly/minute/historical data)
  "disk_cache_entries": 13,
  "disk_cache_size_bytes": 264707384,
  "disk_cache_size_mb": 252.52,
  "disk_cache_limit_mb": 500,
  "disk_cache_usage_percent": 50.50,

  // System info
  "uptime_secs": 101,
  "current_system_time": "2025-11-05T13:08:03.087470+00:00"
}
```

#### Example

```bash
curl "http://localhost:3000/health"
```

---

### GET /tickers/group

Get ticker groups from `ticker_group.json`.

#### Response Format

```json
{
  "VN30": ["VCB", "VIC", "VHM", "VNM", ...],
  "BANKING": ["VCB", "CTG", "BID", "TCB", ...],
  "TECH": ["FPT", "CMG", ...],
  ...
}
```

#### Example

```bash
curl "http://localhost:3000/tickers/group"
```

---

### GET /raw/*

**⚠️ LEGACY ENDPOINT - Will be removed in future versions**

GitHub proxy endpoint for backward compatibility. Proxies requests to the GitHub raw data repository.

#### Path Parameter

| Parameter | Type | Description |
|-----------|------|-------------|
| `*path` | string | Path to file in GitHub repository |

#### Base Repository

```
https://raw.githubusercontent.com/quanhua92/aipriceaction-data/refs/heads/main/
```

#### Behavior

- Simple pass-through proxy (no caching)
- Proxies to GitHub raw files
- Content-Type determined by file extension
- Cache-Control: max-age=30

#### Examples

```bash
# Proxy to GitHub CSV file
curl "http://localhost:3000/raw/data/VCB.csv"

# Equivalent GitHub URL:
# https://raw.githubusercontent.com/quanhua92/aipriceaction-data/refs/heads/main/data/VCB.csv
```

#### Supported Content Types

| Extension | Content-Type |
|-----------|-------------|
| `.csv` | `text/csv` |
| `.json` | `application/json` |
| `.txt` | `text/plain` |
| Other | `application/octet-stream` |

---

## Data Formats

### Price Format

**Default (legacy=false):**
- Stock prices in **full VND** (e.g., 60800.0 for 60,800 VND)
- Market indices unchanged (e.g., 1250.5)

**Legacy (legacy=true):**
- Stock prices **divided by 1000** (e.g., 60.8)
- Market indices unchanged (e.g., 1250.5)

### Market Indices

The following tickers are treated as indices (prices not divided when legacy=true):
- `VNINDEX` - Vietnam Stock Index
- `VN30` - VN30 Index
- Any ticker starting with `VN`

### Technical Indicators

| Indicator | Formula | Range | Description |
|-----------|---------|-------|-------------|
| `ma10` | SMA(10) | Price | 10-period simple moving average |
| `ma20` | SMA(20) | Price | 20-period simple moving average |
| `ma50` | SMA(50) | Price | 50-period simple moving average |
| `ma10_score` | `((close - ma10) / ma10) * 100` | % | Distance from MA10 |
| `ma20_score` | `((close - ma20) / ma20) * 100` | % | Distance from MA20 |
| `ma50_score` | `((close - ma50) / ma50) * 100` | % | Distance from MA50 |
| `close_changed` | `((curr_close - prev_close) / prev_close) * 100` | % | Price change from previous period |
| `volume_changed` | `((curr_volume - prev_volume) / prev_volume) * 100` | % | Volume change from previous period |

---

## Examples

### Get Today's Data

**Request:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB"
```

**Response:**
```json
{
  "VCB": [
    {
      "time": "2025-11-05",
      "open": 60000.0,
      "high": 61100.0,
      "low": 59900.0,
      "close": 60800.0,
      "volume": 4601700,
      "symbol": "VCB",
      "ma10": 59960.0,
      "ma20": 60970.0,
      "ma50": 62971.0,
      "ma10_score": 1.4009,
      "ma20_score": -0.2788,
      "ma50_score": -3.4476,
      "close_changed": 1.5234,
      "volume_changed": -10.2341
    }
  ]
}
```

### Get Hourly Data

**Request:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&interval=1H&start_date=2025-11-05"
```

**Response:**
```json
{
  "VCB": [
    {
      "time": "2025-11-05 02:00:00",
      "open": 59200.0,
      "high": 59600.0,
      "low": 59200.0,
      "close": 59400.0,
      "volume": 317700,
      "symbol": "VCB",
      "ma10": 62490.0,
      "ma20": 62865.0,
      "ma50": 63316.0,
      "ma10_score": -4.9448,
      "ma20_score": -5.5118,
      "ma50_score": -6.1849
    }
  ]
}
```

### Legacy Price Format

**Request:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&legacy=true"
```

**Response:**
```json
{
  "VCB": [
    {
      "time": "2025-11-05",
      "open": 60.0,           // Divided by 1000
      "high": 61.1,           // Divided by 1000
      "low": 59.9,            // Divided by 1000
      "close": 60.8,          // Divided by 1000
      "volume": 4601700,
      "symbol": "VCB",
      "ma10": 59.96,          // Divided by 1000
      "ma20": 60.97,          // Divided by 1000
      "ma50": 62.971,         // Divided by 1000
      "ma10_score": 1.4009,   // Unchanged (percentage)
      "ma20_score": -0.2788,  // Unchanged (percentage)
      "ma50_score": -3.4476   // Unchanged (percentage)
    }
  ]
}
```

### Multiple Tickers

**Request:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&symbol=FPT&symbol=VNM"
```

**Response:**
```json
{
  "VCB": [...],
  "FPT": [...],
  "VNM": [...]
}
```

### CSV Export

**Request:**
```bash
curl "http://localhost:3000/tickers?symbol=VCB&format=csv" -o VCB.csv
```

**Output file (VCB.csv):**
```csv
symbol,time,open,high,low,close,volume,ma10,ma20,ma50,ma10_score,ma20_score,ma50_score,close_changed,volume_changed
VCB,2025-11-04,59200.0,60400.0,59100.0,60100.0,2952400,59840.0,61160.0,63017.8,0.4345,-1.7332,-4.6301,1.5234,-10.2341
VCB,2025-11-05,60000.0,61100.0,59900.0,60800.0,4601700,59960.0,60970.0,62971.0,1.4009,-0.2788,-3.4476,1.1673,55.8934
```

---

## Error Responses

### 400 Bad Request

Invalid query parameters.

```json
{
  "error": "Invalid interval. Valid values: 1D, 1H, 1m, 5m, 15m, 30m, 1W, 2W, 1M (or daily, hourly, minute)"
}
```

```json
{
  "error": "Invalid start_date format. Expected YYYY-MM-DD"
}
```

### 404 Not Found

Endpoint not found.

### 502 Bad Gateway

GitHub proxy endpoint failed to fetch from GitHub.

```json
{
  "error": "Failed to fetch from GitHub: ..."
}
```

---

## Rate Limiting

Currently, there is **no rate limiting** implemented. All endpoints can be called without restrictions.

---

## CORS

The server allows requests from **any origin** with the following methods:
- GET
- POST
- OPTIONS

---

## Caching

The API implements a sophisticated multi-tier caching system for optimal performance.

### Cache Architecture

The server uses two cache layers:

1. **Memory Cache** (Daily data)
   - Stores last 1 year of daily data
   - Fast in-memory access
   - TTL: 60 seconds

2. **Disk Cache** (Hourly/Minute/Historical data)
   - Intelligent disk read caching
   - Configurable size limit (default: 500MB via `MAX_CACHE_SIZE_MB` env)
   - Per-item size limit: 100MB
   - LRU (Least Recently Used) eviction
   - TTL: 60 seconds

### Cache Behavior

| Cache Parameter | Description | Behavior |
|----------------|-------------|---------|
| `cache=true` (default) | Use cache | Daily: memory cache, Hourly/Minute: disk cache with TTL |
| `cache=false` | Bypass primary cache | All data read from disk (but may use disk cache for subsequent reads) |

### Disk Cache Details

The disk cache automatically stores frequently accessed data:

- **What gets cached:**
  - Hourly data (`interval=1H`)
  - Minute data (`interval=1m`)
  - Historical daily queries (`cache=false` or with date filters)

- **Size limits:**
  - Maximum total cache: 500MB (configurable via `MAX_CACHE_SIZE_MB`)
  - Maximum per-item: 100MB
  - Items larger than 100MB are not cached

- **Eviction policy:**
  - LRU-based: Oldest entries removed when cache is full
  - Automatic expiration after 60 seconds (TTL)

- **Performance impact:**
  - First request: Disk read + cache population
  - Subsequent requests: Served from cache (fast)
  - Cache hit logged as: "Cache hit for {ticker}/{interval}"

### TTL (Time To Live)

- **Memory cache TTL**: 60 seconds for daily data
- **Disk cache TTL**: 60 seconds for hourly/minute/historical data
- **Auto-refresh**: Cache automatically refreshes from disk when expired
- **Race condition prevention**: DataSync preserves existing indicators during updates

### Cache Statistics

Monitor cache performance via `/health` endpoint:

```json
{
  "disk_cache_entries": 13,           // Number of cached items
  "disk_cache_size_mb": 252.52,       // Total cache size
  "disk_cache_limit_mb": 500,         // Maximum cache size
  "disk_cache_usage_percent": 50.50   // Percentage used
}
```

### Configuration

Set the maximum disk cache size via environment variable:

```bash
# Default: 500MB
export MAX_CACHE_SIZE_MB=500

# Increase for better caching (more memory usage)
export MAX_CACHE_SIZE_MB=1000

# Decrease for lower memory usage
export MAX_CACHE_SIZE_MB=250
```

### Use Cases

**Use cache=true (default) for:**
- Normal API usage with best performance
- Multiple requests for same data within TTL window
- Real-time dashboard or charting applications
- Hourly/minute data queries (benefits from disk cache)

**Use cache=false for:**
- Getting absolute latest data after background worker updates
- Debugging data integrity issues
- Ensuring you have the most recent indicators
- First-time historical data queries (will populate disk cache)

### Examples

```bash
# Fast response from memory cache for daily data
curl "http://localhost:3000/tickers?symbol=VCB&interval=1D&cache=true"

# First request reads from disk and populates cache
curl "http://localhost:3000/tickers?symbol=VCB&interval=1H"

# Second request served from disk cache (fast)
curl "http://localhost:3000/tickers?symbol=VCB&interval=1H"

# Bypass primary cache but may use disk cache
curl "http://localhost:3000/tickers?symbol=VCB&cache=false"

# Monitor cache behavior in logs
docker logs aipriceaction | grep -E "(Cache hit|Cache miss|Cached)"

# Check cache statistics
curl "http://localhost:3000/health" | jq '{disk_cache_entries, disk_cache_size_mb, disk_cache_usage_percent}'
```

### Cache Headers

All responses include cache headers:

```
Cache-Control: max-age=30
```

Clients can cache responses for up to 30 seconds, independent of the internal TTL cache.

---

## Notes

1. **Data Updates:**
   - Daily data: Updated every 15 seconds
   - Hourly/Minute data: Updated every 5 minutes

2. **Memory Usage:**
   - The server loads the last 1 year of data into memory
   - Typical memory usage: ~2.8 GB for 282 tickers

3. **Timezone:**
   - All timestamps are in UTC
   - Vietnam is UTC+7

4. **Legacy Endpoint:**
   - The `/raw/*` endpoint is temporary and will be removed
   - Clients should migrate to using local data via `/tickers`

---

## Migration Guide

### From Old Proxy to New Server

1. **Change endpoint:**
   - Old: `/tickers?symbol=VCB&all=true`
   - New: `/tickers?symbol=VCB&start_date=2024-01-01`

2. **Price format:**
   - Add `&legacy=true` for old format (prices ÷ 1000)
   - Or update client to handle full VND prices

3. **Time format:**
   - Update client to parse VCI string format instead of unix timestamps
   - Daily: `"2025-11-05"` (parse as date)
   - Hourly/Minute: `"2025-11-05 09:30:00"` (parse as datetime)

4. **Default behavior:**
   - Old proxy: Returns all data by default
   - New server: Returns last 2 days by default
   - Add `start_date` parameter for historical data

---

## Support

For issues or questions, please create an issue at:
https://github.com/quanhua92/aipriceaction/issues
