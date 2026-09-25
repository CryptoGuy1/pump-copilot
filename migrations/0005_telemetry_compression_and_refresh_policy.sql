-- 0005: compress telemetry and keep telemetry_1m refreshed by policy.
-- Queries are per asset, day and signal (db.QUERY_FUNCTIONS), so those segment the compressed
-- data and time orders it. The unique (observed_at, provenance_hash) constraint stays: loading
-- the same file again still inserts nothing, into compressed chunks too.
ALTER TABLE telemetry SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'asset_id, signal_name',
    timescaledb.compress_orderby = 'observed_at'
);

-- the chunks loaded so far
SELECT count(compress_chunk(c, if_not_compressed => true)) FROM show_chunks('telemetry') c;

-- later loads: CIRA days are historical, so every new chunk is compressed on the next run
SELECT add_compression_policy('telemetry', compress_after => INTERVAL '7 days',
                              if_not_exists => true);

-- `db load` still refreshes the loaded range explicitly; the policy is a backstop for anything
-- written outside it. Refresh only processes invalidated ranges, so the full range is cheap.
SELECT add_continuous_aggregate_policy('telemetry_1m', start_offset => NULL,
                                       end_offset => INTERVAL '1 minute',
                                       schedule_interval => INTERVAL '1 hour',
                                       if_not_exists => true);
