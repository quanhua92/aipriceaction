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

---

## `aipa get-ohlcv-data` — Raw OHLCV Data

Fetch raw OHLCV price data without AI analysis. Outputs price data with optional moving averages.

```bash
aipa get-ohlcv-data TICKER [options]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `TICKER` | — | Ticker symbol (auto-uppercased) |
| `--interval` | `1D` | Time interval: `1m`, `1h`, `1D` |
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
# Banking sector quick look
aipa get-ohlcv-data VCB --limit 30
aipa get-ohlcv-data TCB --limit 30
aipa get-ohlcv-data MBB --limit 30
aipa get-ohlcv-data CTG --limit 30

# Market index
aipa get-ohlcv-data VNINDEX --limit 50

# Blue chips
aipa get-ohlcv-data VIC --limit 50
aipa get-ohlcv-data FPT --limit 50
aipa get-ohlcv-data VNM --limit 50
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

8. **One ticker per call**: Unlike `analyze`, `get-ohlcv-data` takes a single ticker. To compare multiple tickers, run multiple commands.
