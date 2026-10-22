# Repository contributor guide

The supported API implementation is the Python service in `aipriceaction-api/`.
The old Rust API project is archived at `legacy/rust/aipriceaction/`; do not use
its Compose files or Docker workflow for new deployments. Production migration
status is tracked in `aipriceaction-api/MIGRATION.md`.

## Main components

- `aipriceaction-api/src/aipriceaction_api/`: FastAPI app, SQLite store, shared
  history loader, providers, archive, calculations, worker, and CLI.
- `aipriceaction-api/docker-compose.yml`: Python API, worker, initializer, and
  RustFS object store; API host port `8899` by default.
- `sdk/aipriceaction-python/`: client SDK and local analysis helpers.
- `aipriceaction-terminal/`: `aipa` CLI and TUI.
- `skills/`: agent workflows. Root `AGENTS.md` contains the market-data policy
  and mandatory temporary database lifecycle rules.

## Local development

```sh
cd aipriceaction-api
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests scripts
```

For Docker integration, configure a non-empty `REFRESH_SECRET` in the ignored
`.env`, then run `docker compose up -d --build`. Check health at
`http://localhost:8899/health`. The primary website is a separate Vite app;
`aipriceaction-api/scripts/serve_local_web.py` runs a temporary copy and proxies
its API calls without editing the sibling web repository.

## Data safety

Keep active SQLite files, archive objects, receipts, and operational rollback
images intact. For test, benchmark, migration-rehearsal, and restore copies,
follow the temporary-directory and cleanup requirements in root `AGENTS.md`.
Do not create extra full database copies only to demonstrate that backups exist.
Never bulk-delete canonical OHLCV, manifests, or rollback references.

## Documentation rules

Describe measured behavior with dates, scope, and whether cache was bypassed.
Separate local candidate results from production evidence. Treat SJC as quote-derived
synthetic OHLCV, and distinguish API `mode` from stored source identity. Update
`CONTRACT.md` and its tests when endpoint behavior changes.
