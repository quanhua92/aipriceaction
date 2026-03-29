-- Tickers lookup table (~380 rows, no partitioning)
CREATE TABLE tickers (
    id     SERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name   TEXT,
    status TEXT,
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

-- ── ohlcv interval partitions (only stored intervals: 1m, 1h, 1D) ──

CREATE TABLE ohlcv_minute  PARTITION OF ohlcv FOR VALUES IN ('1m') PARTITION BY RANGE (time);
CREATE TABLE ohlcv_hourly  PARTITION OF ohlcv FOR VALUES IN ('1h') PARTITION BY RANGE (time);
CREATE TABLE ohlcv_daily   PARTITION OF ohlcv FOR VALUES IN ('1D');

