-- Drop DEFAULT 'ready' from tickers.status so new crypto tickers
-- start as NULL (full-download-requested is set explicitly by sync_crypto_tickers).
ALTER TABLE tickers ALTER COLUMN status DROP DEFAULT;
ALTER TABLE tickers ALTER COLUMN status DROP NOT NULL;
