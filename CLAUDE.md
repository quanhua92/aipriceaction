# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Financial data management system for Vietnamese stock market and cryptocurrency OHLCV data. Rust backend with PostgreSQL storage, background sync workers, and a REST API serving price data with technical indicators.

## Build & Run Commands

All commands run from the `aipriceaction/` directory (the Rust crate root):

```bash
# Build
cargo build --release

# Serve (requires DATABASE_URL env var)
cargo run -- serve --port 3000

# Run tests
cargo test

# Lint check
cargo check

# CLI utilities (require compiled binary and DATABASE_URL)
./target/release/aipriceaction status
./target/release/aipriceaction test-vci --ticker VCB
./target/release/aipriceaction test-binance --ticker BTCUSDT
./target/release/aipriceaction test-perf
./target/release/aipriceaction stats --tickers VCB,FPT --intervals 1D
```

Docker alternative: `docker compose up -d` (includes PostgreSQL 18 with pgvector).

## Architecture

```
aipriceaction/src/
├── main.rs              # Entry point, delegates to cli::run()
├── cli.rs               # clap-based CLI: serve, status, stats, import, test-vci, test-binance, test-perf
├── constants.rs         # All tuning knobs (worker intervals, priority tiers, cache config, API defaults)
├── db.rs                # PostgreSQL connection via sqlx + auto-migration on startup
├── models/              # Data structures (Interval enum, OHLCV rows, ticker metadata)
├── providers/           # External API clients
│   ├── vci.rs           # VCI/Vietcap — Vietnamese stock data with multi-client rate limiting
│   ├── binance.rs       # Binance — cryptocurrency data
│   └── ohlcv.rs         # Shared OHLCV fetch/save helpers
├── queries/             # SQL query implementations (ohlcv joins, batch queries, aggregations)
├── server/              # Axum HTTP server
│   ├── api.rs           # Route handlers (/tickers, /health, /tickers/group, /upload)
│   ├── cache.rs         # In-memory response cache (TTL 10s, 500 entries)
│   ├── analysis/        # Top performers, MA scores, sector rotation endpoints
│   ├── upload.rs        # CSV/ZIP file upload handling
│   └── legacy.rs        # Legacy GitHub proxy endpoints (deprecated)
├── services/            # Business logic (OHLCV service layer, CSV import)
├── workers/             # Background data sync (tokio::spawn)
│   ├── vci_daily|hourly|minute|dividend*.rs   # VN stock workers (trading hours 9:00-15:00 ICT)
│   └── binance_daily|hourly|minute|bootstrap*.rs  # Crypto workers (24/7)
└── csv/                 # CSV parsing for legacy import
```

### Data Flow

1. **Workers** fetch OHLCV from VCI/Binance APIs and upsert into PostgreSQL
2. **Queries** read from partitioned `ohlcv` tables joined with `tickers` metadata
3. **Server** serves data via Axum REST endpoints with in-memory caching
4. **Aggregated intervals** (5m, 15m, 30m, 1W, 2W, 1M) are computed on-demand from base 1m/1D data

### Database Design

- **Partitioning**: `ohlcv` table partitioned by interval (`1m`, `1h`, `1D`), with yearly sub-partitions for minute/hourly (2010-2050 pre-created)
- **Migrations**: Embedded in binary via sqlx, run automatically on startup
- **Two data sources**: `source = 'vn'` for Vietnamese stocks, `source = 'crypto'` for cryptocurrencies
- **No ORM**: Raw SQL with sqlx `query_as`

### Key Design Decisions

- **Rust edition 2024** with Tokio async runtime
- **sqlx** with compile-time checked queries (uses `query_as!` macro in some places, runtime `query_as` in others)
- **Priority scheduling**: VCI tickers ranked by money flow into 4 tiers with different sync intervals (see `constants.rs::vci_worker::priority`)
- **Smart date-range heuristics**: Progressive window expansion for limit-only queries to avoid scanning full partitions
- **Multi-client rate limiting**: VCI provider rotates through multiple HTTP clients with proxy support (`HTTP_PROXIES` env var)
- **Two HTTP clients**: `reqwest` (async, used by server/workers) and `isahc` (sync, used by VCI provider for its specific requirements)

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | PostgreSQL connection string |
| `PORT` | No | 3000 | Server bind port |
| `RUST_LOG` | No | info | Log level |
| `VCI_WORKERS` | No | true | Enable VN stock sync workers |
| `VCI_DIVIDEND_WORKER` | No | true | Enable dividend detection worker |
| `BINANCE_WORKERS` | No | true | Enable crypto sync workers |
| `HTTP_PROXIES` | No | — | Comma-separated proxy URLs for VCI & Binance |

## SDK

TypeScript SDK in `sdk/aipriceaction-js/`, built with pnpm. See `sdk/aipriceaction-js/README.md` for usage.
