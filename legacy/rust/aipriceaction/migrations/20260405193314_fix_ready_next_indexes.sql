-- Drop old VN-specific partial indexes
DROP INDEX IF EXISTS ix_tickers_next_1d;
DROP INDEX IF EXISTS ix_tickers_next_1h;
DROP INDEX IF EXISTS ix_tickers_next_1m;

-- Create composite partial indexes matching the get_due_tickers query shape:
-- WHERE source = $1 AND status = 'ready' AND next_* < NOW()
-- Leading on source so the planner can narrow by source first, then use
-- the index-ordered next_* column for the inequality filter and ORDER BY.
CREATE INDEX IF NOT EXISTS ix_tickers_source_next_1d_ready ON tickers (source, next_1d) WHERE status = 'ready';
CREATE INDEX IF NOT EXISTS ix_tickers_source_next_1h_ready ON tickers (source, next_1h) WHERE status = 'ready';
CREATE INDEX IF NOT EXISTS ix_tickers_source_next_1m_ready ON tickers (source, next_1m) WHERE status = 'ready';
