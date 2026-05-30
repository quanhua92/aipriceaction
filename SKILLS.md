# AIPriceAction Skills

[Tiếng Việt](SKILLS.vn.md)

Free AI Agent Skills for financial market analysis — Vietnamese stocks, crypto, global markets, SJC gold. No sign-up, no API key required.

**100% FREE** · No account · No authentication

---

## Install in 30 seconds

### Option 1: Skills (recommended)

```bash
npx skills add quanhua92/aipriceaction
```

Select skills (aipa-data, aipa-analyze, aipa-research), choose your AI agent (Claude Code, Gemini CLI, Codex, Cursor...), done — restart agent and start asking.

### Option 2: AGENTS.md (no skill install)

```bash
curl -sSL https://raw.githubusercontent.com/quanhua92/aipriceaction/main/AGENTS.md -o AGENTS.md
```

For Claude Code, create a symlink:

```bash
ln -s AGENTS.md CLAUDE.md
```

Gemini CLI auto-detects AGENTS.md. The AI agent auto-installs `aipa-cli` on first use.

### Requirements

- Python 3.10+ (for `aipa-cli`, auto-installed via `uvx`)
- **No API key** when used with an AI agent — the agent reads data and analyzes it for you
- `OPENAI_API_KEY` is only needed if you run `aipa analyze` directly from terminal without an AI agent

### Updates

```bash
npx skills update
```

The CLI auto-updates when using `uvx aipa-cli` — always the latest version.

---

## 3 Skills for every analysis need

### aipa-data — Real-time market data

Raw data, no AI, no API key needed. OHLCV candles, volume profile, top performers, live data, watchlists, **fundamental data (company info, financial ratios, PE, ROE, NPL, CAR, screening/ranking)**.

- `What is the current price of VCB?`
- `Top 10 stocks by trading value`
- `Compare SJC gold price with global gold GC=F`
- `Volume profile for BTCUSDT — where is the POC?`
- `List all banking sector stocks`
- `How is the market doing today? Which stocks are up the most?`
- `What is VCB's PE ratio?` *(fundamentals)*
- `Rank all banks by ROE` *(fundamentals)*
- `Screen for low PE stocks with high ROE` *(fundamentals)*
- `Company profile and shareholders for ACB` *(fundamentals)*
- `Compare bank NPL and CAR` *(fundamentals)*

### aipa-analyze — AI-powered technical analysis

Analyze single or multiple stocks with Wyckoff, VPA, Smart Money, MA Momentum. Can also incorporate **fundamental data** (PE, PB, ROE, NPL, CAR) to enrich technical analysis when you ask for it. When used with an AI agent (Claude Code, Gemini CLI...), the agent reads data and analyzes it — no API key needed.

- `Compare VCB, TCB, MBB — which bank has the strongest trend?`
- `Wyckoff analysis for HPG`
- `Analyze BTCUSDT on the 4h chart`
- `Detect stocks with unusual moves and find the news behind them`
- `Compare FPT, VNM, VIC with MA momentum analysis`
- `Which stocks are accumulating? What are the smart money signals?`

**Built-in analysis templates:**
- Trading Opportunity (trading setup, Wyckoff, Smart Money)
- News & Events Research (detect extreme moves, search for news)
- Price Action & Volume (VPA, smart money footprints)
- MA Momentum & Trend (trend direction, crossover, volume confirmation)
- Wyckoff Method (phases, Spring, Upthrust, SOS, price targets)
- Bob Volman Price Action (micro pullback entries, breakout/fading setups)

### aipa-research — Comprehensive market research

Multi-agent sector analysis: Supervisor decomposes tasks, Workers analyze in parallel, Aggregator synthesizes, Reviewer validates quality. Agent-driven mode (recommended) requires no API key.

- `Deep research banking sector: top 10 banks, trend direction, VPA signals`
- `Comprehensive analysis of the Vietnamese stock market`
- `Research crypto: Layer 1 vs DeFi vs AI tokens`
- `Which sectors are leading the market right now?`
- `Full analysis of the crypto market this week`

**Pipeline:**

```
Supervisor → decomposes into 3-5 sectors (Banking, Securities, Real Estate...)
     ↓
Parallel Workers → each worker analyzes one sector (~10 stocks per sector)
     ↓
Aggregator → cross-sector synthesis, ranking table
     ↓
Reviewer → quality check (MA scores, data integrity)
     ↓
Final report
```

---

## 4 markets, 1 tool

### Vietnamese Stocks

Data from VCI, Vietstock, VNDirect, VPS, DNSE. Covers VNINDEX, VN30 and 1600+ stocks.

**Timeframes:** 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W, 2W

**Built-in watchlists:** VN30 (30 tickers), VINGROUP, MASAN, TM, INDEX (22 indices), CROSS (cross-market)

### Crypto

Data from Binance. BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT and 300+ pairs.

**Timeframes:** 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W, 2W · 24/7 market

### Global Stocks

Data from Yahoo Finance. AAPL, TSLA, NVDA, SPY, ^GSPC and thousands more.

**Timeframes:** 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W, 2W

### SJC Gold

Data from sjc.com.vn. Daily SJC gold prices.

**Timeframes:** 1D

---

## Supported AI Agents

| AI Agent | Supported |
|---|---|
| Claude Code | Yes |
| Gemini CLI | Yes |
| Codex | Yes |
| Cursor | Yes |
| openCode | Yes |
| Any AI agent that reads files + runs terminal | Yes (via AGENTS.md) |

---

## Why AIPriceAction?

| | **AIPriceAction Skills** | **Other solutions** |
|---|---|---|
| Price | Free forever | Free (Beta), uncertain later |
| Sign-up / Auth | **None** | Requires API key |
| Vietnamese stocks | Yes (VCI, Vietstock, VNDirect, VPS) | Yes |
| Crypto | Yes (Binance) | Limited or none |
| Global stocks | Yes (Yahoo Finance) | Limited or none |
| SJC Gold | Yes | No |
| Technical analysis | Wyckoff, VPA, Smart Money, Bob Volman | Market summary only |
| Fundamental data | Yes (PE, PB, ROE, NPL, CAR, screening) | No |
| Volume Profile | Yes (POC, Value Area, multi-day) | No |
| Deep Research | Yes (multi-agent pipeline) | No |
| AI Agent support | Claude Code, Gemini CLI, Codex, Cursor, openCode | More limited |
| Open source | Yes (MIT) | Yes |

---

## FAQ

**Is it free?**
Completely free. When used with an AI agent like Claude Code or Gemini CLI, the agent reads data and analyzes it for you — no API key needed at all.

**Why no API key for data?**
OHLCV data is served from a public S3 archive — plain HTTP access, no authentication needed. Volume profile, performers, live-data all work without any credentials.

**Do I need to install Python?**
`aipa-cli` requires Python 3.10+, but you don't need to install it manually. The AI agent auto-installs it via `uvx` on first run. `uvx` manages its own virtual environment — no impact on your system.

**What's the difference between the 3 skills?**
- **aipa-data:** Raw data — candles, volume profile, performers, live data, fundamental data (PE, ROE, NPL, CAR, company info). No AI, no API key.
- **aipa-analyze:** Technical analysis — Wyckoff, VPA, Smart Money, MA Momentum + optional fundamental context. AI agent reads and analyzes, no API key.
- **aipa-research:** Deep research — multi-agent pipeline for sector-wide analysis with optional fundamental screening. Agent-driven mode needs no API key.

**Is the data accurate?**
Data is aggregated from reputable sources (VCI, Vietstock, VNDirect, Binance, Yahoo Finance, sjc.com.vn). Analysis is AI-generated and may contain errors — always verify before trading.

**Can I place trades?**
No. AIPriceAction Skills focuses on market data analysis — it does not execute buy/sell orders. This is an information tool, not a trading platform.

---

> AIPriceAction Skills is an information and analysis tool. Analysis is AI-generated and may contain errors. Not investment advice, not a recommendation to buy or sell. Stock and crypto trading involves risk. Past performance does not guarantee future results.

[Github](https://github.com/quanhua92/aipriceaction) · [Website](https://aipriceaction.com) · [PyPI](https://pypi.org/project/aipa-cli/) · License: MIT
