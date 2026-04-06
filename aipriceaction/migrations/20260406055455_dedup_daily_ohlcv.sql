-- Deduplicate daily OHLCV rows caused by providers returning slightly
-- different timestamptz values for the same daily bar on each fetch.
--
-- For each (ticker_id, date) group, keeps only the most recently updated row,
-- preferring rows that already have a midnight UTC timestamp.

-- Delete rows that are NOT the latest-updated for their date
DELETE FROM ohlcv
WHERE interval = '1D'
  AND (ticker_id, interval, time) IN (
    SELECT ticker_id, interval, time
    FROM (
        SELECT ticker_id, interval, time,
               ROW_NUMBER() OVER (
                   PARTITION BY ticker_id, date_trunc('day', time)
                   ORDER BY
                     CASE WHEN time = date_trunc('day', time) THEN 0 ELSE 1 END,
                     updated_at DESC
               ) AS rn
        FROM ohlcv
        WHERE interval = '1D'
    ) ranked
    WHERE rn > 1
  );

-- Normalize remaining non-midnight daily timestamps to midnight UTC
UPDATE ohlcv
SET time = date_trunc('day', time)
WHERE interval = '1D' AND time != date_trunc('day', time);
