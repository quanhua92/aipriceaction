-- Tickers lookup table (~380 rows, no partitioning)
CREATE TABLE tickers (
    id     SERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name   TEXT,
    status TEXT NOT NULL DEFAULT 'ready',
    UNIQUE(source, ticker)
);

-- OHLCV partitioned table: narrow price/volume + updated_at
CREATE TABLE ohlcv (
    ticker_id INT           NOT NULL REFERENCES tickers(id),
    interval  TEXT          NOT NULL,
    time      TIMESTAMPTZ   NOT NULL,
    open      DOUBLE PRECISION NOT NULL,
    high      DOUBLE PRECISION NOT NULL,
    low       DOUBLE PRECISION NOT NULL,
    close     DOUBLE PRECISION NOT NULL,
    volume    BIGINT            NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY LIST (interval);

ALTER TABLE ohlcv ADD CONSTRAINT ohlcv_pkey
    PRIMARY KEY (ticker_id, interval, time);

-- OHLCV indicators partitioned table: MA columns + processed_at
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

-- Indexes
CREATE INDEX idx_ohlcv_ticker_time
    ON ohlcv (ticker_id, interval, time DESC);

CREATE INDEX idx_indicators_interval_time_close
    ON ohlcv_indicators (interval, time DESC, close_changed)
    WHERE close_changed IS NOT NULL;

CREATE INDEX idx_ohlcv_updated
    ON ohlcv (updated_at DESC);

-- ── ohlcv partitions ──

-- Raw intervals (fetched from API)
CREATE TABLE ohlcv_minute  PARTITION OF ohlcv FOR VALUES IN ('1m') PARTITION BY RANGE (time);
CREATE TABLE ohlcv_hourly  PARTITION OF ohlcv FOR VALUES IN ('1h') PARTITION BY RANGE (time);
CREATE TABLE ohlcv_daily   PARTITION OF ohlcv FOR VALUES IN ('1D');

-- Aggregated intervals (computed from raw)
CREATE TABLE ohlcv_5min    PARTITION OF ohlcv FOR VALUES IN ('5m');
CREATE TABLE ohlcv_15min   PARTITION OF ohlcv FOR VALUES IN ('15m');
CREATE TABLE ohlcv_30min   PARTITION OF ohlcv FOR VALUES IN ('30m');
CREATE TABLE ohlcv_weekly  PARTITION OF ohlcv FOR VALUES IN ('1W');
CREATE TABLE ohlcv_2week   PARTITION OF ohlcv FOR VALUES IN ('2W');
CREATE TABLE ohlcv_monthly PARTITION OF ohlcv FOR VALUES IN ('1M');

-- Sub-partition minute by year
CREATE TABLE ohlcv_minute_2024 PARTITION OF ohlcv_minute FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ohlcv_minute_2025 PARTITION OF ohlcv_minute FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ohlcv_minute_2026 PARTITION OF ohlcv_minute FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

-- Sub-partition hourly by year
CREATE TABLE ohlcv_hourly_2024 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ohlcv_hourly_2025 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ohlcv_hourly_2026 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

-- Autovacuum tuning for high-write minute partitions
ALTER TABLE ohlcv_minute_2026 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02,
    autovacuum_vacuum_cost_delay = 10
);

-- ── ohlcv_indicators partitions ──

-- Raw intervals
CREATE TABLE indicators_minute  PARTITION OF ohlcv_indicators FOR VALUES IN ('1m') PARTITION BY RANGE (time);
CREATE TABLE indicators_hourly  PARTITION OF ohlcv_indicators FOR VALUES IN ('1h') PARTITION BY RANGE (time);
CREATE TABLE indicators_daily   PARTITION OF ohlcv_indicators FOR VALUES IN ('1D');

-- Aggregated intervals
CREATE TABLE indicators_5min    PARTITION OF ohlcv_indicators FOR VALUES IN ('5m');
CREATE TABLE indicators_15min   PARTITION OF ohlcv_indicators FOR VALUES IN ('15m');
CREATE TABLE indicators_30min   PARTITION OF ohlcv_indicators FOR VALUES IN ('30m');
CREATE TABLE indicators_weekly  PARTITION OF ohlcv_indicators FOR VALUES IN ('1W');
CREATE TABLE indicators_2week   PARTITION OF ohlcv_indicators FOR VALUES IN ('2W');
CREATE TABLE indicators_monthly PARTITION OF ohlcv_indicators FOR VALUES IN ('1M');

-- Sub-partition minute by year
CREATE TABLE indicators_minute_2024 PARTITION OF indicators_minute FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE indicators_minute_2025 PARTITION OF indicators_minute FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE indicators_minute_2026 PARTITION OF indicators_minute FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

-- Sub-partition hourly by year
CREATE TABLE indicators_hourly_2024 PARTITION OF indicators_hourly FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE indicators_hourly_2025 PARTITION OF indicators_hourly FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE indicators_hourly_2026 PARTITION OF indicators_hourly FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

-- Autovacuum tuning for high-write minute partitions
ALTER TABLE indicators_minute_2026 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02,
    autovacuum_vacuum_cost_delay = 10
);
