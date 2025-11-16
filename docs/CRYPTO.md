# CryptoCompare API Integration Guide

This document describes the CryptoCompare API endpoints used for fetching cryptocurrency historical data.

## Overview

CryptoCompare provides three historical OHLCV data endpoints:
- **Daily**: `/data/v2/histoday` - Daily candles
- **Hourly**: `/data/v2/histohour` - Hourly candles
- **Minute**: `/data/v2/histominute` - Minute candles (7-day retention only)

**Base URL**: `https://min-api.cryptocompare.com`

## API Endpoints

### 1. Daily Historical Data

```
GET /data/v2/histoday
```

**Example**:
```
https://min-api.cryptocompare.com/data/v2/histoday?fsym=BTC&tsym=USD&limit=10
```

**Cache**: 610 seconds

### 2. Hourly Historical Data

```
GET /data/v2/histohour
```

**Example**:
```
https://min-api.cryptocompare.com/data/v2/histohour?fsym=BTC&tsym=USD&limit=10
```

**Cache**: 610 seconds

### 3. Minute Historical Data

```
GET /data/v2/histominute
```

**Example**:
```
https://min-api.cryptocompare.com/data/v2/histominute?fsym=BTC&tsym=USD&limit=10
```

**Cache**: 40 seconds
**Data Retention**: Only 7 days of minute data stored

## Request Parameters

### Common Parameters (All Endpoints)

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `fsym` | string | ✅ Yes | - | Cryptocurrency symbol (e.g., "BTC", "ETH") |
| `tsym` | string | ✅ Yes | - | Currency to convert to (e.g., "USD", "VND") |
| `e` | string | No | "CCCAGG" | Exchange to fetch from (or "CCCAGG" for aggregate) |
| `limit` | int | No | Varies* | Number of data points to return |
| `aggregate` | int | No | 1 | Time period aggregation factor |
| `tryConversion` | boolean | No | true | Try conversion through intermediary currencies |
| `aggregatePredictableTimePeriods` | boolean | No | true | Use predictable time slot alignment |
| `toTs` | timestamp | No | - | Returns data before this Unix timestamp |
| `explainPath` | boolean | No | false | Include conversion path details in response |
| `extraParams` | string | No | "NotAvailable" | Application identifier |
| `sign` | boolean | No | false | Cryptographically sign the response |

**Default `limit` values**:
- Daily: 30
- Hourly: 168 (7 days)
- Minute: 1440 (24 hours)

### Daily-Only Parameter

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `allData` | boolean | No | false | Returns all available historical data (only on `/histoday`) |

## Parameter Details

### `fsym` (required)
The cryptocurrency symbol of interest.
- Min length: 1, Max length: 30
- Examples: "BTC", "ETH", "SOL", "XRP"

### `tsym` (required)
The currency symbol to convert into.
- Min length: 1, Max length: 30
- Examples: "USD", "EUR", "VND", "JPY"

### `e` (exchange)
The exchange to obtain data from.
- Default: "CCCAGG" (CryptoCompare aggregate across multiple exchanges)
- Can specify specific exchanges: "Binance", "Coinbase", "Kraken", etc.
- Min length: 2, Max length: 30

### `aggregate`
Time period aggregation factor.
- For daily: `aggregate=7` means weekly candles (1 point per 7 days)
- For hourly: `aggregate=4` means 4-hour candles
- For minute: `aggregate=5` means 5-minute candles
- **Constraint**: `limit * aggregate <= 2000` (API enforces this)

### `aggregatePredictableTimePeriods`
Controls time slot alignment when using `aggregate > 1`.

**If `true` (default)**:
- Time slots are fixed/predictable
- Example: With `aggregate=2`, slots are always 8am, 10am, 12pm, 2pm, etc.

**If `false`**:
- Time slots based on current call time
- Example: If called at 1:30pm with `aggregate=2`, slots might be 9am, 11am, 1pm

### `limit`
Number of data points to return.
- Returns `limit + 1` points (includes current point)
- **Max constraint**: `limit * aggregate <= 2000`
- Example: `limit=1000` with `aggregate=4` will be reduced to 500 points

### `toTs` (timestamp)
Unix timestamp in seconds to fetch data before.
- Used for pagination when fetching full history
- Pattern: `limit=2000&toTs={earliest_timestamp_from_previous_call}`

**Example pagination**:
```
1. /histoday?fsym=BTC&tsym=USD&limit=2000
2. Get earliest timestamp from response (e.g., 1640000000)
3. /histoday?fsym=BTC&tsym=USD&limit=2000&toTs=1640000000
4. Repeat until all data fetched
```

### `tryConversion`
Only valid when `e=CCCAGG`.
- If `false`: Only fetch direct trading pairs (BTC/USD must exist directly)
- If `true`: API will convert through intermediary pairs (BTC→ETH→USD if needed)

### `explainPath`
If `true`, response includes conversion path calculation details.
- Used for debugging/verification
- Shows how price was calculated (direct vs multi-hop conversion)
- Note: Manual recalculation may not match exactly due to caching

### `extraParams`
Application identifier string.
- Recommended to send your app name (like User-Agent)
- Min length: 1, Max length: 2000

### `sign`
If `true`, server cryptographically signs the response.
- Used for smart contracts requiring verifiable on-chain data
- Default: false

### `allData` (daily only)
If `true`, returns ALL available historical data.
- Ignores `limit` parameter
- Only available on `/histoday` endpoint
- Not available for hourly/minute (too much data)

## Response Format

### Success Response Structure

```json
{
  "Response": "Success",
  "Message": "",
  "HasWarning": false,
  "Type": 100,
  "RateLimit": {},
  "Data": {
    "Aggregated": false,
    "TimeFrom": 1640000000,
    "TimeTo": 1640864000,
    "Data": [
      {
        "time": 1640000000,
        "high": 47000.50,
        "low": 46000.25,
        "open": 46500.00,
        "close": 46800.00,
        "volumefrom": 12345.67,
        "volumeto": 578900000.00,
        "conversionType": "direct",
        "conversionSymbol": ""
      }
    ]
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `time` | int | Unix timestamp in seconds |
| `open` | float | Opening price |
| `high` | float | Highest price |
| `low` | float | Lowest price |
| `close` | float | Closing price |
| `volumefrom` | float | Trading volume in base currency (cryptocurrency) |
| `volumeto` | float | Trading volume in quote currency (USD, etc.) |
| `conversionType` | string | "direct" or conversion method used |
| `conversionSymbol` | string | Intermediate symbol if conversion used (empty for direct) |

### Error Response

```json
{
  "Response": "Error",
  "Message": "Rate limit exceeded",
  "HasWarning": false,
  "Type": 99,
  "RateLimit": {
    "calls_made": {
      "second": 6,
      "minute": 6,
      "hour": 6,
      "day": 41,
      "month": 41
    },
    "calls_left": {
      "second": -1,
      "minute": 294,
      "hour": 2994,
      "day": 7459,
      "month": 49959
    }
  },
  "Data": {}
}
```

**Error Type Codes**:
- `99`: Rate limit error
- Other codes: Various API errors

## Rate Limits

**Free Tier Limits**:
- 5 calls per second
- 300 calls per minute
- 3,000 calls per hour
- 7,500 calls per day
- 50,000 calls per month

**Rate Limit Strategy**:
- Must implement delays between requests (200ms minimum for 5/sec limit)
- Monitor `RateLimit` object in error responses
- Implement exponential backoff on rate limit errors
- Consider upgrading to paid tier for higher limits

## Data Retention

| Endpoint | Retention | Notes |
|----------|-----------|-------|
| Daily | Full history | Years of data available |
| Hourly | Full history | Years of data available |
| Minute | **7 days only** | Cannot fetch minute data older than 7 days |

## Cryptocurrency List

Reference: `crypto_top_100.json` in project root

Top 100 cryptocurrencies by market cap including:
- BTC (Bitcoin)
- ETH (Ethereum)
- USDT (Tether)
- XRP (Ripple)
- BNB (Binance Coin)
- SOL (Solana)
- And 94 more...

Each crypto has a `symbol` field that maps to the `fsym` parameter.

## Key Differences from VCI API

| Feature | CryptoCompare | VCI (Stock API) |
|---------|---------------|-----------------|
| Batch fetching | ❌ No | ✅ Yes (50 tickers/batch) |
| Rate limits | 5/sec, 300/min | 30/min |
| Timestamp format | Unix seconds | ISO date strings |
| Volume fields | 2 fields (from/to) | 1 field |
| Dividend adjustments | ❌ N/A | ✅ Yes |
| Minute data retention | 7 days | Longer |
| Multi-exchange support | ✅ Yes | ❌ No |

## Integration Considerations

### 1. No Batch API
- Must fetch one cryptocurrency at a time
- For 100 cryptos: 100 API calls needed
- Need sequential processing with rate limit delays

### 2. Rate Limit Management
- 5 calls/second max = 200ms delay between calls minimum
- For 100 cryptos: ~20 seconds minimum (practical: 30-40s with overhead)
- Implement request queue with rate limiting

### 3. Pagination for Full History
- Use `limit=2000` + `toTs` loop pattern
- Store earliest timestamp from each batch
- Continue until no more data returned

### 4. Minute Data Limitation
- Only 7 days retention
- Cannot sync historical minute data beyond 7 days
- Resume mode must handle this constraint

### 5. Custom Intervals via Aggregation
- Use `aggregate` parameter for 5m, 15m, 30m intervals
- Example: `aggregate=5` on minute endpoint = 5-minute candles
- Example: `aggregate=4` on hourly endpoint = 4-hour candles

### 6. Multi-Currency Support
- Can fetch BTC/USD, BTC/VND, BTC/EUR simultaneously
- Each pair counts as separate API call
- Consider which currency pairs to support

### 7. Exchange Selection
- Default CCCAGG provides aggregate across exchanges
- Can specify individual exchanges for different data
- Exchange-specific data may have gaps

## Example Usage Patterns

### Fetch Latest Daily Data
```bash
curl "https://min-api.cryptocompare.com/data/v2/histoday?fsym=BTC&tsym=USD&limit=30"
```

### Fetch 5-Minute Candles
```bash
curl "https://min-api.cryptocompare.com/data/v2/histominute?fsym=ETH&tsym=USD&aggregate=5&limit=100"
```

### Fetch Full History (Pagination)
```bash
# First batch
curl "https://min-api.cryptocompare.com/data/v2/histoday?fsym=BTC&tsym=USD&limit=2000&allData=true"

# Or paginate manually
curl "https://min-api.cryptocompare.com/data/v2/histoday?fsym=BTC&tsym=USD&limit=2000"
curl "https://min-api.cryptocompare.com/data/v2/histoday?fsym=BTC&tsym=USD&limit=2000&toTs=1640000000"
```

### Fetch from Specific Exchange
```bash
curl "https://min-api.cryptocompare.com/data/v2/histohour?fsym=BTC&tsym=USD&e=Binance&limit=24"
```

## Next Steps for Integration

1. **Create CryptoCompareClient** (similar to VciClient)
   - Implement rate limiting (5/sec, 300/min)
   - Retry logic with exponential backoff
   - Response parsing and error handling

2. **Extend Data Models**
   - Support crypto tickers alongside stock tickers
   - Handle `volumefrom`/`volumeto` dual volume fields
   - Unix timestamp conversion to DateTime

3. **Storage Strategy**
   - Decide: Separate `crypto_data/` or unified `market_data/`
   - CSV format: Keep same 20-column structure or adapt?
   - Handle crypto-specific metadata

4. **CLI Integration**
   - Add `pull-crypto` command or extend `pull` with `--source` flag
   - Load crypto list from `crypto_top_100.json`
   - Respect 7-day minute data limitation

5. **API Server Updates**
   - Extend `/tickers` endpoint to support crypto symbols
   - Add ticker type distinction (stock vs crypto)
   - Update caching strategy for crypto data

6. **Configuration**
   - Add crypto ticker groups to `ticker_group.json`
   - Configure target currency (USD, VND, or both)
   - Set rate limit parameters

## References

- **CryptoCompare API Docs**: https://min-api.cryptocompare.com/documentation
- **Daily Endpoint**: https://min-api.cryptocompare.com/data/v2/histoday
- **Hourly Endpoint**: https://min-api.cryptocompare.com/data/v2/histohour
- **Minute Endpoint**: https://min-api.cryptocompare.com/data/v2/histominute
- **Crypto List**: `crypto_top_100.json` (100 cryptocurrencies)
