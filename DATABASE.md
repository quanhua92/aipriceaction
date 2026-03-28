# DATABASE.md — PostgreSQL Storage Design

This document describes the PostgreSQL schema that replaces CSV file storage for all market data (stocks, crypto, all intervals).

## Why PostgreSQL

- Single source of truth instead of ~1600+ CSV files
- Atomic UPSERT replaces "delete CSV + re-download" patterns
- Efficient range queries replace full file scans
- Built-in concurrency — no file locking concerns
- Aggregated intervals stored alongside raw intervals

## Schema

### Three tables

The schema uses three tables: a small `tickers` lookup table, a narrow `ohlcv` table for price/volume data, and a separate `ohlcv_indicators` table for computed technical indicators.

#### tickers — lookup table (~380 rows, no partitioning)

```sql
CREATE TABLE tickers (
    id     SERIAL PRIMARY KEY,
    source TEXT NOT NULL,        -- 'vn', 'crypto', 'us', 'hk', ...
    ticker TEXT NOT NULL,
    name   TEXT,                 -- optional display name
    status TEXT NOT NULL DEFAULT 'ready',  -- 'ready', 'dividend_detected'
    UNIQUE(source, ticker)
);
```

`status` tracks the ticker's lifecycle state:
- `ready` — normal operation, eligible for sync and enhancement
- `dividend_detected` — price adjustment detected, awaiting full history rebuild

#### ohlcv — narrow price/volume, partitioned by interval

```sql
CREATE TABLE ohlcv (
    ticker_id  INT           NOT NULL REFERENCES tickers(id),
    interval   TEXT          NOT NULL,  -- '1m','5m','15m','30m','1h','1D','1W','2W','1M'
    time       TIMESTAMPTZ   NOT NULL,
    open       DOUBLE PRECISION NOT NULL,
    high       DOUBLE PRECISION NOT NULL,
    low        DOUBLE PRECISION NOT NULL,
    close      DOUBLE PRECISION NOT NULL,
    volume     BIGINT            NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY LIST (interval);

ALTER TABLE ohlcv ADD CONSTRAINT ohlcv_pkey
    PRIMARY KEY (ticker_id, interval, time);
```

#### ohlcv_indicators — MA columns + processed_at, identical partitioning

```sql
CREATE TABLE ohlcv_indicators (
    ticker_id INT           NOT NULL REFERENCES tickers(id),
    interval  TEXT          NOT NULL,
    time      TIMESTAMPTZ   NOT NULL,
    ma10        DOUBLE PRECISION,
    ma20        DOUBLE PRECISION,
    ma50        DOUBLE PRECISION,
    ma100       DOUBLE PRECISION,
    ma200       DOUBLE PRECISION,
    ma10_score  DOUBLE PRECISION,
    ma20_score  DOUBLE PRECISION,
    ma50_score  DOUBLE PRECISION,
    ma100_score DOUBLE PRECISION,
    ma200_score DOUBLE PRECISION,
    close_changed       DOUBLE PRECISION,
    volume_changed      DOUBLE PRECISION,
    total_money_changed DOUBLE PRECISION,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY LIST (interval);

ALTER TABLE ohlcv_indicators ADD CONSTRAINT ohlcv_indicators_pkey
    PRIMARY KEY (ticker_id, interval, time);
```

### Indexes

```sql
-- Latest N records for a ticker (most common query)
CREATE INDEX idx_ohlcv_ticker_time
    ON ohlcv (ticker_id, interval, time DESC);

-- Top performers / sector analysis (on indicators table)
CREATE INDEX idx_indicators_interval_time_close
    ON ohlcv_indicators (interval, time DESC, close_changed)
    WHERE close_changed IS NOT NULL;

-- Enhancer stale scan: find rows needing re-processing
CREATE INDEX idx_ohlcv_updated
    ON ohlcv (updated_at DESC);
```

### Partitions

Both `ohlcv` and `ohlcv_indicators` use identical partitioning. Indicator partitions are prefixed with `indicators_` to avoid name collisions.

```sql
-- ohlcv partitions
CREATE TABLE ohlcv_minute  PARTITION OF ohlcv FOR VALUES IN ('1m') PARTITION BY RANGE (time);
CREATE TABLE ohlcv_hourly  PARTITION OF ohlcv FOR VALUES IN ('1h') PARTITION BY RANGE (time);
CREATE TABLE ohlcv_daily   PARTITION OF ohlcv FOR VALUES IN ('1D');
CREATE TABLE ohlcv_5min    PARTITION OF ohlcv FOR VALUES IN ('5m');
CREATE TABLE ohlcv_15min   PARTITION OF ohlcv FOR VALUES IN ('15m');
CREATE TABLE ohlcv_30min   PARTITION OF ohlcv FOR VALUES IN ('30m');
CREATE TABLE ohlcv_weekly  PARTITION OF ohlcv FOR VALUES IN ('1W');
CREATE TABLE ohlcv_2week   PARTITION OF ohlcv FOR VALUES IN ('2W');
CREATE TABLE ohlcv_monthly PARTITION OF ohlcv FOR VALUES IN ('1M');

-- ohlcv_indicators partitions
CREATE TABLE indicators_minute  PARTITION OF ohlcv_indicators FOR VALUES IN ('1m') PARTITION BY RANGE (time);
CREATE TABLE indicators_hourly  PARTITION OF ohlcv_indicators FOR VALUES IN ('1h') PARTITION BY RANGE (time);
CREATE TABLE indicators_daily   PARTITION OF ohlcv_indicators FOR VALUES IN ('1D');
CREATE TABLE indicators_5min    PARTITION OF ohlcv_indicators FOR VALUES IN ('5m');
CREATE TABLE indicators_15min   PARTITION OF ohlcv_indicators FOR VALUES IN ('15m');
CREATE TABLE indicators_30min   PARTITION OF ohlcv_indicators FOR VALUES IN ('30m');
CREATE TABLE indicators_weekly  PARTITION OF ohlcv_indicators FOR VALUES IN ('1W');
CREATE TABLE indicators_2week   PARTITION OF ohlcv_indicators FOR VALUES IN ('2W');
CREATE TABLE indicators_monthly PARTITION OF ohlcv_indicators FOR VALUES IN ('1M');
```

Sub-partitions for high-volume intervals (both tables):

```sql
-- ohlcv sub-partitions
CREATE TABLE ohlcv_minute_2024 PARTITION OF ohlcv_minute FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ohlcv_minute_2025 PARTITION OF ohlcv_minute FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ohlcv_minute_2026 PARTITION OF ohlcv_minute FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE ohlcv_hourly_2024 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ohlcv_hourly_2025 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ohlcv_hourly_2026 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

-- indicators sub-partitions
CREATE TABLE indicators_minute_2024 PARTITION OF indicators_minute FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE indicators_minute_2025 PARTITION OF indicators_minute FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE indicators_minute_2026 PARTITION OF indicators_minute FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE indicators_hourly_2024 PARTITION OF indicators_hourly FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE indicators_hourly_2025 PARTITION OF indicators_hourly FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE indicators_hourly_2026 PARTITION OF indicators_hourly FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
```

Partition table names use descriptive words (`minute`, `hourly`, `monthly`) instead of abbreviated interval values to avoid PostgreSQL case-folding collisions (e.g., `ohlcv_1m` vs `ohlcv_1M` both fold to lowercase). Daily and aggregated intervals are small enough to not need sub-partitioning. New year partitions are added via migration.

## Key design decisions

### Why `tickers` table

A dedicated lookup table replaces repeating `(source, ticker)` text columns across millions of rows:

- 4-byte INT FK instead of two TEXT columns in every OHLCV row — significant storage savings on 50M+ rows/year
- Single place for ticker metadata (display name, status)
- `status` column enables lifecycle tracking without scanning OHLCV data
- Adding a new market requires zero schema change — insert into `tickers` with a new `source` value

### Why split indicators into a separate table

MA/score columns are computed after OHLCV is written, creating a two-phase write pattern:

- **Independent write rates** — OHLCV is upserted during sync; indicators are written by the enhancer. Splitting avoids UPDATE-in-place on hot rows
- **Stale detection via LEFT JOIN** — finding unprocessed rows is a simple join on matching PKs, no NULL checks on the same row
- **Narrower ohlcv rows** — 9 columns instead of 25, more rows per page, better sequential scan performance
- **Targeted autovacuum** — minute partition autovacuum only touches OHLCV data, not indicator columns

### Why single table per concept (not separate per interval)

With `PARTITION BY LIST (interval)`, each interval is physically separate storage. Queries with `WHERE interval = '1D'` never touch 1m data. Benefits:

- Uniform schema — one model, one DAO, one UPSERT path
- Aggregated intervals use identical query patterns as raw intervals
- Schema changes apply once, not per-table

### Why aggregated intervals are pre-computed (not on-the-fly)

- Identical query path for all intervals (`WHERE ticker_id = ? AND interval = ?`)
- Aggregated candles can also get enhanced data (MAs on 5m candles are valid)
- Avoids CPU cost on every API request

## Write patterns

### Ensure ticker exists (upsert)

```sql
INSERT INTO tickers (source, ticker, name)
VALUES ($1, $2, $3)
ON CONFLICT (source, ticker) DO NOTHING
RETURNING id;
```

Application code fetches `ticker_id` once at startup or on first use, then uses the integer FK for all subsequent operations.

### UPSERT OHLCV (sync / resume)

```sql
INSERT INTO ohlcv (ticker_id, interval, time, open, high, low, close, volume, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
ON CONFLICT (ticker_id, interval, time) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high,
    low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume,
    updated_at = NOW();
```

### Enhancer (compute MAs / scores)

```sql
INSERT INTO ohlcv_indicators (ticker_id, interval, time,
    ma10, ma20, ma50, ma100, ma200,
    ma10_score, ma20_score, ma50_score, ma100_score, ma200_score,
    close_changed, volume_changed, total_money_changed,
    processed_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, NOW())
ON CONFLICT (ticker_id, interval, time) DO UPDATE SET
    ma10 = EXCLUDED.ma10, ma20 = EXCLUDED.ma20, ma50 = EXCLUDED.ma50,
    ma100 = EXCLUDED.ma100, ma200 = EXCLUDED.ma200,
    ma10_score = EXCLUDED.ma10_score, ma20_score = EXCLUDED.ma20_score,
    ma50_score = EXCLUDED.ma50_score, ma100_score = EXCLUDED.ma100_score,
    ma200_score = EXCLUDED.ma200_score,
    close_changed = EXCLUDED.close_changed, volume_changed = EXCLUDED.volume_changed,
    total_money_changed = EXCLUDED.total_money_changed,
    processed_at = NOW();
```

### Find stale rows (enhancer needs re-run)

Stale detection uses a LEFT JOIN between ohlcv and ohlcv_indicators on matching primary keys. Rows are stale when the indicator row is missing (never processed) or when `processed_at < updated_at` (OHLCV was updated after indicators were computed).

```sql
SELECT o.ticker_id, o.interval, o.time
FROM ohlcv o
LEFT JOIN ohlcv_indicators oi
    ON o.ticker_id = oi.ticker_id
    AND o.interval = oi.interval
    AND o.time = oi.time
WHERE oi.processed_at IS NULL OR oi.processed_at < o.updated_at
ORDER BY o.updated_at DESC;
```

### Dividend / stock split detection and handling

When a company issues a dividend, stock split, or bonus shares, the exchange retroactively adjusts historical prices. The system detects this during daily sync and rebuilds all data for the affected ticker.

**Detection (daily interval only):**

1. Fetch recent data from API (e.g., last 3 weeks)
2. Compare close prices for matching dates between API data and existing DB rows
3. If `existing_close / new_close > 1.02` (>2% difference), a price adjustment is detected
4. Skip index tickers (VNINDEX, VN30) — they never have corporate actions

```sql
-- Compare existing vs freshly fetched close prices for same dates
SELECT e.time, e.close AS old_close, $new_close AS new_close,
       e.close / $new_close AS ratio
FROM ohlcv e
JOIN tickers t ON e.ticker_id = t.id
WHERE t.source = 'vn' AND t.ticker = $ticker AND e.interval = '1D'
  AND e.time < (SELECT MAX(time) FROM ohlcv o JOIN tickers t2 ON o.ticker_id = t2.id
                WHERE t2.source = 'vn' AND t2.ticker = $ticker AND o.interval = '1D')
  AND e.time IN (<dates_from_new_data>)
ORDER BY e.time DESC;
```

**Handling:**

Once detected, set ticker status to `dividend_detected`, then delete ALL rows for that ticker across both tables and all intervals:

```sql
-- Mark ticker for rebuild
UPDATE tickers SET status = 'dividend_detected'
WHERE source = 'vn' AND ticker = 'VCB';

-- Delete all OHLCV data (all intervals cascade via ticker_id)
DELETE FROM ohlcv WHERE ticker_id = (
    SELECT id FROM tickers WHERE source = 'vn' AND ticker = 'VCB'
);

-- Delete all indicator data
DELETE FROM ohlcv_indicators WHERE ticker_id = (
    SELECT id FROM tickers WHERE source = 'vn' AND ticker = 'VCB'
);

-- After re-download completes, mark ready again
UPDATE tickers SET status = 'ready'
WHERE source = 'vn' AND ticker = 'VCB';
```

This replaces the current CSV-based flow which required:
- Deleting 3 CSV files (1D.csv, 1h.csv, 1m.csv)
- Sending MPSC cache clear notifications
- Triggering re-download for each interval separately

With PostgreSQL, it's `status` update + DELETE + normal UPSERT on next sync + `status` update. The cache invalidation happens naturally — application cache entries for this ticker can be evicted by key.

**Detection scope:**

| Interval | Runs detection? | Why |
|----------|----------------|-----|
| 1D | Yes | Primary detection point — price adjustments are most visible on daily candles |
| 1h | No | Covered by 1D detection. When 1D deletes all rows, 1h re-downloads on next sync |
| 1m | No | Same as 1h — cascading delete handles it |

**Limitations (same as current system):**

- 2% threshold — small dividends/splits below 2% may go undetected
- One-way detection — only catches price decreases (ratio > 1.02). Reverse stock splits (price increase) are not detected
- No distinction between dividends, stock splits, and bonus shares — all trigger the same full rebuild

### Aggregation (1m -> 5m)

```sql
INSERT INTO ohlcv (ticker_id, interval, time, open, high, low, close, volume)
SELECT
    ticker_id,
    '5m',
    date_trunc('hour', time) + INTERVAL '5 min' * FLOOR(EXTRACT(MINUTE FROM time) / 5),
    (ARRAY_AGG(open ORDER BY time))[1],
    MAX(high),
    MIN(low),
    (ARRAY_AGG(close ORDER BY time DESC))[1],
    SUM(volume)
FROM ohlcv
WHERE ticker_id = (SELECT id FROM tickers WHERE source = 'crypto' AND ticker = 'BTCUSDT')
  AND interval = '1m'
  AND time >= '2026-03-28'
GROUP BY ticker_id,
    date_trunc('hour', time) + INTERVAL '5 min' * FLOOR(EXTRACT(MINUTE FROM time) / 5)
ON CONFLICT (ticker_id, interval, time) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high,
    low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume;
```

## Read patterns

```sql
-- Latest N records (OHLCV only, fast)
SELECT o.time, o.open, o.high, o.low, o.close, o.volume
FROM ohlcv o
JOIN tickers t ON o.ticker_id = t.id
WHERE t.source = 'vn' AND t.ticker = 'VCB' AND o.interval = '1D'
ORDER BY o.time DESC LIMIT 10;

-- Full data with indicators (JOIN on PK)
SELECT o.time, o.open, o.high, o.low, o.close, o.volume,
       oi.ma10, oi.ma20, oi.ma50, oi.ma100, oi.ma200,
       oi.ma10_score, oi.ma20_score, oi.ma50_score, oi.ma100_score, oi.ma200_score,
       oi.close_changed, oi.volume_changed, oi.total_money_changed
FROM ohlcv o
JOIN tickers t ON o.ticker_id = t.id
LEFT JOIN ohlcv_indicators oi ON o.ticker_id = oi.ticker_id
    AND o.interval = oi.interval AND o.time = oi.time
WHERE t.source = 'vn' AND t.ticker = 'VCB' AND o.interval = '1D'
ORDER BY o.time ASC;

-- Top performers (indicators table only — avoids touching ohlcv)
SELECT t.ticker, o.close, oi.close_changed, o.volume
FROM ohlcv_indicators oi
JOIN ohlcv o ON oi.ticker_id = o.ticker_id AND oi.interval = o.interval AND oi.time = o.time
JOIN tickers t ON oi.ticker_id = t.id
WHERE t.source = 'vn' AND oi.interval = '1D'
  AND oi.time = (
    SELECT MAX(o2.time) FROM ohlcv o2
    JOIN tickers t2 ON o2.ticker_id = t2.id
    WHERE t2.source = 'vn' AND o2.interval = '1D'
  )
ORDER BY oi.close_changed DESC NULLS LAST
LIMIT 10;

-- MA scores by sector (indicators table only)
SELECT t.ticker, o.close, oi.ma50_score, oi.ma200_score
FROM ohlcv_indicators oi
JOIN ohlcv o ON oi.ticker_id = o.ticker_id AND oi.interval = o.interval AND oi.time = o.time
JOIN tickers t ON oi.ticker_id = t.id
WHERE t.source = 'vn' AND oi.interval = '1D'
  AND oi.time = (
    SELECT MAX(o2.time) FROM ohlcv o2
    JOIN tickers t2 ON o2.ticker_id = t2.id
    WHERE t2.source = 'vn' AND o2.interval = '1D'
  )
  AND oi.ma50_score IS NOT NULL
ORDER BY oi.ma50_score DESC;
```

## Size estimates

Narrower ohlcv rows (9 data columns instead of 25) mean more rows per page and smaller tables for the bulk of the data.

| Partition (ohlcv) | Interval | Tickers | Rows/year | Est. size |
|---|---|---|---|---|
| ohlcv_daily | 1D | 380 | ~95K | ~6 MB |
| ohlcv_hourly | 1h | 380 | ~2.3M | ~140 MB |
| ohlcv_minute | 1m | 380 | ~37M | ~2.3 GB |
| ohlcv_5min | 5m | 380 | ~7.4M | ~460 MB |
| ohlcv_15min | 15m | 380 | ~2.5M | ~155 MB |
| ohlcv_weekly | 1W | 380 | ~20K | ~1.3 MB |
| **ohlcv Total** | | | **~50M/yr** | **~3.1 GB/yr** |

| Partition (indicators) | Interval | Rows/year | Est. size |
|---|---|---|---|
| indicators_daily | 1D | ~95K | ~9 MB |
| indicators_hourly | 1h | ~2.3M | ~220 MB |
| indicators_minute | 1m | ~37M | ~3.5 GB |
| indicators_5min | 5m | ~7.4M | ~700 MB |
| indicators_15min | 15m | ~2.5M | ~240 MB |
| indicators_weekly | 1W | ~20K | ~2 MB |
| **indicators Total** | | **~50M/yr** | **~4.7 GB/yr** |

**Combined total: ~7.8 GB/year** (comparable to the previous single-table estimate, but with better write separation and narrower ohlcv rows for the hot path).

## Tuning

```sql
-- Aggressive autovacuum for high-write minute partitions (both tables)
ALTER TABLE ohlcv_minute_2026 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02,
    autovacuum_vacuum_cost_delay = 10
);

ALTER TABLE indicators_minute_2026 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02,
    autovacuum_vacuum_cost_delay = 10
);
```

## Application-level cache

PostgreSQL replaces CSV files as the persistent store, but an application-level LRU/TTL cache (current `DataStore` pattern) should remain for hot paths. PG is fast but sub-millisecond repeated reads benefit from in-memory caching.

## Migration plan

1. Create schema, partitions, indexes
2. Migrate existing CSV data into PostgreSQL
3. Rewrite `src/` storage layer — replace `DataStore`, `DataSync`, `csv_enhancer` with PG-backed services
4. Keep API interface unchanged (`/tickers`, `/health`, `/tickers/group`)
5. Remove CSV-related code, file locking, MPSC channel for CSV updates
