# Cache Key Collision Bug in Minute Data

## 🔍 Bug Summary

**Critical Issue:** Minute data API requests return wrong data due to cache key collision in disk cache layer.

**Root Cause:** Cache key generation only includes `(ticker, interval)` but **excludes date ranges** (`start_date`, `end_date`, `limit`).

## 🎯 Exact URLs That Reproduce the Bug

### **Primary Test Case:**
```bash
# First query: Populates cache with Jan 15th data (228 records)
curl "http://localhost:3001/tickers?symbol=VNINDEX&interval=1m&start_date=2025-01-15&end_date=2025-01-15"
# Returns: 228 records

# Second query: Should return May 15th data but hits same cache key (0 records)
curl "http://localhost:3001/tickers?symbol=VNINDEX&interval=1m&start_date=2025-05-15&end_date=2025-05-15"
# Returns: 0 records (WRONG - should be ~200+ records)

# Third query: Same issue - cache hit returns cached Jan 15th data filtered for March 10th
curl "http://localhost:3001/tickers?symbol=VNINDEX&interval=1m&start_date=2025-03-10&end_date=2025-03-10"
# Returns: 0 records (WRONG - should be ~200+ records)
```

## 📍 Bug Location

**File:** `src/services/data_store.rs:1308`

**Current Code:**
```rust
let cache_key = (ticker.clone(), interval);  // ❌ MISSING date ranges!
```

**Correct Code:**
```rust
let cache_key = (
    ticker.clone(),
    interval,
    start_date,  // ✅ Add missing date ranges
    end_date,    // ✅ Add missing date ranges
    limit        // ✅ Add limit for completeness
);
```

## 🔍 Debug Evidence from Server Logs

### **First Request (Jan 15th):**
```
[19:08:10] Starting second CSV file read (disk cache miss) ticker="VNINDEX"
[19:08:10] Starting smart CSV read ticker="VNINDEX" strategy=CompleteFile
[19:08:10] Reading complete CSV file ticker="VNINDEX" strategy="complete"
[19:08:10] Successfully read second CSV file ticker="VNINDEX" records_read=228
[19:08:10] Returning ticker data ticker_count=1 total_records=228
```

### **Second Request (May 15th):**
```
[19:08:18] Disk cache hit for VNINDEX/1m.csv
[19:08:18] get_data_smart complete: 0.03ms, 0 tickers, 0 records
[19:08:18] Returning ticker data ticker_count=0 total_records=0
```

### **Third Request (March 10th):**
```
[19:08:26] Disk cache hit for VNINDEX/1m.csv
[19:08:26] get_data_smart complete: 0.02ms, 0 tickers, 0 records
[19:08:26] Returning ticker data ticker_count=0 total_records=0
```

## 🚨 Impact Analysis

### **Affected APIs:**
- ✅ `/tickers?interval=1m` - **BROKEN** (cache collision)
- ✅ Volume profile `/analysis/volume-profile` - **BROKEN** (depends on minute data)
- ✅ All aggregated intervals (5m, 15m, 30m) - **DEPENDS** on minute data accuracy

### **Data Flow Architecture:**
```
API Request → get_data_with_cache() → get_data_from_disk_with_cache()
                     ↓
Minute data ALWAYS bypasses memory cache (line 1092-1094)
                     ↓
Disk cache with BROKEN key: (ticker, interval)
                     ↓
Returns cached first result regardless of date parameters
```

## 🔧 Technical Details

### **Cache Architecture:**
- **Memory cache:** Daily + Hourly data only
- **Disk cache:** Minute data + ALL intervals with cache misses
- **TTL:** 30 seconds for disk cache

### **Cache Key Logic:**
```rust
// Current (BUGGY):
let cache_key = (ticker.clone(), interval);

// Should be (FIXED):
let cache_key = (ticker.clone(), interval, start_date, end_date, limit);
```

### **Smart CSV Reading:**
- **File:** `market_data/VNINDEX/1m.csv` (~21MB, 120K+ rows)
- **Strategy:** CompleteFile reads with date filtering
- **Performance:** 40ms for full read, 0.03ms for cache hits

## 🧪 Test Patterns

### **Cache Collision Reproduction:**
1. **First call:** Any date range → Populates cache
2. **Subsequent calls:** Different date ranges → Cache hit with wrong data

### **Expected vs Actual:**
| Date Range | Expected Records | Actual Records | Status |
|-------------|----------------|----------------|--------|
| 2025-01-15 | ~228 | 228 | ✅ First call works |
| 2025-05-15 | ~200+ | 0 | ❌ **Cache collision** |
| 2025-03-10 | ~200+ | 0 | ❌ **Cache collision** |

### **Volume Profile Impact:**
```bash
# Production URL showing the bug:
curl "https://api.aipriceaction.com/analysis/volume-profile?symbol=VNINDEX&date=2025-12-01&bins=10&mode=vn"

# Root cause: Volume profile calls get_data_with_cache with minute data
# which hits the broken cache key collision
```

## 🛠️ Fix Implementation

### **Immediate Fix (Critical):**
```rust
// In src/services/data_store.rs:1308
let cache_key = (
    ticker.clone(),
    interval,
    start_date,  // ✅ Include date ranges in cache key
    end_date,    // ✅ Include date ranges in cache key
    limit        // ✅ Include limit for cache completeness
);
```

### **Hash Function Update:**
```rust
// Need to update CacheKey type definition to include date ranges
type CacheKey = (String, Interval, Option<DateTime<Utc>>, Option<DateTime<Utc>>, Option<usize>);
```

### **Related File Updates:**
- `src/services/data_store.rs` - Main fix
- Cache entry validation logic
- Disk cache TTL management

## 🔍 Verification Steps

### **After Fix:**
```bash
# Should return different record counts for different dates
curl "http://localhost:3001/tickers?symbol=VNINDEX&interval=1m&start_date=2025-01-15&end_date=2025-01-15"  # ~228
curl "http://localhost:3001/tickers?symbol=VNINDEX&interval=1m&start_date=2025-05-15&end_date=2025-05-15"  # ~200+
curl "http://localhost:3001/tickers?symbol=VNINDEX&interval=1m&start_date=2025-03-10&end_date=2025-03-10"  # ~200+

# Volume profile should work with different dates
curl "http://localhost:3001/analysis/volume-profile?symbol=VNINDEX&date=2025-05-15&bins=10&mode=vn"
```

### **Regression Tests:**
```bash
# Cache collision test suite
./scripts/test_cache_collision.sh

# Volume profile regression tests
./scripts/test-volume-profile.sh
```

## 📊 Production Impact Assessment

### **Affected Users:**
- **Volume profile analysts** - Getting wrong data
- **Minute data API users** - Cache pollution across date ranges
- **Aggregated interval users** - Inherited from minute data issues

### **Data Integrity:**
- **Wrong analysis results** - Historical analysis uses incorrect data
- **Financial decisions** - Based on wrong market data
- **Backtesting failures** - Strategy testing with corrupted data

### **Performance Impact:**
- **Cache effectiveness:** Currently broken for minute data
- **Memory usage:** Cache pollution with duplicate keys
- **API response times:** Fast but wrong (cache hits vs correct data)

## 🚨 Urgency Level

**CRITICAL** - This bug affects data accuracy for all minute data APIs and dependent features.

### **Recommended Actions:**
1. **Immediate fix** in production codebase
2. **Cache invalidation** after deployment
3. **Regression test coverage** to prevent future occurrences
4. **Documentation update** for cache architecture

## 🔎 Discovery Timeline

### **How It Was Found:**
1. User suspected cache behavior with same-date queries
2. Systematic testing revealed the pattern
3. Code analysis identified root cause in cache key generation
4. Debug logging confirmed cache collision behavior

### **User Hypothesis Validation:**
✅ **CONFIRMED:** User's suspicion was 100% correct about cache behavior issues with date ranges, though the specific mechanism is cache key collision rather than smart CSV reading problems.

---

**Status:** Ready for immediate fix deployment 🚨