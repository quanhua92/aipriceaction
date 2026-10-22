-- Keep historical intraday recovery independent of ticker readiness.
-- Normal hourly/minute workers maintain the recent tail while these jobs
-- rebuild older, dividend-adjusted history.
CREATE TABLE vci_backfill_jobs (
    ticker_id     INT NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
    interval      TEXT NOT NULL CHECK (interval IN ('1h', '1m')),
    before_time   TIMESTAMPTZ,
    retry_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempts      INT NOT NULL DEFAULT 0,
    completed_at  TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker_id, interval)
);

CREATE INDEX ix_vci_backfill_jobs_pending
    ON vci_backfill_jobs (retry_at, updated_at)
    WHERE completed_at IS NULL;
