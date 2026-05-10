# AIPriceAction Skills for Claude Code

AI agent skills for financial market analysis using the [aipa CLI](https://aipriceaction.com). Compatible with Claude Code, Gemini CLI, and Codex.

## Installation

Claude Code discovers skills from `.claude/skills/<name>/SKILL.md` (project-level) or `~/.claude/skills/<name>/SKILL.md` (user-level).

```bash
# Option A: symlink (project-level)
cd /path/to/your/project
ln -s /Volumes/data/workspace/aipriceaction/skills/aipa-analyze .claude/skills/aipa-analyze
ln -s /Volumes/data/workspace/aipriceaction/skills/aipa-data .claude/skills/aipa-data
ln -s /Volumes/data/workspace/aipriceaction/skills/aipa-research .claude/skills/aipa-research

# Option B: symlink (user-level, available in all projects)
ln -s /Volumes/data/workspace/aipriceaction/skills/aipa-analyze ~/.claude/skills/aipa-analyze
ln -s /Volumes/data/workspace/aipriceaction/skills/aipa-data ~/.claude/skills/aipa-data
ln -s /Volumes/data/workspace/aipriceaction/skills/aipa-research ~/.claude/skills/aipa-research

# Option C: copy (if you prefer)
cp -r /Volumes/data/workspace/aipriceaction/skills/aipa-analyze .claude/skills/
cp -r /Volumes/data/workspace/aipriceaction/skills/aipa-data .claude/skills/
cp -r /Volumes/data/workspace/aipriceaction/skills/aipa-research .claude/skills/
```

After installing, restart Claude Code. You can verify skills are loaded by asking "what skills are available?"

## Available Skills

| Skill | Description |
|---|---|
| **aipa-analyze** | AI-powered stock/crypto analysis — single or multi-ticker analysis, technical analysis, trading insights |
| **aipa-data** | Raw OHLCV price data fetch — candle data, moving averages, historical prices, no AI needed |
| **aipa-research** | Multi-agent deep research — sector-wide investigation, comprehensive market reports with supervisor/worker/reviewer pipeline |

## Example Prompts

Once installed, try these prompts in Claude Code:

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
- Backend API running (default: `http://localhost:3000`) or set `DATABASE_URL`

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
