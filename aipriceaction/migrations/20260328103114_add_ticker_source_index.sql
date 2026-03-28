-- Speed up COUNT queries that filter by source (e.g. status command).
-- The existing UNIQUE(source, ticker) index cannot be used alone
-- because the query only filters on source.
CREATE INDEX idx_tickers_source ON tickers (source);
