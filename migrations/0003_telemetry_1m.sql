-- 0003: dashboard view, one row per asset, day, signal and minute.
-- Materialized only; `pumpcopilot db load` refreshes the loaded range explicitly.
CREATE MATERIALIZED VIEW telemetry_1m
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT time_bucket(INTERVAL '1 minute', observed_at)   AS bucket,
       asset_id,
       source_day,
       signal_name,
       min(value)                                      AS min_value,
       max(value)                                      AS max_value,
       avg(value)                                      AS mean_value,
       count(value)                                    AS sample_count,
       sum(CASE WHEN is_reading THEN 1 ELSE 0 END)     AS reading_count
FROM telemetry
GROUP BY bucket, asset_id, source_day, signal_name
WITH NO DATA;
