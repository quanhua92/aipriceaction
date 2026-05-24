# AIPriceAction

**Financial data platform with AI-powered analysis for Vietnamese stocks, crypto, and global markets.**

[![PyPI - aipriceaction](https://img.shields.io/pypi/v/aipriceaction?label=aipriceaction&color=blue)](https://pypi.org/project/aipriceaction/)
[![PyPI - aipa-cli](https://img.shields.io/pypi/v/aipa-cli?label=aipa-cli&color=blue)](https://pypi.org/project/aipa-cli/)
[![Docker Pulls](https://img.shields.io/docker/pulls/quanhua92/aipriceaction)](https://hub.docker.com/r/quanhua92/aipriceaction)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Tiếng Việt](README.vn.md)

---

## Get started in 30 seconds

```bash
npx skills add quanhua92/aipriceaction
```

Then ask any AI agent:

> "Compare VCB, TCB, MBB, CTG across 1h and 1D — which bank has the strongest trend?"

> "Analyze VIC with volume profile and price action"

> "Compare SJC gold price with global gold (GC=F)"

> "Volume profile for BTCUSDT — where is the POC and value area?"

> "Compare FPT, VNM, HPG with Wyckoff analysis"

> "Show me top performers ranked by trading value"

> "Deep research: which sectors are leading the VN market right now?"

Three skills are installed: **aipa-data** (raw OHLCV), **aipa-analyze** (AI-powered analysis), and **aipa-research** (multi-agent deep research). Works with Claude Code, Gemini CLI, and Codex.

### No install — one file

Download [AGENTS.md](AGENTS.md) and drop it in your project root. Gemini CLI auto-detects it; for Claude Code, copy or symlink it:

```bash
# Option 1: Symlink (auto-updates when AGENTS.md changes)
ln -s AGENTS.md CLAUDE.md

# Option 2: Copy (manual update needed)
cp AGENTS.md CLAUDE.md
```

Any AI agent now has full `aipa-cli` documentation — raw data commands, analysis flags, caching rules, and when-to-use table — without installing skills. Requires Python on the machine — the AI auto-installs `aipa-cli` on first use. Web-only agents (e.g. Claude.ai web) won't work. Note: with copy, AGENTS.md must be downloaded again manually when updated, while skills update easily via `npx skills update`.

---

## Install

| I want to... | Install | One-liner |
|---|---|---|
| Use AI agent — no skill install | Download [AGENTS.md](AGENTS.md) | Auto-installs aipa-cli |
| Add AI agent skills | `npx skills add quanhua92/aipriceaction` | Auto-installs aipa-cli |
| Use the CLI / TUI | `uv tool install aipa-cli` | Terminal analysis |
| Build with Python | `pip install aipriceaction` | Pandas DataFrames |

### Updating

```bash
# Update AI agent skills
npx skills update

# Update CLI (if installed via uv tool)
uv tool upgrade aipa-cli
```

---

## Featured capabilities

### Volume Profile

POC, value area, and volume-by-price histogram from 1-minute data.

```bash
aipa volume-profile VCB
aipa volume-profile BTCUSDT --source crypto --bins 30
aipa volume-profile FPT --start-date 2026-05-05 --end-date 2026-05-09
```

### Top Performers

Rank tickers by price change, volume, MA scores, money flow, or sector.

```bash
aipa performers --sort-by value --limit 5
aipa performers --sort-by ma50_score --group NGAN_HANG
aipa performers --sort-by total_money_changed --source crypto
```

### AI Analysis

Wyckoff, VPA, and smart money signals with structured context.

```bash
aipa analyze VCB --interval 1D
aipa analyze VCB FPT VIC --interval 1h
aipa deep-research --run
```

---

## Architecture

```mermaid
flowchart LR
    subgraph Sources ["📡 Data Sources"]
        VN["VCI · Vietstock · VNDirect<br/>VPS · DNSE<br/>VN Stocks"]
        BIN["Binance<br/>Crypto"]
        YAH["Yahoo Finance<br/>US / Global"]
        SJC["sjc.com.vn<br/>SJC Gold"]
    end

    subgraph Backend ["🦀 Rust Backend"]
        W["Background Workers"]
        PG[("PostgreSQL")]
        RD[("Redis")]
        API["REST API<br/>:3000"]
        W --> PG --> RD --> API
    end

    subgraph S3 ["🗄️ S3 Archive"]
        CSV["Daily CSV<br/>Yearly CSV<br/>+ tickers.json"]
    end

    subgraph Consumers ["👥 Consumers"]
        SDK["Python SDK<br/>pip install aipriceaction"]
        CLI["AIPA CLI<br/>uv tool install aipa-cli"]
        SKILLS["AI Agent Skills<br/>aipa-data · aipa-analyze · aipa-research"]
        WEB["aipriceaction.com<br/>Web Frontend"]
    end

    VN & BIN & YAH & SJC --> W
    API -- "sync every 60m" --> CSV
    CSV -- "plain HTTP<br/>no credentials" --> SDK
    API -- "live overlay" --> SDK
    SDK --> CLI --> SKILLS
    API --> WEB
```

---

## Data sources

| Market | Provider | Ticker examples | Intervals |
|---|---|---|---|
| Vietnamese stocks | VCI / Vietstock / VNDirect / VPS | VCB, FPT, VNINDEX | 1m, 1h, 1D |
| US / intl. stocks | Yahoo Finance | AAPL, GOOGL, GC=F | 1m, 1h, 1D |
| Cryptocurrency | Binance | BTCUSDT, ETHUSDT | 1m, 1h, 1D |
| SJC gold | sjc.com.vn | SJC-GOLD | 1D |

Aggregated intervals (5m, 15m, 30m, 4h, 1W, 2W, 1M) are computed on-demand from base 1m/1D data.

---

## Components

### AI Agent Skills

Three skills for Claude Code, Gemini CLI, and Codex: **aipa-data** fetches raw OHLCV data, **aipa-analyze** runs AI-powered single/multi-ticker analysis with Wyckoff and VPA patterns, **aipa-research** runs a multi-agent supervisor/worker/reviewer pipeline for sector-wide deep research. See [skills/README.md](skills/README.md).

### AIPA Terminal

Textual-based TUI with streaming chat, thinking/reasoning display, autocomplete, and slash commands. Also ships CLI subcommands for non-interactive analysis, volume profile, performers, live data, and deep research. If you're not using `uvx`, ask your AI agent to update: *"update aipa-cli to the latest version"*. See [aipriceaction-terminal/README.md](aipriceaction-terminal/README.md).

### Python SDK

Reads OHLCV data from a public S3 archive via plain HTTP — no credentials needed. Returns pandas DataFrames with optional MA indicators and scores. Includes an AI Context Builder for LLM-powered analysis, LangChain/LangGraph agent integration, and live data overlay. See [sdk/aipriceaction-python/README.md](sdk/aipriceaction-python/README.md).

### Rust Backend

Axum REST API with background workers that sync OHLCV data from multiple providers into PostgreSQL, served through a Redis edge cache. Deploys as a single Docker container. See [aipriceaction/README.md](aipriceaction/README.md).

### Frontend

Human-facing web UI at [aipriceaction.com](https://aipriceaction.com). Source at [aipriceaction-web](https://github.com/quanhua92/aipriceaction-web).

---

## Self-host

```bash
cd aipriceaction
cp .env.example .env
docker compose up -d
```

See [aipriceaction/README.md](aipriceaction/README.md) for the full setup guide, build-from-source instructions, and production deployment with HAProxy.

---

## Repository structure

```
aipriceaction/              Rust backend -- API server, background workers, PostgreSQL + Redis
sdk/
  aipriceaction-python/     Python SDK -- reads from S3 archive, no credentials needed
aipriceaction-terminal/     Python TUI and CLI for AI-powered ticker analysis
skills/                     Claude Code skills for market analysis workflows
```

---

## Deep dives

| Document | What it covers |
|---|---|
| [DATA_FLOW.md](DATA_FLOW.md) | S3 archive, live API, cache freshness, and the merge pipeline |
| [VOLUME_PROFILE.md](VOLUME_PROFILE.md) | Volume-by-price algorithm: POC, value area, and why uniform distribution |
| [PERFORMERS.md](PERFORMERS.md) | Top/worst market rankings: metrics, MA scores, money flow |
| [MULTI_AGENTS_ANALYSIS.md](MULTI_AGENTS_ANALYSIS.md) | AI agent architecture: single-agent analyze vs multi-agent deep-research pipeline |

## Supply chain security

Rust dependencies are managed with [cargo-cooldown](https://crates.io/crates/cargo-cooldown) — a 14-day minimum publish age policy ensures freshly published crates are not picked up until they've been available long enough for maintainers and automated scanners to review. See `aipriceaction/cooldown.toml` for configuration.

```bash
cargo cooldown update   # instead of cargo update
cargo cooldown check    # instead of cargo check
```

## Development

See [CLAUDE.md](CLAUDE.md) for development guidelines, architecture details, and contributor instructions.

## License

MIT
