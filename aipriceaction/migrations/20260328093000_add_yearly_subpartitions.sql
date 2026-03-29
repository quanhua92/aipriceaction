-- Pre-create all yearly sub-partitions (2010-2050) for minute and hourly data.
-- Future-proofs the schema so inserts never fail with "no partition found for row".

-- ── ohlcv hourly sub-partitions ──

CREATE TABLE ohlcv_hourly_2010 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2010-01-01') TO ('2011-01-01');
CREATE TABLE ohlcv_hourly_2011 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2011-01-01') TO ('2012-01-01');
CREATE TABLE ohlcv_hourly_2012 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2012-01-01') TO ('2013-01-01');
CREATE TABLE ohlcv_hourly_2013 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2013-01-01') TO ('2014-01-01');
CREATE TABLE ohlcv_hourly_2014 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2014-01-01') TO ('2015-01-01');
CREATE TABLE ohlcv_hourly_2015 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2015-01-01') TO ('2016-01-01');
CREATE TABLE ohlcv_hourly_2016 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2016-01-01') TO ('2017-01-01');
CREATE TABLE ohlcv_hourly_2017 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2017-01-01') TO ('2018-01-01');
CREATE TABLE ohlcv_hourly_2018 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2018-01-01') TO ('2019-01-01');
CREATE TABLE ohlcv_hourly_2019 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2019-01-01') TO ('2020-01-01');
CREATE TABLE ohlcv_hourly_2020 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE ohlcv_hourly_2021 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE ohlcv_hourly_2022 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE ohlcv_hourly_2023 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE ohlcv_hourly_2024 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ohlcv_hourly_2025 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ohlcv_hourly_2026 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE ohlcv_hourly_2027 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE ohlcv_hourly_2028 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2028-01-01') TO ('2029-01-01');
CREATE TABLE ohlcv_hourly_2029 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2029-01-01') TO ('2030-01-01');
CREATE TABLE ohlcv_hourly_2030 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2030-01-01') TO ('2031-01-01');
CREATE TABLE ohlcv_hourly_2031 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2031-01-01') TO ('2032-01-01');
CREATE TABLE ohlcv_hourly_2032 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2032-01-01') TO ('2033-01-01');
CREATE TABLE ohlcv_hourly_2033 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2033-01-01') TO ('2034-01-01');
CREATE TABLE ohlcv_hourly_2034 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2034-01-01') TO ('2035-01-01');
CREATE TABLE ohlcv_hourly_2035 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2035-01-01') TO ('2036-01-01');
CREATE TABLE ohlcv_hourly_2036 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2036-01-01') TO ('2037-01-01');
CREATE TABLE ohlcv_hourly_2037 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2037-01-01') TO ('2038-01-01');
CREATE TABLE ohlcv_hourly_2038 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2038-01-01') TO ('2039-01-01');
CREATE TABLE ohlcv_hourly_2039 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2039-01-01') TO ('2040-01-01');
CREATE TABLE ohlcv_hourly_2040 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2040-01-01') TO ('2041-01-01');
CREATE TABLE ohlcv_hourly_2041 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2041-01-01') TO ('2042-01-01');
CREATE TABLE ohlcv_hourly_2042 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2042-01-01') TO ('2043-01-01');
CREATE TABLE ohlcv_hourly_2043 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2043-01-01') TO ('2044-01-01');
CREATE TABLE ohlcv_hourly_2044 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2044-01-01') TO ('2045-01-01');
CREATE TABLE ohlcv_hourly_2045 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2045-01-01') TO ('2046-01-01');
CREATE TABLE ohlcv_hourly_2046 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2046-01-01') TO ('2047-01-01');
CREATE TABLE ohlcv_hourly_2047 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2047-01-01') TO ('2048-01-01');
CREATE TABLE ohlcv_hourly_2048 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2048-01-01') TO ('2049-01-01');
CREATE TABLE ohlcv_hourly_2049 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2049-01-01') TO ('2050-01-01');
CREATE TABLE ohlcv_hourly_2050 PARTITION OF ohlcv_hourly FOR VALUES FROM ('2050-01-01') TO ('2051-01-01');

-- ── ohlcv minute sub-partitions ──

CREATE TABLE ohlcv_minute_2010 PARTITION OF ohlcv_minute FOR VALUES FROM ('2010-01-01') TO ('2011-01-01');
CREATE TABLE ohlcv_minute_2011 PARTITION OF ohlcv_minute FOR VALUES FROM ('2011-01-01') TO ('2012-01-01');
CREATE TABLE ohlcv_minute_2012 PARTITION OF ohlcv_minute FOR VALUES FROM ('2012-01-01') TO ('2013-01-01');
CREATE TABLE ohlcv_minute_2013 PARTITION OF ohlcv_minute FOR VALUES FROM ('2013-01-01') TO ('2014-01-01');
CREATE TABLE ohlcv_minute_2014 PARTITION OF ohlcv_minute FOR VALUES FROM ('2014-01-01') TO ('2015-01-01');
CREATE TABLE ohlcv_minute_2015 PARTITION OF ohlcv_minute FOR VALUES FROM ('2015-01-01') TO ('2016-01-01');
CREATE TABLE ohlcv_minute_2016 PARTITION OF ohlcv_minute FOR VALUES FROM ('2016-01-01') TO ('2017-01-01');
CREATE TABLE ohlcv_minute_2017 PARTITION OF ohlcv_minute FOR VALUES FROM ('2017-01-01') TO ('2018-01-01');
CREATE TABLE ohlcv_minute_2018 PARTITION OF ohlcv_minute FOR VALUES FROM ('2018-01-01') TO ('2019-01-01');
CREATE TABLE ohlcv_minute_2019 PARTITION OF ohlcv_minute FOR VALUES FROM ('2019-01-01') TO ('2020-01-01');
CREATE TABLE ohlcv_minute_2020 PARTITION OF ohlcv_minute FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE ohlcv_minute_2021 PARTITION OF ohlcv_minute FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE ohlcv_minute_2022 PARTITION OF ohlcv_minute FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE ohlcv_minute_2023 PARTITION OF ohlcv_minute FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE ohlcv_minute_2024 PARTITION OF ohlcv_minute FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ohlcv_minute_2025 PARTITION OF ohlcv_minute FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ohlcv_minute_2026 PARTITION OF ohlcv_minute FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE ohlcv_minute_2027 PARTITION OF ohlcv_minute FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE ohlcv_minute_2028 PARTITION OF ohlcv_minute FOR VALUES FROM ('2028-01-01') TO ('2029-01-01');
CREATE TABLE ohlcv_minute_2029 PARTITION OF ohlcv_minute FOR VALUES FROM ('2029-01-01') TO ('2030-01-01');
CREATE TABLE ohlcv_minute_2030 PARTITION OF ohlcv_minute FOR VALUES FROM ('2030-01-01') TO ('2031-01-01');
CREATE TABLE ohlcv_minute_2031 PARTITION OF ohlcv_minute FOR VALUES FROM ('2031-01-01') TO ('2032-01-01');
CREATE TABLE ohlcv_minute_2032 PARTITION OF ohlcv_minute FOR VALUES FROM ('2032-01-01') TO ('2033-01-01');
CREATE TABLE ohlcv_minute_2033 PARTITION OF ohlcv_minute FOR VALUES FROM ('2033-01-01') TO ('2034-01-01');
CREATE TABLE ohlcv_minute_2034 PARTITION OF ohlcv_minute FOR VALUES FROM ('2034-01-01') TO ('2035-01-01');
CREATE TABLE ohlcv_minute_2035 PARTITION OF ohlcv_minute FOR VALUES FROM ('2035-01-01') TO ('2036-01-01');
CREATE TABLE ohlcv_minute_2036 PARTITION OF ohlcv_minute FOR VALUES FROM ('2036-01-01') TO ('2037-01-01');
CREATE TABLE ohlcv_minute_2037 PARTITION OF ohlcv_minute FOR VALUES FROM ('2037-01-01') TO ('2038-01-01');
CREATE TABLE ohlcv_minute_2038 PARTITION OF ohlcv_minute FOR VALUES FROM ('2038-01-01') TO ('2039-01-01');
CREATE TABLE ohlcv_minute_2039 PARTITION OF ohlcv_minute FOR VALUES FROM ('2039-01-01') TO ('2040-01-01');
CREATE TABLE ohlcv_minute_2040 PARTITION OF ohlcv_minute FOR VALUES FROM ('2040-01-01') TO ('2041-01-01');
CREATE TABLE ohlcv_minute_2041 PARTITION OF ohlcv_minute FOR VALUES FROM ('2041-01-01') TO ('2042-01-01');
CREATE TABLE ohlcv_minute_2042 PARTITION OF ohlcv_minute FOR VALUES FROM ('2042-01-01') TO ('2043-01-01');
CREATE TABLE ohlcv_minute_2043 PARTITION OF ohlcv_minute FOR VALUES FROM ('2043-01-01') TO ('2044-01-01');
CREATE TABLE ohlcv_minute_2044 PARTITION OF ohlcv_minute FOR VALUES FROM ('2044-01-01') TO ('2045-01-01');
CREATE TABLE ohlcv_minute_2045 PARTITION OF ohlcv_minute FOR VALUES FROM ('2045-01-01') TO ('2046-01-01');
CREATE TABLE ohlcv_minute_2046 PARTITION OF ohlcv_minute FOR VALUES FROM ('2046-01-01') TO ('2047-01-01');
CREATE TABLE ohlcv_minute_2047 PARTITION OF ohlcv_minute FOR VALUES FROM ('2047-01-01') TO ('2048-01-01');
CREATE TABLE ohlcv_minute_2048 PARTITION OF ohlcv_minute FOR VALUES FROM ('2048-01-01') TO ('2049-01-01');
CREATE TABLE ohlcv_minute_2049 PARTITION OF ohlcv_minute FOR VALUES FROM ('2049-01-01') TO ('2050-01-01');
CREATE TABLE ohlcv_minute_2050 PARTITION OF ohlcv_minute FOR VALUES FROM ('2050-01-01') TO ('2051-01-01');

-- Autovacuum tuning for high-write minute partitions
ALTER TABLE ohlcv_minute_2023 SET (autovacuum_vacuum_scale_factor = 0.05, autovacuum_analyze_scale_factor = 0.02, autovacuum_vacuum_cost_delay = 10);
ALTER TABLE ohlcv_minute_2024 SET (autovacuum_vacuum_scale_factor = 0.05, autovacuum_analyze_scale_factor = 0.02, autovacuum_vacuum_cost_delay = 10);
ALTER TABLE ohlcv_minute_2025 SET (autovacuum_vacuum_scale_factor = 0.05, autovacuum_analyze_scale_factor = 0.02, autovacuum_vacuum_cost_delay = 10);
ALTER TABLE ohlcv_minute_2026 SET (autovacuum_vacuum_scale_factor = 0.05, autovacuum_analyze_scale_factor = 0.02, autovacuum_vacuum_cost_delay = 10);
