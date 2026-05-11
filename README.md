# AIPriceAction

**Live site:** [aipriceaction.com](https://aipriceaction.com) | **GitHub:** [aipriceaction](https://github.com/quanhua92/aipriceaction) | **Frontend:** [aipriceaction-web](https://github.com/quanhua92/aipriceaction-web) | **Docker image:** [`quanhua92/aipriceaction:latest`](https://hub.docker.com/r/quanhua92/aipriceaction) | **Python SDK:** [`aipriceaction` on PyPI](https://pypi.org/project/aipriceaction/) | **AIPA Terminal:** [`aipa-cli` on PyPI](https://pypi.org/project/aipa-cli/)

Market data platform for Vietnamese stocks, US stocks, cryptocurrencies, commodities, and SJC gold. Fetches, stores, and serves OHLCV data with technical indicators through a REST API backed by PostgreSQL and Redis. This repository contains the Rust backend, client SDKs (Python and TypeScript), a terminal-based analysis tool, and Claude Code skills for AI-powered market research.

## Repository structure

```
aipriceaction/              Rust backend -- API server, background workers, PostgreSQL + Redis
sdk/
  aipriceaction-python/     Python SDK -- reads from S3 archive, no credentials needed
  aipriceaction-js/         TypeScript SDK -- type-safe API client
aipriceaction-terminal/     Python TUI and CLI for AI-powered ticker analysis
skills/                     Claude Code skills for market analysis workflows
```

## Data sources

| Market            | Provider      | Ticker examples        | Intervals        |
| ----------------- | ------------- | ---------------------- | ---------------- |
| Vietnamese stocks | VCI / Vietstock / VNDirect / VPS | VCB, FPT, VNINDEX      | 1m, 1h, 1D       |
| US / intl. stocks | Yahoo Finance | AAPL, GOOGL, GC=F      | 1m, 1h, 1D       |
| Cryptocurrency    | Binance       | BTCUSDT, ETHUSDT       | 1m, 1h, 1D       |
| SJC gold          | sjc.com.vn    | SJC-GOLD               | 1D               |

## Rust backend

Axum-based REST API with background workers that sync OHLCV data from multiple providers into PostgreSQL. Data is served through a Redis edge cache for low-latency reads, with automatic fallback to PostgreSQL when Redis is unavailable. Supports aggregated intervals (5m, 15m, 30m, 4h, 1W, 2W, 1M) computed on-demand from base 1m/1D data. Deploys as a single Docker container or at scale behind HAProxy with rolling updates.

Exports data to a self-hosted S3 archive as per-day CSV files with enriched ticker metadata, which the SDKs consume directly.

See [aipriceaction/README.md](aipriceaction/README.md) for setup, API reference, environment variables, and configuration.

## Claude Code skills

Agent skills for financial market analysis, compatible with Claude Code, Gemini CLI, and Codex. Three skills cover different use cases:

- **aipa-data** -- fetches raw OHLCV price data (candle data, moving averages, historical prices) without AI analysis
- **aipa-analyze** -- AI-powered single and multi-ticker analysis with technical indicators, Wyckoff patterns, and trading insights
- **aipa-research** -- multi-agent deep research pipeline with a supervisor/worker/reviewer architecture for sector-wide investigation and comprehensive market reports

See [skills/README.md](skills/README.md) for installation and usage.

## AIPA Terminal

Textual-based terminal interface for AI-powered ticker analysis. Provides both an interactive TUI with streaming chat, thinking/reasoning display, autocomplete, and slash commands, plus a set of CLI subcommands for non-interactive use.

The `analyze` command builds structured context from OHLCV data and sends it to an LLM with a question. It supports single and multi-ticker analysis, customizable question templates (Wyckoff, support/resistance, momentum, news impact), and configurable interval and MA type. The `deep-research` command runs a multi-agent pipeline that produces comprehensive market reports with session checkpointing for resume.

Other commands include `live-data` for browsing current market prices (with filtering by source and top-N sorting), `ticker-list` for discovering available tickers, and `get-ohlcv-data` for dumping raw candle data without calling an LLM. Works with any OpenAI-compatible provider via `OPENAI_BASE_URL`.

```bash
# Run directly (no install)
uvx aipa-cli

# Or install as a standalone tool
uv tool install aipa-cli
```

See [aipriceaction-terminal/README.md](aipriceaction-terminal/README.md) for full documentation and CLI reference.

## Python SDK

Reads OHLCV data from a public S3 archive via plain HTTP -- no API credentials or S3 SDK needed. Data is returned as pandas DataFrames with optional moving average indicators and MA scores. The SDK auto-detects the market from the ticker symbol (priority: vn > yahoo > sjc > crypto), or you can override with an explicit source parameter.

Beyond data access, the SDK includes an AI Context Builder (`AIContextBuilder`) that constructs structured context strings for LLM-powered investment analysis. The builder assembles composable system prompts from togglable sections (data policy, analysis framework, MA score explanation, disclaimer), supports multi-ticker and multi-timeframe contexts, and optimizes for LLM KV cache by reusing the same context prefix across follow-up questions. A built-in `answer()` method calls any OpenAI-compatible LLM directly.

For agent frameworks, the SDK integrates with LangChain and LangGraph. The provided examples demonstrate ReAct agents with tool-calling and multi-agent parallel research pipelines using `Send()`. Live data can be overlaid on top of S3 data via `use_live=True`, which fetches from the REST API and falls back to archive-only when the API is unreachable.

```bash
pip install aipriceaction
```

See [sdk/aipriceaction-python/README.md](sdk/aipriceaction-python/README.md) for documentation and examples.

## TypeScript SDK

Type-safe API client for the AIPriceAction REST API, works in both Node.js and browsers. Uses the native fetch API with zero runtime dependencies, and includes automatic retry with exponential backoff. Covers ticker queries, top performers, MA scores, sector analysis, RRG charts, and CSV export.

```bash
pnpm add aipriceaction-js
```

See [sdk/aipriceaction-js/README.md](sdk/aipriceaction-js/README.md) for documentation.

## Quick start

The backend runs in Docker with PostgreSQL and Redis included:

```bash
cd aipriceaction
cp .env.example .env
docker compose up -d
```

See [aipriceaction/README.md](aipriceaction/README.md) for the full setup guide, build-from-source instructions, and production deployment with HAProxy.

## Development

See [CLAUDE.md](CLAUDE.md) for development guidelines, architecture details, and contributor instructions.

## License

MIT
