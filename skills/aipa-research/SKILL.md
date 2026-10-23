---
name: aipa-research
description: >
  Broad, multi-ticker or sector research using the aipa CLI. Use for deep dives,
  market overviews, sector comparisons, or detailed reports. For one ticker use
  aipa-analyze; for raw data use aipa-data.
---

# Deep research

The CLI has two modes: a market snapshot and a full multi-agent research run.
The snapshot needs no LLM key. The full run requires a configured API key and
takes longer.

## Choose a mode first

Before running an `aipa` command, ask whether the user wants:

- **Fast research:** fetch a snapshot, then investigate relevant tickers and
  sectors with the available tools or parallel agents.
- **Full pipeline:** run `aipa deep-research --run`, which executes the CLI's
  supervisor, worker, aggregator, and review stages.

Skip the question only when the user explicitly names a mode. Do not start the
full pipeline without the user's choice.

## Commands

```sh
aipa deep-research [QUESTION] --source vn|crypto|global|sjc
aipa deep-research --run [QUESTION] --source vn|crypto|global|sjc
aipa deep-research --run --resume SESSION_ID
aipa deep-research --run --output report.md
```

Check `aipa deep-research --help` for current options. Use `aipa config get
use_sma` before discussing moving-average signals.

## Fast research workflow

1. Fetch a snapshot for the requested market.
2. Select a small set of relevant sectors and tickers. Use `aipa performers`
   for more than one perspective when ranking market leaders or laggards.
3. Assign separate sector or ticker groups to parallel workers when available.
   Give every worker the same data range and questions.
4. Compare results using the same dates and definitions. Remove unsupported
   claims, resolve conflicting data, and produce one combined report.

For Vietnamese stocks, fundamental analysis is optional and should only be run
when requested. Rank or screen peers before fetching detailed ratios. See the
[fundamentals skill](../aipa-fundamentals/SKILL.md).

## Quality rules

- Use command output for all numeric claims. Verify ticker, date, and reporting
  period; mark missing data as unavailable.
- Compare volume with its 20-day average. Do not treat a day-over-day change as
  the 20-day comparison.
- Present both bullish and bearish evidence. State the data range and key limits.
- Before proposing an entry, inspect daily 60 bars, weekly 52 bars, and a volume
  profile covering at least 30 days. Check for Buying Climax, Upthrust After
  Distribution, and weekly trend conflict.
- Calculate reward-to-risk. Block setups below 1:1; flag below 2:1. Anchor
  targets to observed resistance or another stated structure.
- For Vietnamese shares, check T+2 settlement before recommending a sale.
- Keep the final report concise and separate observed data from interpretation.
