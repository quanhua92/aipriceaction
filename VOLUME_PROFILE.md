# Volume Profile

Visual guide to how `compute_volume_profile()` works.

---

## What is Volume Profile?

Instead of looking at volume over **time** (like a normal chart), volume profile shows volume over **price**.

```
Traditional chart                          Volume Profile
(volume over time)                         (volume over price)

Price                                      Price
  |                                          |
  |    /\                                     |  ████                 <- most volume here
  |   /  \     /\                            |  ██████
  |  /    \   /  \                           |  ████████
  | /      \ /    \                          |  ██████████
  |/        X      \                         |  ██████
  |          \                     500 ---    |  ████
  |           \                                |  ██
  |                                490 ---    |  ████
  |                                480 ---    |  ████████
  |                                470 ---    |  ████████████████   <- POC!
  |                                460 ---    |  ██████████
  |                                450 ---    |  ██████
  |                                440 ---    |  ████
  |                                430 ---    |  ██
  +-------------------- Time                 +---------- Volume
```

You take all the 1-minute candles for a session and answer: **"At which prices did the most volume trade?"**

---

## The Algorithm, Step by Step

### Step 1 — Determine tick size

Each market has a minimum price increment (tick size). It determines the resolution of the profile.

```
Vietnamese stocks:
  avg price < 10,000 VND   -> tick = 10 VND
  avg price < 50,000 VND   -> tick = 50 VND
  avg price >= 50,000 VND  -> tick = 100 VND
  index tickers (VN30...)  -> tick = 0.01

Crypto:
  avg price < 1 USD        -> tick = 0.0001
  avg price < 100 USD      -> tick = 0.01
  avg price < 1,000 USD    -> tick = 0.1
  avg price >= 1,000 USD   -> tick = 1.0
```

### Step 2 — Distribute volume across each candle's range

This is the core idea. For every 1-minute candle, the bar's total volume is **evenly split** across every tick between `low` and `high`.

Example — three candles:

```
Candle A          Candle B          Candle C
high 480 -+      high 475 -+      high 490 -+
          | X              | X              | X
low  470 -+      low  465 -+      low  480 -+
vol: 1000        vol: 2000        vol: 500
```

Spreading Candle A (high=480, low=470, volume=1000):

```
  11 price levels (470, 471, 472, ... 480)
  Volume per level = 1000 / 11 = ~91

  480 --- 91
  479 --- 91
  478 --- 91
  ...
  470 --- 91
```

Spreading Candle B (high=475, low=465, volume=2000):

```
  11 price levels
  Volume per level = 2000 / 11 = ~182

  475 --- 182
  474 --- 182
  ...
  465 --- 182
```

Spreading Candle C (high=490, low=480, volume=500):

```
  11 price levels
  Volume per level = 500 / 11 = ~45

  490 --- 45
  489 --- 45
  ...
  480 --- 45
```

### Step 3 — Sum volumes at each price level

Add up all three candles at every price level:

```
  490 ---  45                   I
  489 ---  45                   I
  488 ---  45                   I
  487 ---  45                   I
  486 ---  45                   I
  485 ---  45                   I
  484 ---  45                   I
  483 ---  45                   I
  482 ---  45                   I
  481 ---  45                   I
  480 --- 136  (91 + 45)        III
  479 ---  91                   II
  478 ---  91                   II
  477 ---  91                   II
  476 ---  91                   II
  475 --- 273  (91 + 182)       ██████
  474 --- 273  (91 + 182)       ██████
  473 --- 273  (91 + 182)       ██████
  472 --- 273  (91 + 182)       ██████
  471 --- 273  (91 + 182)       ██████
  470 --- 273  (91 + 182)       ██████
  469 --- 182                   ████
  468 --- 182                   ████
  467 --- 182                   ████
  466 --- 182                   ████
  465 --- 182                   ████
```

### Step 4 — Bin into equal-width buckets

If the tick-level profile has more levels than the requested `bins` (default 50), they get merged into equally-spaced buckets. Each bucket sums the volumes of all tick levels inside it.

```
Tick-level (many rows):               Binned (50 rows):

  490 --- 45                           490 --- 90     (45 + 45)
  489 --- 45                           488 --- 90
  488 --- 45                           486 --- 90
  487 --- 45                           ...
  486 --- 45                           ...
  ...                                  465 --- 182
```

This keeps the output manageable and smooths out noise.

### Step 5 — POC (Point of Control)

The price level with the **highest** volume. That's the fair value where the most trading happened.

```
  POC = max(profile, key=volume)
```

```
  475 --- 273  ██████  +-- POC zone: 465-475 has the most volume
  474 --- 273  ██████  |
  473 --- 273  ██████  |   (tied in this simplified example;
  472 --- 273  ██████  |    real data has one clear winner)
  471 --- 273  ██████  |
  470 --- 273  ██████  +
```

### Step 6 — Value Area (default 70%)

The value area is a price range that captures the target percentage of total volume. It expands outward from the POC, always picking the side with more volume, until the target is reached.

```
  Algorithm:

  1. Start at POC. accumulated = POC volume
  2. Look one level UP, one level DOWN
  3. Pick whichever side has MORE volume. Add it to accumulated.
  4. Repeat until accumulated >= 70% of total volume.
```

Visual:

```
                      +---- Value Area (70%) ----+
  490 ---  45  I     |                           |
  ...                |                           |
  480 --- 136  III   |   <- upper expansion      |
  479 ---  91  II    |                           |
  ...                |                           |
  475 --- 273  ████████  <- POC (start here)     |
  ...                |                           |
  469 --- 182  ████  |                           |
  468 --- 182  ████  |   <- lower expansion      |
  ...                |                           |
  465 --- 182  ████  |                           |
                      +---------------------------+
```

The result gives you:
- **VAH** (Value Area High) — the top of the range
- **VAL** (Value Area Low) — the bottom of the range

### Step 7 — Statistics

Volume-weighted statistics are computed from the profile:

- **Mean** — volume-weighted average price
- **Median** — price level where cumulative volume crosses 50%
- **Standard deviation** — how spread out the volume is
- **Skewness** — whether volume leans toward higher or lower prices

---

## Why It Matters

```
Price
  |
  |     above value area -> overbought zone (expensive)
  |  --- VAH (Value Area High) ----------------------
  |     ████
  |     ██████         <- "fair value" where most
  |     ██████████       trading happened
  |     ████████████
  |     ██████
  |     ████
  |  --- VAL (Value Area Low) -----------------------
  |     below value area -> oversold zone (cheap)
  |
```

Traders use volume profile for:

- **Support / Resistance** — price tends to bounce off VAH and VAL
- **Fair value** — POC is where the market agreed on price the most
- **Breakouts** — price leaving the value area signals conviction
- **Skewness** — volume stacked on one side shows directional bias

---

## Why Uniform Distribution?

This implementation uses **uniform distribution** — splitting each candle's volume evenly across its high-low range. It ignores open and close prices.

There are other methods:

| Method | Data needed | How it works |
|---|---|---|
| **Uniform** (this code) | OHLCV | Spreads volume evenly — "I don't know where it traded, so I assume everywhere equally" |
| Close-position weighted | OHLCV | Shifts volume toward the close — assumes most activity happened near where the bar closed |
| Tick-by-tick | Raw trade data | Every real trade at its exact price — no guessing |

### Why uniform is the right choice for 1m data

With 1m bars, we can't know the actual path price took within each bar. A candle might look like this:

```
  close=104 ─-+
               | X     <- looks like price climbed steadily
  open=100  ─-+

  But the real path might have been:
  - 90% of volume traded at 100 in the first 10 seconds
  - price then drifted up to 104 on thin volume
```

Close-position weighting **assumes** most volume happened near the close. But that's just a guess too — and sometimes a wrong one.

Uniform distribution is honest about the uncertainty: **"I don't know where in this range the volume happened, so I spread it evenly."** It's the least wrong assumption for OHLCV data.

---

## Usage

### Python SDK

```python
from aipriceaction import AIPriceAction, compute_volume_profile

client = AIPriceAction()
df = client.get_ohlcv("VCB", interval="1m", start_date="2025-01-15", end_date="2025-01-15")

result = compute_volume_profile(df, "VCB", source="vn")

print(f"POC: {result.poc.price}")
print(f"Value Area: {result.value_area.low} - {result.value_area.high}")
print(f"VA %: {result.value_area.percentage:.1f}%")
```

### CLI

```bash
aipa volume-profile VCB --date 2025-01-15
aipa volume-profile BTCUSDT --source crypto --bins 80 --value-area-pct 68
```

---

## File Reference

| File | Purpose |
|---|---|
| `sdk/aipriceaction-python/src/aipriceaction/volume_profile.py` | Core algorithm — `compute_volume_profile()` and all helpers |
| `sdk/aipriceaction-python/src/aipriceaction/__init__.py` | Exports `VolumeProfileResult` and `compute_volume_profile` |
| `sdk/aipriceaction-python/tests/test_volume_profile.py` | Test suite |
| `aipriceaction-terminal/src/aipriceaction_terminal/cli_commands.py` | CLI handler — `cmd_volume_profile()` |
| `aipriceaction-terminal/src/aipriceaction_terminal/agents/tools.py` | AI agent tool — `create_volume_profile_tool()` |
