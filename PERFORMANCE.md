# Performance Analysis & Optimization

## Current Performance Metrics (Batch Size: 10)

### Per-Ticker Breakdown
```
Total per ticker:  1.5-1.6s
├─ Batch API:      0.05s per ticker (0.14s for 3 tickers)
├─ Dividend check: 1.55s (API: 0.04s + mandatory sleep: 1.5s) ⚠️ BOTTLENECK
└─ File I/O:       0.001-0.002s (negligible)
```

### Full Sync (290 tickers)
```
Batch processing:  ~40s (29 batches × ~1.4s)
├─ API calls:      ~4s (29 × 0.14s)
└─ Rate limiting:  ~36s (29 × 1.25s avg sleep)

Individual processing: ~440s (290 × 1.52s avg)
├─ Dividend checks: ~450s (290 × 1.55s) ⚠️ MAJOR BOTTLENECK
├─ Data merging:   ~3s
└─ File I/O:       ~0.6s

Total estimated:   ~480s (8 minutes)
```

## Bottleneck Analysis

### 1. Dividend Check (94% of total time)
Each ticker requires:
- API call: 0.04s
- Mandatory sleep: 1.5s
- Total: 1.55s × 290 tickers = **449.5s (7.5 minutes)**

### 2. Rate Limiting Delays (7% of total time)
- Between batches: 1-2s × 29 batches = **~36s**

### 3. Actual API Work (1% of total time)
- Batch API calls: 0.14s × 29 = **4s**
- Dividend API calls: 0.04s × 290 = **12s**
- Total API time: **~16s**

## Optimization Strategies

### ✅ Recommended: Increase Batch Size

**Impact**: Reduces number of batch API calls and sleep delays

| Batch Size | Batches | Sleep Time | Total Batch Time | Speedup |
|------------|---------|------------|------------------|---------|
| 10 (current) | 29 | ~36s | ~40s | 1.0x |
| 20 | 15 | ~19s | ~21s | 1.9x |
| 30 | 10 | ~13s | ~15s | 2.7x |
| 50 | 6 | ~8s | ~9s | 4.4x |

**Recommendation**: Use batch size of **30-50** for optimal performance

**Usage**:
```bash
./target/release/aipriceaction pull --intervals daily --batch-size 30
```

### ⚠️ Skip Dividend Check for Indices

**Impact**: Save 1.55s per index ticker

Indices (VNINDEX, VN30) don't pay dividends. Skipping their dividend checks would save:
- 2 tickers × 1.55s = **3.1s per sync**

### 🔧 Optimize Dividend Check Sleep

**Current**: 1.5s mandatory sleep after each check
**Possible**: Reduce to 1.0s or make configurable

**Impact**:
- 290 tickers × 0.5s saved = **145s (2.4 minutes) saved**
- Total time: 8 min → 5.6 min (**30% faster**)

### 🚀 Parallel Dividend Checks (Future)

**Current**: Sequential processing (1.55s × 290 = 449s)
**With 10 parallel workers**: 449s / 10 = **~45s**

**Impact**: Could reduce total sync time from 8 min to **~2 min** (4x faster)

⚠️ Requires careful rate limit management

## Testing Different Batch Sizes

### Test Command
```bash
# Test with batch size 10 (default)
./target/release/aipriceaction pull --intervals daily --batch-size 10 --resume-days 3

# Test with batch size 20
./target/release/aipriceaction pull --intervals daily --batch-size 20 --resume-days 3

# Test with batch size 30
./target/release/aipriceaction pull --intervals daily --batch-size 30 --resume-days 3

# Test with batch size 50
./target/release/aipriceaction pull --intervals daily --batch-size 50 --resume-days 3
```

### Expected Results

| Batch Size | Batch API Time | Sleep Time | Dividend Time | Total Time | Speedup |
|------------|----------------|------------|---------------|------------|---------|
| 10 | 4s | 36s | 450s | 490s (8.2 min) | 1.0x |
| 20 | 4s | 19s | 450s | 473s (7.9 min) | 1.04x |
| 30 | 4s | 13s | 450s | 467s (7.8 min) | 1.05x |
| 50 | 4s | 8s | 450s | 462s (7.7 min) | 1.06x |

**Note**: Modest improvement because dividend checks dominate (92% of time)

## Recommendations

### Immediate (No Code Changes)
1. **Use batch size 30-50** for faster batch processing
   ```bash
   ./target/release/aipriceaction pull --batch-size 30
   ```

### Short Term (Small Code Changes)
1. **Skip dividend checks for indices** (VNINDEX, VN30, etc.)
   - Add `is_index()` check before dividend detection
   - Save ~3s per sync

2. **Make dividend check sleep configurable**
   - Add `--dividend-check-sleep` option
   - Allow users to experiment with lower values (e.g., 1.0s, 0.5s)

### Long Term (Significant Refactor)
1. **Implement parallel dividend checks**
   - Use tokio task pool with configurable concurrency
   - Could reduce 8 min → 2 min (4x speedup)
   - Requires careful rate limit coordination

2. **Smart dividend check caching**
   - Only check tickers that historically have dividends
   - Skip recent checks (e.g., if checked within last hour)

3. **Incremental update optimization**
   - For resume mode with few changes, skip dividend checks entirely
   - Only check if data shows anomalies (price drops > 5%)

## Current Status

✅ Timing instrumentation added
✅ Batch size configurable via --batch-size
⏳ Testing different batch sizes
⏳ Measuring real-world performance

## Next Steps

1. Complete batch size performance testing
2. Implement index skip for dividend checks
3. Consider adding parallel dividend check option
