ALTER TABLE tickers
    ADD COLUMN next_1d TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN next_1h TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN next_1m TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX ix_tickers_next_1d ON tickers (next_1d) WHERE source = 'vn' AND status = 'ready';
CREATE INDEX ix_tickers_next_1h ON tickers (next_1h) WHERE source = 'vn' AND status = 'ready';
CREATE INDEX ix_tickers_next_1m ON tickers (next_1m) WHERE source = 'vn' AND status = 'ready';
