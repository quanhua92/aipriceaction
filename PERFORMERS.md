# Performers

Visual guide to how `build_performers()` ranks top and worst market movers.

---

## What are Performers?

Performers ranks every stock by a metric you choose — price change, volume, MA score, money flow — and returns the **top N** and **worst N**.

```
  Ranked by close_changed (% price change):

  Top performers (biggest gains)          Worst performers (biggest drops)

  VCB   +6.82%  ████████████████████       LPB   -5.11%  ████████████████████
  FPT   +4.51%  █████████████             HPG   -3.87%  ██████████████
  MWG   +3.20%  ██████████                VRE   -2.95%  ███████████
  HSG   +2.88%  ████████                  VIC   -1.44%  ██████
  VPB   +1.55%  █████                     EIB   -0.92%  ███
```

Think of it as a daily leaderboard for the market.

---

## The Data Pipeline

```
  REST API                   Python SDK                     CLI / Agent
  ┌──────────┐              ┌──────────────────┐           ┌──────────────┐
  │ /tickers │─── JSON ───> │ fetch_live_data()│─── dict ─>│              │
  │ ?ma=true │              └──────┬───────────┘           │ build_       │
  └──────────┘                     │                       │ performers() │
                                   v                       │              │
                          ┌──────────────────┐             │ top[]        │
                          │ Each ticker gets │             │ worst[]      │
                          │ MA scores,       │             └──────────────┘
                          │ % changes,       │
                          │ money flow       │
                          └──────────────────┘
```

1. **API** returns live 1D candles with MA indicators pre-computed
2. **SDK** fetches and caches the data (2-minute TTL)
3. **`build_performers()`** takes the last candle per ticker, ranks, and slices

---

## The Metrics, Explained

### close_changed — Price change %

```
  close_changed = ((today_close - yesterday_close) / yesterday_close) x 100

  Yesterday: 25,000 VND
  Today:     26,250 VND
  Change:    +5.0%

  VCB  +5.0%  ████████████████████   <- up 5%
  FPT  +2.3%  █████████              <- up 2.3%
  MWG  -1.1%  █████                  <- down 1.1%
```

### volume — Raw trading volume

```
  Total shares traded today.

  VCB  12.5M  ████████████████████
  FPT   8.2M  ██████████████
  MWG   3.1M  ██████
```

### value — Trading value (close x volume)

```
  value = close x volume

  Measures total money exchanged. A 100 VND stock trading 10M shares
  has less value than a 50,000 VND stock trading 1M shares.

  VCB  625B VND  ████████████████████
  FPT  410B VND  ██████████████
  MWG  155B VND  ██████
```

### volume_changed — Volume change %

```
  volume_changed = ((today_vol - yesterday_vol) / yesterday_vol) x 100

  Yesterday: 5,000,000 shares
  Today:     7,500,000 shares
  Change:    +50.0%

  Sudden volume spikes often signal institutional activity.
```

### MA Score — Distance from moving average

```
  MA Score = ((close - MA) / MA) x 100

  close = 25,000    MA50 = 23,000
  MA50 Score = ((25,000 - 23,000) / 23,000) x 100 = +8.7%

  Positive (+%)  = price is ABOVE the moving average (bullish)
  Negative (-%)  = price is BELOW the moving average (bearish)
  Near 0         = price is AT the moving average (neutral)
```

There are 5 MA score periods available:

```
  Period   Sensitivity         Use case
  ──────   ─────────────────   ──────────────────────────────
  MA10     Very reactive       Scalping, day trading
  MA20     Short-term          Swing entries
  MA50     Medium-term         Trend confirmation
  MA100    Long-term           Position trading
  MA200    Major trend         Bull/bear market filter
```

Visual — same stock, different MA periods:

```
  Price
    |
    |      close = 25,000
    |  ─── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  MA10 = 24,800  (+0.8%)
    |  ── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  MA20 = 24,200  (+3.3%)
    |  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ MA50 = 23,000  (+8.7%)
    | ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─MA100= 21,500  (+16.3%)
    |─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ MA200= 19,800  (+26.3%)
    |
```

A stock with all positive MA scores (MA10 through MA200) is in a **strong uptrend** across all timeframes.

> **MA Type**: The MA type (SMA vs EMA) used for MA scores follows the `use_sma` setting in `~/.aipriceaction/settings.json` (default: `true` = SMA). Use `aipa config set use_sma false` for EMA.

### total_money_changed — Net money flow

```
  total_money_changed = (today_close - yesterday_close) x today_volume

  Measures how much actual money flowed in or out.
  Positive = money flowing IN (buyers dominant)
  Negative = money flowing OUT (sellers dominant)

  VCB:  close changed +1,000 VND  x  12,500,000 shares  =  +12.5B VND
  FPT:  close changed  -500 VND   x   8,200,000 shares  =   -4.1B VND
```

This is **not** a percentage — it's an absolute money amount. It tells you where the big money is moving:

```
  Top money inflow                      Top money outflow

  VCB  +12.5B VND  ████████████████████  HPG  -8.2B VND  ████████████████████
  FPT  + 8.1B VND  ██████████████        VIC  -5.7B VND  █████████████
  MWG  + 3.4B VND  ██████               VRE  -2.1B VND  █████
```

---

## The Algorithm, Step by Step

### Step 1 — Fetch live data

```
  client.fetch_live_data("1D", ma=True)

  Returns:
  {
    "VCB": [{close: 26250, volume: 12500000, ma10: 24800, ma10_score: 0.8, ...}],
    "FPT": [{close: 152000, volume: 8200000, ma10: 149000, ma10_score: 2.0, ...}],
    ...hundreds of tickers...
  }
```

Each ticker has a list of candles. Only the **last candle** (today) is used.

### Step 2 — Filter out noise

```
  SKIP:  Index tickers (VNINDEX, VN30, ...)  -> not tradeable
  SKIP:  VN tickers with volume < 10,000     -> illiquid, unreliable
  KEEP:  Crypto tickers always pass           -> no min_volume filter
```

### Step 3 — Build PerformerInfo for each ticker

```
  For each qualifying ticker, extract from the last candle:

  ┌─────────────────────────────────────────────────┐
  │ PerformerInfo                                   │
  │                                                 │
  │ symbol:             "VCB"                       │
  │ close:              26,250                      │
  │ volume:             12,500,000                  │
  │ value:              26,250 x 12,500,000 = 328B  │
  │ close_changed:       +5.0%                      │
  │ volume_changed:     +23.5%                      │
  │ ma10_score:         +0.8%                       │
  │ ma20_score:         +3.3%                       │
  │ ma50_score:         +8.7%                       │
  │ ma100_score:        +16.3%                      │
  │ ma200_score:        +26.3%                      │
  │ total_money_changed: +12.5B VND                 │
  │ sector:             "Banking"                   │
  └─────────────────────────────────────────────────┘
```

### Step 4 — Sort and slice

```
  Sort all performers by chosen metric:
    desc (default) -> highest first
    asc            -> lowest first

  None values always sort LAST (don't pollute rankings).

  top   = first N entries (strongest)
  worst = last N entries  (weakest)

  ┌─────────────────────────────────────┐
  │ sort_by="close_changed", limit=3    │
  │                                     │
  │ Top:    VCB +5.0%, FPT +2.3%, ...  │
  │ Worst:  LPB -5.1%, HPG -3.9%, ... │
  └─────────────────────────────────────┘
```

---

## Sorting Options

| `sort_by` value | What it ranks | Unit |
|---|---|---|
| `close_changed` | Price change | % |
| `volume` | Trading volume | shares |
| `value` | Trading value (price x volume) | currency |
| `volume_changed` | Volume change vs yesterday | % |
| `ma10_score` | Distance from 10-period MA | % |
| `ma20_score` | Distance from 20-period MA | % |
| `ma50_score` | Distance from 50-period MA | % |
| `ma100_score` | Distance from 100-period MA | % |
| `ma200_score` | Distance from 200-period MA | % |
| `total_money_changed` | Net money flow | currency |

---

## Why min_volume Matters

```
  Without min_volume filter:                With min_volume=10,000:

  AAA   +15.0%  ████████████████████        VCB   +5.0%  ████████████████████
  BBB   +12.0%  ██████████████████          FPT   +2.3%  █████████
  CCC   +10.0%  ████████████████            MWG   +1.8%  ███████
  VCB   + 5.0%  ██████████
  FPT   + 2.3%  █████

  AAA traded 500 shares total.             AAA, BBB, CCC filtered out.
  That +15% is noise, not signal.          Only liquid stocks remain.
```

Low-volume stocks can show extreme percentage moves on a handful of trades. The `min_volume` filter removes them so the rankings show real market activity, not noise.

---

## Usage

### Python SDK

```python
from aipriceaction import AIPriceAction
from aipriceaction.performers import build_performers

client = AIPriceAction()

# Fetch live 1D data with MA indicators
data = client.fetch_live_data("1D", ma=True)

# Build sector map from ticker metadata
tickers_meta = client.get_tickers(source="vn")
sector_map = {t.ticker: t.group for t in tickers_meta if t.group}

# Rank by MA50 score
top, worst = build_performers(
    data, sector_map,
    sort_by="ma50_score",
    direction="desc",
    limit=10,
    min_volume=10000,
)

for p in top:
    print(f"{p.symbol}  MA50 score: {p.ma50_score:+.1f}%  sector: {p.sector}")
```

### CLI

```bash
# Default: top/worst by price change
aipa performers

# Rank by MA50 score
aipa performers --sort-by ma50_score

# Rank by money flow, limit to 5
aipa performers --sort-by total_money_changed --limit 5

# Filter to a specific sector
aipa performers --group Banking

# Crypto performers
aipa performers --source crypto --sort-by volume
```

---

## File Reference

| File | Purpose |
|---|---|
| `sdk/aipriceaction-python/src/aipriceaction/performers.py` | Core algorithm — `build_performers()` and sort helpers |
| `sdk/aipriceaction-python/src/aipriceaction/indicators.py` | MA, change metrics, and `calculate_ma_score()` |
| `sdk/aipriceaction-python/src/aipriceaction/client.py` | `fetch_live_data()` — API fetch with caching |
| `sdk/aipriceaction-python/src/aipriceaction/__init__.py` | Exports `PerformerInfo` and `build_performers` |
| `sdk/aipriceaction-python/tests/test_performers.py` | Test suite |
| `aipriceaction-terminal/src/aipriceaction_terminal/cli_commands.py` | CLI handler — `cmd_performers()` |
| `aipriceaction-terminal/src/aipriceaction_terminal/agents/tools.py` | AI agent tool — `create_performers_tool()` |
| `aipriceaction-terminal/src/aipriceaction_terminal/system.py` | MA score explanation strings (EN/VN) |
| `src/server/analysis/performers.rs` | Rust server-side performers endpoint |
