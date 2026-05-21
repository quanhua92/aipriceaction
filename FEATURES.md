# AIPriceAction — Feature Reference

Complete inventory of all features across every component. Organized by subsystem with file references.

---

## 1. Data Sources & Markets

| Market | Provider(s) | Ticker examples | Base intervals | Aggregated intervals |
|---|---|---|---|---|
| Vietnamese stocks | VCI, Vietstock, VNDirect, VPS, DNSE | VCB, FPT, HPG, VNINDEX | 1m, 1h, 1D | 5m, 15m, 30m, 4h, 1W, 2W |
| Cryptocurrency | Binance | BTCUSDT, ETHUSDT, SOLUSDT | 1m, 1h, 1D | 5m, 15m, 30m, 4h, 1W, 2W |
| US / International | Yahoo Finance | AAPL, GOOGL, GC=F, SPY | 1m, 1h, 1D | 5m, 15m, 30m, 4h, 1W, 2W |
| SJC Gold | sjc.com.vn | SJC-GOLD | 1D | — |

- **Auto source detection**: CLI/SDK auto-detects market from ticker symbol
- **Multi-source querying**: `mode=all` merges data across providers
- **Trading hours awareness**: VN market 09:00–15:00 ICT (Mon–Fri), crypto 24/7

**Files**: `aipriceaction/src/providers/vci.rs`, `binance.rs`, `yahoo.rs`, `yahoo_raw.rs`, `sjc.rs`, `udf.rs`

---

## 2. Rust Backend

### 2.1 CLI Commands

| Command | Description | Key flags |
|---|---|---|
| `serve` | Start HTTP API server | `--host`, `--port` |
| `status` | Show database status, ticker counts, OHLCV record counts | — |
| `stats` | Benchmark API query performance | `--tickers`, `--intervals`, `--limit`, `--source`, `--with-raw`, `--with-all` |
| `import` | Import CSV files into PostgreSQL | `--market-data`, `--ticker`, `--interval`, `--source` |
| `test-vci` | Test VCI provider connectivity | `--ticker`, `--rate-limit`, `--count-back` |
| `test-binance` | Test Binance provider connectivity | `--ticker`, `--interval`, `--limit`, `--rate-limit` |
| `test-yahoo` | Test Yahoo Finance provider connectivity | `--ticker`, `--rate-limit` |
| `test-proxy` | Test SOCKS5 proxy connectivity against Yahoo Finance | — |
| `test-redis` | Test Redis ZSET connectivity | `--ticker` |
| `test-s3` | Test S3 archive upload | `--ticker`, `--interval`, `--days`, `--create-bucket` |
| `test-udf` | Test TradingView UDF providers | `--ticker`, `--source`, `--rate-limit`, `--count-back` |
| `test-perf` | Run benchmark queries against the database | — |
| `backfill-redis` | One-shot Redis ZSET backfill from PostgreSQL | — |
| `checkpoint` | Create checkpoint file from database | `--candles`, `--output` |
| `generate-company-info` | Fetch company info & financial ratios from VCI | `--ticker`, `--rate-limit`, `--save` |

**File**: `aipriceaction/src/cli.rs`

### 2.2 REST API Endpoints

**Core Endpoints**

| Method | Path | Description | Key parameters |
|---|---|---|---|
| GET | `/health` | Health check with trading hours detection, system stats | — |
| GET | `/tickers` | Main OHLCV data endpoint | `mode`, `interval`, `symbol`, `limit`, `start_date`, `end_date`, `ma`, `ema`, `redis`, `snap`, `format` |
| POST | `/tickers/refresh` | Refresh ticker schedules (requires `REFRESH_SECRET`) | — |
| GET | `/tickers/group` | Get ticker groups by sector/market | `source` |
| GET | `/tickers/name` | Get ticker names from JSON files | — |
| GET | `/tickers/info` | Get merged company information | — |
| GET | `/explorer` | Serve HTML explorer interface | — |

**Analysis Endpoints**

| Method | Path | Description |
|---|---|---|
| GET | `/analysis/top-performers` | Top/worst performing stocks with MA scores |
| GET | `/analysis/ma-scores-by-sector` | Moving average analysis grouped by sector |
| GET | `/analysis/volume-profile` | Volume profile analysis with POC and value areas |
| GET | `/analysis/rrg` | Relative Rotation Graph (RRG) analysis |

**Sync & Upload Endpoints**

| Method | Path | Description |
|---|---|---|
| GET/POST | `/sync/{key}` | KV-sync endpoint for data synchronization (requires `SYNC_TOKEN`) |
| POST | `/upload` | CSV/ZIP file upload handling |

**Files**: `aipriceaction/src/server/api/`, `aipriceaction/src/server/analysis/`, `aipriceaction/src/server/sync.rs`, `aipriceaction/src/server/upload.rs`

### 2.3 Background Workers

**Vietnamese Stock Workers** (trading hours 09:00–15:00 ICT)

| Worker | Description |
|---|---|
| `vci_daily` | Daily OHLCV sync with priority-based scheduling (4 tiers by money flow) |
| `vci_hourly` | Hourly OHLCV sync |
| `vci_minute` | Minute-level OHLCV sync |
| `vci_dividend` | Dividend detection and price adjustment flagging |
| `vci_bootstrap` | Initial historical data bootstrap for new tickers |

**Crypto Workers** (24/7)

| Worker | Description |
|---|---|
| `binance_daily` | Daily crypto price sync |
| `binance_hourly` | Hourly crypto price sync |
| `binance_minute` | Minute crypto price sync |
| `binance_bootstrap` | Initial crypto data bootstrap |

**Yahoo Finance Workers**

| Worker | Description |
|---|---|
| `yahoo_daily` | Daily global market data |
| `yahoo_hourly` | Hourly global market data |
| `yahoo_minute` | Minute global market data |
| `yahoo_bootstrap` | Initial global data bootstrap |

**SJC Gold Workers**

| Worker | Description |
|---|---|
| `sjc_daily` | Daily SJC gold price updates |
| `sjc_bootstrap` | Initial SJC gold price data |

**Utility Workers**

| Worker | Description |
|---|---|
| `health` | Health monitoring and statistics collection |
| `redis_worker` | Redis ZSET cache management and backfill |
| `s3_archive` | S3 data archiving (sync every 60 minutes) |

**Worker toggles** (environment variables): `VCI_WORKERS`, `BINANCE_WORKERS`, `YAHOO_WORKERS`, `SJC_WORKERS`, `REDIS_WORKERS`, `S3_ARCHIVE_WORKER`

**Files**: `aipriceaction/src/workers/`

### 2.4 Data Providers

| Provider | Market | Features |
|---|---|---|
| VCI (`vci.rs`) | Vietnamese stocks | OHLCV + company info, multi-client rate limiting, proxy rotation |
| UDF (`udf.rs`) | Vietnamese stocks | TradingView UDF — Vietstock, VNDirect, DNSE, VPS brokers |
| Binance (`binance.rs`) | Cryptocurrency | OHLCV for all trading pairs |
| Yahoo (`yahoo.rs`, `yahoo_raw.rs`) | US / International | Global market data via Yahoo Finance API |
| SJC (`sjc.rs`) | SJC Gold | Gold price data from sjc.com.vn |

**Shared**: `ohlcv.rs` — generic OHLCV fetch/save helpers

**Files**: `aipriceaction/src/providers/`

### 2.5 Database & Storage

- **PostgreSQL** with sqlx (no ORM)
- **Partitioned tables**: `ohlcv` partitioned by interval (`1m`, `1h`, `1D`) with yearly sub-partitions (2010–2050 pre-created)
- **Auto-migration**: Embedded in binary, run on startup
- **Two data sources**: `source = 'vn'` for VN stocks, `source = 'crypto'` for crypto
- **Raw SQL**: Compile-time checked queries via `query_as!` macro and runtime `query_as`
- **Checkpoint system**: Export database to compressed JSON for offline use

**Files**: `aipriceaction/src/db.rs`, `aipriceaction/src/queries/ohlcv.rs`, `aipriceaction/src/queries/import.rs`, `aipriceaction/src/queries/s3_archive.rs`

### 2.6 Analysis Engine (Server-Side)

| Feature | Description |
|---|---|
| Moving Averages | SMA, EMA, WMA with configurable periods |
| MA Scores | Distance from MA as percentage: `((close - MA) / MA) × 100` |
| Top Performers | Rank tickers by price change, volume, value, MA scores, money flow |
| Volume Profile | Server-side volume-by-price with POC and value area |
| RRG | Relative Rotation Graph for sector rotation analysis |
| OHLCV Aggregation | On-demand aggregation of 1m/1D base data into 5m, 15m, 30m, etc. |

**Files**: `aipriceaction/src/server/analysis/`, `aipriceaction/src/models/indicators.rs`, `aipriceaction/src/models/aggregated_interval.rs`, `aipriceaction/src/services/aggregator.rs`

### 2.7 Caching

| Layer | Description |
|---|---|
| In-memory cache | TTL 10s, 500 entries (Axum server) |
| Redis ZSET | Edge cache for OHLCV data with configurable sizes per interval |
| Redis snapshot | Full cache snapshots for fast reads |

**Files**: `aipriceaction/src/server/cache.rs`, `aipriceaction/src/server/redis_reader.rs`, `aipriceaction/src/redis.rs`

### 2.8 Infrastructure

| Feature | Description |
|---|---|
| Multi-client rate limiting | VCI provider rotates through multiple HTTP clients with proxy support |
| HTTP proxies | `HTTP_PROXIES` env var for SOCKS5 proxy rotation (VCI & Binance) |
| Priority scheduling | Tickers ranked by money flow into 4 tiers with different sync intervals |
| Smart date-range heuristics | Progressive window expansion for limit-only queries |
| CORS & security headers | X-Frame-Options, X-Content-Type-Options, origin validation |
| Token authentication | `REFRESH_SECRET` for refresh endpoint, `SYNC_TOKEN` for sync endpoint |
| OpenTelemetry tracing | Optional distributed tracing via `tracing_otel.rs` |
| Graceful shutdown | Ctrl+C and SIGTERM handling |

**Files**: `aipriceaction/src/constants.rs`, `aipriceaction/src/tracing_otel.rs`

---

## 3. Python SDK (`aipriceaction`)

### 3.1 Core Client

| Feature | Description |
|---|---|
| `get_tickers()` | Fetch ticker metadata with source and sector info |
| `get_ohlcv()` | Main OHLCV data fetcher — S3 historical + live API overlay |
| `fetch_live_data()` | Real-time market data from REST API (2-min memory cache) |
| `get_content_hash()` | Change detection without downloading |
| `download_csv()` | Export OHLCV data to CSV files |
| Multi-ticker support | Fetch multiple tickers in a single call via ThreadPoolExecutor (8 workers) |
| Date range filtering | `start_date` / `end_date` parameters |
| Interval support | Native (1m, 1h, 1D) + aggregated (5m, 15m, 30m, 4h, 1W, 2W) |
| Timezone support | Configurable UTC offset (default UTC+7 for Vietnam) |

**File**: `sdk/aipriceaction-python/src/aipriceaction/client.py`

### 3.2 Technical Indicators

| Indicator | Description |
|---|---|
| SMA | Simple Moving Average — periods: 10, 20, 50, 100, 200 |
| EMA | Exponential Moving Average — periods: 10, 20, 50, 100, 200 |
| MA Score | `((close - MA) / MA) × 100` — distance from moving average |
| Change metrics | Price change %, volume change %, total money flow |

**File**: `sdk/aipriceaction-python/src/aipriceaction/indicators.py`

### 3.3 Volume Profile

| Feature | Description |
|---|---|
| Volume-by-price histogram | From 1-minute data, distributes volume across high-low range |
| POC (Point of Control) | Price level with highest traded volume |
| Value Area | Configurable percentage (60–90%, default 70%) captured around POC |
| VAH / VAL | Value Area High and Low price levels |
| Statistics | Volume-weighted mean, median, standard deviation, skewness |
| Adaptive tick sizing | Market-specific tick sizes (VN stocks by price tier, crypto by price range) |
| Configurable bins | 2–200 bins (default 50) |
| Multi-day ranges | Preferred for reliable support/resistance levels |

**File**: `sdk/aipriceaction-python/src/aipriceaction/volume_profile.py`

### 3.4 Performers Ranking

| Feature | Description |
|---|---|
| `build_performers()` | Rank all tickers by a chosen metric, return top N and worst N |
| Sort metrics | `close_changed`, `volume`, `value`, `volume_changed`, `ma10_score` through `ma200_score`, `total_money_changed` |
| Sector filtering | Filter by group (e.g., `NGAN_HANG`, `CHUNG_KHOAN`) |
| Minimum volume filter | Remove illiquid stocks (default: 10,000 shares for VN) |
| Direction control | `desc` (strongest first) or `asc` (weakest first) |
| Index exclusion | Auto-skip VNINDEX, VN30, etc. from rankings |

**File**: `sdk/aipriceaction-python/src/aipriceaction/performers.py`

### 3.5 AI Context Builder

| Feature | Description |
|---|---|
| Single-ticker context | OHLCV data + MA indicators + system prompt + reference ticker |
| Multi-ticker context | Side-by-side comparison with shared reference ticker |
| Question templates | 6 single-ticker + 7 multi-ticker templates (Wyckoff, VPA, MA, etc.) |
| Reference ticker | Auto-detected: VNINDEX (VN), BTCUSDT (crypto), ^GSPC (global) |
| System prompt framework | Modular sections with EN/VN language support |
| MA type selection | Choose SMA or EMA for context |
| `--context-only` mode | Dump raw context without calling LLM (no API key needed) |

**Files**: `sdk/aipriceaction-python/src/aipriceaction/context.py`, `sdk/aipriceaction-python/src/aipriceaction/single.py`, `sdk/aipriceaction-python/src/aipriceaction/multi.py`, `sdk/aipriceaction-python/src/aipriceaction/system.py`

### 3.6 Multi-Agent System (LangGraph)

| Feature | Description |
|---|---|
| Supervisor agent | Decomposes question into 3–5 sector subtasks, selects top 10 tickers per sector |
| Parallel workers | Fan-out via `Send()` — each worker analyzes one sector with tool access |
| Aggregator agent | Synthesizes sector reports, builds cross-sector ranking table |
| Reviewer agent | Quality gate — checks phantom stocks, MA score fidelity, table completeness |
| Review loop | Up to 5 rounds of reject/fix between reviewer and aggregator |
| Persistent checkpoints | Save/resume interrupted sessions |
| Agent tools | `get_live_data`, `get_ohlcv_data`, `get_ticker_list`, `get_performers`, `get_volume_profile` |
| Structured output | Fake tools as JSON schema definitions for LLM structured output |

**Files**: `sdk/aipriceaction-python/src/aipriceaction/multi.py` (SDK pipeline), `aipriceaction-terminal/src/aipriceaction_terminal/deep_research.py` (CLI orchestration)

### 3.7 OHLCV Aggregation (Client-Side)

| From | To | Method |
|---|---|---|
| 1m | 5m, 15m, 30m | Aggregate 1m candles into larger buckets |
| 1m | 4h | 4-hour aggregation from minute data |
| 1D | 1W, 2W | Weekly aggregation from daily data |

**File**: `sdk/aipriceaction-python/src/aipriceaction/aggregator.py`

### 3.8 Caching & Storage

| Feature | Description |
|---|---|
| S3 disk cache | `~/.aipriceaction/s3-cache/` with `.hash` sidecar files |
| Freshness persistence | `.freshness.json` — TTL 30 min, survives process restarts |
| Two-phase parallel fetch | Probe 10 newest days → read remaining from disk if 5 hash matches |
| 404 caching | Weekend/holiday dates cached as empty hash, skip S3 entirely |
| Early stopping | 10+ consecutive cache hits → skip remaining freshness checks |
| Live data memory cache | 120-second in-memory TTL (no disk persistence) |
| Checkpoint storage | `~/.aipriceaction/checkpoints/` for session persistence |
| Analyze storage | `~/.aipriceaction/analyze/<uuid>/` for input/output persistence |

**File**: `sdk/aipriceaction-python/src/aipriceaction/client.py`

---

## 4. CLI & TUI (`aipa-cli`)

### 4.1 CLI Subcommands

**`aipa analyze`** — AI-Powered Analysis

| Feature | Description |
|---|---|
| Single-ticker analysis | `aipa analyze VCB` |
| Multi-ticker comparison | `aipa analyze VCB TCB MBB CTG` |
| Custom questions | `--question "Wyckoff analysis with phases and price targets"` |
| Question templates | `--questions` to list; `--question` or template index to select |
| Interval selection | `--interval 1m/5m/15m/30m/1h/4h/1D/1W/2W` |
| Date range | `--start-date`, `--end-date` |
| Source filtering | `--source vn/crypto/global` |
| Reference ticker | `--reference-ticker VN30` (override auto-detection) |
| Language | `--lang en/vn` |
| MA type | `--ma-type ema/sma` |
| Context-only mode | `--context-only` — dump context, no LLM call (no API key) |
| Verbose mode | `--verbose` — show thinking/reasoning tokens |
| Streaming | Real-time token streaming with status markers |

**Question Templates (Single-Ticker)**

| # | Template | Focus |
|---|---|---|
| 0 | Trading Opportunity | Wyckoff phases, Smart Money, risk management |
| 1 | News & Events Research | Extreme moves detection (>6.7%), web search |
| 2 | Price Action & Volume | VPA analysis, supply/demand zones |
| 3 | MA Momentum & Trend | MA alignment, crossovers, volume confirmation |
| 4 | Wyckoff Method | Phases, Spring/Upthrust/SOS events, targets |
| 5 | Bob Volman Price Action | Micro pullback entries, breakout/fading setups |

**Question Templates (Multi-Ticker)**

| # | Template | Focus |
|---|---|---|
| 0 | Trading Opportunity | Rank by opportunity quality |
| 1 | Stock Performance Comparison | Price strength, MA momentum, volume |
| 2 | Market Trend Analysis | Sector rotation, accumulation/distribution |
| 3 | Risk & Support/Resistance | S/R levels with volume context |
| 4 | News & Events Research | Extreme moves across multiple tickers |
| 5 | Bob Volman Price Action | Multi-ticker with ranking |
| 6 | Wyckoff Method | Multi-ticker Wyckoff with ranking |

---

**`aipa deep-research`** — Multi-Agent Deep Research

| Feature | Description |
|---|---|
| Market snapshot | Fast dump without `--run` (no API key needed) |
| Full pipeline | `--run` triggers 4-agent pipeline (5–10 min) |
| Custom question | `aipa deep-research "banking sector analysis"` |
| Source selection | `--source vn/crypto/global/sjc` |
| Resume | `--resume ID` — resume interrupted session from checkpoint |
| Output to file | `--output report.md` |
| Language | `--lang en/vn` |

**Pipeline**: Supervisor → Parallel Workers (fan-out) → Aggregator → Reviewer (up to 5 rounds) → Final Report

---

**`aipa get-ohlcv-data`** — Raw OHLCV Data

| Feature | Description |
|---|---|
| Multi-ticker | `aipa get-ohlcv-data VCB TCB MBB` |
| Interval & limit | `--interval 1h --limit 50` |
| Date range | `--start-date 2025-01-01 --end-date 2025-05-01` |
| MA indicators | `--ma` / `--no-ma` / `--ema` |
| Source filter | `--source vn/crypto/global` |
| Clean output | `--no-system-prompt` for raw data only |

---

**`aipa live-data`** — Latest Market Data

| Feature | Description |
|---|---|
| Top tickers | Top N by trading value (default 50) |
| Specific tickers | `aipa live-data VCB TCB MBB` |
| Interval selection | `--interval 1h --top 20` |
| Source filtering | `--source vn/crypto/global/sjc` |

---

**`aipa performers`** — Market Ranking

| Feature | Description |
|---|---|
| Sort metrics | `--sort-by close_changed/volume/value/ma*_score/total_money_changed` |
| Direction | `--direction desc/asc` |
| Sector filter | `--group NGAN_HANG/CHUNG_KHOAN/BAT_DONG_SAN/...` |
| Limit | `--limit N` (default 10) |
| Minimum volume | `--min-volume N` (default 10,000, VN stocks only) |
| Source | `--source vn/crypto/global/sjc` |

---

**`aipa volume-profile`** — Volume Analysis

| Feature | Description |
|---|---|
| Single ticker | `aipa volume-profile VCB` |
| Date range | `--start-date` + `--end-date` (preferred over single date) |
| Bin count | `--bins 2–200` (default 50) |
| Value area % | `--value-area-pct 60–90` (default 70) |
| Source | `--source vn/crypto` |

---

**`aipa ticker-list`** — Ticker Metadata

| Feature | Description |
|---|---|
| Source filter | `--source vn/crypto/global` |
| Sector filter | `--group NGAN_HANG` |
| Compact output | `--compact` for comma-separated format |

---

**`aipa watchlist`** — Watchlist Management

| Subcommand | Description |
|---|---|
| `ls` | List all watchlists (predefined + custom) |
| `get NAME` | Show tickers in a watchlist (space-separated for CLI substitution) |
| `set NAME TICKER...` | Create or update a custom watchlist |
| `rm NAME` | Delete a custom watchlist |

**Predefined Watchlists**

| Name | Tickers | Count |
|---|---|---|
| VN30 | ACB, BID, BSR, CTG, FPT, GAS, GVR, HDB, HPG, LPB, MBB, MSN, MWG, PLX, SAB, SHB, SSB, SSI, STB, TCB, TPB, VCB, VHM, VIB, VIC, VJC, VNM, VPB, VRE, VPL | 30 |
| VINGROUP | VIC, VHM, VRE, VPL | 4 |
| TM | GEX, GEE, VIX, EIB, VGC, IDC | 6 |
| MASAN | MSN, MCH, MSR, MML, VCF, VSN, NET | 7 |
| INDEX | VNINDEX, VN30, VN30F1M, VN100, VNMIDCAP, VNSMALLCAP, VNALLSHARE, VNXALLSHARE, VNFIN, HNX30, VNREAL, VNENE, VNMITECH, VNUTI, VNCONS, VNCOND, VNHEAL, VNIND, VNFINLEAD, VNFINSELECT, VNDIAMOND, VNDIVIDEND | 22 |
| CROSS | VNINDEX, ^GSPC, GC=F, SJC-GOLD, KC=F, BZ=F, BTCUSDT | 7 |

Custom watchlists persisted to `~/.aipriceaction/watchlist.json`.

---

**`aipa resume`** — Session Management

| Feature | Description |
|---|---|
| List sessions | Show saved chat sessions |
| Resume by index | `aipa resume 1` |
| Resume by UUID | `aipa resume abc123...` |

---

**`aipa setup`** — Interactive Configuration

| Setting | Description |
|---|---|
| Language | English or Vietnamese |
| API key | OPENAI_API_KEY for LLM-powered analysis |
| Model | Select LLM model |
| Base URL | Custom API endpoint (e.g., OpenRouter) |

Persisted to `~/.aipriceaction/settings.json`.

**Files**: `aipriceaction-terminal/src/aipriceaction_terminal/cli_commands.py`, `aipriceaction-terminal/src/aipriceaction_terminal/cli.py`, `aipriceaction-terminal/src/aipriceaction_terminal/analyze.py`

### 4.2 TUI Interface (Textual)

| Tab | Key | Features |
|---|---|---|
| Chat | 1 | Streaming AI responses, thinking/reasoning display, slash commands, history navigation |
| Workflows | 2 | Structured analysis forms with question bank |
| Vietnam | 3 | Browse/search VN stock tickers |
| Crypto | 4 | Browse/search crypto tickers |
| Global | 5 | Browse/search global/Yahoo tickers |
| Settings | 6 | Configure API key, model, base URL, language |

**Chat Slash Commands**

| Command | Description |
|---|---|
| `/analyze VCB [1h] [2]` | AI analysis with optional interval and template index |
| `/analyze VCB --question "..."` | Custom question analysis |
| `/export VCB FPT` | Export context to markdown |
| `/deep-research` | Multi-agent research |
| `/save` | Export chat to markdown |
| `/resume` | Load saved session |
| `/new` | Start new session |
| `/clear` | Clear display |
| `/exit` | Quit |

**Files**: `aipriceaction-terminal/src/aipriceaction_terminal/app.py`, `chat.py`, `workflows.py`, `ticker_data.py`, `settings_tab.py`, `session.py`, `widgets/`

### 4.3 Streaming & Status Markers

CLI outputs to two streams: **stdout** = final result, **stderr** = status messages.

| Marker | Meaning |
|---|---|
| `[build]` | Context building / data fetching status |
| `[analyze]` | Analysis question sent to LLM |
| `[tool]` | Tool call being executed |
| `[tool-result]` | Tool execution result |
| `[thinking]` | Agent reasoning tokens (with `--verbose`) |
| `[error]` | Error message |
| `[done]` | Complete, includes total time |
| `[result]` | Final output follows |

**File**: `aipriceaction-terminal/src/aipriceaction_terminal/agents/callbacks.py`

---

## 5. AI Agent Skills

Three installable skills for Claude Code, Gemini CLI, and Codex. Installed via `npx skills add quanhua92/aipriceaction`.

### 5.1 `aipa-data` — Raw OHLCV Data (no API key needed)

Triggers for: price data, candle data, OHLCV data, historical prices, stock quotes, crypto prices, moving averages, volume data, top performers, worst performers, market movers, volume profile, POC, value area, support/resistance by volume.

Available commands:
- `get-ohlcv-data` — Fetch OHLCV with optional MA indicators
- `live-data` — Latest market data
- `performers` — Top/worst performers ranking
- `volume-profile` — Volume-by-price analysis
- `ticker-list` — Ticker metadata

**File**: `skills/aipa-data/`

### 5.2 `aipa-analyze` — AI-Powered Analysis

Triggers for: analyze a ticker, compare stocks, technical analysis, financial market questions, price action analysis, moving average analysis, support/resistance, sector comparison, Wyckoff analysis, trading insights.

Available commands:
- `analyze` — AI-powered single/multi-ticker analysis with streaming
- All `aipa-data` commands for data gathering before analysis

Three analysis modes:
1. **Built-in agent**: `aipa analyze VCB` — uses your API key
2. **Agent-only (no API key)**: `--context-only` — AI agent reads raw context and analyzes
3. **Double agent**: Built-in LLM + your AI agent adds insights on top

**File**: `skills/aipa-analyze/`

### 5.3 `aipa-research` — Multi-Agent Deep Research

Triggers for: deep research, thorough market analysis, sector-wide investigation, comprehensive stock comparison, detailed financial report, comprehensive market overview.

Available commands:
- `deep-research` — Multi-agent pipeline with supervisor/worker/aggregator/reviewer
- All `aipa-data` and `aipa-analyze` commands

Pipeline: Supervisor → Parallel Workers → Aggregator → Reviewer → Final Report

**File**: `skills/aipa-research/`

---

## 6. Infrastructure & DevOps

### 6.1 Docker & Self-Hosting

| Feature | Description |
|---|---|
| Single Docker container | Includes Rust binary + PostgreSQL 18 + pgvector |
| `docker compose up -d` | One-command deployment |
| `.env.example` | Template for environment configuration |
| HAProxy production setup | Documented in `aipriceaction/README.md` |
| Docker Hub | `quanhua92/aipriceaction` image |

### 6.2 S3 Archive

| Feature | Description |
|---|---|
| Daily CSV files | Per-ticker per-interval per-day |
| Yearly CSV files | Per-ticker per-interval per-year for bulk historical data |
| tickers.json | Ticker metadata snapshot |
| Plain HTTP access | No credentials needed — public read via HTTP |
| Auto-sync | Worker syncs from PostgreSQL to S3 every 60 minutes |

**Files**: `aipriceaction/src/workers/s3_archive.rs`, `aipriceaction/src/queries/s3_archive.rs`

### 6.3 Redis

| Feature | Description |
|---|---|
| ZSET cache | Sorted sets for OHLCV data with configurable sizes |
| Snapshot cache | Full cache snapshots for fast reads |
| Backfill worker | One-shot backfill from PostgreSQL |
| Configurable | `REDIS_URL` env var |

**Files**: `aipriceaction/src/redis.rs`, `aipriceaction/src/workers/redis_worker.rs`, `aipriceaction/src/server/redis_reader.rs`

### 6.4 Proxy Support

| Feature | Description |
|---|---|
| SOCKS5 proxies | `HTTP_PROXIES` env var — comma-separated proxy URLs |
| Multi-client rotation | VCI provider rotates through proxies for rate limit avoidance |
| Proxy testing | `test-proxy` CLI command |

**Files**: `aipriceaction/src/providers/vci.rs`, `aipriceaction/src/test_proxy.rs`

### 6.5 Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | required | PostgreSQL connection string |
| `PORT` | 3000 | Server bind port |
| `RUST_LOG` | info | Log level |
| `VCI_WORKERS` | true | Enable VN stock sync workers |
| `VCI_DIVIDEND_WORKER` | true | Enable dividend detection |
| `BINANCE_WORKERS` | false | Enable crypto sync workers |
| `YAHOO_WORKERS` | false | Enable Yahoo Finance workers |
| `SJC_WORKERS` | false | Enable SJC gold workers |
| `REDIS_WORKERS` | false | Enable Redis ZSET worker |
| `S3_ARCHIVE_WORKER` | false | Enable S3 archive worker |
| `HTTP_PROXIES` | — | SOCKS5 proxy URLs for VCI & Binance |
| `DUE_TICKER_FRACTION` | 0.5 | Fraction of due tickers per worker loop |
| `SYNC_TOKEN` | — | Bearer token for /sync endpoint |
| `REFRESH_SECRET` | — | Secret for /tickers/refresh endpoint |

---

## File Reference Index

### Rust Backend

```
aipriceaction/src/
├── main.rs                          Entry point
├── cli.rs                           CLI subcommands
├── constants.rs                     Tuning knobs and configuration
├── db.rs                            PostgreSQL connection & migration
├── redis.rs                         Redis client wrapper
├── tracing_otel.rs                  OpenTelemetry tracing
├── test_proxy.rs                    Proxy testing
├── test_redis.rs                    Redis testing
├── generate_company_info.rs         Company info fetching
├── models/
│   ├── interval.rs                  Interval definitions & parsing
│   ├── ohlcv.rs                     OHLCV data models
│   ├── indicators.rs                SMA, EMA, WMA calculations
│   ├── aggregated_interval.rs       Custom interval aggregation
│   └── checkpoint.rs                Checkpoint data models
├── providers/
│   ├── vci.rs                       VCI (VN stocks) provider
│   ├── udf.rs                       TradingView UDF providers
│   ├── binance.rs                   Binance crypto provider
│   ├── yahoo.rs                     Yahoo Finance provider
│   ├── yahoo_raw.rs                 Raw Yahoo API wrapper
│   ├── sjc.rs                       SJC gold provider
│   └── ohlcv.rs                     Shared OHLCV fetch/save
├── queries/
│   ├── ohlcv.rs                     Main OHLCV SQL queries
│   ├── import.rs                    Database import operations
│   └── s3_archive.rs                S3 archive queries
├── server/
│   ├── api/                         REST API route handlers
│   ├── analysis/                    Analysis endpoints
│   │   ├── performers.rs            Top performers
│   │   ├── ma_scores.rs             MA scores by sector
│   │   ├── volume_profile.rs        Volume profile
│   │   └── rrg.rs                   Relative Rotation Graph
│   ├── cache.rs                     In-memory response cache
│   ├── redis_reader.rs              Redis cache reader
│   ├── sync.rs                      KV-sync endpoint
│   ├── upload.rs                    CSV/ZIP upload handling
│   ├── legacy.rs                    Legacy proxy endpoints
│   └── types.rs                     Shared server types
├── services/
│   ├── ohlcv.rs                     OHLCV service layer
│   ├── aggregator.rs                OHLCV aggregation
│   ├── checkpoint.rs                Checkpoint creation
│   └── import.rs                    CSV import service
├── workers/
│   ├── vci_daily.rs                 VN daily sync
│   ├── vci_hourly.rs                VN hourly sync
│   ├── vci_minute.rs                VN minute sync
│   ├── vci_dividend.rs              Dividend detection
│   ├── vci_shared.rs                VN shared utilities
│   ├── binance_daily.rs             Crypto daily sync
│   ├── binance_hourly.rs            Crypto hourly sync
│   ├── binance_minute.rs            Crypto minute sync
│   ├── binance_shared.rs            Crypto shared utilities
│   ├── yahoo_daily.rs               Yahoo daily sync
│   ├── yahoo_hourly.rs              Yahoo hourly sync
│   ├── yahoo_minute.rs              Yahoo minute sync
│   ├── yahoo_shared.rs              Yahoo shared utilities
│   ├── sjc_daily.rs                 SJC gold daily sync
│   ├── sjc_bootstrap.rs             SJC gold bootstrap
│   ├── sjc_shared.rs                SJC shared utilities
│   ├── health.rs                    Health monitoring
│   ├── redis_worker.rs              Redis backfill
│   └── s3_archive.rs                S3 archiving
└── csv/
    ├── legacy.rs                    Legacy CSV parsing
    └── mod.rs                       CSV module
```

### Python SDK

```
sdk/aipriceaction-python/src/aipriceaction/
├── __init__.py                      Public API exports
├── client.py                        AIPriceAction client (S3 + live API)
├── context.py                       AIContextBuilder for LLM analysis
├── single.py                        Single-ticker context assembly
├── multi.py                         Multi-ticker context + multi-agent pipeline
├── indicators.py                    SMA, EMA, MA score, change metrics
├── performers.py                    Top/worst performer ranking
├── volume_profile.py                Volume-by-price histogram analysis
├── aggregator.py                    Client-side OHLCV aggregation
├── system.py                        System prompt templates (EN/VN)
├── llm_models.py                    LLM model configuration
├── settings.py                      User settings management
├── models.py                        Data models
├── ticker.py                        Ticker metadata
├── exceptions.py                    Custom exceptions
└── checkpoint.py                    Session checkpoint management
```

### CLI & TUI

```
aipriceaction-terminal/src/aipriceaction_terminal/
├── __init__.py                      Version info
├── __main__.py                      Entry point
├── cli.py                           Click CLI definition
├── cli_commands.py                  CLI subcommand implementations
├── cli_setup.py                     First-run setup
├── analyze.py                       Analyze command logic
├── app.py                           Textual TUI application
├── chat.py                          Chat tab with streaming
├── workflows.py                     Workflows tab
├── ticker_data.py                   Ticker browse/search tabs
├── settings_tab.py                  Settings tab
├── session.py                       Chat session management
├── deep_research.py                 Multi-agent deep research pipeline
├── bindings.py                      Key bindings
├── theme.py                         UI theme
├── utils.py                         Utility functions
├── verbose.py                       Verbose logging
├── actions.py                       Shared actions
├── chart.py                         Chart rendering
├── user_settings.py                 User settings persistence
├── user_watchlist.py                Custom watchlist persistence
├── predefined_watchlists.py         Built-in watchlists
├── agents/
│   ├── agent.py                     AgentSession — streaming, retry, memory
│   ├── callbacks.py                 Stream event types
│   ├── config.py                    Agent configuration
│   ├── personas.py                  Agent personas & system prompts
│   └── tools.py                     Tool registry for AI agents
└── widgets/
    ├── chat_input.py                Chat input widget
    ├── ticker_select.py             Ticker selection widget
    └── safe_rich_log.py             Safe rich logging widget
```

### Skills

```
skills/
├── README.md                        Skills overview
├── aipa-data/                       Raw OHLCV data skill
├── aipa-analyze/                    AI analysis skill
└── aipa-research/                   Multi-agent deep research skill
```

### Documentation

```
README.md                            Project overview & quick start
README.vn.md                         Vietnamese version
AGENTS.md                            AI agent workflow reference
CLAUDE.md                            Development guidelines & architecture
DATA_FLOW.md                         S3 + live API + merge pipeline
VOLUME_PROFILE.md                    Volume profile algorithm
PERFORMERS.md                        Performers ranking algorithm
MULTI_AGENTS_ANALYSIS.md             Multi-agent analysis architecture
```
