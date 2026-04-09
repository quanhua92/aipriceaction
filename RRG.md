# Relative Rotation Graph (RRG)

## Overview

A Relative Rotation Graph (RRG) plots securities in a 2D quadrant chart using two metrics:

- **RS-Ratio (X-axis):** How a security is performing relative to a benchmark
- **RS-Momentum (Y-axis):** Whether the relative performance is improving or deteriorating

The four quadrants represent:

| Quadrant | RS-Ratio | RS-Momentum | Interpretation |
|----------|----------|-------------|----------------|
| Leading | > 100 | > 100 | Outperforming and accelerating |
| Weakening | > 100 | < 100 | Outperforming but losing steam |
| Lagging | < 100 | < 100 | Underperforming and deteriorating |
| Improving | < 100 | > 100 | Underperforming but recovering |

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

## API Usage

```
GET /analysis/rrg                             # VN stocks vs VNINDEX
GET /analysis/rrg?benchmark=VN30              # VN stocks vs VN30
GET /analysis/rrg?mode=crypto                 # crypto vs BTCUSDT
GET /analysis/rrg?mode=all&benchmark=BTCUSDT  # all tickers vs BTCUSDT
GET /analysis/rrg?trails=true&trail_length=30 # with trail history
GET /analysis/rrg?algorithm=jdk&period=14     # explicit algorithm + period
```

### Query Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `benchmark` | VNINDEX | Reference ticker symbol |
| `algorithm` | jdk | Algorithm to use (only `jdk` available) |
| `period` | 10 | WMA smoothing period, clamped [4..=50] |
| `trails` | false | Include historical trail points |
| `trail_length` | 60 | Number of trail points, clamped [10..=120] |
| `mode` | vn | Data source: vn, crypto, yahoo, all |

## Adding New Algorithms

Each algorithm implements the same signature:

```rust
type RrgComputeFn = fn(security: &[f64], benchmark: &[f64], period: usize) -> Option<(Vec<f64>, Vec<f64>)>;
```

To add a new algorithm:

1. Add a variant to the `RrgAlgorithm` enum in `rrg.rs`
2. Implement a function matching `RrgComputeFn`
3. Add the dispatch case in the handler
