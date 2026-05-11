---
name: aipa-data
description: >
  Fetch raw OHLCV price data using the aipa CLI. Use this skill whenever the
  user asks for price data, candle data, OHLCV data, historical prices, stock
  quotes, crypto prices, moving averages, volume data, or any raw market data
  without AI analysis. Also use when the user wants to inspect what data is
  available, build charts, perform their own calculations, or get numbers for
  a spreadsheet. Even if the user doesn't mention "aipa", trigger this skill
  for any raw financial data request.
---

# aipa-data

Developed by AIPriceAction. More data and documentation at https://aipriceaction.com

## What is aipa

`aipa` is an AI-powered financial analysis CLI for Vietnamese stocks, cryptocurrencies, and global assets. The `get-ohlcv-data` command fetches raw OHLCV price data — no AI, no API key required.

## Installation

```bash
# One-time use (no install needed)
uvx aipa-cli get-ohlcv-data VCB

# Persistent install
uv tool install aipa-cli
aipa get-ohlcv-data VCB
```

## Environment Variables

None required. `get-ohlcv-data` fetches data from public S3 archives — no backend API or API key needed.

## Available Data Sources

- **Vietnamese stocks** (`source: vn`): VIC, VCB, FPT, HPG, VNM, MBB, TCB, CTG, VPB, HDB, etc.
- **Cryptocurrencies** (`source: crypto`): BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, etc.
- **Global/Yahoo** (`source: global/yahoo`): AAPL, TSLA, NVDA, SPY, etc.
- **SJC Gold** (`source: sjc`): SJC gold prices

### Supported Intervals

| Interval | Description | Best For |
|---|---|---|
| `1D` | 1 day (default) | Swing trading, trend analysis |
| `1h` | 1 hour | Intraday analysis, day trading |
| `1m` | 1 minute | Scalping, micro structure |
| `5m` | 5 minutes | Scalping, micro structure |
| `15m` | 15 minutes | Intraday patterns |
| `30m` | 30 minutes | Intraday patterns |
| `4h` | 4 hours | Swing trading, intraday |
| `1W` | 1 week | Medium-term trend analysis |
| `2W` | 2 weeks | Medium-term trend analysis |

---

## `aipa get-ohlcv-data` — Raw OHLCV Data

Fetch raw OHLCV price data without AI analysis. Outputs price data with optional moving averages.

```bash
aipa get-ohlcv-data TICKER [TICKERS...] [options]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `TICKER [TICKERS...]` | — | One or more ticker symbols (auto-uppercased) |
| `--interval` | `1D` | Time interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` |
| `--limit N` | — | Number of bars |
| `--start-date` | — | Start date (e.g. `2025-01-01`) |
| `--end-date` | — | End date (e.g. `2025-05-01`) |
| `--source` | auto-detect | Filter by source: `vn`, `crypto`, `global` |
| `--ma` / `--no-ma` | included | Include/exclude moving averages |
| `--ema` | — | Use EMA instead of SMA |
| `--no-system-prompt` | — | Exclude persona header from output |

---

## Useful Presets

These presets cover the most common data-fetching scenarios. Use them as-is or adapt the parameters.

### Quick Look

```bash
# Last 20 daily candles with EMA (default — fastest)
aipa get-ohlcv-data VCB

# Last 20 daily candles, raw OHLCV only
aipa get-ohlcv-data VCB --no-ma
```

### Trend Analysis (Swing Trading)

```bash
# 50 daily bars with EMA — good for trend identification
aipa get-ohlcv-data VCB --limit 50

# 100 daily bars for long-term trend
aipa get-ohlcv-data VIC --limit 100

# SMA instead of EMA for classic trend analysis
aipa get-ohlcv-data FPT --limit 50 --ema --no-ma
```

### Intraday Data

```bash
# Last 50 hourly candles
aipa get-ohlcv-data BTCUSDT --interval 1h --limit 50

# Last 100 hourly candles for intraday patterns
aipa get-ohlcv-data ETHUSDT --interval 1h --limit 100

# Minute data for scalping analysis
aipa get-ohlcv-data BTCUSDT --interval 1m --limit 100
```

### Date Range

```bash
# Specific date range
aipa get-ohlcv-data FPT --start-date 2025-01-01 --end-date 2025-05-01

# From a date to today
aipa get-ohlcv-data VCB --start-date 2025-04-01

# All data in a range, no MA
aipa get-ohlcv-data HPG --start-date 2025-01-01 --end-date 2025-05-01 --no-ma
```

### Cryptocurrency

```bash
# BTC daily with EMA
aipa get-ohlcv-data BTCUSDT --limit 50

# ETH hourly for intraday
aipa get-ohlcv-data ETHUSDT --interval 1h --limit 100

# SOL raw candles, no MA
aipa get-ohlcv-data SOLUSDT --limit 30 --no-ma

# BNB daily, SMA
aipa get-ohlcv-data BNBUSDT --limit 50 --ema
```

### Vietnamese Stocks

```bash
# Banking sector — all in one call
aipa get-ohlcv-data VCB TCB MBB CTG --limit 30

# Blue chips
aipa get-ohlcv-data VIC FPT VNM --limit 50

# Market index
aipa get-ohlcv-data VNINDEX --limit 50
```

### Global Stocks

```bash
# US tech stocks
aipa get-ohlcv-data AAPL --limit 50
aipa get-ohlcv-data NVDA --limit 50
aipa get-ohlcv-data TSLA --limit 50

# Market index
aipa get-ohlcv-data SPY --limit 100
```

### Minimal Output (for parsing / spreadsheets)

```bash
# Strip persona header for clean data output
aipa get-ohlcv-data VCB --no-system-prompt

# Raw OHLCV only, no MA, no header — cleanest output
aipa get-ohlcv-data VCB --no-ma --no-system-prompt
```

---

## `aipa ticker-list` — List Available Tickers

List available ticker symbols with metadata (name, group, exchange, source). No LLM involved, no API key needed.

Use this to discover what tickers are available before fetching data.

```bash
aipa ticker-list [--source vn|crypto|global|sjc] [--group GROUP] [--compact]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--source` | — | Filter by source: `vn`, `crypto`, `global`, `sjc` |
| `--group` | — | Filter by group (e.g. `NGAN_HANG`, `CHUNG_KHOAN`, `BAT_DONG_SAN`) |
| `--compact` | — | Output symbols only, comma-separated |

### Usage Examples

```bash
# All tickers
aipa ticker-list

# VN stocks only
aipa ticker-list --source vn

# Banking sector
aipa ticker-list --source vn --group NGAN_HANG

# Crypto symbols only (for passing to other commands)
aipa ticker-list --source crypto --compact
```

### Data Fields

Each row includes: ticker, name, group, exchange, source.

---

## `aipa live-data` — Top Tickers by Trading Value

Fetch the latest candle for all tickers or specific tickers. No LLM involved, no API key needed. When no tickers are specified, returns top N tickers sorted by trading value (close × volume) descending.

Use this to quickly identify the most actively traded tickers and get a market overview.

```bash
aipa live-data [TICKERS...] [--top 50] [--interval 1D]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `TICKERS...` | — | Optional ticker symbols (auto-uppercased). Omit for top N by trading value. |
| `--top N` | `50` | Number of top tickers to show when no tickers specified |
| `--interval` | `1D` | Time interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` |
| `--source` | — | Filter by source: `vn`, `crypto`, `global`, `sjc` |

### Usage Examples

```bash
# Top 50 by trading value (broad market overview)
aipa live-data

# Top 10 only
aipa live-data --top 10

# Top 20 hourly
aipa live-data --interval 1h --top 20

# Filter by source: SJC gold
aipa live-data --source sjc

# Filter by source: crypto top 10
aipa live-data --source crypto --top 10

# Specific tickers only
aipa live-data VCB TCB MBB
```

### Data Fields

Each row includes: ticker, time, open, high, low, close, volume, close_changed (%), volume_changed (%), ma10_score, ma50_score.

---

## Interpreting Output

The CLI outputs to two streams:

- **stdout**: The OHLCV data table. This is what you should present to the user.
- **stderr**: Status messages with structured markers.

### Status Markers (stderr)

| Marker | Meaning |
|---|---|
| `[build]` | Data fetching status and timing |
| `[error]` | Error message |
| `[done]` | Fetch complete, includes total time |

### Data Fields

Each row includes: date/time, open, high, low, close, volume. When `--ma` is enabled (default), moving average columns are also included.

---

## When to Use This Skill vs Others

| User Request | Use |
|---|---|
| "Get price data for VCB" | `aipa-data` (this skill) |
| "Show me OHLCV candles for BTC" | `aipa-data` (this skill) |
| "What's the moving average for FPT?" | `aipa-data` (this skill) |
| "Historical prices for VNINDEX" | `aipa-data` (this skill) |
| "What are the top stocks today?" | `aipa live-data` (this skill) |
| "Most active tickers" | `aipa live-data` (this skill) |
| "Show me market overview" | `aipa live-data` (this skill) |
| "What tickers are available?" | `aipa ticker-list` (this skill) |
| "List banking stocks" | `aipa ticker-list --source vn --group NGAN_HANG` (this skill) |
| "Analyze VCB" | `aipa-analyze` (AI analysis) |
| "Compare FPT and VNM" | `aipa-analyze` (AI comparison) |
| "Research the banking sector" | `aipa-research` (multi-agent pipeline) |

Key rule: **raw numbers → `aipa-data`, AI insights → `aipa-analyze`, comprehensive report → `aipa-research`**.

---

## Tips for AI Agents

1. **No API key or backend needed**: `get-ohlcv-data` fetches from public S3 archives. Works without `OPENAI_API_KEY` or a running backend.

2. **Auto-uppercase**: Ticker symbols are automatically uppercased. `vcb`, `btcusdt`, `spy` all work.

3. **Default is 20 bars**: If the user doesn't specify a count, they get 20 bars. Use `--limit 50` or `--limit 100` when more context is needed.

4. **`--no-ma --no-system-prompt` for clean data**: When the user wants raw numbers for their own analysis or a spreadsheet, strip everything except OHLCV with these flags.

5. **For AI analysis, use `aipa analyze`**: If the user wants insights, patterns, or recommendations, use the `aipa-analyze` skill instead. This skill is for raw data only.

6. **Date range vs limit**: Use `--start-date`/`--end-date` for specific periods. Use `--limit` for "last N bars". Don't combine both — the CLI handles conflicts gracefully but the intent is clearer with one approach.

7. **`--ema` flag controls SMA vs EMA**: By default, EMA is shown. Add `--ema` to switch to SMA (yes, the flag name is counterintuitive — it means "use EMA calculation" which overrides the default).

8. **Multi-ticker support**: Pass multiple space-separated tickers to fetch them in one call (e.g. `aipa get-ohlcv-data VCB TCB MBB`). The output table includes a `symbol` column to distinguish rows.

9. **Use `aipa live-data` for market overview**: When you need to identify the most active tickers or get a broad market snapshot, use `aipa live-data` instead of fetching individual tickers. It returns the latest candle sorted by trading value. Call it first with no arguments to discover what's moving, then drill into specific tickers with `get-ohlcv-data`.

10. **Use `aipa ticker-list` to discover tickers**: When you need to know what tickers are available or find tickers in a specific sector, use `aipa ticker-list`. Add `--group` to filter by sector (e.g. `NGAN_HANG` for banking) and `--compact` to get a comma-separated list for passing to other commands.
