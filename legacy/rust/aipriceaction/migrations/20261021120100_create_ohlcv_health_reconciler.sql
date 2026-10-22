-- The reconciler commits after each ticker. That keeps the advisory lock and
-- counter-row locks local to a single indexed ticker range while allowing
-- concurrent ingestion for all other tickers to continue.
CREATE PROCEDURE reconcile_ohlcv_health_counts()
LANGUAGE plpgsql
AS $$
DECLARE
    current_ticker_id INT;
BEGIN
    FOR current_ticker_id IN
        SELECT ticker_id FROM ohlcv_health_count_state ORDER BY ticker_id
    LOOP
        PERFORM pg_advisory_lock(191897, current_ticker_id);
        BEGIN
            UPDATE ohlcv_health_counts
            SET row_count = 0
            WHERE ticker_id = current_ticker_id;

            INSERT INTO ohlcv_health_counts (ticker_id, interval, row_count)
            SELECT current_ticker_id, interval, COUNT(*)::bigint
            FROM ohlcv
            WHERE ticker_id = current_ticker_id
            GROUP BY interval
            ON CONFLICT (ticker_id, interval)
            DO UPDATE SET row_count = EXCLUDED.row_count;

            UPDATE ohlcv_health_count_state
            SET reconciled = TRUE
            WHERE ticker_id = current_ticker_id;
        EXCEPTION WHEN OTHERS THEN
            PERFORM pg_advisory_unlock(191897, current_ticker_id);
            RAISE;
        END;
        -- Keep the session lock until the baseline and reconciled flag are
        -- committed. A writer blocked in its AFTER trigger must observe the
        -- committed flag before it applies its delta.
        COMMIT;
        PERFORM pg_advisory_unlock(191897, current_ticker_id);
    END LOOP;
END;
$$;
