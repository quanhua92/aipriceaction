# Volume Profile API - Technical Documentation

## 1. Overview

**Volume Profile** (also known as **Price-by-Volume** or **Market Profile**) is a technical analysis tool that displays the distribution of trading volume across different price levels for a specific trading session. Unlike traditional volume indicators that show volume over time, Volume Profile shows volume at specific price levels, revealing where the most trading activity occurred.

### Key Concepts

- **Point of Control (POC)**: The price level with the highest traded volume
- **Value Area (VA)**: Price range containing 70% of the total volume (typically centered around POC)
- **High Volume Nodes (HVN)**: Price levels with significantly high volume (support/resistance areas)
- **Low Volume Nodes (LVN)**: Price levels with minimal volume (areas of quick price movement)

### Why Volume Profile Matters

1. **Support/Resistance Identification**: High volume nodes indicate strong support/resistance levels
2. **Market Acceptance**: POC shows where the market agreed on "fair value" for that session
3. **Trading Range Analysis**: Value Area defines the primary trading range
4. **Breakout Confirmation**: Low volume areas often lead to volatile price movements

## 2. Calculation Method: Uniform Distribution (OHLCV Approximation)

### The Challenge

Ideal volume profile requires tick-by-tick trade data showing exact price and volume for every transaction. However, the aipriceaction system uses **minute-level OHLCV data** (Open, High, Low, Close, Volume), which aggregates all trades within each minute.

### The Solution: Smearing Method

Since we don't have tick data, we use the **Uniform Distribution Method** (also called "smearing"), which assumes volume is distributed evenly across all price levels between the Low and High of each minute candle.

#### Algorithm Steps

For each 1-minute candle:

1. **Validate Volume**: Skip if `volume <= 0`
2. **Calculate Price Range**: `spread = high - low`
3. **Determine Tick Count**:
   ```
   low_idx = Round(low / tick_size)
   high_idx = Round(high / tick_size)
   num_steps = (high_idx - low_idx) + 1
   ```
   *Note: We add 1 because even a Doji candle (high == low) represents 1 price level*

4. **Allocate Volume Per Tick**:
   ```
   volume_per_tick = candle.volume / num_steps
   ```

5. **Distribute Volume**: For each tick from `low_idx` to `high_idx`:
   ```
   price_level_idx = current_idx
   profile_map[price_level_idx] += volume_per_tick
   ```

6. **Convert Back to Prices**:
   ```
   real_price = price_level_idx × tick_size
   ```

### Floating-Point Safety

**CRITICAL**: Never use raw float prices as HashMap keys due to precision errors (e.g., `100.00` vs `100.0000001`).

**Solution**: Convert prices to **integer indices** by dividing by tick size:
```rust
let price_idx = (price / tick_size).round() as i64;
profile_map.entry(price_idx).or_insert(0.0) += volume;
```

## 3. Vietnamese Stock Market Tick Sizes

Vietnamese stocks use **dynamic tick sizes** based on current price level:

| Price Range (VND) | Tick Size (VND) | Example |
|-------------------|-----------------|---------|
| < 10,000 | 10 | 9,990 → 10,000 |
| 10,000 - 49,990 | 50 | 23,200 → 23,250 |
| ≥ 50,000 | 100 | 95,400 → 95,500 |

### Tick Size Determination Logic

```rust
pub fn get_tick_size(price: f64) -> f64 {
    if price < 10_000.0 {
        10.0
    } else if price < 50_000.0 {
        50.0
    } else {
        100.0
    }
}
```

**Important**: The aipriceaction system stores prices in **full VND format** (e.g., 23,200 not 23.2), so tick sizes are also in full format.

### Handling Variable Tick Sizes

Since tick size changes across price ranges, the algorithm must:

1. Calculate the **session average price** from all minute candles:
   ```rust
   let total_price: f64 = filtered_data.iter().map(|d| (d.high + d.low) / 2.0).sum();
   let avg_price = total_price / filtered_data.len() as f64;
   let tick_size = get_tick_size(avg_price);
   ```

2. Use this **single tick size** for the entire session when distributing volume

**Rationale**: Using a single tick size per session is more efficient than per-candle calculation, and price typically doesn't vary enough within a single day to cross multiple tick size boundaries.

## 4. API Design

### Endpoint

```
GET /analysis/volume-profile
```

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `symbol` | String | **Yes** | - | Ticker symbol (e.g., "VCB", "FPT") |
| `date` | String | **Yes** | - | Trading date in YYYY-MM-DD format |
| `mode` | String | No | "vn" | Market mode: "vn" or "crypto" |
| `bins` | Integer | No | 50 | Number of price bins for aggregation (10-200) |
| `value_area_pct` | Float | No | 70.0 | Value area percentage (60-90) |

### Response Format

```json
{
  "analysis_date": "2024-01-15",
  "analysis_type": "volume_profile",
  "symbol": "VCB",
  "total_volume": 15234500,
  "total_minutes": 360,
  "price_range": {
    "low": 60100,
    "high": 60800,
    "spread": 700
  },
  "poc": {
    "price": 60400,
    "volume": 850000,
    "percentage": 5.58
  },
  "value_area": {
    "low": 60200,
    "high": 60600,
    "volume": 10664150,
    "percentage": 70.0
  },
  "profile": [
    {
      "price": 60100,
      "volume": 125000,
      "percentage": 0.82,
      "cumulative_percentage": 0.82
    },
    {
      "price": 60150,
      "volume": 340000,
      "percentage": 2.23,
      "cumulative_percentage": 3.05
    },
    ...
  ],
  "statistics": {
    "mean_price": 60385.5,
    "median_price": 60400,
    "std_deviation": 145.2,
    "skewness": 0.15
  }
}
```

### Response Fields Explained

- **total_volume**: Sum of all volume across all minute candles
- **total_minutes**: Number of minute candles processed (typically 360 for full trading day)
- **price_range**: Lowest and highest prices reached during the session
- **poc**: Point of Control - price with highest volume
- **value_area**: Price range containing 70% of volume (centered on POC)
- **profile**: Array of price levels with volume distribution (sorted by price ascending)
  - `volume`: Actual volume traded at this price level
  - `percentage`: Percentage of total volume (volume / total_volume × 100)
  - `cumulative_percentage`: Running total of volume percentage
- **statistics**: Statistical measures of the volume distribution

## 5. Implementation Architecture

### File Structure

```
src/server/analysis/
├── mod.rs                    # Export volume_profile_handler
├── performers.rs             # Existing top performers
├── ma_scores.rs              # Existing MA scores by sector
└── volume_profile.rs         # NEW: Volume profile implementation
```

### Integration Points

1. **Data Source**: `SharedDataStore` (already handles VN + Crypto modes)
   - Fetch minute-level data: `Interval::Minute`
   - Single date filtering via `start_date` and `end_date`

2. **Date Handling**: Reuse existing `parse_analysis_date()` helper

3. **Error Handling**: Follow existing `AnalysisResponse` pattern

4. **Routing**: Register in `src/server/mod.rs`:
   ```rust
   .route("/volume-profile", get(volume_profile_handler))
   ```
   Note: This route is nested under `/analysis`, so the full path is `/analysis/volume-profile`

### Core Algorithm Structure

```rust
pub struct VolumeProfileBuilder {
    profile_map: HashMap<i64, f64>,  // Key: price_idx, Value: volume
    tick_size: f64,
}

impl VolumeProfileBuilder {
    pub fn new(tick_size: f64) -> Self { ... }

    pub fn add_candle(&mut self, candle: &StockData) {
        // Validate volume
        if candle.volume == 0 { return; }

        // Calculate indices
        let low_idx = (candle.low / self.tick_size).round() as i64;
        let high_idx = (candle.high / self.tick_size).round() as i64;

        // Calculate steps
        let num_steps = (high_idx - low_idx) + 1;
        let vol_per_step = candle.volume as f64 / num_steps as f64;

        // Distribute volume
        for idx in low_idx..=high_idx {
            *self.profile_map.entry(idx).or_insert(0.0) += vol_per_step;
        }
    }

    pub fn build(self) -> Vec<PriceLevelVolume> {
        let mut profile: Vec<_> = self.profile_map
            .into_iter()
            .map(|(idx, vol)| PriceLevelVolume {
                price: idx as f64 * self.tick_size,
                volume: vol,
            })
            .collect();

        profile.sort_by(|a, b| a.price.partial_cmp(&b.price).unwrap());
        profile
    }
}
```

## 6. Value Area Calculation

The Value Area represents the price range where 70% of the volume was traded (or other percentage specified by user).

### Algorithm

1. **Find POC**: Price level with maximum volume
2. **Initialize VA**: Start with POC as both low and high bounds
3. **Expand VA**: Iteratively add the adjacent level (above or below) with higher volume
4. **Stop Condition**: When cumulative volume reaches 70% of total volume

```rust
fn calculate_value_area(
    profile: &[PriceLevelVolume],
    poc_price: f64,
    target_percentage: f64,
) -> ValueArea {
    let total_volume: f64 = profile.iter().map(|p| p.volume).sum();
    let target_volume = total_volume * (target_percentage / 100.0);

    // Find POC index
    let poc_idx = profile.iter()
        .position(|p| p.price == poc_price)
        .unwrap();

    let mut va_low_idx = poc_idx;
    let mut va_high_idx = poc_idx;
    let mut accumulated_volume = profile[poc_idx].volume;

    // Expand value area
    while accumulated_volume < target_volume {
        let vol_below = if va_low_idx > 0 {
            profile[va_low_idx - 1].volume
        } else { 0.0 };

        let vol_above = if va_high_idx < profile.len() - 1 {
            profile[va_high_idx + 1].volume
        } else { 0.0 };

        if vol_below > vol_above && va_low_idx > 0 {
            va_low_idx -= 1;
            accumulated_volume += profile[va_low_idx].volume;
        } else if va_high_idx < profile.len() - 1 {
            va_high_idx += 1;
            accumulated_volume += profile[va_high_idx].volume;
        } else {
            break;  // No more levels to add
        }
    }

    ValueArea {
        low: profile[va_low_idx].price,
        high: profile[va_high_idx].price,
        volume: accumulated_volume,
        percentage: (accumulated_volume / total_volume) * 100.0,
    }
}
```

## 7. Binning for Visualization

For better visualization, aggregate nearby price levels into bins (similar to histogram buckets).

### Why Binning?

- Raw tick-level data can produce 100+ price levels for a single day
- Binning reduces noise and makes patterns more visible
- Typical bin counts: 30-50 for daily charts, 100-200 for detailed analysis

### Binning Algorithm

```rust
fn aggregate_into_bins(profile: Vec<PriceLevelVolume>, num_bins: usize) -> Vec<PriceLevelVolume> {
    if profile.len() <= num_bins {
        return profile;  // No need to bin
    }

    let price_min = profile.first().unwrap().price;
    let price_max = profile.last().unwrap().price;
    let bin_size = (price_max - price_min) / num_bins as f64;

    let mut bins = vec![PriceLevelVolume { price: 0.0, volume: 0.0 }; num_bins];

    for level in profile {
        let bin_idx = ((level.price - price_min) / bin_size).floor() as usize;
        let bin_idx = bin_idx.min(num_bins - 1);  // Clamp to last bin

        bins[bin_idx].volume += level.volume;
        bins[bin_idx].price = price_min + (bin_idx as f64 + 0.5) * bin_size;  // Bin center
    }

    bins.retain(|b| b.volume > 0.0);  // Remove empty bins
    bins
}
```

## 8. Usage Examples

### Basic Volume Profile

```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=VCB&date=2024-01-15"
```

### Crypto Volume Profile

```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=BTC&date=2024-01-15&mode=crypto"
```

### High-Resolution Profile (More Bins)

```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=FPT&date=2024-01-15&bins=100"
```

### Custom Value Area (80%)

```bash
curl "http://localhost:3000/analysis/volume-profile?symbol=HPG&date=2024-01-15&value_area_pct=80"
```

## 9. Testing Strategy

### Unit Tests

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn test_volume_distribution_single_candle() {
        // Test: Single candle with known OHLCV
        // Expected: Volume distributed evenly across price range
    }

    #[test]
    fn test_doji_candle_handling() {
        // Test: Candle with high == low
        // Expected: All volume assigned to single price level
    }

    #[test]
    fn test_poc_calculation() {
        // Test: Multiple candles with clear POC
        // Expected: Correct identification of highest volume price
    }

    #[test]
    fn test_value_area_calculation() {
        // Test: Known volume distribution
        // Expected: VA contains exactly 70% of volume
    }

    #[test]
    fn test_tick_size_selection() {
        // Test: Various price ranges
        // Expected: Correct tick size for each range
    }
}
```

### Integration Tests

Add to `scripts/test-analysis.sh`:

```bash
# Test 1: Basic volume profile
response=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=VCB&date=2024-01-15")
check_field "$response" "poc.price" "Volume profile POC price"
check_field "$response" "value_area.low" "Volume profile VA low"
check_field "$response" "total_volume" "Volume profile total volume"

# Test 2: Crypto mode
response=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=BTC&date=2024-01-15&mode=crypto")
check_status "200" "Crypto volume profile"

# Test 3: Invalid date
response=$(curl -s "$BASE_URL/analysis/volume-profile?symbol=VCB&date=2099-01-01")
check_error "$response" "No data" "Volume profile with future date"

# Test 4: Missing symbol
response=$(curl -s "$BASE_URL/analysis/volume-profile?date=2024-01-15")
check_error "$response" "required" "Volume profile without symbol"
```

### Manual Testing Checklist

- [ ] Test with high-volume stock (VCB, VIC, HPG)
- [ ] Test with low-volume stock (small-cap)
- [ ] Test with index (VNINDEX) - should handle without errors
- [ ] Test with crypto (BTC, ETH)
- [ ] Test with holiday/weekend date (no data)
- [ ] Test with various bin counts (10, 50, 100, 200)
- [ ] Verify POC matches visual chart inspection
- [ ] Verify Value Area makes sense (70% volume coverage)
- [ ] Test performance with full minute data (360 candles)

## 10. Performance Considerations

### Complexity Analysis

- **Time Complexity**: O(M × T) where M = number of minute candles, T = average ticks per candle
  - Typical: 360 candles × 10 ticks = 3,600 operations
  - Worst case: 360 candles × 100 ticks = 36,000 operations
- **Space Complexity**: O(P) where P = unique price levels (typically 50-200)

### Optimization Strategies

1. **Tick Size Selection**: Use average price of candle to determine tick size once
2. **HashMap Efficiency**: Integer keys (price indices) ensure O(1) lookups
3. **Sorting**: Single sort at the end (O(P log P)) rather than maintaining sorted order
4. **Caching**: Leverage existing DataStore minute-level cache (15s TTL)

### Expected Performance

- **Data Fetch**: ~50-100ms (from DataStore cache)
- **Calculation**: ~5-10ms (processing 360 minute candles)
- **Total Response Time**: ~60-120ms

## 11. Future Enhancements

### Phase 2 Features

1. **Multi-Day Composite Profile**: Combine volume profiles across multiple trading days
2. **TPO Chart Support**: Time Price Opportunity chart (letter-based visualization)
3. **Delta Analysis**: Buy volume vs Sell volume (requires bid/ask data)
4. **Profile Comparison**: Compare today's profile vs historical average
5. **Alerts**: Notify when price breaks out of Value Area

### Advanced Algorithms

1. **Weighted Distribution**: Use different weighting schemes (VWAP-weighted, time-weighted)
2. **Machine Learning**: Train models to predict price movement based on profile shape
3. **Profile Matching**: Find historical days with similar volume profiles

## 12. References

### Academic & Industry

- **Market Profile**: Developed by J. Peter Steidlmayer (CBOT, 1985)
- **Volume Profile Studies**: Adopted by CME, Bloomberg Terminal, TradingView

### Implementation References

- TradingView Volume Profile indicator
- Sierra Chart Volume Profile documentation
- NinjaTrader Market Analyzer

### Related API Endpoints

- `/tickers` - Fetch minute-level OHLCV data
- `/analysis/top-performers` - Daily performance analysis
- `/analysis/ma-scores-by-sector` - Moving average analysis

---

## Appendix A: Example Response (Full)

```json
{
  "analysis_date": "2024-01-15",
  "analysis_type": "volume_profile",
  "symbol": "VCB",
  "total_volume": 15234500,
  "total_minutes": 360,
  "price_range": {
    "low": 60100,
    "high": 60800,
    "spread": 700
  },
  "poc": {
    "price": 60400,
    "volume": 850000,
    "percentage": 5.58
  },
  "value_area": {
    "low": 60200,
    "high": 60600,
    "volume": 10664150,
    "percentage": 70.0
  },
  "profile": [
    { "price": 60100, "volume": 125000, "percentage": 0.82, "cumulative_percentage": 0.82 },
    { "price": 60150, "volume": 340000, "percentage": 2.23, "cumulative_percentage": 3.05 },
    { "price": 60200, "volume": 520000, "percentage": 3.41, "cumulative_percentage": 6.46 },
    { "price": 60250, "volume": 680000, "percentage": 4.46, "cumulative_percentage": 10.92 },
    { "price": 60300, "volume": 750000, "percentage": 4.92, "cumulative_percentage": 15.84 },
    { "price": 60350, "volume": 820000, "percentage": 5.38, "cumulative_percentage": 21.22 },
    { "price": 60400, "volume": 850000, "percentage": 5.58, "cumulative_percentage": 26.80 },
    { "price": 60450, "volume": 800000, "percentage": 5.25, "cumulative_percentage": 32.05 },
    { "price": 60500, "volume": 720000, "percentage": 4.73, "cumulative_percentage": 36.78 },
    { "price": 60550, "volume": 650000, "percentage": 4.27, "cumulative_percentage": 41.05 },
    { "price": 60600, "volume": 580000, "percentage": 3.81, "cumulative_percentage": 44.86 },
    { "price": 60650, "volume": 490000, "percentage": 3.22, "cumulative_percentage": 48.08 },
    { "price": 60700, "volume": 380000, "percentage": 2.49, "cumulative_percentage": 50.57 },
    { "price": 60750, "volume": 280000, "percentage": 1.84, "cumulative_percentage": 52.41 },
    { "price": 60800, "volume": 190000, "percentage": 1.25, "cumulative_percentage": 53.66 }
  ],
  "statistics": {
    "mean_price": 60385.5,
    "median_price": 60400,
    "std_deviation": 145.2,
    "skewness": 0.15
  }
}
```

## Appendix B: Vietnamese Market Characteristics

### Trading Hours

- **Morning Session**: 09:00 - 11:30 ICT (150 minutes)
- **Afternoon Session**: 13:00 - 15:00 ICT (120 minutes)
- **Pre-Market**: 09:00 - 09:15 (call auction, 15 minutes)
- **Closing**: 14:30 - 15:00 (periodic matching, 30 minutes)
- **Total Active Trading**: ~240-270 minutes of continuous trading

### Volume Profile Patterns (Common in VN Market)

1. **Morning Volume Spike**: High volume in first 30 minutes (09:00-09:30)
2. **Lunch Trough**: Low volume before lunch break (11:00-11:30)
3. **Afternoon Opening**: Moderate volume spike after lunch (13:00-13:15)
4. **Closing Rush**: High volume in last 30 minutes (14:30-15:00)

### Price Limits

Vietnamese stocks have daily price limits:
- **±7%** for most stocks
- **±10%** for some large-cap stocks
- **±15%** for newly listed stocks (first 3 days)

These limits affect volume profile shape, often creating concentrated volume near limit prices.
