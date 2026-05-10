# AIPriceAction Skills for Claude Code

AI agent skills for financial market analysis using the [aipa CLI](https://aipriceaction.com). Compatible with Claude Code, Gemini CLI, and Codex.

## Installation

### Option A: npx skills (recommended)

```bash
npx skills add quanhua92/aipriceaction
```

This will prompt you to select skills and target agents (Claude Code, Gemini CLI, Codex, Cursor, etc.), then install them via symlink.

### Option B: Clone and copy

```bash
git clone --depth 1 https://github.com/quanhua92/aipriceaction.git /tmp/aipriceaction

# Project-level
cp -r /tmp/aipriceaction/skills/aipa-analyze .claude/skills/aipa-analyze
cp -r /tmp/aipriceaction/skills/aipa-data .claude/skills/aipa-data
cp -r /tmp/aipriceaction/skills/aipa-research .claude/skills/aipa-research

# Or personal (all projects)
cp -r /tmp/aipriceaction/skills/aipa-analyze ~/.claude/skills/aipa-analyze
cp -r /tmp/aipriceaction/skills/aipa-data ~/.claude/skills/aipa-data
cp -r /tmp/aipriceaction/skills/aipa-research ~/.claude/skills/aipa-research

rm -rf /tmp/aipriceaction
```

### Option C: Manual copy

If you already have the repo cloned, copy the skill folders:

```bash
# Project-level (from your project root)
cp -r /path/to/aipriceaction/skills/aipa-analyze .claude/skills/aipa-analyze
cp -r /path/to/aipriceaction/skills/aipa-data .claude/skills/aipa-data
cp -r /path/to/aipriceaction/skills/aipa-research .claude/skills/aipa-research
git add .claude/skills/
git commit -m "add aipa CLI skills for financial analysis"
```

Restart Claude Code after installing. Verify by asking "what skills are available?"

## Available Skills

| Skill | Description |
|---|---|
| **aipa-analyze** | AI-powered stock/crypto analysis — single or multi-ticker analysis, technical analysis, trading insights |
| **aipa-data** | Raw OHLCV price data fetch — candle data, moving averages, historical prices, no AI needed |
| **aipa-research** | Multi-agent deep research — sector-wide investigation, comprehensive market reports with supervisor/worker/reviewer pipeline |

## Example Prompts

### Data Fetch (aipa-data)

- "Get price data for VCB"
- "Show me the last 50 daily candles for BTCUSDT"
- "OHLCV data for VNINDEX with moving averages"
- "Get hourly data for ETHUSDT, last 100 bars"
- "Historical prices for FPT from January to May 2025"
- "Raw candle data for SOL, no moving averages"
- "Get me AAPL and NVDA price data"

### Analysis (aipa-analyze)

- "Analyze VCB stock"
- "Compare FPT, VNM, VIC, VCB with MA momentum analysis"
- "What's the price action for BTCUSDT on the 4h chart?"
- "Wyckoff analysis for HPG"
- "Which bank stock has the strongest trend? Analyze VCB, TCB, MBB, CTG"
- "Detect any extreme moves in VNM, HPG, FPT and research the news"

### Deep Research (aipa-research)

- "Deep research on the Vietnamese stock market"
- "Research the banking sector in detail"
- "Comprehensive analysis of real estate stocks"
- "Deep dive into the current market rotation patterns"
- "Research which sectors are leading and lagging right now"

## Prerequisites

- Python 3.10+ with [uv](https://docs.astral.sh/uv/) installed
- `OPENAI_API_KEY` environment variable set (or configured via `aipa setup`)

Install the CLI:

```bash
# One-time use (no install)
uvx aipa-cli analyze VCB

# Persistent install
uv tool install aipa-cli
aipa analyze VCB
```

## More Information

Developed by AIPriceAction. Full documentation and data at [https://aipriceaction.com](https://aipriceaction.com).
