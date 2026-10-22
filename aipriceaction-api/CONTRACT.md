# Python API route contract

This is a high-level route-family summary for the current FastAPI service, not a
complete per-parameter schema. Route definitions, query/body validation,
authentication, defaults, response fields, and edge behavior are implemented in
`src/aipriceaction_api/app.py` and `uploads.py`; focused contract and
differential tests live under `tests/test_api_contract.py`,
`tests/test_notes_contract.py`, and related modules. The legacy Rust behavior is a
compatibility reference, not the live route authority.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET`, `HEAD` | `/tickers` | OHLCV by symbol(s), mode, interval, limit/date range, MA/EMA, and JSON/CSV format. |
| `GET`, `HEAD` | `/tickers/group` | Groups for the selected mode. |
| `GET`, `HEAD` | `/tickers/name` | Symbol-to-name map for the selected mode. |
| `GET`, `HEAD` | `/tickers/info` | Ticker metadata and available snapshot information. |
| `POST` | `/tickers/refresh` | Secret-guarded refresh scheduling. |
| `GET`, `HEAD` | `/health` | Process, market, ingestion, and storage health summary. |
| `GET`, `HEAD`, `POST` | `/sync/{key}` | Optional bearer-token-protected key/value sync. |
| `GET`, `HEAD` | `/analysis/top-performers` | Ranked daily performers and filters. |
| `GET`, `HEAD` | `/analysis/ma-scores-by-sector` | Sector-level moving-average scores. |
| `GET`, `HEAD` | `/analysis/volume-profile` | Volume-by-price profile for a symbol/date range. |
| `GET`, `HEAD` | `/analysis/rrg` | Relative rotation graph data (`jdk` and `mascore`). |
| `POST` | `/upload/markdown` | Upload a notes Markdown document. |
| `GET`, `HEAD` | `/uploads/{session_id}/markdown/{filename}` | Retrieve a notes Markdown document. |
| `DELETE` | `/uploads/{session_id}` | Delete an upload session. |
| `GET`, `HEAD` | `/explorer` | Packaged API explorer (not included in OpenAPI schema). |
| `GET`, `HEAD` | `/public` | Serve the packaged public `index.html`. |
| `GET`, `HEAD` | `/public/{path}` | Serve packaged static files (not included in OpenAPI schema). |

`/tickers` accepts repeated `symbol` query keys and supports `interval`,
`limit`, `start_date`, `end_date`, `mode`, `ma`, `ema`, `format=csv`, and
cache/snapshot controls. The endpoint can return one ticker, multiple requested
tickers, or the selected market universe. Exact constraints and response fields
are enforced by the Pydantic query models and tests; update those tests when
changing the contract.

The table lists routes and explicit methods only. It does not replace the
request models or tests as the source for per-route parameter bounds, defaults,
authentication, and response schemas.

The compatibility middleware accepts `HEAD` for every `GET` route and returns
the matching headers without a response body. The static `/public` mount also
supports `GET` and `HEAD` for its index and files.

Mutation and private-route wire shapes are:

- `POST /tickers/refresh`: JSON `{ "interval": ..., "mode": ..., "key": ... }`;
  `key` must match the configured `REFRESH_SECRET`.
- `GET /sync/{key}`: bearer `SYNC_TOKEN` plus the required `secret` query
  parameter. `POST /sync/{key}` uses the bearer token and JSON
  `{ "secret": ..., "value": ... }`.
- `POST /upload/markdown`: multipart `file` plus required `session_id` and
  `secret` query parameters. Exactly one Markdown file is accepted.
- `GET /uploads/{session_id}/markdown/{filename}`: retrieval is allowed only
  for public sessions; the optional `secret` query field is ignored.
- `DELETE /uploads/{session_id}`: requires the session `secret` query parameter.

Detailed field constraints and response shapes remain defined by the request
models, route handlers, and the linked contract tests.

`mode=yahoo` includes both stored Yahoo and SJC records. `mode=all` includes all
configured sources. A stored `source` field distinguishes the records.

The root `/openapi.json` and interactive docs are disabled to preserve the
public compatibility surface. Inspect `app.openapi()` in tests for the
application schema. This document intentionally avoids treating the retired Rust
router or its unmounted routes as current endpoints.
