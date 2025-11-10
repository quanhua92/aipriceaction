# Analysis API Documentation

This document describes the analysis endpoints of the AIPriceAction API, providing market insights and technical analysis capabilities for Vietnamese stock market data.

## Overview

The Analysis API offers advanced market analysis endpoints that leverage the existing DataStore infrastructure to provide insights on stock performance, moving average analysis, and sector-based metrics.

### Architecture

- **Data Source**: Uses the same DataStore as the main API with dual-layer caching
- **Price Format**: Full VND (e.g., 60300.0 not 60.3) - consistent with main API
- **Sector Mapping**: Integrates with `ticker_group.json` for sector-based analysis
- **Performance**: Leverages existing memory cache for daily data (60s TTL)

### Common Features

- **Date Support**: All endpoints support historical analysis with `date` parameter (YYYY-MM-DD format)
- **Rate Limiting**: Respects VCI API rate limits through shared DataStore
- **Error Handling**: Consistent error response format with main API
- **Data Freshness**: Same data freshness guarantees as `/tickers` endpoint

---

## Endpoints

### GET /analysis/top-performers

Returns top/bottom performing stocks based on various metrics with customizable sorting and filtering options.

#### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `date` | string | latest | Analysis date in YYYY-MM-DD format (e.g., "2024-01-15") |
| `sort_by` | string | "close_changed" | Metric to sort by (see available metrics below) |
| `direction` | string | "desc" | Sort direction: "asc" (ascending) or "desc" (descending) |
| `limit` | number | 10 | Number of results to return (1-100) |
| `sector` | string | - | Filter by sector name (e.g., "VN30", "BANKING") |
| `min_volume` | number | 10000 | Minimum trading volume filter |

#### Available Sort Metrics

| Metric | Description | Use Case |
|--------|-------------|----------|
| `close_changed` | Percentage price change from previous close | Find best/worst percentage performers |
| `volume` | Trading volume | Find most actively traded stocks |
| `volume_changed` | Volume change percentage from previous volume | Find unusual volume activity |
| `ma10_score` | 10-day moving average score | Short-term momentum analysis |
| `ma20_score` | 20-day moving average score | Medium-term momentum |
| `ma50_score` | 50-day moving average score | Long-term trend analysis |
| `ma100_score` | 100-day moving average score | Very long-term trends |
| `ma200_score` | 200-day moving average score | Major trend analysis |

#### Response Structure

```json
{
  "analysis_date": "2024-01-15",
  "analysis_type": "top_performers",
  "total_analyzed": 5,
  "data": {
    "performers": [
      {
        "symbol": "VCB",
        "close": 60300.0,
        "volume": 1500000,
        "close_changed": 2.55,
        "volume_changed": 15.2,
        "ma10": 59500.0,
        "ma20": 58800.0,
        "ma50": 57500.0,
        "ma100": 56200.0,
        "ma200": 54800.0,
        "ma10_score": 1.34,
        "ma20_score": 2.26,
        "ma50_score": 4.87,
        "ma100_score": 7.29,
        "ma200_score": 10.04,
        "sector": "BANKING"
      }
    ]
  }
}
```

#### Field Descriptions

- `symbol`: Stock ticker symbol
- `close`: Closing price in full VND
- `volume`: Trading volume
- `close_changed`: Percentage price change from previous close (null if no previous data)
- `volume_changed`: Percentage volume change from previous volume (null if previous volume was 0)
- `ma*`: Moving average values (null if insufficient data)
- `ma*_score`: Moving average momentum score
- `sector`: Sector name from ticker group mapping

#### Example Requests

```bash
# Top 10 performers by percentage change
GET /analysis/top-performers?sort_by=close_changed&limit=10

# Bottom 5 performers (ascending order)
GET /analysis/top-performers?sort_by=close_changed&direction=asc&limit=5

# Top volume leaders with minimum 1M volume
GET /analysis/top-performers?sort_by=volume&min_volume=1000000&limit=10

# VN30 sector top performers by MA20 score
GET /analysis/top-performers?sector=VN30&sort_by=ma20_score&limit=10

# Historical analysis for specific date
GET /analysis/top-performers?date=2024-01-10&sort_by=close_changed&limit=10
```

---

### GET /analysis/ma-scores-by-sector

Provides moving average analysis grouped by stock sectors, showing sector performance and top stocks per sector.

#### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `date` | string | latest | Analysis date in YYYY-MM-DD format |
| `ma_period` | number | 20 | Moving average period: 10, 20, 50, 100, or 200 |
| `min_score` | number | 0.0 | Minimum MA score threshold |
| `above_threshold_only` | boolean | false | Include only stocks above threshold |
| `top_per_sector` | number | 10 | Maximum stocks per sector (1-50) |

#### Response Structure

```json
{
  "analysis_date": "2024-01-15",
  "analysis_type": "ma_scores_by_sector",
  "total_analyzed": 282,
  "data": {
    "sectors": [
      {
        "sector_name": "BANKING",
        "total_stocks": 12,
        "stocks_above_threshold": 8,
        "average_score": 3.45,
        "top_stocks": [
          {
            "symbol": "VCB",
            "close": 60300.0,
            "volume": 1234567,
            "ma_value": 58800.0,
            "ma_score": 2.55,
            "close_changed": 1.25,
            "volume_changed": 15.5
          }
        ]
      }
    ],
    "ma_period": 20,
    "threshold": 0.0
  }
}
```

#### Field Descriptions

- `sector_name`: Sector name from ticker group mapping
- `total_stocks`: Total stocks analyzed in this sector
- `stocks_above_threshold`: Stocks meeting the minimum score requirement
- `average_score`: Average MA score across all stocks in sector
- `top_stocks`: Top performing stocks in this sector (sorted by score)
- `close`: Current closing price in full VND
- `volume`: Trading volume
- `ma_value`: Moving average value for the specified period
- `ma_score`: Moving average momentum score (percentage distance from MA)
- `close_changed`: Percentage change from previous close
- `volume_changed`: Percentage change from previous volume

#### MA Score Interpretation

- **Positive scores**: Price is above moving average (bullish)
- **Higher scores**: Stronger momentum relative to the MA period
- **Trend analysis**: Compare scores across different MA periods
- **Sector strength**: Average score indicates sector momentum

#### Example Requests

```bash
# MA20 scores by sector (default)
GET /analysis/ma-scores-by-sector

# MA50 scores with minimum score threshold
GET /analysis/ma-scores-by-sector?ma_period=50&min_score=1.0

# Only stocks above threshold
GET /analysis/ma-scores-by-sector?ma_period=20&min_score=2.0&above_threshold_only=true

# Top 5 stocks per sector
GET /analysis/ma-scores-by-sector?top_per_sector=5

# Long-term analysis with MA200
GET /analysis/ma-scores-by-sector?ma_period=200&min_score=0.5

# Historical analysis
GET /analysis/ma-scores-by-sector?date=2024-01-10&ma_period=20
```

---

## Error Handling

### HTTP Status Codes

| Status Code | Description | Common Causes |
|-------------|-------------|----------------|
| `200` | Success | Request processed successfully |
| `400` | Bad Request | Invalid parameters (e.g., invalid MA period) |
| `500` | Internal Server Error | Sector data loading failure |

### Error Response Format

```json
{
  "error": "Invalid MA period. Must be one of: 10, 20, 50, 100, 200"
}
```

### Common Error Scenarios

1. **Invalid MA Period**: Using MA period other than 10, 20, 50, 100, 200
2. **Invalid Date Format**: Date not in YYYY-MM-DD format
3. **Sector Data Unavailable**: ticker_group.json file missing or corrupted
4. **Invalid Date**: Future dates or dates with no available data

---

## Usage Notes

### Performance Considerations

- **Caching**: Analysis endpoints leverage the same 60-second cache as the main API
- **Data Limits**: Results are limited to ensure fast response times
- **Concurrent Access**: Multiple analysis requests share the same DataStore cache

### Best Practices

1. **Date Selection**: Use recent dates for most relevant analysis
2. **MA Period Selection**:
   - Short-term (10, 20): For recent momentum
   - Medium-term (50, 100): For trend confirmation
   - Long-term (200): For major trend analysis
3. **Score Interpretation**: Higher scores indicate stronger momentum
4. **Volume Filtering**: Use `min_volume` to filter low-liquidity stocks
5. **Sector Analysis**: Compare sectors to identify market leadership

### Data Freshness

- **Real-time**: Analysis reflects latest available market data
- **Historical**: Full historical analysis capabilities with date parameter
- **Update Frequency**: Data updates align with main API synchronization

---

## Testing

### Automated Testing

Run the comprehensive test suite:

```bash
# Test all analysis endpoints
./scripts/test-analysis.sh

# Test against different server URLs
./scripts/test-analysis.sh http://localhost:3001
```

### Manual Testing

```bash
# Quick health check
curl http://localhost:3000/analysis/top-performers?limit=5

# Test specific parameters
curl "http://localhost:3000/analysis/ma-scores-by-sector?ma_period=20&min_score=1.0"
```

### Integration Testing

The test script covers:
- ✅ All parameter combinations
- ✅ Error scenarios
- ✅ Edge cases and validation
- ✅ Response format verification
- ✅ Performance benchmarks

---

## Examples

### Market Analysis Workflow

1. **Market Overview**: Get top performers by percentage change
   ```bash
   GET /analysis/top-performers?sort_by=close_changed&limit=20
   ```

2. **Sector Leadership**: Find sectors with strongest MA20 momentum
   ```bash
   GET /analysis/ma-scores-by-sector?ma_period=20&min_score=2.0
   ```

3. **Volume Analysis**: Identify unusual volume activity
   ```bash
   GET /analysis/top-performers?sort_by=volume_changed&min_volume=500000
   ```

4. **Trend Confirmation**: Long-term trend analysis
   ```bash
   GET /analysis/top-performers?sort_by=ma200_score&limit=10
   ```

### Sample Analysis Response

```bash
# Get top performers with MA20 analysis
curl "http://localhost:3000/analysis/top-performers?sort_by=ma20_score&limit=5"

# Response shows stocks with strongest MA20 momentum,
# useful for identifying medium-term trend leaders
```

---

## Support

For questions or issues with the Analysis API:

1. **Documentation**: Refer to this guide and main API documentation
2. **Testing**: Use the provided test script for verification
3. **Logs**: Check server logs for detailed error information
4. **Performance**: Monitor cache hit rates for optimal usage

---

*Last Updated: January 2025*
*Version: 1.0 - Compatible with AIPriceAction v0.3.0+*