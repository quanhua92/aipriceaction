-- no-transaction
-- CALL must be top-level so the procedure can commit after each ticker.
CALL reconcile_ohlcv_health_counts();
