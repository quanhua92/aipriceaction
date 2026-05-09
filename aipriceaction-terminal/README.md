# AIPA Terminal

**Live site:** [aipriceaction.com](https://aipriceaction.com) | **GitHub:** [aipriceaction](https://github.com/quanhua92/aipriceaction) | **Frontend:** [aipriceaction-web](https://github.com/quanhua92/aipriceaction-web) | **Docker image:** [`quanhua92/aipriceaction:latest`](https://hub.docker.com/r/quanhua92/aipriceaction) | **Python SDK:** [`aipriceaction` on PyPI](https://pypi.org/project/aipriceaction/) | **AIPA Terminal:** [`aipa-cli` on PyPI](https://pypi.org/project/aipa-cli/)

Textual-based terminal interface for AI-powered ticker analysis. Features streaming chat with thinking/reasoning display, autocomplete, slash commands, and workflow tabs.

## Install

```bash
# Run directly (no install)
uvx aipa-cli

# Or install as a standalone tool
uv tool install aipa-cli

# Use either command
aipa
aipa-cli
```

## Requirements

- Python 3.13+
- An OpenAI-compatible API key (`OPENAI_API_KEY`) — only needed for AI analysis, not for data fetching
- Optional: set `OPENAI_BASE_URL` for custom providers like OpenRouter

## Quick Start

```bash
# Launch the TUI — first run auto-starts interactive setup
aipa

# Or run setup manually at any time
aipa setup

# AI analysis with default question template
aipa analyze VCB

# AI analysis with a custom question
aipa analyze VCB --question "What is the support level and stop loss?"

# Browse available question templates
aipa analyze VCB --questions

# Use a specific question template by index
aipa analyze VCB 2 --question "What is the current trend of VCB?"

# Dump raw context data without calling the LLM (no API key needed)
aipa analyze VCB --context-only

# Override language to English (default is your saved setting)
aipa analyze VCB --lang en

# Use hourly interval with SMA instead of EMA
aipa analyze BTCUSDT --interval 1h --ma-type sma

# Analyze multiple tickers at once
aipa analyze VCB FPT VIC --interval 1D

# Run multi-agent deep research pipeline
aipa deep-research

# Deep research with a custom question and save report to file
aipa deep-research "Which sectors are leading the market?" --output report.md

# Resume a previous deep-research session from checkpoint
aipa deep-research --resume <session-id>

# Fetch raw OHLCV data as a table
aipa get-ohlcv-data VCB --interval 1D --limit 10

# Fetch with date range and no moving averages
aipa get-ohlcv-data VCB --start-date 2026-04-01 --end-date 2026-04-30 --no-ma

# List saved chat sessions
aipa resume

# Open TUI with a resumed session
aipa resume 0
```

## CLI Commands

### `aipa analyze`

AI-powered analysis for one or more tickers. Builds context from OHLCV data and sends it to the LLM with a question.

```
# Default: uses question template 0 with your saved language setting
aipa analyze VCB

# Custom question
aipa analyze VCB --question "Is this a good time to buy?"

# List all question templates (trading opportunity, news, Wyckoff, etc.)
aipa analyze VCB --questions

# Raw data dump only (no LLM call, no API key required)
aipa analyze VCB --context-only

# Multi-ticker analysis
aipa analyze VCB FPT MBB

# Hourly data with SMA indicators
aipa analyze VCB --interval 1h --ma-type sma --limit 50

# Force English output
aipa analyze VCB --lang en
```

| Flag | Description |
|---|---|
| `--question TEXT` | Custom analysis question |
| `--questions` | List available question templates and exit |
| `--context-only` | Dump raw context without LLM (no API key needed) |
| `--interval` | Time interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W` (default: `1D`) |
| `--limit N` | Number of bars (default: 20) |
| `--source` | Filter by source: `vn` or `crypto` |
| `--start-date` / `--end-date` | Date range (e.g. `2026-04-01`) |
| `--reference-ticker` | Reference ticker for market context (default: `VNINDEX`) |
| `--lang` | Language: `en` or `vn` (default: saved setting) |
| `--ma-type` | Moving average type: `ema` or `sma` (default: `ema`) |

### `aipa deep-research`

Multi-agent deep research pipeline: supervisor decomposes into sector subtasks, parallel workers fetch data and analyze, aggregator synthesizes, and reviewer validates data integrity.

```
# Default: comprehensive market overview with all VN sectors
aipa deep-research

# Custom research question
aipa deep-research "Compare banking vs real estate sectors"

# Save final report to file
aipa deep-research --output ~/reports/market-analysis.md

# Resume from a previous checkpoint session
aipa deep-research --resume 019e0cbb-0466-fa9f-d68c-2da40d35a68f

# Force Vietnamese output
aipa deep-research --lang vn
```

| Flag | Description |
|---|---|
| `--resume ID` | Resume from a checkpoint session ID |
| `--output FILE` | Save final report to file |
| `--lang` | Language: `en` or `vn` (default: saved setting) |

### `aipa get-ohlcv-data`

Fetch raw OHLCV data as a table (no LLM involved, works without setup).

```
# Default: daily data with EMA indicators
aipa get-ohlcv-data VCB

# Hourly data, last 10 bars
aipa get-ohlcv-data VCB --interval 1h --limit 10

# Date range, no moving averages
aipa get-ohlcv-data VCB --start-date 2026-04-01 --end-date 2026-04-30 --no-ma

# Crypto data
aipa get-ohlcv-data BTCUSDT --interval 1D --limit 30
```

| Flag | Description |
|---|---|
| `--interval` | Time interval (default: `1D`) |
| `--limit N` | Number of bars |
| `--start-date` / `--end-date` | Date range |
| `--source` | Filter by source: `vn` or `crypto` |
| `--ma` / `--no-ma` | Include/exclude moving averages (default: included) |
| `--ema` | Use EMA instead of SMA |

### `aipa setup`

Interactive first-run configuration. Prompts for language, reference ticker, API key, base URL, and model. Settings are saved to `~/.aipriceaction/settings.json`. Re-running shows current values as defaults.

```
# Run interactively
aipa setup
```

### `aipa resume`

List saved chat sessions or open the TUI with a resumed one. Sessions are auto-saved under `~/.aipriceaction/sessions/`.

```
# List recent sessions
aipa resume

# Open TUI with a specific session
aipa resume 0

# Open TUI by UUID prefix
aipa resume 0194a2b3
```

| Argument | Description |
|---|---|
| `session` | Session ID prefix or list index (omit to list all) |

## First-Run Setup

Commands that require an API key will auto-run `aipa setup` on first use if not yet configured. Commands that don't need an API key always work immediately.

| Command | Setup required? |
|---|---|
| `aipa get-ohlcv-data` | No setup needed |
| `aipa analyze VCB --context-only` | No setup needed |
| `aipa analyze VCB --questions` | No setup needed |
| `aipa resume` | No setup needed |
| `aipa setup` | Runs setup |
| `aipa` | Auto-runs setup first |
| `aipa analyze VCB` | Auto-runs setup first |
| `aipa deep-research` | Auto-runs setup first |

## TUI

Launch the TUI with `aipa`. The interface has six tabs:

- **Chat** — AI-powered chat with streaming responses, thinking/reasoning display, slash commands, and arrow-key history navigation
- **Vietnam** — Browse and search Vietnamese stock tickers
- **Crypto** — Browse and search cryptocurrency tickers
- **Global** — Browse and search global/Yahoo tickers
- **Workflows** — Structured analysis forms with question bank dropdown for ticker analysis and deep research
- **Settings** — Configure API key, model, base URL, and other preferences

### Slash Commands (Chat tab)

```
/analyze VCB                  # Default AI analysis
/analyze VCB 1h               # AI analysis with hourly interval
/analyze VCB 2                # Use question template index 2
/analyze VCB --question What is support?   # Custom question
/export VCB FPT               # Export context to markdown file
/deep-research                # Multi-agent research
/save                         # Export chat to markdown (default: ~/aipriceaction-chat-<id>.md)
/save ~/my-chat.md            # Export chat to custom path
/resume                       # List saved sessions
/resume 0                     # Load session by index
/resume 0194a2b3              # Load session by UUID prefix
/sessions                     # Alias for /resume
/new                          # Start new session (clears history)
/clear                        # Clear chat display only
/exit                         # Quit
```

Chat sessions are auto-saved to `~/.aipriceaction/sessions/` and restored with full LLM context when resumed.

Press `Ctrl+O` in the Chat tab to view thinking/reasoning history.

### Settings Tab

Configure your API key, model, and base URL directly in the TUI. Settings are saved to `~/.aipriceaction/settings.json` and shared across both TUI and CLI.

## Configuration

Settings are loaded from `~/.aipriceaction/settings.json`. You can configure them via the TUI Settings tab or set environment variables:

| Variable | Description | Default |
|---|---|---|
| `OPENAI_API_KEY` | API key for the LLM provider | — |
| `OPENAI_BASE_URL` | Base URL for OpenAI-compatible API | OpenRouter |
| `OPENAI_MODEL` | Model name | `openrouter/owl-alpha` |
| `DATABASE_URL` | Backend API URL | `http://localhost:3000` |

## License

MIT
