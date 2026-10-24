---
name: aipa-data
description: >
  Fetch raw OHLCV and market data with the aipa CLI. Use this skill for candle
  data, prices, moving averages, volume, performers, ticker lists, volume
  profiles, and raw financial data. For market interpretation, use aipa-analyze
  or aipa-research.
---

# Fetch market data

Use this skill when the user asks for data or a calculation based on market
data. Do not add trading advice unless they ask for analysis.

## Commands

| Need | Command |
| --- | --- |
| OHLCV candles | `aipa get-ohlcv-data TICKER --interval 1D --limit 50` |
| Several tickers | `aipa get-ohlcv-data VCB TCB MBB --limit 50` |
| Date range | `aipa get-ohlcv-data VCB --start-date 2026-01-01 --end-date 2026-06-30` |
| Raw candles without indicators | Add `--no-ma` |
| EMA instead of the saved MA type | Add `--ema` |
| Latest data by value | `aipa live-data --top 10` |
| Ticker list | `aipa ticker-list --source vn` |
| Rank market movers | `aipa performers --sort-by value --limit 10` |
| Volume profile | `aipa volume-profile VCB --start-date 2026-01-01 --end-date 2026-01-31` |
| Financial ratios | `aipa fundamentals ratios VCB --latest` |

Add `--help` to a command for its options. Use `--source vn`, `crypto`,
`yahoo`, or `sjc` when the ticker alone does not identify its market. The CLI
also accepts `global` as an alias for Yahoo data.

## Data sources

| Market | Provider |
| --- | --- |
| Vietnam stocks | VPS, VNDirect, DNSE; optional VCI fallback for older one-minute data |
| Crypto | Binance |
| Global markets | Yahoo Finance |
| SJC gold | SJC daily quotes |

The API stores Yahoo and SJC under separate source identities. A request using
`mode=yahoo` may include both.

## Direct Python API catalog

This applies to direct HTTP requests to the Python API; it does not change the
CLI's own behavior.

- The metadata endpoints `/tickers/group`, `/tickers/name`, `/tickers/info`,
  and `/tickers/directory` default to a VND 387,175,000,000 market-cap cutoff.
  In the Oct 24, 2026 catalog snapshot this returned 657 stocks and covered
  99% of known market cap. The cutoff is fixed; its coverage can shift when
  market data is refreshed.
- To include the full metadata catalog, pass
  `min_market_cap_vnd=0&include_unknown_market_cap=true`. Unknown-cap rows
  otherwise stay out of the response.
- Group labels are localized API labels: use
  `/tickers/group?mode=vn&lang=vi` for Vietnamese or `lang=en` for English.
  Treat labels as display names; do not assume they are stable IDs.
- A direct `/tickers?symbol=TICKER&mode=vn&interval=1D` request is not
  market-cap filtered. It still requires stored OHLCV for that symbol.
- Catalog ingestion is independent of the metadata cutoff. Current production
  schedules daily data for all 1,519 currently listed VN stocks (Oct 24, 2026
  snapshot); hourly and minute ingestion is limited to selected liquid stocks.
  Verify live worker configuration and data freshness before promising coverage.


## Data checks

- Use tool output for every price, volume, indicator, and date. Never estimate
  or invent a value.
- Check the exact ticker and date row before reporting it.
- State the data source and interval when relevant.
- Moving-average values follow the saved setting unless a command flag
  overrides it. Run `aipa config get use_sma` to check the setting.
- `volume_changed` compares with the previous candle. To compare with the
  20-day average, calculate today's volume divided by the average of the prior
  20 daily candles.
- A missing row does not prove that the market was closed or that data is
  complete.
- Use a date range of 30 or more days for a useful volume profile unless the
  user asks about a single session.
- Fundamental data is available for Vietnamese stocks. Run fundamentals
  commands only when the user asks about financial metrics or company details.
  Use `rank` to compare tickers; inspect individual `ratios` after ranking.

## Calculation examples

Calculate derived values from tool output with Python:

~~~sh
python3 -c 'entry, stop, target = 8400, 7700, 9500; print((target-entry)/(entry-stop))'
~~~

For multiple lots, calculate average cost as total cost divided by total shares.
Do not report derived values unless the calculation used verified inputs.

## Output

Answer the requested data question directly. Include units and dates. Do not
turn a data request into a recommendation. For market-data attribution, use:

> Data by [AIPriceAction](https://aipriceaction.com/).

If the user also requests interpretation, use the
[aipa-analyze skill](../aipa-analyze/SKILL.md).
