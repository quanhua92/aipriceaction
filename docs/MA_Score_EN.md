# MA Score (Moving Average Score)

## Introduction to MA Score

Although in finance you may hear similar concepts called "MA Deviation," our MA Score is designed to provide you with a superior, clear, and action-oriented metric for analyzing market momentum.

### What is MA Score?

MA Score measures the percentage difference between a security's current closing price and its average price over a specific period (e.g., the last 10, 50, or 200 days). It helps identify when a security's price is "overstretched" and may be due for a potential adjustment or reversal.

### The Superiority of MA Score: Powerful Comparison & Sorting

MA Score isn't just a deviation; it's a standardized, powerful metric that provides unparalleled ease in analyzing across the entire market.

#### 1. Standardized for Easy Comparison

MA Score uses a simple, universal percentage calculation:

MA Score = ((close - ma) / ma) × 100

This calculation is the key to its power. A score of +5.0 essentially means the same thing for a $1 stock as it does for a $10,000 stock: the price is exactly 5% higher than its moving average.

This standardization eliminates the need to interpret absolute price differences, allowing you to immediately compare the momentum and "overstretched" status of completely different securities.

#### 2. Optimal Tool for Screening and Sorting

Because MA Score is a direct percentage, it becomes an extremely powerful screening and sorting tool in your application:

- **Find "Most Overbought" stocks**: You can immediately sort the entire market list by MA10 Score in descending order to find stocks experiencing the strongest, most immediate upward pressure and most likely due for a downward adjustment.

- **Identify "Oversold" opportunities**: Sorting by MA50 Score in ascending order will quickly highlight stocks that are oversold relative to their medium-term trend, indicating potential bounce candidates.

- **Filter by trend consensus**: You can easily filter assets where MA10 Score, MA50 Score, and MA200 Score are all positive, showing strong consensus across all timeframes.

#### 3. Actionable Overbought/Oversold Signals

The scores act as a highly sensitive "rubber band" indicator:

- **High positive scores (e.g., +5 or above)**: Price is likely overbought and may soon bounce down toward the moving average.
- **High negative scores (e.g., -5 or below)**: Price is likely oversold and may soon bounce up toward the moving average.

## Implementation in aipriceaction

### Available MA Periods

The system calculates MA scores for 5 periods:

- **MA10**: 10-day moving average (short-term trend)
- **MA20**: 20-day moving average (medium-term trend)
- **MA50**: 50-day moving average (intermediate trend)
- **MA100**: 100-day moving average (long-term trend)
- **MA200**: 200-day moving average (very long-term trend)

### Data Format

MA Score is integrated into the system's 19-column CSV format:

```
ticker,time,open,high,low,close,volume,
ma10,ma20,ma50,ma100,ma200,
ma10_score,ma20_score,ma50_score,ma100_score,ma200_score,
close_changed,volume_changed
```

**Columns 13-17**: MA Scores for each period (calculated as percentages)

### Price Format Handling

**Important**: The system handles stocks and indices differently:

**Stock Tickers (VCB, FPT, etc.):**
- Stored prices: 23.2 (divided by 1000 in CSV)
- Internal calculation: 23200.0 (full VND format)
- MA scores: Always percentages (format-independent)

**Market Indices (VNINDEX, VN30):**
- Stored prices: 1250.5 (actual value)
- Internal calculation: 1250.5 (no conversion)
- MA scores: Always percentages (format-independent)

### API Endpoints

MA Score is available through the following endpoints:

- **GET /tickers**: Returns MA scores for individual tickers
- **GET /analysis/top-performers**: Sorts by MA scores
- **GET /analysis/ma-scores-by-sector**: Sector-based MA analysis

### API Response Example

```json
{
  "time": "2025-11-10",
  "close": 60100,
  "ma10": 59840,
  "ma10_score": 0.4345,
  "ma20": 61160,
  "ma20_score": -1.7332,
  "ma50": 63017.8,
  "ma50_score": -4.6301
}
```

## Practical Usage

### Interpretation Guidelines

- **Positive Score**: Price above MA (bullish momentum)
- **Negative Score**: Price below MA (bearish momentum)
- **Magnitude**: Strength of trend
  - > 5%: Strong momentum
  - 2-5%: Moderate momentum
  - < 2%: Weak momentum

### Common Use Cases

1. **Short-term Momentum**: MA10 scores for intraday trading
2. **Medium-term Trends**: MA20 scores for swing trading
3. **Long-term Analysis**: MA50/MA200 scores for investment decisions
4. **Sector Analysis**: Average MA scores by sector groups
5. **Filters**: Stocks above/below specific thresholds

### Code Examples (SDK)

```typescript
// Sort by MA20 score
const topMA20 = await client.getTopPerformers({
  sort_by: SortMetric.MA20Score,
  direction: SortDirection.Descending
});

// Sector analysis with MA50 threshold
const sectorMA50 = await client.getMAScoresBySector({
  ma_period: MAPeriod.MA50,
  min_score: 2.0,
  above_threshold_only: true
});
```

## Technical Features

### Calculation Rules

- **Zero Division Protection**: If MA = 0, score = 0.0
- **Insufficient Data**: Score = None/empty until enough data points
- **Precision**: Scores rounded to 4 decimal places
- **Change Indicators**: Calculated from previous row (no first-row data)

### Data Requirements

- **MA10**: Needs 10+ records
- **MA50**: Needs 50+ records
- **MA200**: Needs 200+ records
- **Change indicators**: Need previous record

### Performance Optimization

- **Single-pass enhancement**: All indicators calculated in memory
- **No redundant disk I/O**: Write once, enhance in memory
- **Safe file locking**: Concurrent access safe during background syncs

---

By leveraging MA Score, you gain a powerful, intuitive tool that transforms complex price action into simple, comparable, and sortable data points, helping you make faster, smarter trading decisions.