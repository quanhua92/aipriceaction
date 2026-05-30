# Fundamental Data Feature

Fetches company profile and financial ratios from VCI REST API (`iq.vietcap.com.vn`) and archives them to S3. Runs as part of the `s3_archive` worker — no separate worker or DB schema changes required.

## S3 Layout

```
fundamental/
  vn/
    _index.json                          # manifest of all tickers + fetch status
    {TICKER}/
      _meta.json                         # persistent checkpoint (survives restarts)
      company_info.json                  # merged company profile
      financial_ratios.json              # merged quarterly ratios
```

### Files

| File | Description |
|---|---|
| `company_info.json` | Industry, market cap, shareholders, officers, profile (exchange not available from REST API) |
| `financial_ratios.json` | Envelope `{ticker, updated_at, count, ratios: [...]}` with ~40 fields per quarterly entry |
| `_meta.json` | `{ticker, last_fetch, company_info_uploaded, financial_ratios_uploaded}` — persistent checkpoint |
| `_index.json` | `{updated_at, date, count, fetched_today, tickers: [...]}` — manifest of all tickers |

## Data Flow

```
s3_archive worker (hourly loop)
    │
    ├─ Create VciProvider (graceful: OHLCV continues if this fails)
    │
    ├─ fundamental_cycle()
    │   ├─ Load all VN tickers from DB
    │   ├─ Filter to non-index tickers
    │   │
    │   ├─ For each ticker (381 total):
    │   │   ├─ Check _meta.json from S3 (inline hydration, acts as cooldown)
    │   │   │   └─ If already fetched today → skip (unless FUNDAMENTAL_SKIP_S3_HYDRATE=true)
    │   │   │
    │   │   ├─ provider.company_info(ticker)
    │   │   │   ├─ GET /v1/company/{symbol}/details → industry, profile, shares
    │   │   │   ├─ GET /v1/company/{symbol}/shareholder → shareholders + officers
    │   │   │   ├─ GET existing from S3 → merge (new over old, fallback to old for nulls)
    │   │   │   ├─ validate (must have industry OR shareholders OR profile)
    │   │   │   └─ upload_json() with SHA256 hash-dedup
    │   │   │
    │   │   ├─ sleep(2000ms)  — rate limiting
    │   │   │
    │   │   ├─ provider.financial_ratios(ticker, "quarter")
    │   │   │   ├─ ensure_handshake() → GET trading.vietcap.com.vn/priceboard (cookies, 10s timeout)
    │   │   │   ├─ GET /v1/company/{symbol}/statistics-financial → ratios (75s total timeout)
    │   │   │   ├─ Map year→yearReport, quarter→lengthReport for merge compatibility
    │   │   │   ├─ GET existing from S3 → merge by (yearReport, lengthReport) key
    │   │   │   ├─ validate (must have yearReport + ticker + at least one key metric)
    │   │   │   └─ upload_json() with SHA256 hash-dedup, wrapped in envelope
    │   │   │
    │   │   ├─ On RateLimit/NoData + no S3 data → fallback to local company_info.json
    │   │   ├─ If both OK → mark_done() + save _meta.json to S3
    │   │   └─ If either failed → skip marking (retries next cycle)
    │   │
    │   ├─ VCI-dead detection: 0 healthy HTTP responses after 5+ tickers → fallback-only
    │   ├─ Circuit breaker: 3 consecutive rate limits → abort cycle
    │   └─ upload_fundamental_index() → _index.json manifest
    │
    └─ Continue with OHLCV archival (unchanged)
```

## REST API Endpoints (iq.vietcap.com.vn)

| Method | Endpoint | Data |
|---|---|---|
| `company_info(symbol)` | `GET /v1/company/{symbol}/details` | Company name, industry, profile, shares |
| `company_info(symbol)` | `GET /v1/company/{symbol}/shareholder` | Shareholders (all types) + officers (ownerType=INDIVIDUAL with position) |
| `financial_ratios(symbol)` | `GET /v1/company/{symbol}/statistics-financial` | PE, PB, ROE, ROA, margins, ratios, etc. |

### Response Structure

`/details` returns keys: `analyst`, `comGroupCode`, `comTypeCode`, `currentPrice`, `enOrganName`, `enProfile`, `foreignerPercentage`, `freeFloat`, `icbCodeLv2`, `icbCodeLv4`, `numberOfSharesMktCap`, `organCode`, `sectorVn`, `viOrganName`, `viOrganShortName`, `profile`, etc.

`/statistics-financial` returns array of objects with keys: `afterTaxProfitMargin`, `assetTurnover`, `cashRatio`, `currentRatio`, `debtToEquity`, `dividendYield`, `evToEbitda`, `grossMargin`, `pe`, `pb`, `priceToCashFlow`, `quickRatio`, `roe`, `roa`, `roic`, `year`, `quarter`, etc.

### Handshake (for financial ratios)

`GET https://trading.vietcap.com.vn/priceboard` — fetches session cookies. Stored in `VciProvider.handshake_cookies` (Mutex). Called once via `ensure_handshake()` before the first `financial_ratios()` call. 10s timeout per attempt, all proxy clients tried in rotation.

### Timeouts

| Component | Timeout |
|---|---|
| `make_get_request` total | 75s (15s × 5 attempts) |
| `handshake` per attempt | 10s |
| Client-level (isahc) | 30s |

### Proxy Support

All REST requests use `make_get_request()` which has the same proxy rotation as OHLCV `make_request()`:
- 5 total attempts across all clients (direct + proxies from `HTTP_PROXIES` env var)
- Per-client rate limiter
- Random User-Agent rotation
- Backoff on 403/429

### Field Mapping (REST → internal)

| REST API field | Internal field | Notes |
|---|---|---|
| `year` | `yearReport` | For merge key compatibility |
| `quarter` | `lengthReport` | For merge key compatibility |
| `ownerType=INDIVIDUAL` + `positionName` | `officers` | Filtered from shareholder list |
| Other owners with `percentage > 0` | `shareholders` | Non-individual shareholders |
| `sectorVn` / `icbName3` | `industry` | Prefer Vietnamese sector name |
| `numberOfSharesMktCap` / `issueShare` | `outstanding_shares` | REST returns f64, cast to u64 |
| `currentPrice` | `current_price` | Present in details endpoint |

## Inline Per-Ticker Hydration

Instead of batch-hydrating all 381 `_meta.json` files upfront (which caused a 3-minute stall), hydration is done **inline** for each ticker in the loop:

1. Before fetching from VCI, check that ticker's `_meta.json` from S3
2. If `last_fetch >= today` → skip VCI calls (already done today)
3. The S3 GET serves as natural cooldown between VCI requests

This eliminates the startup stall and spreads S3 reads across the entire ~13 min cycle.

## Fetch Once Per Day

Each ticker is fetched **at most once per day**. Two-layer tracking:

1. **In-memory** `FundamentalState` — `HashMap<String, NaiveDate>` keyed by ticker
2. **S3 `_meta.json`** — persistent checkpoint per ticker folder

On process restart:
- In-memory state is empty
- Per-ticker inline hydration checks `_meta.json` during the loop
- Tickers with `last_fetch >= today` are skipped
- Prevents redundant VCI API calls (saves quota)

Failed tickers are **not** marked as done, so they retry on the next hourly cycle.

## Merge Strategy

### company_info.json

Prefer new data, fallback to old when new is `None` or empty:

| Field | Strategy |
|---|---|
| `Option<T>` fields (exchange, industry, etc.) | `new.or(old)` |
| `Vec<T>` fields (shareholders, officers) | `new` if non-empty, else `old` |
| `symbol` | Always from `new` |

### financial_ratios.json

Entries matched by composite key `(yearReport, lengthReport)`:
- New entries replace old entries with the same key (fresher data)
- Old entries with no new counterpart are preserved (historical quarters)
- Result sorted by period (newest first)

### Validation

- **company_info**: Must have industry OR shareholders OR profile. Market cap sanity check. (exchange not available from REST API)
- **financial_ratios**: Must have `yearReport` + `ticker` + at least one key metric (`pe`, `revenue`, or `netProfit`).

Invalid/empty data after merge is logged and **skipped** — not uploaded.

## Hash Dedup

`upload_json()` computes SHA256 of the serialized JSON and stores it as `x-amz-meta-content-hash` S3 metadata. Before uploading, it checks the existing object's hash — skips the PUT if unchanged. Saves bandwidth and avoids unnecessary writes.

## Configuration

Defined in `src/constants.rs` — `s3_archive` module:

| Constant | Value | Description |
|---|---|---|
| `FUNDAMENTAL_RATE_LIMIT` | 30 | VCI requests per minute (documented limit) |
| `FUNDAMENTAL_DELAY_MS` | 2000 | Delay between fetches |
| `FUNDAMENTAL_MAX_CONSECUTIVE_RATE_LIMIT` | 3 | Abort cycle after this many consecutive rate-limited requests |
| `FUNDAMENTAL_VCI_DEAD_THRESHOLD` | 5 | Minimum tickers before checking if VCI is dead (0 healthy HTTP responses) |
| `LOOP_SECS` | 3600 | Worker cycle interval (1 hour) |

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `FUNDAMENTAL_SKIP_S3_HYDRATE` | `false` | Set to `true` to skip per-ticker `_meta.json` checks, forcing re-fetch of all tickers |

## Rate Limit Circuit Breaker

If 3 consecutive tickers hit VCI rate limit, the cycle **aborts immediately** — remaining tickers are skipped and retried on the next hourly cycle.

## VCI-Dead Detection

Tracks `vci_healthy_requests` — the count of tickers where VCI returned a successful HTTP 200 response. After processing 5+ tickers, if **zero** healthy requests were received, VCI is declared dead and the cycle switches to **fallback-only**.

This distinguishes between:
- **VCI alive but stock has no data**: HTTP 200 returned, `ticker_vci_ok=true` → VCI stays alive
- **VCI unreachable**: No HTTP responses at all, `vci_healthy_requests=0` → VCI declared dead, use fallback

## Local File Fallback

When VCI returns `RateLimit` **or** `NoData` **and** there is no existing data in S3 for that ticker, the cycle falls back to the local `company_info.json` file (bundled in the container image at build time).

- **Loaded lazily**: the 37MB file is only read into memory when the first rate-limited ticker needs it
- **Loaded once per cycle**: a `tried` flag prevents re-reading if the file is missing or corrupt
- **No crash**: if the file doesn't exist or fails to parse, fallback is disabled for the rest of the cycle
- Covers both `company_info` and `financial_ratios`

## Known Data Gaps

- ~381 VN tickers total, all pass validation via REST API
- Index tickers (VNINDEX, VN30, VN30F1M, etc.) are **excluded** — they have no company info
- REST API works from VPS without proxies (unlike the old GraphQL endpoint)
- `exchange` field is `None` — not returned by REST `/details` endpoint
- Handshake cookies may be required for `/statistics-financial` — fetched automatically on first call
- `/statistics-financial` returns ~9-40 ratio items per ticker (quarterly data)

## Log Prefix

All fundamental-related logs use `[FUNDAMENTAL]` prefix for easy filtering:

```
[FUNDAMENTAL] per-ticker hydration disabled (FUNDAMENTAL_SKIP_S3_HYDRATE=true)
[FUNDAMENTAL] fetching 381 tickers
[FUNDAMENTAL] [1/381] AAA — start
[FUNDAMENTAL] [company-details] AAA keys: ["analyst", "comGroupCode", ...]
[FUNDAMENTAL] [company-details] AAA parsed: exchange=None industry=Some("Hóa chất") shares=Some(393742730) ...
[FUNDAMENTAL] [financial-ratios] AAA returned 40 items, first keys: ["afterTaxProfitMargin", ...]
[FUNDAMENTAL] [1/381] AAA — done (ci=ok, fr=ok, 3.6s)
[FUNDAMENTAL] [6/381] ABI — already fetched 2026-05-30 (via _meta.json), skipping
[FUNDAMENTAL] cycle done — 381 ok, 0 failed
[FUNDAMENTAL] uploaded fundamental/vn/_index.json
```

## Key Files

| File | Role |
|---|---|
| `src/workers/s3_archive.rs` | Fundamental cycle, inline hydration, merge, validation, S3 upload, fallback, VCI-dead detection |
| `src/providers/vci.rs` | `company_info()` REST, `financial_ratios()` REST, `make_get_request()`, `ensure_handshake()`, `CompanyInfo` struct |
| `src/constants.rs` | `FUNDAMENTAL_RATE_LIMIT`, `FUNDAMENTAL_DELAY_MS`, `FUNDAMENTAL_VCI_DEAD_THRESHOLD` |
| `src/generate_company_info.rs` | CLI tool to bulk-generate company_info.json |
| `scripts/analyze_company_info.py` | Analysis script for bulk company_info.json data |
