-- 0008: what the Step 4b API needs.
-- * stream_events: the live stream's event log. Ids are handed out under a transaction-level
--   advisory lock (events.emit), so they appear in commit order and a client that resumes from
--   the highest id it saw (Last-Event-ID) misses nothing.
-- * worker_heartbeats: one row per replay worker, for /health.
-- * scores.median / band_low / band_high: the window median and its band, so a case can show
--   the signal against its band without re-scoring.
-- * pumpcopilot_api: the role the API runs as. It can read everything and write only replay
--   control, case events and stream events: no endpoint can write telemetry or readings.

CREATE TABLE stream_events (
    event_id   bigserial   PRIMARY KEY,
    event_type text        NOT NULL CHECK (event_type IN
        ('replay.progress', 'score.batch', 'case.event')),
    payload    jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX stream_events_type ON stream_events (event_type, event_id);

CREATE TABLE worker_heartbeats (
    worker_id        text        PRIMARY KEY,
    host             text        NOT NULL,
    pid              int         NOT NULL,
    started_at       timestamptz NOT NULL,
    last_seen        timestamptz NOT NULL,
    status           text        NOT NULL CHECK (status IN ('running', 'stopped')),
    sessions_stepped bigint      NOT NULL DEFAULT 0,
    last_session_id  bigint
);

ALTER TABLE scores ADD COLUMN median double precision,
                   ADD COLUMN band_low double precision,
                   ADD COLUMN band_high double precision;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pumpcopilot_api') THEN
        CREATE ROLE pumpcopilot_api NOLOGIN;
    END IF;
END $$;
GRANT pumpcopilot_api TO CURRENT_USER;
GRANT USAGE ON SCHEMA public TO pumpcopilot_api;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO pumpcopilot_api;
GRANT INSERT ON case_events, stream_events, replay_sessions TO pumpcopilot_api;
GRANT UPDATE ON replay_sessions TO pumpcopilot_api;
GRANT USAGE ON SEQUENCE case_ids, case_events_event_id_seq, stream_events_event_id_seq,
    replay_sessions_session_id_seq TO pumpcopilot_api;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON telemetry, readings, state_segments, ingest_runs,
    scores, worker_heartbeats, schema_migrations FROM pumpcopilot_api;
