# Python API routes

This table lists the current FastAPI routes. Exact parameters, defaults,
validation, and response fields are defined in the route models and tests under
`src/aipriceaction_api/` and `tests/`.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET`, `HEAD` | `/tickers` | OHLCV by symbol, mode, interval, date range, indicators, and format |
| `GET`, `HEAD` | `/tickers/group` | ICB sector groups for VN stocks; other source groups |
| `GET`, `HEAD` | `/tickers/name` | Symbol-to-name map for the full VN stock list and other sources |
| `GET`, `HEAD` | `/tickers/info` | Full VN stock directory enriched with available company profiles |
| `GET`, `HEAD` | `/tickers/directory` | Complete Vietnamese stock directory with optional market-cap filter |
| `POST` | `/tickers/refresh` | Schedule a refresh; requires the configured secret |
| `GET`, `HEAD` | `/health` | API, market, ingestion, and storage status |
| `GET`, `HEAD`, `POST` | `/sync/{key}` | Optional bearer-token-protected key/value sync |
| `GET`, `HEAD` | `/analysis/top-performers` | Ranked performers and filters |
| `GET`, `HEAD` | `/analysis/ma-scores-by-sector` | Moving-average scores by sector |
| `GET`, `HEAD` | `/analysis/volume-profile` | Volume-by-price data |
| `GET`, `HEAD` | `/analysis/rrg` | Relative rotation graph data |
| `POST` | `/upload/markdown` | Upload one Markdown file |
| `GET`, `HEAD` | `/uploads/{session_id}/markdown/{filename}` | Retrieve a public-session file |
| `DELETE` | `/uploads/{session_id}` | Delete an upload session |
| `GET`, `HEAD` | `/explorer` | Packaged API explorer |
| `GET`, `HEAD` | `/public`, `/public/{path}` | Packaged public page and static files |

## Request details

`GET /tickers` accepts repeated `symbol` parameters. It supports `interval`,
`limit`, `start_date`, `end_date`, `mode`, `ma`, `ema`, `format=csv`, and cache
controls. It can return one symbol, several requested symbols, or a market
universe.

`GET /tickers/group`, `/tickers/name`, and `/tickers/info` use the same saved
VN stock catalog as `/tickers/directory`. They default to a shared minimum
market cap of VND 387,175,000,000. This fixed constant kept 657 stocks and
covered 99% of known market cap in the catalog generated on Oct 24, 2026. A
later refresh may change that percentage. Set `min_market_cap_vnd` to override
the cutoff. Use `min_market_cap_vnd=0` for all stocks with known market cap;
add `include_unknown_market_cap=true` to include unknown-cap stocks too.
`/tickers/info` merges the older company profile and financial ratio snapshot
where one exists; otherwise profile fields are empty while listing, name,
sector, and market-cap fields remain available. Market-cap query parameters
apply to these metadata routes only. `/tickers` continues to return stored
OHLCV data for its selected source and symbols.
VN group keys use English ICB level-2 names by default, such as `Banks`. Pass
`lang=vi` or `lang=vn` to get Vietnamese names such as `Ngân hàng`. Stock
metadata rows include both names in `icb_level2_name` and
`icb_level2_name_en`.
`/tickers/group` and `/tickers/name` also keep the VN index symbols named in
`watchlist.json`; a market-cap threshold filters the stock catalog and omits
those non-stock indices. With `mode=all`, the filter affects only VN entries;
other source groups and names are unchanged. The catalog retains delisted
stocks with `listing_status=delisted`; a query below the default cap cutoff can
include them only when they have a known market cap or unknown caps are allowed.

`GET /tickers/directory` returns the stock-only Vietcap listing from the Python
catalog file `vn_tickers.json`. It uses the same default cutoff and filtering
rules as the other metadata endpoints. Set `min_market_cap_vnd=0` and
`include_unknown_market_cap=true` to return every catalog row. Market cap is an
estimated VND snapshot based on the KBS quote board's price and listed-share
count; the response includes the price basis and as-of time.
The API loads this file into process memory. Restart API processes after running
the refresh script so metadata and analysis pick up the new catalog.

- `mode=yahoo` includes stored Yahoo and SJC records. `mode=all` includes all
  configured sources. Each returned row keeps its stored source identity.
- Every `GET` route also accepts `HEAD` and returns headers without a body.
- The public `/openapi.json` and interactive API docs are disabled to preserve
  the compatibility surface. Tests can inspect `app.openapi()`.

## Protected operations

- `POST /tickers/refresh`: JSON body with `interval`, `mode`, and `key`. The key
  must match `REFRESH_SECRET`.
- `GET /sync/{key}`: bearer `SYNC_TOKEN` and required `secret` query parameter.
- `POST /sync/{key}`: bearer `SYNC_TOKEN` and JSON body with `secret` and
  `value`.
- `POST /upload/markdown`: multipart `file`, plus `session_id` and `secret`
  query parameters. Exactly one Markdown file is accepted.
- `GET /uploads/{session_id}/markdown/{filename}`: only public sessions can be
  read; the optional `secret` parameter is ignored.
- `DELETE /uploads/{session_id}`: requires the session's `secret`.

Update the contract tests when route behavior changes. The retired Rust router
is a compatibility reference, not the current route authority.
