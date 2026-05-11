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

# Market snapshot only (default, fast, no API key needed)
aipa deep-research

# Market snapshot for crypto
aipa deep-research --source crypto

# Run the full multi-agent deep research pipeline (5-10 min)
aipa deep-research --run

# Full pipeline for global stocks
aipa deep-research --source global --run

# Full pipeline with a custom question and save report to file
aipa deep-research --run "Which sectors are leading the market?" --output report.md

# Resume a previous deep-research session from checkpoint
aipa deep-research --run --resume <session-id>

# Fetch raw OHLCV data as a table
aipa get-ohlcv-data VCB --interval 1D --limit 10

# Fetch multiple tickers at once
aipa get-ohlcv-data VCB TCB MBB --limit 10

# Fetch with date range and no moving averages
aipa get-ohlcv-data VCB --start-date 2026-04-01 --end-date 2026-04-30 --no-ma

# Top tickers by trading value (latest candle)
aipa live-data

# Top 10 VN stocks by trading value
aipa live-data --top 10

# SJC gold data
aipa live-data --source sjc

# Crypto top 10
aipa live-data --source crypto --top 10

# Latest candle for specific tickers
aipa live-data VCB TCB MBB

# List available tickers
aipa ticker-list

# List tickers by source and group
aipa ticker-list --source vn --group NGAN_HANG

# Compact output (symbols only)
aipa ticker-list --source crypto --compact

# Top 10 VN performers by price change (default)
aipa performers

# Top 5 by volume
aipa performers --sort-by volume --limit 5

# Top 10 by trading value
aipa performers --sort-by value --limit 10

# Volume profile for VCB today
aipa volume-profile VCB

# Volume profile for BTCUSDT
aipa volume-profile BTCUSDT --source crypto --bins 30

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
| `--interval` | Time interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` (default: `1D`) |
| `--limit N` | Number of bars (default: 20) |
| `--source` | Filter by source: `vn` or `crypto` |
| `--start-date` / `--end-date` | Date range (e.g. `2026-04-01`) |
| `--reference-ticker` | Override auto-detected reference ticker (auto: `VNINDEX` for VN stocks, `BTCUSDT` for crypto, `^GSPC` for global) |
| `--lang` | Language: `en` or `vn` (default: saved setting) |
| `--ma-type` | Moving average type: `ema` or `sma` (default: `ema`) |

### `aipa deep-research`

Multi-agent deep research pipeline: supervisor decomposes into sector subtasks, parallel workers fetch data and analyze, aggregator synthesizes, and reviewer validates data integrity. By default, dumps a market snapshot only. Add `--run` to execute the full pipeline (takes 5-10 minutes). Use `--source` to target different markets — the supervisor, workers, and default question all adapt to the selected source.

```
# Default: VN market snapshot only (fast, no API key needed)
aipa deep-research

# Crypto market snapshot
aipa deep-research --source crypto

# Run the full multi-agent pipeline
aipa deep-research --run

# Full pipeline for global stocks
aipa deep-research --source global --run

# Full pipeline with custom research question
aipa deep-research --run "Compare banking vs real estate sectors"

# Save final report to file
aipa deep-research --run --output ~/reports/market-analysis.md

# Resume from a previous checkpoint session
aipa deep-research --run --resume 019e0cbb-0466-fa9f-d68c-2da40d35a68f

# Force Vietnamese output (full pipeline)
aipa deep-research --run --lang vn
```

| Flag | Description |
|---|---|
| `--run` | Run the full multi-agent pipeline (5-10 min). Default is market snapshot only. |
| `--source` | Data source: `vn` (default), `crypto`, `global`, `sjc` |
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
| `--interval` | Time interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` (default: `1D`) |
| `--limit N` | Number of bars |
| `--start-date` / `--end-date` | Date range |
| `--source` | Filter by source: `vn` or `crypto` |
| `--ma` / `--no-ma` | Include/exclude moving averages (default: included) |
| `--ema` | Use EMA instead of SMA |

Note: Pass multiple space-separated ticker symbols (e.g. `VCB TCB MBB`) to fetch them in one call.

### `aipa live-data`

Fetch the latest candle for all tickers or specific tickers. No LLM involved, no API key needed. When no tickers are specified, returns top N tickers sorted by trading value (close × volume) descending.

```
# Top 50 by trading value (default)
aipa live-data

# Top 10
aipa live-data --top 10

# Hourly interval
aipa live-data --interval 1h

# Specific tickers
aipa live-data VCB TCB MBB
```

| Flag | Description |
|---|---|
| `TICKERS...` | Optional ticker symbols (omit for top N) |
| `--top N` | Number of top tickers to show (default: 50) |
| `--interval` | Time interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` (default: `1D`) |
| `--source` | Filter by source: `vn`, `crypto`, `global`, `sjc` |

### `aipa ticker-list`

List available ticker symbols with metadata (name, group, exchange, source). No LLM involved, no API key needed.

```
# All tickers
aipa ticker-list

# Filter by source
aipa ticker-list --source vn

# Filter by group (e.g. banking sector)
aipa ticker-list --source vn --group NGAN_HANG

# Compact: symbols only, comma-separated
aipa ticker-list --source crypto --compact
```

| Flag | Description |
|---|---|
| `--source` | Filter by source: `vn`, `crypto`, `global`, `sjc` |
| `--group` | Filter by group (e.g. `NGAN_HANG`, `CHUNG_KHOAN`, `BAT_DONG_SAN`) |
| `--compact` | Output symbols only, comma-separated |

### `aipa performers`

Rank top and worst performers by any metric. Fetches live daily data with MA indicators — no API key needed. Defaults to VN stocks.

```
# Top 10 VN stocks by price change (default)
aipa performers

# Top 5 by volume, ascending
aipa performers --sort-by volume --direction asc --limit 5

# Top 20 by MA50 score
aipa performers --sort-by ma50_score --limit 20

# Crypto performers
aipa performers --source crypto --limit 5

# Banking sector only
aipa performers --group NGAN_HANG --sort-by value

# Securities sector top gainers
aipa performers --group CHUNG_KHOAN --sort-by close_changed --limit 5
```

| Flag | Description |
|---|---|
| `--sort-by` | Metric: `close_changed` (default), `volume`, `value`, `volume_changed`, `ma10_score`, `ma20_score`, `ma50_score`, `ma100_score`, `ma200_score`, `total_money_changed` |
| `--direction` | `desc` (default) or `asc` |
| `--limit N` | Entries per list (default: 10) |
| `--min-volume N` | Min volume filter for VN tickers (default: 10000) |
| `--source` | Data source: `vn` (default), `crypto`, `global`, `yahoo`, `sjc` |
| `--group` | Filter by sector (e.g. `NGAN_HANG`, `CHUNG_KHOAN`, `BAT_DONG_SAN`) |

### `aipa volume-profile`

Volume-by-price histogram analysis from 1-minute data. Shows POC, value area, volume-weighted statistics, and a visual bar chart — no API key needed.

```
# Today's profile for VCB
aipa volume-profile VCB

# Specific date
aipa volume-profile VCB --date 2026-05-09

# Crypto with fewer bins
aipa volume-profile BTCUSDT --source crypto --bins 30

# Date range with custom value area
aipa volume-profile FPT --start-date 2026-05-05 --end-date 2026-05-09 --value-area-pct 80
```

| Flag | Description |
|---|---|
| `TICKER` | Ticker symbol (required) |
| `--date` | Single date (YYYY-MM-DD), defaults to today |
| `--start-date` / `--end-date` | Date range |
| `--source` | Source for tick size logic: `vn` (default), `crypto`, `global`, `yahoo`, `sjc` |
| `--bins N` | Number of price bins (default: 50, range: 2–200) |
| `--value-area-pct` | Value area target % (default: 70, range: 60–90) |

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
| `aipa live-data` | No setup needed |
| `aipa ticker-list` | No setup needed |
| `aipa performers` | No setup needed |
| `aipa volume-profile VCB` | No setup needed |
| `aipa analyze VCB --context-only` | No setup needed |
| `aipa analyze VCB --questions` | No setup needed |
| `aipa resume` | No setup needed |
| `aipa setup` | Runs setup |
| `aipa` | Auto-runs setup first |
| `aipa analyze VCB` | Auto-runs setup first |
| `aipa deep-research` | No setup needed (market snapshot only) |
| `aipa deep-research --run` | Auto-runs setup first |

## TUI

Launch the TUI with `aipa`. The interface has six tabs:

- **Chat** — AI-powered chat with streaming responses, thinking/reasoning display, slash commands, and arrow-key history navigation
- **Workflows** — Structured analysis forms with question bank dropdown for ticker analysis and deep research
- **Vietnam** — Browse and search Vietnamese stock tickers
- **Crypto** — Browse and search cryptocurrency tickers
- **Global** — Browse and search global/Yahoo tickers
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
