---
name: aipa-analyze
description: >
  AI-powered stock and crypto analysis using the aipa CLI. Use this skill
  whenever the user asks to analyze a ticker, compare stocks, get technical
  analysis, or answer any financial market question about Vietnamese stocks
  (VIC, VCB, FPT...), cryptocurrencies (BTC, ETH...), or global assets.
  Also use for price action analysis, moving average analysis, support/resistance
  questions, sector comparison, Wyckoff analysis, or trading insights.
  For raw price data without AI, use the aipa-data skill instead.
---

# aipa-analyze

Developed by AIPriceAction. More data and documentation at https://aipriceaction.com

## What is aipa

`aipa` is an AI-powered financial analysis CLI for Vietnamese stocks, cryptocurrencies, and global assets. It combines OHLCV price data with LLM analysis to produce actionable trading insights using frameworks like Wyckoff, VPA (Volume Price Analysis), and MA momentum scoring.

## Installation

```bash
# One-time use (no install needed)
uvx aipa-cli analyze VCB

# Persistent install
uv tool install aipa-cli
aipa analyze VCB
```

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes (for AI) | — | API key for the LLM provider |
| `OPENAI_BASE_URL` | No | OpenRouter | Base URL for OpenAI-compatible API |
| `OPENAI_MODEL` | No | `openrouter/owl-alpha` | Model to use for analysis |

Run `aipa setup` for interactive first-run configuration. Settings are saved to `~/.aipriceaction/settings.json`.

## Available Data Sources

- **Vietnamese stocks** (`source: vn`): VIC, VCB, FPT, HPG, VNM, MBB, TCB, CTG, VPB, HDB, etc.
- **Cryptocurrencies** (`source: crypto`): BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, etc.
- **Global/Yahoo** (`source: global/yahoo`): AAPL, TSLA, NVDA, SPY, etc.
- **SJC Gold** (`source: sjc`): SJC gold prices

### Supported Intervals

| Interval | Description |
|---|---|
| `1m` | 1 minute |
| `5m` | 5 minutes |
| `15m` | 15 minutes |
| `30m` | 30 minutes |
| `1h` | 1 hour |
| `4h` | 4 hours |
| `1D` | 1 day (default) |
| `1W` | 1 week |

Note: All intervals work natively — `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W`. Non-native intervals are aggregated client-side from base data.

---

## `aipa analyze` — AI Analysis

Run AI-powered technical analysis on one or more tickers.

```bash
aipa analyze TICKER [TICKERS...] [options]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `TICKER [TICKERS...]` | — | One or more ticker symbols (auto-uppercased) |
| `--interval` | `1D` | Time interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` |
| `--limit N` | `20` | Number of bars/candles to fetch |
| `--source` | auto-detect | Filter by source: `vn`, `crypto`, `global` |
| `--start-date` | — | Start date (e.g. `2025-04-01`) |
| `--end-date` | — | End date (e.g. `2025-04-30`) |
| `--reference-ticker` | auto-detect | Reference ticker: `VNINDEX` (VN stocks), `BTCUSDT` (crypto), `^GSPC` (global) |
| `--lang` | saved setting | Language: `en` or `vn` |
| `--ma-type` | `ema` | Moving average type: `ema` or `sma` |
| `--question TEXT` | template 0 | Custom analysis question (overrides templates) |
| `--questions` | — | List available question templates and exit |
| `--context-only` | — | Dump raw context without calling LLM (no API key needed) |
| `--no-system-prompt` | — | Exclude system prompt from context output |
| `--verbose` | — | Show thinking tokens during analysis |

### Usage Examples

```bash
# Basic single-ticker analysis
aipa analyze VCB

# Multi-ticker comparison
aipa analyze VCB TCB MBB CTG VPB

# Specific interval and bar count
aipa analyze BTCUSDT --interval 4h --limit 50

# Custom date range
aipa analyze FPT --start-date 2025-01-01 --end-date 2025-05-01

# Custom analysis question
aipa analyze VIC --question "What is the Wyckoff phase and what are the key price targets?"

# Vietnamese language output
aipa analyze VNM --lang vn

# Dump raw data context without AI analysis (no API key needed)
aipa analyze VCB --context-only

# Show thinking tokens during analysis
aipa analyze HPG --verbose

# Override auto-detected reference ticker
aipa analyze VCB --reference-ticker VN30

# Specific source
aipa analyze BTCUSDT --source crypto
```

---

## When to Use This Skill vs Others

| User Request | Use |
|---|---|
| "Analyze VCB" | `aipa analyze VCB` |
| "Compare FPT and VNM" | `aipa analyze FPT VNM` |
| "Wyckoff analysis for HPG" | `aipa analyze HPG --question "Wyckoff analysis with phases, events, and price targets"` |
| "Research the banking sector deeply" | Use the `aipa-research` skill instead |
| "Get price data for VCB" | Use the `aipa-data` skill instead |
| "Show me OHLCV candles for BTC" | Use the `aipa-data` skill instead |
| "What are the top stocks today?" | `aipa live-data` (no AI, quick market overview) |
| "What banking stocks are available?" | `aipa ticker-list --source vn --group NGAN_HANG` |

Key rule: **AI insights → `aipa-analyze`, raw numbers → `aipa-data`, comprehensive report → `aipa-research`, quick market overview → `aipa live-data`**.

---

## Question Templates

The CLI includes pre-built analysis question templates organized by framework. Use `--questions` to list all available templates.

### Single-Ticker Templates (English)

| Index | Template | Description |
|---|---|---|
| 0 | Trading Opportunity | Wyckoff phases, Smart Money behavior, deployment roadmap, risk management |
| 1 | News & Events Research | Detect extreme moves (>6.7% or Volume >150%), web search for causes |
| 2 | Price Action & Volume | VPA analysis, smart money footprints, supply/demand zones |
| 3 | MA Momentum & Trend | MA alignment, crossover detection, volume confirmation |
| 4 | Wyckoff Method | Wyckoff phases, Spring/Upthrust/SOS events, price targets |
| 5 | Bob Volman Price Action | Micro pullback entries, breakout/fading setups, trade planning |

### Multi-Ticker Templates (English)

| Index | Template | Description |
|---|---|---|
| 0 | Trading Opportunity | Analyze all tickers, rank by opportunity quality |
| 1 | Stock Performance Comparison | Compare price action strength, MA momentum, volume |
| 2 | Market Trend Analysis | Sector rotation via MA scores, accumulation/distribution |
| 3 | Risk & Support/Resistance | Map S/R levels with volume context |
| 4 | News & Events Research | Detect extreme moves across multiple tickers |
| 5 | Bob Volman Price Action | Applied to multiple tickers with ranking |
| 6 | Wyckoff Method | Multi-ticker Wyckoff analysis with ranking |

Vietnamese translations exist for all templates (use `--lang vn`).

### Using Templates

```bash
# List all templates
aipa analyze --questions

# Use a specific template by index (TUI only: /analyze VCB 2)
# In CLI, use --question for custom questions
aipa analyze HPG --question "Phân tích theo phương pháp Wyckoff: xác định pha, sự kiện quan trọng (Spring/Upthrust/SOS), mục tiêu giá và xác nhận khối lượng"
```

---

## Interpreting Output

The CLI outputs to two streams:

- **stdout**: The final analysis result (the AI's response). This is what you should present to the user.
- **stderr**: Status messages with structured markers for tracking progress.

### Status Markers (stderr)

| Marker | Meaning |
|---|---|
| `[build]` | Context building status and timing |
| `[analyze]` | Analysis question being sent to the LLM |
| `[tool]` | Tool call being executed (e.g., fetching OHLCV data) |
| `[tool-result]` | Tool execution result returned |
| `[thinking]` | Agent reasoning tokens (only with `--verbose`) |
| `[error]` | Error message |
| `[done]` | Analysis complete, includes total time |
| `[result]` | Final analysis output follows |

When presenting results to the user, always use the stdout output (after the `[result]` marker in stderr).

---

## No API Key — Agent Fallback

When `OPENAI_API_KEY` is not set, `aipa analyze` prints the raw context to stdout and exits with `[info]` messages instead of an error. The context contains all the OHLCV data, MA scores, and market metadata needed for analysis.

**If you are an AI agent and see this fallback output, you should perform the analysis yourself** using the printed context:

1. Read the OHLCV data, MA scores, and market context from the stdout output
2. Apply the same analytical framework the CLI would have used (Wyckoff, VPA, MA momentum, etc.)
3. Produce the analysis result as if the LLM agent had generated it
4. Present the result to the user in the same format the CLI normally would

The context output is identical to `aipa analyze --context-only`, so treat it as your input data and do the reasoning yourself.

---

## Tips for AI Agents

1. **Auto-uppercase**: Ticker symbols are automatically converted to uppercase. You can pass `vcb`, `btcusdt`, etc. — they will be treated as `VCB`, `BTCUSDT`.

2. **Use `--context-only` for data inspection**: If you only need to see what data is available without spending API credits, use `aipa analyze VCB --context-only`.

3. **Default is good enough**: The default template (index 0, Trading Opportunity) is comprehensive. Only specify `--question` when the user has a specific analytical framework in mind.

4. **Multi-ticker for comparisons**: When the user asks to "compare" or "which is better", pass multiple tickers: `aipa analyze VCB TCB MBB`.

5. **Use `--lang vn` for Vietnamese users**: If the user writes in Vietnamese or asks for Vietnamese output, add `--lang vn`.

6. **Use `--verbose` for transparency**: When the user wants to see the reasoning process, add `--verbose`.

7. **`aipa-data` for raw numbers**: If the user asks for "price data", "candle data", or "OHLCV" without wanting AI analysis, use the `aipa-data` skill instead.

8. **Interval matters**: For intraday analysis, use `1h` or `4h`. For swing trading, use `1D`. For scalping, use `15m` or `5m`.

9. **Combine with `--limit`**: More bars = more context. Use `--limit 50` or `--limit 100` for deeper analysis. Default is 20.

10. **Reference ticker**: Auto-detected based on the ticker's source — `VNINDEX` for VN stocks, `BTCUSDT` for crypto, `^GSPC` for global stocks. Use `--reference-ticker` to override.
