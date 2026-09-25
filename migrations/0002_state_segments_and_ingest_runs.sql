-- 0002: derived operating-state segments, and one row per file per load run.

CREATE TABLE state_segments (
    asset_id                  text        NOT NULL,
    source_day                date        NOT NULL,
    state                     text        NOT NULL
        CHECK (state IN ('running', 'off', 'transition')),
    start_at                  timestamptz NOT NULL,
    end_at                    timestamptz NOT NULL,
    motor_unconfirmed         boolean,  -- running segments only: an attribute, not a flag
    pressurized_while_stopped boolean,  -- off segments only
    rules_version             text        NOT NULL,  -- sha256 of data/operating_rules.yaml
    ingest_run_id             bigint,
    PRIMARY KEY (asset_id, source_day, rules_version, start_at),
    CHECK (start_at <= end_at),
    CHECK ((state = 'running') = (motor_unconfirmed IS NOT NULL)),
    CHECK ((state = 'off') = (pressurized_while_stopped IS NOT NULL))
);

CREATE TABLE ingest_runs (
    run_id             bigserial   PRIMARY KEY,
    source             text        NOT NULL,
    source_file        text        NOT NULL,
    source_file_sha256 text        NOT NULL,
    rules_sha256       text        NOT NULL,
    column_map_sha256  text        NOT NULL,
    started_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz,
    rows_staged        bigint,
    telemetry_inserted bigint,
    telemetry_skipped  bigint,
    readings_inserted  bigint,
    readings_skipped   bigint,
    segments_inserted  bigint,
    segments_skipped   bigint
);
