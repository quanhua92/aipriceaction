# Python API routes

This table lists the current FastAPI routes. Exact parameters, defaults,
validation, and response fields are defined in the route models and tests under
`src/aipriceaction_api/` and `tests/`.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET`, `HEAD` | `/tickers` | OHLCV by symbol, mode, interval, date range, indicators, and format |
| `GET`, `HEAD` | `/tickers/group` | Ticker groups |
| `GET`, `HEAD` | `/tickers/name` | Symbol-to-name map |
| `GET`, `HEAD` | `/tickers/info` | Ticker and snapshot metadata |
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
