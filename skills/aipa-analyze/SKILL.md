---
name: aipa-analyze
description: >
  Technical and price-action analysis for Vietnamese stocks, crypto, and global
  assets. Use for ticker analysis, market comparisons, Wyckoff, volume, moving
  averages, support and resistance, or trading questions. Use aipa-data for raw
  price data and aipa-fundamentals for requested fundamental analysis.
---

# Technical analysis

Use `aipa analyze` for AI-assisted analysis and `aipa volume-profile` for
volume-based price levels. The CLI supports Vietnamese stocks, crypto, global
assets, and SJC gold. For raw prices without analysis, use the `aipa-data` skill.

## Setup and commands

Use `uvx aipa-cli@latest <command>` if the CLI is not installed. Check the active
moving-average setting before discussing moving averages:

```sh
aipa config get use_sma
aipa analyze VCB --limit 50
aipa analyze VCB TCB MBB --limit 50
aipa analyze BTCUSDT --source crypto --interval 4h --limit 50
aipa volume-profile VCB --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

Use `--context-only` to inspect data without an API key. Use `--question` for a
specific analysis question and `--lang en|vn` to choose the output language.

## Workflow

1. Start with daily data. Use at least 50 bars for general analysis and 60 or
   more when assessing Wyckoff phases or setting targets.
2. For support and resistance, use a volume profile covering at least 30 trading
   days. Add an intraday interval only when timing matters or the user asks.
3. Combine the results into one answer. Cover trend, volume behavior, relevant
   price levels, and both bullish and bearish evidence.
4. Before proposing an entry, check daily 60 bars, weekly 52 bars, and a volume
   profile covering at least 30 days. Do not propose an entry if the weekly trend
   conflicts or the data shows a Buying Climax or Upthrust After Distribution.

## Data and risk rules

- Use tool output for every price, volume, date, indicator, and calculation.
  Verify the exact ticker and date; do not use an adjacent row or ticker block.
- Compare volume with the prior 20-day average, not just the previous day.
- Explain whether price is above or below the stated moving average and name
  the active type (SMA or EMA).
- A breakout level must come from prior structure, such as a range ceiling or
  swing high. Do not use the breakout candle's low as the structural level.
- Show both bullish and bearish evidence. Give specific invalidation and risk
  levels for any trade setup.
- Calculate reward-to-risk as `(target - entry) / (entry - stop)`. Block setups
  below 1:1; flag setups below 2:1 as suboptimal. Targets need a real resistance
  or measured-move anchor.
- For Vietnamese shares, check T+2 settlement before recommending a sale.
- Do not recommend taking profit during a confirmed markup phase without
  distribution evidence. Do not recommend a take-profit below 3% gain in markup
  or below 5% after an accumulation breakout.
- Consider an add only at a defined zone with volume confirmation, while the
  thesis and stop remain intact. Limit to two adds; assess each add as a separate
  trade with at least 1:1 reward-to-risk.

For account updates, check the portfolio and watchlist files named in `AGENTS.md`
and keep them consistent. Use the [fundamentals skill](../aipa-fundamentals/SKILL.md)
only when the user asks about financial statements, ratios, or valuation.
