# AIPriceAction agent skills

These skills let AI agents fetch market data and analyze stocks, crypto, global
assets, and SJC gold with the `aipa` CLI.

## Install

```sh
npx skills add quanhua92/aipriceaction
```

Select the skills and agent you want to use, then restart the agent. To use the
general workflow without installing a skill, copy [`AGENTS.md`](AGENTS.md) into
your project.

## Choose a skill

| Skill | Use it for |
| --- | --- |
| [`aipa-data`](skills/aipa-data/SKILL.md) | OHLCV, indicators, performers, watchlists, volume profile, and raw fundamentals |
| [`aipa-analyze`](skills/aipa-analyze/SKILL.md) | Technical analysis and ticker comparisons |
| [`aipa-fundamentals`](skills/aipa-fundamentals/SKILL.md) | Financial ratios, company profiles, ranking, and screening |
| [`aipa-research`](skills/aipa-research/SKILL.md) | Market-wide and sector research |

## API keys

Fetching market data does not need an LLM key. Direct LLM analysis and the full
research pipeline need an OpenAI-compatible key. When an AI agent runs the
commands and analyzes the returned data itself, no LLM key is required by
AIPriceAction.

## Data sources

The Python service uses VPS, VNDirect, and DNSE for Vietnamese stocks; Binance
for crypto; Yahoo Finance for global assets; and SJC quotes for gold. VCI is an
optional fallback for older Vietnamese one-minute history.

All numeric claims must come from tool results. Verify the exact ticker and
date, explain both positive and negative evidence, and follow the risk rules in
[`AGENTS.md`](AGENTS.md). AI analysis can be wrong; verify it before trading.
