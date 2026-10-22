-- Keep the health endpoint's exact candle totals cheap to read. Store one
-- counter per ticker and interval so writes and the online backfill serialize
-- narrowly.
CREATE TABLE ohlcv_health_counts (
    ticker_id INT NOT NULL REFERENCES tickers(id),
    interval TEXT NOT NULL,
    row_count BIGINT NOT NULL CHECK (row_count >= 0),
    PRIMARY KEY (ticker_id, interval)
);

-- Existing identities must not apply deltas until their baseline is complete.
-- A ticker created after trigger installation is safe to initialize at zero.
CREATE TABLE ohlcv_health_count_state (
    ticker_id INT PRIMARY KEY REFERENCES tickers(id),
    reconciled BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE FUNCTION maintain_ohlcv_health_counts() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM pg_advisory_xact_lock(191897, NEW.ticker_id);
        INSERT INTO ohlcv_health_count_state (ticker_id, reconciled)
        VALUES (NEW.ticker_id, TRUE)
        ON CONFLICT (ticker_id) DO NOTHING;
        IF (SELECT reconciled FROM ohlcv_health_count_state
            WHERE ticker_id = NEW.ticker_id) THEN
            INSERT INTO ohlcv_health_counts (ticker_id, interval, row_count)
            VALUES (NEW.ticker_id, NEW.interval, 1)
            ON CONFLICT (ticker_id, interval)
            DO UPDATE SET row_count = ohlcv_health_counts.row_count + 1;
        END IF;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        PERFORM pg_advisory_xact_lock(191897, OLD.ticker_id);
        IF COALESCE((SELECT reconciled FROM ohlcv_health_count_state
                     WHERE ticker_id = OLD.ticker_id), FALSE) THEN
            UPDATE ohlcv_health_counts
            SET row_count = row_count - 1
            WHERE ticker_id = OLD.ticker_id AND interval = OLD.interval;
        END IF;
        RETURN OLD;
    ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.ticker_id <> NEW.ticker_id THEN
            -- Lock in ID order so concurrent ticker moves cannot deadlock.
            IF OLD.ticker_id < NEW.ticker_id THEN
                PERFORM pg_advisory_xact_lock(191897, OLD.ticker_id);
                PERFORM pg_advisory_xact_lock(191897, NEW.ticker_id);
            ELSE
                PERFORM pg_advisory_xact_lock(191897, NEW.ticker_id);
                PERFORM pg_advisory_xact_lock(191897, OLD.ticker_id);
            END IF;
        ELSE
            PERFORM pg_advisory_xact_lock(191897, NEW.ticker_id);
        END IF;

        INSERT INTO ohlcv_health_count_state (ticker_id, reconciled)
        VALUES (NEW.ticker_id, TRUE)
        ON CONFLICT (ticker_id) DO NOTHING;
        IF OLD.interval IS DISTINCT FROM NEW.interval OR OLD.ticker_id <> NEW.ticker_id THEN
            IF COALESCE((SELECT reconciled FROM ohlcv_health_count_state
                         WHERE ticker_id = OLD.ticker_id), FALSE) THEN
                UPDATE ohlcv_health_counts
                SET row_count = row_count - 1
                WHERE ticker_id = OLD.ticker_id AND interval = OLD.interval;
            END IF;
            IF (SELECT reconciled FROM ohlcv_health_count_state
                WHERE ticker_id = NEW.ticker_id) THEN
                INSERT INTO ohlcv_health_counts (ticker_id, interval, row_count)
                VALUES (NEW.ticker_id, NEW.interval, 1)
                ON CONFLICT (ticker_id, interval)
                DO UPDATE SET row_count = ohlcv_health_counts.row_count + 1;
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- The trigger acquires a brief parent-table write lock while it is installed.
-- Seed this small catalog state under that lock, then commit before the later
-- ticker-by-ticker count reconciliation begins.
CREATE TRIGGER ohlcv_health_count_changes
AFTER INSERT OR UPDATE OR DELETE ON ohlcv
FOR EACH ROW EXECUTE FUNCTION maintain_ohlcv_health_counts();

INSERT INTO ohlcv_health_count_state (ticker_id, reconciled)
SELECT id, FALSE FROM tickers
ON CONFLICT (ticker_id) DO NOTHING;
