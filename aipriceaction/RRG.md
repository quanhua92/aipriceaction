# Relative Rotation Graph (RRG)

## Overview

A Relative Rotation Graph (RRG) plots securities in a 2D quadrant chart using two metrics.

Two algorithms are available:

### JdK RS-Ratio (default)

Compares each security against a **benchmark**:

- **RS-Ratio (X-axis):** How a security is performing relative to a benchmark
- **RS-Momentum (Y-axis):** Whether the relative performance is improving or deteriorating

Quadrant threshold: **100** (above = outperforming, below = underperforming).

### MA Score

Self-comparison using pre-computed moving average scores — **no benchmark needed**:

- **MA20 Score (X-axis):** How price relates to its 20-day moving average
- **MA100 Score (Y-axis):** How price relates to its 100-day moving average

Quadrant threshold: **0** (above = above MA, below = below MA).

### Quadrant Interpretation

| Quadrant | X-axis | Y-axis | JdK Meaning | MA Score Meaning |
|----------|--------|--------|-------------|------------------|
| Leading | > threshold | > threshold | Outperforming and accelerating | Above both MA20 and MA100 |
| Weakening | > threshold | < threshold | Outperforming but losing steam | Above MA20 but below MA100 |
| Lagging | < threshold | < threshold | Underperforming and deteriorating | Below both MA20 and MA100 |
| Improving | < threshold | > threshold | Underperforming but recovering | Below MA20 but above MA100 |

## JdK Algorithm

The JdK (Julius de Kempenaer) RS-Ratio method uses double-smoothed WMA with Z-score normalization.

### Step 1 — Weighted Moving Average (WMA)

Linear weights: oldest = 1, newest = period.

```
weight_sum = period * (period + 1) / 2
for i in (period-1)..data.len():
    window = data[i-period+1 .. i+1]
    wma[i] = sum(window[j] * (j+1) for j in 0..period) / weight_sum
```

Positions before `period-1` are `0.0`.

### Step 2 — Double-Smoothed WMA

WMA applied twice (JdK lag-reduction technique):

```
first_pass = calculate_wma(data, period)
second_pass = calculate_wma(first_pass, period)
```

Output has non-zero values starting at index `2*(period-1)`.

### Step 3 — Raw Relative Strength

```
raw_rs[i] = security_close[i] / benchmark_close[i]
```

### Step 4 — RS-Ratio

```
rs_ratio_raw = double_smoothed_wma(raw_rs, period)
```

### Step 5 — RS-Momentum (1-period ROC)

```
rs_mom_raw[i] = (rs_ratio_raw[i] - rs_ratio_raw[i-1]) / rs_ratio_raw[i-1]
```

`rs_mom_raw` is 1 element shorter than `rs_ratio_raw`.

### Step 6 — Rolling Z-Score Normalization (100-base)

For both rs_ratio_raw and rs_mom_raw independently:

```
for i in (window-1)..values.len():
    slice = values[i-window+1 .. i+1]
    mean = slice.sum() / window
    std_dev = sqrt(sum((x - mean)^2 for x in slice) / window)
    result[i] = 100.0 + 10.0 * (values[i] - mean) / std_dev   // if std_dev > 0
    result[i] = 100.0                                           // if std_dev == 0
```

Default value is 100.0 (neutral).

### Step 7 — Alignment

The two normalized arrays have different lengths due to the ROC offset:

```
ratio_offset = rs_ratio_raw.len() - rs_mom_raw.len()   // = 1

for i in (period-1)..rs_mom_norm.len():
    yield (rs_ratio_norm[i + ratio_offset], rs_mom_norm[i])
```

### Minimum Data Length

`2*period + period + 1` aligned bars = `3*period + 1` total.

With default period=10: 31 bars minimum.

## MA Score Algorithm

Plots each ticker using its pre-computed MA scores from `OhlcvJoined`:

- **X-axis:** `ma20_score` — distance of close from MA20 (positive = above)
- **Y-axis:** `ma100_score` — distance of close from MA100 (positive = above)
- **raw_rs:** always `0.0` (not applicable)
- **trails:** array of trail points when `trails > 0`, `null` when `trails = 0`
- **benchmark:** `null` in response (not applicable)
- **period:** `null` in response (not applicable)

No benchmark fetch, no OHLCV alignment, no `RrgComputeFn` call. When `trails=0`, reads directly from `get_latest_daily_per_ticker`. When `trails>0`, uses `get_ohlcv_joined_batch` to fetch historical rows and builds trail points from `ma20_score` / `ma100_score` over time.

## API Usage

```
GET /analysis/rrg                             # JdK: VN stocks vs VNINDEX (with 10 trail points)
GET /analysis/rrg?benchmark=VN30              # JdK: VN stocks vs VN30
GET /analysis/rrg?mode=crypto                 # JdK: crypto vs BTCUSDT
GET /analysis/rrg?mode=all&benchmark=BTCUSDT  # JdK: all tickers vs BTCUSDT
GET /analysis/rrg?trails=30                   # JdK: with 30 trail points
GET /analysis/rrg?algorithm=jdk&period=14     # JdK: explicit algorithm + period
GET /analysis/rrg?algorithm=mascore           # MA Score: VN stocks
GET /analysis/rrg?algorithm=mascore&mode=crypto  # MA Score: crypto
GET /analysis/rrg?algorithm=mascore&mode=all   # MA Score: all sources
GET /analysis/rrg?algorithm=mascore&trails=30  # MA Score: with 30 trail points
GET /analysis/rrg?trails=0                    # No trails (both algorithms)
GET /analysis/rrg?min_volume=100000           # Exclude low-volume tickers
GET /analysis/rrg?algorithm=mascore&min_volume=50000  # MA Score with volume filter
GET /analysis/rrg?date=2025-01-15                       # JdK: snapshot at 2025-01-15
GET /analysis/rrg?date=2025-01-15&trails=20             # JdK: snapshot with 20 trail points
GET /analysis/rrg?algorithm=mascore&date=2025-01-15      # MA Score: snapshot at 2025-01-15
```

### Query Parameters

| Parameter | Default | Applicable | Description |
|-----------|---------|------------|-------------|
| `algorithm` | jdk | both | Algorithm: `jdk` or `mascore` |
| `benchmark` | VNINDEX | jdk only | Reference ticker symbol (ignored by mascore) |
| `period` | 10 | jdk only | WMA smoothing period, clamped [4..=50] (ignored by mascore) |
| `trails` | 10 | both | Number of trail points (0 = no trails, clamped to 1-120 when > 0) |
| `min_volume` | 100000 | both | Exclude tickers with latest volume below this value |
| `date` | none (today) | both | Cutoff date YYYY-MM-DD — compute RRG using data up to this date |
| `mode` | vn | both | Data source: vn, crypto, yahoo, all |

### Response Differences by Algorithm

| Field | JdK | MA Score |
|-------|-----|----------|
| `benchmark` | ticker symbol (e.g. `"VNINDEX"`) | `null` |
| `period` | number (e.g. `10`) | `null` |
| `tickers[].rs_ratio` | JdK RS-Ratio | MA20 Score |
| `tickers[].rs_momentum` | JdK RS-Momentum | MA100 Score |
| `tickers[].raw_rs` | security/benchmark ratio | `0.0` |
| `tickers[].trails` | array of trail points or `null` | array of trail points or `null` |

## Adding New Algorithms

The JdK path uses a shared compute signature:

```rust
type RrgComputeFn = fn(security: &[f64], benchmark: &[f64], period: usize) -> Option<(Vec<f64>, Vec<f64>)>;
```

The MA Score path uses a different data flow (`get_latest_daily_per_ticker` or `get_ohlcv_joined_batch` for trails → read pre-computed scores), so it does not implement `RrgComputeFn`.

To add a new algorithm:

1. Add a variant to the `RrgAlgorithm` enum in `rrg.rs`
2. For benchmark-based algorithms: implement a function matching `RrgComputeFn` and add a handler in the dispatch
3. For pre-computed-score algorithms: add a handler that reads from `OhlcvJoined` fields directly
