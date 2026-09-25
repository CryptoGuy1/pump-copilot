-- 0001: canonical 1 Hz telemetry and value-change readings (A5), both hypertables.
-- CIRA days are separate recordings: source_day is part of every query key, and nothing
-- joins across days by time.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE telemetry (
    observed_at     timestamptz      NOT NULL,
    asset_id        text             NOT NULL,
    source_day      date             NOT NULL,
    source_file     text             NOT NULL,
    sample_id       text             NOT NULL,  -- <file stem>:<row number in the file>
    signal_name     text             NOT NULL,
    value           double precision,           -- NULL only together with the 'missing' flag
    unit            text             NOT NULL,
    operating_state text             NOT NULL
        CHECK (operating_state IN ('running', 'off', 'transition', 'unknown')),
    quality_flags   text[]           NOT NULL DEFAULT '{}',
    is_reading      boolean          NOT NULL,  -- a new sensor reading starts on this row (A5)
    provenance_hash text             NOT NULL,
    ingest_run_id   bigint,
    CONSTRAINT telemetry_idempotency UNIQUE (observed_at, provenance_hash)
);
SELECT create_hypertable('telemetry', by_range('observed_at', INTERVAL '1 day'));
CREATE INDEX telemetry_asset_day ON telemetry (asset_id, source_day, signal_name, observed_at);

-- One row per reading event (value change); the 1 Hz repeats in between are not readings.
CREATE TABLE readings (
    observed_at     timestamptz      NOT NULL,
    asset_id        text             NOT NULL,
    source_day      date             NOT NULL,
    signal_name     text             NOT NULL,
    value           double precision NOT NULL,
    unit            text             NOT NULL,
    held_s          double precision,           -- seconds until the next reading; NULL for the last
    operating_state text             NOT NULL,
    provenance_hash text             NOT NULL,  -- of the telemetry row the reading starts on
    ingest_run_id   bigint,
    CONSTRAINT readings_idempotency UNIQUE (observed_at, provenance_hash)
);
SELECT create_hypertable('readings', by_range('observed_at', INTERVAL '1 day'));
CREATE INDEX readings_asset_day ON readings (asset_id, source_day, signal_name, observed_at);
