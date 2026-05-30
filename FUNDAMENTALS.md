# Fundamental Data Feature

Fetches company profile and financial ratios from VCI REST API (`iq.vietcap.com.vn`) and archives them to S3. Runs as part of the `s3_archive` worker — no separate worker or DB schema changes required.

## S3 Layout

```
fundamental/
  vn/
    vn.zip                              # ZIP bundle of ALL tickers (single download)
    _index.json                          # manifest of all tickers + fetch status
    {TICKER}/
      _meta.json                         # persistent checkpoint (survives restarts)
      company_info.json                  # merged company profile
      financial_ratios.json              # merged quarterly ratios
```

### Files

| File | Description | Sample |
|---|---|---|
| `vn.zip` | ZIP bundle of ALL VN tickers' fundamental data | [download](https://s3.aipriceaction.com/fundamental/vn.zip) |
| `company_info.json` | Merged company profile | [ACB](https://s3.aipriceaction.com/fundamental/vn/ACB/company_info.json) |
| `financial_ratios.json` | Envelope with quarterly ratios (60 fields per entry) | [ACB](https://s3.aipriceaction.com/fundamental/vn/ACB/financial_ratios.json) |
| `_meta.json` | Per-ticker persistent checkpoint | |
| `_index.json` | Manifest of all tickers + fetch status | |

### JSON Schemas

#### `company_info.json`

> Sample: https://s3.aipriceaction.com/fundamental/vn/ACB/company_info.json

```json
{
  "symbol": "ACB",
  "exchange": null,
  "industry": "Ngân hàng",
  "company_type": null,
  "established_year": null,
  "employees": null,
  "market_cap": 122252427056200.0,
  "current_price": 23500.0,
  "outstanding_shares": 5136656599,
  "company_profile": "<div style=\"FONT-FAMILY: Arial...",
  "website": null,
  "shareholders": [
    { "name": "Sather Gate Investments Limited", "percentage": 0.0499 },
    { "name": "Dragon Financial Holdings Limited", "percentage": 0.036243 }
  ],
  "officers": [
    { "name": "Trần Hùng Huy", "position": "Chủ tịch HĐQT", "percentage": null },
    { "name": "Mai Thị Hằng", "position": "Tổng Giám đốc", "percentage": null }
  ]
}
```

| Field | Type | Source | Notes |
|---|---|---|---|
| `symbol` | `string` | Always set | Ticker symbol |
| `exchange` | `string?` | Not from REST | Always `null` from REST API; may exist from legacy data merge |
| `industry` | `string?` | `sectorVn` | Vietnamese sector name from `/details` |
| `company_type` | `string?` | — | Not populated by REST |
| `established_year` | `u32?` | — | Not populated by REST |
| `employees` | `u32?` | — | Not populated by REST |
| `market_cap` | `f64?` | `currentPrice × numberOfSharesMktCap` | From `/details` |
| `current_price` | `f64?` | `currentPrice` | From `/details` |
| `outstanding_shares` | `u64?` | `numberOfSharesMktCap` | From `/details` (f64 → u64) |
| `company_profile` | `string?` | `profile` | HTML string from `/details` |
| `website` | `string?` | — | Not populated by REST |
| `shareholders` | `ShareholderInfo[]` | `/shareholder` | Filtered: non-INDIVIDUAL or percentage > 0 |
| `officers` | `OfficerInfo[]` | `/shareholder` | Filtered: ownerType=INDIVIDUAL with positionName |

#### `financial_ratios.json`

> Sample: https://s3.aipriceaction.com/fundamental/vn/ACB/financial_ratios.json
>
> **60 fields per entry** — all fields from REST `/statistics-financial` are stored as-is (passthrough `HashMap<String, Value>`).

```json
{
  "ticker": "ACB",
  "updated_at": "2026-05-30T12:06:57",
  "count": 62,
  "ratios": [
    {
      "yearReport": 2025,
      "lengthReport": 5,
      "ticker": "ACB",
      "pe": 8.2386621048,
      "pb": 1.3531858822,
      "ps": 3.7843420353,
      "roe": 0.1755767655,
      "roa": 0.0165353343,
      "roic": 0.0,
      "grossMargin": 0.6767738678,
      "afterTaxProfitMargin": 0.4622981564,
      "preTaxProfitMargin": 0.5781065045,
      "ebitMargin": 0.0,
      "netInterestMargin": 0.0292074524,
      "assetTurnover": 0.0,
      "debtToEquity": 0.0,
      "debtPerEquity": 0.0,
      "financialLeverage": 0.0,
      "currentRatio": 0.0,
      "quickRatio": 0.0,
      "cashRatio": 0.0,
      "equityToLiabilities": 0.1014889219,
      "equityToLoans": 0.1376278917,
      "totalEquityTotalAsset": 0.0921379415,
      "ownersEquity": 0.0,
      "dividendYield": 0.0,
      "evToEbitda": 0.0,
      "priceToCashFlow": 4.5419891063,
      "ebit": 0.0,
      "ebitda": 0.0,
      "marketCap": 122252427056200.0,
      "numberOfSharesMktCap": 5136656599,
      "fixedAssetTurnover": 0.0,
      "cashCycle": 0.0,
      "daySaleOutstanding": 0.0,
      "daysInventoryOutstanding": 0.0,
      "daysPayableOutstanding": 0.0,
      "nonAndInterestIncome": 0.2517315758,
      "cir": -0.3232261322,
      "costToIncome": -0.3232261322,
      "npl": 0.0097140463,
      "ldrLoanDepositRatio": 1.1736169155,
      "car": 0.1245,
      "casaRatio": 0.2181753594,
      "averageCostOfFinancing": -0.0382595263,
      "averageYieldOnEarningAssets": 0.063782336,
      "depositGrowth": 0.0891032739,
      "loansGrowth": 0.182699529,
      "loansLossReserveToLoans": 0.0111021847,
      "loansLossReservesToNPLs": -1.1429001196,
      "provisionToOutstandingLoans": -0.0052620809,
      "bsb113": 585180175000000.0,
      "nob66": 124538983000000.0,
      "nob69": 2805219000000.0,
      "nob70": 327693000000.0,
      "organCode": "ACB",
      "ratioTTMId": 12283575,
      "ratioType": "RATIO_YEAR",
      "ratioYearId": 12283575
    }
  ]
}
```

| Envelope field | Type | Description |
|---|---|---|
| `ticker` | `string` | Ticker symbol |
| `updated_at` | `string` | ISO 8601 timestamp of upload |
| `count` | `number` | Number of ratio entries |
| `ratios` | `array` | Array of period objects |

| Ratio field | Type | Category | Notes |
|---|---|---|---|
| `yearReport` | `number` | Key | Year (mapped from REST `year`) — merge key |
| `lengthReport` | `number` | Key | Period: 1-4 = quarter, 5 = year — merge key |
| `ticker` | `string` | Key | Ticker symbol |
| `organCode` | `string` | Key | Same as ticker |
| `ratioType` | `string` | Key | "RATIO_QUARTER" or "RATIO_YEAR" |
| `ratioTTMId` | `number?` | Key | VCI internal ID |
| `ratioYearId` | `number?` | Key | VCI internal ID |
| `pe` | `f64?` | Valuation | Price-to-Earnings |
| `pb` | `f64?` | Valuation | Price-to-Book |
| `ps` | `f64?` | Valuation | Price-to-Sales |
| `evToEbitda` | `f64?` | Valuation | EV/EBITDA |
| `priceToCashFlow` | `f64?` | Valuation | Price/Cash Flow |
| `dividendYield` | `f64?` | Valuation | Dividend yield |
| `marketCap` | `f64?` | Valuation | Market capitalization |
| `numberOfSharesMktCap` | `f64?` | Valuation | Outstanding shares |
| `roe` | `f64?` | Profitability | Return on Equity |
| `roa` | `f64?` | Profitability | Return on Assets |
| `roic` | `f64?` | Profitability | Return on Invested Capital |
| `grossMargin` | `f64?` | Profitability | Gross margin |
| `afterTaxProfitMargin` | `f64?` | Profitability | Net profit margin |
| `preTaxProfitMargin` | `f64?` | Profitability | Pre-tax profit margin |
| `ebitMargin` | `f64?` | Profitability | EBIT margin |
| `netInterestMargin` | `f64?` | Profitability | Net interest margin (banks) |
| `ebit` | `f64?` | Profitability | EBIT |
| `ebitda` | `f64?` | Profitability | EBITDA |
| `assetTurnover` | `f64?` | Efficiency | Asset turnover ratio |
| `fixedAssetTurnover` | `f64?` | Efficiency | Fixed asset turnover |
| `debtToEquity` | `f64?` | Leverage | Debt/Equity |
| `debtPerEquity` | `f64?` | Leverage | Debt per Equity |
| `financialLeverage` | `f64?` | Leverage | Financial leverage |
| `equityToLiabilities` | `f64?` | Leverage | Equity/Liabilities |
| `equityToLoans` | `f64?` | Leverage | Equity/Loans (banks) |
| `totalEquityTotalAsset` | `f64?` | Leverage | Total equity/Total assets |
| `ownersEquity` | `f64?` | Leverage | Owner's equity |
| `currentRatio` | `f64?` | Liquidity | Current ratio |
| `quickRatio` | `f64?` | Liquidity | Quick ratio |
| `cashRatio` | `f64?` | Liquidity | Cash ratio |
| `cashCycle` | `f64?` | Cash cycle | Cash conversion cycle |
| `daySaleOutstanding` | `f64?` | Cash cycle | Days sales outstanding |
| `daysInventoryOutstanding` | `f64?` | Cash cycle | Days inventory outstanding |
| `daysPayableOutstanding` | `f64?` | Cash cycle | Days payable outstanding |
| `nonAndInterestIncome` | `f64?` | Bank | Non-interest income ratio |
| `cir` / `costToIncome` | `f64?` | Bank | Cost-to-Income ratio |
| `npl` | `f64?` | Bank | Non-performing loan ratio |
| `ldrLoanDepositRatio` | `f64?` | Bank | Loan-to-Deposit ratio |
| `car` | `f64?` | Bank | Capital Adequacy Ratio |
| `casaRatio` | `f64?` | Bank | CASA ratio |
| `averageCostOfFinancing` | `f64?` | Bank | Average cost of funding |
| `averageYieldOnEarningAssets` | `f64?` | Bank | Average yield on earning assets |
| `depositGrowth` | `f64?` | Bank | Deposit growth rate |
| `loansGrowth` | `f64?` | Bank | Loan growth rate |
| `loansLossReserveToLoans` | `f64?` | Bank | Loan loss reserve/Loans |
| `loansLossReservesToNPLs` | `f64?` | Bank | Loan loss reserves/NPLs |
| `provisionToOutstandingLoans` | `f64?` | Bank | Provision/Outstanding loans |
| `bsb113` | `f64?` | Bank | Total assets (raw) |
| `nob66` | `f64?` | Bank | Customer deposits (raw) |
| `nob69` | `f64?` | Bank | Outstanding loans (raw) |
| `nob70` | `f64?` | Bank | Other balance sheet (raw) |

Non-bank tickers have fewer fields populated — bank-specific fields (NPL, CAR, CASA, LDR, etc.) are `0.0` or `null`.

~9-62 items per ticker. `lengthReport` values: 1-4 = quarterly, 5 = yearly.

#### `_meta.json`

```json
{
  "ticker": "ACB",
  "last_fetch": "2026-05-30",
  "company_info_uploaded": true,
  "financial_ratios_uploaded": true
}
```

#### `_index.json`

```json
{
  "updated_at": "2026-05-30T12:20:00.000000+00:00",
  "date": "2026-05-30",
  "count": 381,
  "fetched_today": 326,
  "tickers": [
    {
      "ticker": "AAA",
      "name": "Công ty Cổ phần Nhựa An Phát Xanh",
      "fetched_today": true,
      "date": "2026-05-30"
    },
    {
      "ticker": "ZZZ",
      "name": "...",
      "fetched_today": false,
      "date": ""
    }
  ]
}
```

## ZIP Bundle (`vn.zip`)

After each fundamental cycle, a single ZIP file is built containing **ALL** VN tickers (including indices like VNINDEX, VN30). This allows clients to download **one file** instead of 800+ individual HTTP requests.

### Structure

```
vn.zip  (~15-20 MB, deflated)
├── ACB/
│   ├── company_info.json          (full data)
│   └── financial_ratios.json      (full data)
├── FPT/
│   ├── company_info.json
│   └── financial_ratios.json
├── VCB/
│   ├── company_info.json          {}
│   └── financial_ratios.json      {}
├── VNINDEX/
│   ├── company_info.json          {}
│   └── financial_ratios.json      {}
└── ... (~400 tickers)
```

### Design

- **No-data tickers get `{}`** — empty JSON object, never empty files or missing entries
- **Includes ALL VN tickers** — no filtering, indices included
- **Hash dedup** — SHA256 stored as `x-amz-meta-content-hash`, upload skipped if unchanged
- **Content-Type**: `application/zip`
- **Updated once per cycle** — after per-ticker uploads + `_index.json`

### Build process

1. During the per-ticker loop, JSON bytes are cached as they are uploaded
2. After the loop, tickers not processed this cycle are fetched from S3 in parallel (concurrency=16)
3. All bytes are written to an in-memory ZIP with `Deflated` compression
4. Single PUT to `fundamental/vn.zip` with hash-based dedup

### Performance

| Step | Time |
|---|---|
| Cache bytes during loop | 0 (already serializing for upload) |
| S3 GETs for non-cached tickers | <2s (parallel, concurrency=16) |
| Build ZIP in memory | <1s |
| Upload ZIP to S3 (~15-20 MB) | <5s |
| **Total added to cycle** | **<10s** |

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
    │   │   ├─ Cache uploaded JSON bytes for ZIP bundle
    │   │   ├─ If both OK → mark_done() + save _meta.json to S3
    │   │   └─ If either failed → skip marking (retries next cycle)
    │   │
    │   ├─ VCI-dead detection: 0 healthy HTTP responses after 5+ tickers → fallback-only
    │   ├─ Circuit breaker: 3 consecutive rate limits → abort cycle
    │   ├─ upload_fundamental_index() → _index.json manifest
    │   └─ build_and_upload_fundamental_zip() → vn.zip (ALL tickers, empty = {})
    │       ├─ Use cached bytes from this cycle
    │       ├─ Fetch remaining tickers from S3 in parallel (concurrency=16)
    │       ├─ No-data tickers → {} (empty JSON object)
    │       └─ Single PUT with hash dedup (~15-20 MB)
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

## Python SDK

The `aipriceaction` Python package provides typed access to all fundamental data. All methods download a single `vn.zip` bundle on first call, then read from local disk cache.

### Installation

```bash
pip install aipriceaction
# or
uv add aipriceaction
```

### Data Classes

```python
from aipriceaction import (
    CompanyInfo,
    FinancialRatios,
    FinancialRatioEntry,
    ShareholderInfo,
    OfficerInfo,
    FundamentalRankEntry,
)
```

| Class | Key Fields | Description |
|---|---|---|
| `CompanyInfo` | `symbol`, `industry`, `market_cap`, `current_price`, `outstanding_shares`, `shareholders`, `officers`, `company_profile` | Full company profile with shareholders & officers |
| `FinancialRatios` | `ticker`, `updated_at`, `count`, `ratios` | Envelope containing list of `FinancialRatioEntry` |
| `FinancialRatioEntry` | `pe`, `pb`, `roe`, `roa`, `npl`, `cir`, `car`, `dividend_yield`, `debt_to_equity`, ... (50+ fields) | Single period's financial metrics |
| `ShareholderInfo` | `name`, `percentage` | Shareholder name and ownership % |
| `OfficerInfo` | `name`, `position`, `percentage` | Officer name, title, and ownership % |
| `FundamentalRankEntry` | `ticker`, `industry`, `rank`, `rank_value`, `rank_field`, `company_info`, `latest_ratio` | Ranked result from screening/ranking |

All data classes have `to_dict()` and `from_dict()` methods for serialization.

### Client Methods

```python
from aipriceaction import AIPriceAction

client = AIPriceAction()
```

#### `get_company_info(ticker, *, source=None) → CompanyInfo | None`

Fetch company profile for a single ticker.

```python
ci = client.get_company_info("ACB", source="vn")
if ci:
    print(ci.symbol)          # "ACB"
    print(ci.industry)        # "Ngân hàng"
    print(ci.market_cap)      # 120711430076500.0
    print(ci.current_price)   # 23500.0
    print(len(ci.shareholders))  # 48
    print(len(ci.officers))      # 18
```

Returns `None` for indices (VNINDEX, VN30) and unknown tickers.

#### `get_financial_ratios(ticker, *, source=None) → FinancialRatios | None`

Fetch all financial ratio periods for a ticker.

```python
fr = client.get_financial_ratios("VCB", source="vn")
if fr:
    print(fr.ticker)  # "VCB"
    print(fr.count)   # 40
    latest = fr.ratios[-1]
    print(latest.pe)  # 14.73
    print(latest.roe) # 0.1673 (16.73%)
    print(latest.npl) # 0.0058 (0.58%)
```

#### `get_fundamental(ticker, *, source=None) → tuple[CompanyInfo | None, FinancialRatios | None]`

Fetch both company info and financial ratios in one call.

```python
ci, fr = client.get_fundamental("FPT", source="vn")
```

### Ranking & Screening

#### `build_fundamental_ranking(client, tickers, *, sort_by, direction, limit, source, yearly_only) → list[FundamentalRankEntry]`

Rank tickers by any fundamental field. Downloads `vn.zip` once, then reads all tickers from cache.

```python
from aipriceaction import AIPriceAction, build_fundamental_ranking

client = AIPriceAction()

# Top 10 by ROE (most profitable)
top_roe = build_fundamental_ranking(
    client,
    ["VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "HDB", "SHB", "TPB"],
    sort_by="roe",
    direction="desc",
    limit=10,
)
for entry in top_roe:
    print(f"  #{entry.rank} {entry.ticker:6s} ROE={entry.rank_value * 100:.1f}%  industry={entry.industry}")

# Cheapest by PE (ascending)
cheapest = build_fundamental_ranking(
    client,
    ["VCB", "FPT", "HPG", "VIC", "VNM", "GAS", "MWG"],
    sort_by="pe",
    direction="asc",
    limit=5,
)

# Best bank assets (CAR)
best_capitalized = build_fundamental_ranking(
    client,
    ["VCB", "BID", "CTG", "TCB", "MBB", "ACB"],
    sort_by="car",
    direction="desc",
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `client` | `AIPriceAction` | required | SDK client instance |
| `tickers` | `list[str]` | required | Ticker symbols to rank |
| `sort_by` | `str` | `"roe"` | Field name to rank by (see sortable fields below) |
| `direction` | `str` | `"desc"` | `"desc"` (highest first) or `"asc"` (lowest first) |
| `limit` | `int` | `10` | Max results (1–200) |
| `source` | `str | None` | `None` | Data source override |
| `yearly_only` | `bool` | `True` | Use latest annual report only (`ratio_type == "RATIO_YEAR"`) |

**Sortable fields** (50+): `pe`, `pb`, `ps`, `ev_to_ebitda`, `price_to_cash_flow`, `dividend_yield`, `market_cap`, `roe`, `roa`, `roic`, `gross_margin`, `after_tax_profit_margin`, `pre_tax_profit_margin`, `ebit_margin`, `net_interest_margin`, `ebit`, `ebitda`, `asset_turnover`, `fixed_asset_turnover`, `debt_to_equity`, `debt_per_equity`, `financial_leverage`, `equity_to_liabilities`, `equity_to_loans`, `total_equity_total_asset`, `owners_equity`, `equity`, `current_ratio`, `quick_ratio`, `cash_ratio`, `cash_cycle`, `day_sale_outstanding`, `days_inventory_outstanding`, `days_payable_outstanding`, `npl`, `ldr_loan_deposit_ratio`, `car`, `casa_ratio`, `cir`, `cost_to_income`, `non_and_interest_income`, `deposit_growth`, `loans_growth`, `loans_loss_reserve_to_loans`, `loans_loss_reserves_to_npl`, `provision_to_outstanding_loans`, `average_cost_of_financing`, `average_yield_on_earning_assets`, `outstanding_shares`, `employees`, `current_price`.

#### `screen_fundamentals(client, tickers, *, filters..., sort_by, direction, limit, ...) → list[FundamentalRankEntry]`

Filter tickers by fundamental criteria, then rank by a field. Same return type as `build_fundamental_ranking`.

```python
from aipriceaction import AIPriceAction, screen_fundamentals

client = AIPriceAction()
bank_tickers = ["VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "HDB", "SHB", "TPB"]

# Low PE, high ROE banks
value_banks = screen_fundamentals(
    client,
    bank_tickers,
    pe_max=10.0,
    roe_min=0.15,
    sort_by="roe",
    direction="desc",
)

# Dividend stocks with low debt
dividend_stocks = screen_fundamentals(
    client,
    ["VCB", "FPT", "HPG", "VNM", "GAS", "MWG", "SAB", "PLX", "MSN"],
    dividend_yield_min=0.02,
    debt_to_equity_max=1.5,
    sort_by="dividend_yield",
    direction="desc",
)

# Banks with good asset quality
safe_banks = screen_fundamentals(
    client,
    bank_tickers,
    npl_max=0.02,
    car_min=0.10,
    sort_by="npl",
    direction="asc",
)

# Filter by industry (case-insensitive, supports list)
banking_only = screen_fundamentals(
    client,
    all_vn_tickers,
    industry="ngân hàng",
    sort_by="roe",
    direction="desc",
)

# Multiple industries
finance_tech = screen_fundamentals(
    client,
    all_vn_tickers,
    industry=["ngân hàng", "công nghệ thông tin"],
    pe_max=20.0,
    sort_by="roe",
    direction="desc",
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `pe_min` / `pe_max` | `float | None` | `None` | PE range filter |
| `pb_min` / `pb_max` | `float | None` | `None` | PB range filter |
| `roe_min` / `roe_max` | `float | None` | `None` | ROE range filter |
| `roa_min` / `roa_max` | `float | None` | `None` | ROA range filter |
| `dividend_yield_min` / `dividend_yield_max` | `float | None` | `None` | Dividend yield range |
| `debt_to_equity_min` / `debt_to_equity_max` | `float | None` | `None` | D/E range |
| `current_ratio_min` / `current_ratio_max` | `float | None` | `None` | Current ratio range |
| `gross_margin_min` / `gross_margin_max` | `float | None` | `None` | Gross margin range |
| `npl_min` / `npl_max` | `float | None` | `None` | NPL range (banks) |
| `cir_min` / `cir_max` | `float | None` | `None` | CIR range (banks) |
| `car_min` / `car_max` | `float | None` | `None` | CAR range (banks) |
| `market_cap_min` / `market_cap_max` | `float | None` | `None` | Market cap range |
| `industry` | `str | list[str] | None` | `None` | Industry filter (case-insensitive) |
| `require_data` | `bool` | `True` | Exclude tickers with no fundamental data |
| `sort_by` | `str` | `"roe"` | Field to rank by (same fields as `build_fundamental_ranking`) |
| `direction` | `str` | `"desc"` | Sort direction |
| `limit` | `int` | `50` | Max results (1–500) |
| `yearly_only` | `bool` | `True` | Use latest annual report only |
| `source` | `str | None` | `None` | Data source override |

**Filter behavior:**
- All filters are optional — pass only what you need
- `None` values (missing data) fail the filter — tickers without that field are excluded
- Range filters are inclusive: `roe_min=0.15` matches `roe >= 0.15`
- `industry` is case-insensitive substring match against `CompanyInfo.industry`
- `require_data=False` keeps tickers with no data (rank_value will be `None`)

### Caching

All fundamental methods use the same caching strategy as OHLCV data:

1. **First call**: downloads `vn.zip` (~6–20 MB) from S3, extracts to `~/.aipriceaction/s3-cache/`
2. **Subsequent calls**: reads from local disk — no network request
3. **Hash-based validation**: SHA256 check ensures cache freshness
4. **TTL**: respects the same cache TTL as other S3 data

```python
client = AIPriceAction()  # uses default cache dir

# First call downloads vn.zip (~1-2 seconds)
ci1 = client.get_company_info("VCB", source="vn")

# Second call is instant (reads from disk)
ci2 = client.get_company_info("FPT", source="vn")

# Ranking 30 tickers: still only 1 download
ranking = build_fundamental_ranking(client, ["VCB", "FPT", "HPG", ...], sort_by="roe")
```

### Key Files (Python SDK)

| File | Role |
|---|---|
| `sdk/aipriceaction-python/src/aipriceaction/client.py` | `get_company_info()`, `get_financial_ratios()`, `get_fundamental()`, `_ensure_fundamental_zip()`, `_read_fundamental_json()` |
| `sdk/aipriceaction-python/src/aipriceaction/fundamental.py` | `CompanyInfo`, `FinancialRatios`, `FinancialRatioEntry`, `ShareholderInfo`, `OfficerInfo` dataclasses |
| `sdk/aipriceaction-python/src/aipriceaction/fundamental_ranking.py` | `build_fundamental_ranking()`, `screen_fundamentals()`, `FundamentalRankEntry` |
| `sdk/aipriceaction-python/examples/fundamental_demo.py` | 14-section demo covering all features |
| `sdk/aipriceaction-python/examples/full_client_demo.py` | Full SDK demo with fundamental section |

## aipa-cli fundamentals

CLI access to fundamental data via `aipa fundamentals` subcommand. No API key needed — reads from cached `vn.zip`.

### Subcommands

| Command | Description |
|---|---|
| `aipa fundamentals info TICKER` | Company profile, shareholders, officers |
| `aipa fundamentals ratios TICKER` | Financial ratios by period |
| `aipa fundamentals rank` | Rank tickers by a fundamental field |
| `aipa fundamentals screen` | Multi-criteria screening |

### `aipa fundamentals info`

Show company profile for a ticker.

```bash
aipa fundamentals info ACB
aipa fundamentals info FPT --source vn
```

Displays: industry, market cap, current price, outstanding shares, top shareholders with ownership %, officers with positions.

### `aipa fundamentals ratios`

Show financial ratios for a ticker, organized by category.

```bash
aipa fundamentals ratios VCB                    # All yearly reports
aipa fundamentals ratios VCB --latest            # Only latest yearly
aipa fundamentals ratios VCB --year 2024         # Specific year
aipa fundamentals ratios VCB --no-yearly         # Include quarterly
aipa fundamentals ratios VCB --category bank     # Only bank-specific fields
aipa fundamentals ratios VCB --json              # Raw JSON output
```

| Flag | Default | Description |
|---|---|---|
| `--latest` | off | Show only latest yearly report |
| `--year YEAR` | — | Show specific year |
| `--no-yearly` | off | Include quarterly reports (default: yearly only) |
| `--category` | all | `valuation`, `profitability`, `leverage`, `liquidity`, `bank`, `efficiency` |
| `--json` | off | Dump raw JSON to stdout |
| `--source` | auto | Data source |

**Categories:**

| Category | Fields |
|---|---|
| Valuation | PE, PB, PS, EV/EBITDA, Price/CashFlow, Dividend Yield, Market Cap |
| Profitability | ROE, ROA, ROIC, Gross Margin, After-Tax Margin, Pre-Tax Margin, EBIT Margin, Net Interest Margin |
| Efficiency | Asset Turnover, Fixed Asset Turnover, Cash Cycle, DSO, DIO, DPO |
| Leverage | Debt/Equity, Financial Leverage, Equity/Liabilities, Equity/Loans, Equity/Total Asset |
| Liquidity | Current Ratio, Quick Ratio, Cash Ratio |
| Bank | NPL, LDR, CAR, CASA, CIR, Non-Interest Income, Deposit/Loans Growth, LLR ratios |

### `aipa fundamentals rank`

Rank tickers by any of 50+ fundamental fields.

```bash
aipa fundamentals rank                                           # Top 10 VN by ROE
aipa fundamentals rank --sort-by pe --direction asc --limit 20   # Cheapest 20 by PE
aipa fundamentals rank --tickers VCB BID CTG TCB MBB --sort-by car --direction desc
aipa fundamentals rank --watchlist VN30 --sort-by roe --limit 15
aipa fundamentals rank --sort-by npl --direction asc --limit 10  # Best asset quality
aipa fundamentals rank --sort-by dividend_yield --direction desc  # Highest dividend
```

| Flag | Default | Description |
|---|---|---|
| `--sort-by` | `roe` | Field to rank by (50+ fields: pe, pb, roe, roa, npl, car, dividend_yield, market_cap, ...) |
| `--direction` | `desc` | `desc` (highest first) or `asc` (lowest first) |
| `--limit` | `10` | Max results |
| `tickers` | all VN | Positional ticker symbols |
| `--watchlist` | — | Use watchlist as ticker source (VN30, VINGROUP, TM, MASAN, custom, ...) |
| `--source` | auto | Data source |

**Ticker source resolution** (same for `rank` and `screen`):
1. `--watchlist NAME` — resolve from predefined (VN30, VINGROUP...) or custom watchlists
2. Positional `tickers` — explicit list
3. Default — all VN tickers from ticker metadata

### `aipa fundamentals screen`

Filter tickers by fundamental criteria, then rank by a field.

```bash
# Value stocks: low PE + high ROE
aipa fundamentals screen --pe-max 15 --roe-min 0.15 --sort-by roe

# Banking sector only
aipa fundamentals screen --industry "ngân hàng" --sort-by roe

# Safe banks: low NPL + high CAR
aipa fundamentals screen --npl-max 0.015 --car-min 0.10 --sort-by npl --direction asc

# Dividend stocks
aipa fundamentals screen --dividend-yield-min 0.03 --sort-by dividend_yield

# Screen VN30 watchlist
aipa fundamentals screen --watchlist VN30 --pe-max 20 --roe-min 0.10

# Specific tickers
aipa fundamentals screen VCB FPT HPG VNM --roe-min 0.15 --sort-by pe --direction asc
```

| Flag | Default | Description |
|---|---|---|
| `--sort-by` | `roe` | Field to rank by |
| `--direction` | `desc` | Sort direction |
| `--limit` | `50` | Max results (1–500) |
| `--pe-min` / `--pe-max` | — | PE range filter |
| `--pb-min` / `--pb-max` | — | PB range filter |
| `--roe-min` / `--roe-max` | — | ROE range filter |
| `--roa-min` / `--roa-max` | — | ROA range filter |
| `--dividend-yield-min` / `--dividend-yield-max` | — | Dividend yield range |
| `--debt-to-equity-max` | — | Max Debt/Equity |
| `--npl-max` | — | Max NPL (banks) |
| `--car-min` | — | Min CAR (banks) |
| `--cir-max` | — | Max CIR (banks) |
| `--market-cap-min` / `--market-cap-max` | — | Market cap range |
| `--industry` | — | Industry filter (substring, case-insensitive) |
| `--watchlist` | — | Use watchlist as ticker source |
| `--source` | auto | Data source |

**Filter behavior:**
- All filters are optional — pass only what you need
- Tickers with missing data for a filtered field are excluded
- Range filters are inclusive: `--roe-min 0.15` matches `roe >= 0.15`
- `--industry` is case-insensitive substring match

### Key Files (CLI)

| File | Role |
|---|---|
| `aipriceaction-terminal/src/aipriceaction_terminal/cli.py` | `fundamentals` subparser registration + dispatch |
| `aipriceaction-terminal/src/aipriceaction_terminal/cli_commands.py` | `cmd_fundamentals()`, `_fund_info()`, `_fund_ratios()`, `_fund_rank()`, `_fund_screen()` |
