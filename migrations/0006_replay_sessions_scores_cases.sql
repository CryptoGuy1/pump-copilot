-- 0006: replay sessions, persisted scores, append-only case events and the derived case state.
-- Replay copies no telemetry: a session moves a source-time cursor through a stored day.

CREATE TABLE replay_sessions (
    session_id    bigserial   PRIMARY KEY,
    asset_id      text        NOT NULL,
    source_day    date        NOT NULL,
    speed         int         NOT NULL CHECK (speed IN (1, 10, 60)),
    status        text        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'paused', 'completed', 'failed')),
    cursor_at     timestamptz,           -- source time; everything at or before it is scored
    source_start  timestamptz NOT NULL,  -- first and last telemetry of the stored day
    source_end    timestamptz NOT NULL,
    scenario      text,                  -- synthetic scenario, applied in memory only
    synthetic     boolean GENERATED ALWAYS AS (scenario IS NOT NULL) STORED,
    config_name   text        NOT NULL,
    config        jsonb       NOT NULL,  -- the frozen scoring config the session scores with
    config_sha256 text        NOT NULL,
    day_constants jsonb       NOT NULL,  -- declared per-day inputs (see replay.day_constants)
    -- pacing: cursor = anchor_cursor + speed x (wall time - anchor_wall)
    anchor_cursor timestamptz,
    anchor_wall   timestamptz,
    cases_through timestamptz,           -- review windows starting at or before are in cases
    claimed_by    text,
    heartbeat_at  timestamptz,
    error         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (source_start < source_end),
    CHECK (cursor_at IS NULL OR cursor_at BETWEEN source_start AND source_end),
    UNIQUE (session_id, synthetic)       -- target of the synthetic-carrying foreign keys
);
CREATE INDEX replay_sessions_due ON replay_sessions (status) WHERE status IN ('pending', 'running');

-- One persisted ScoredEvidence per session, signal and window. `synthetic` must equal the
-- session's: the composite foreign key makes a real score for a synthetic session impossible.
CREATE TABLE scores (
    session_id      bigint           NOT NULL,
    synthetic       boolean          NOT NULL,
    asset_id        text             NOT NULL,
    source_day      date             NOT NULL,
    signal_name     text             NOT NULL,
    stretch         int              NOT NULL,  -- running stretch (run) of the day
    window_start    timestamptz      NOT NULL,
    window_end      timestamptz      NOT NULL,
    model_id        text             NOT NULL,
    model_version   text             NOT NULL,
    presentation_state text          NOT NULL CHECK (presentation_state IN
        ('normal', 'review_suggested', 'insufficient_evidence', 'data_unavailable')),
    score           double precision,
    abstention_reason text,
    confidence_calibration_status text NOT NULL,
    scored_evidence jsonb            NOT NULL,  -- the full ScoredEvidence
    cursor_at       timestamptz      NOT NULL,  -- replay cursor (source time) when stored
    stored_at       timestamptz      NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (session_id, synthetic) REFERENCES replay_sessions (session_id, synthetic),
    CONSTRAINT scores_idempotency
        UNIQUE (session_id, asset_id, signal_name, window_end, model_version),
    CHECK (window_start < window_end)
);
CREATE INDEX scores_session_state ON scores (session_id, presentation_state, window_start);

-- Cases are an append-only event log; nothing is updated or deleted.
CREATE SEQUENCE case_ids;

CREATE TABLE case_events (
    event_id      bigserial   PRIMARY KEY,
    case_id       bigint      NOT NULL,
    event_type    text        NOT NULL CHECK (event_type IN
        ('opened', 'evidence_added', 'acknowledged', 'note', 'disposition', 'closed')),
    session_id    bigint      NOT NULL,
    synthetic     boolean     NOT NULL,
    asset_id      text        NOT NULL,
    source_day    date        NOT NULL,
    stretch       int         NOT NULL,
    actor         text        NOT NULL,  -- 'worker' or the person acting
    recorded_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- evidence_added: the scored window this refers to
    signal_name   text,
    window_start  timestamptz,
    window_end    timestamptz,
    model_version text,
    score         double precision,
    episode_start boolean,               -- first window of a review episode (3a-3 merge rule)
    -- human events
    note          text,
    disposition   text CHECK (disposition IN ('monitor', 'known condition, no action',
        'data quality issue', 'escalate to reliability engineer (export only)')),
    reason        text,
    FOREIGN KEY (session_id, synthetic) REFERENCES replay_sessions (session_id, synthetic),
    FOREIGN KEY (session_id, asset_id, signal_name, window_end, model_version)
        REFERENCES scores (session_id, asset_id, signal_name, window_end, model_version),
    CHECK ((event_type = 'evidence_added') = (window_end IS NOT NULL AND signal_name IS NOT NULL
        AND model_version IS NOT NULL AND episode_start IS NOT NULL)),
    CHECK ((event_type = 'disposition') = (disposition IS NOT NULL)),
    CHECK (event_type <> 'disposition' OR length(btrim(coalesce(reason, ''))) > 0),
    CHECK ((event_type = 'note') = (note IS NOT NULL))
);
CREATE INDEX case_events_case ON case_events (case_id, event_id);
CREATE INDEX case_events_session ON case_events (session_id, case_id);
-- a scored window is evidence at most once per session (re-running a session adds nothing)
CREATE UNIQUE INDEX case_events_evidence_once ON case_events
    (session_id, asset_id, signal_name, window_end, model_version)
    WHERE event_type = 'evidence_added';

CREATE FUNCTION case_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'case_events is append-only: % is not allowed', TG_OP;
END $$;
CREATE TRIGGER case_events_no_update BEFORE UPDATE OR DELETE ON case_events
    FOR EACH ROW EXECUTE FUNCTION case_events_append_only();
CREATE TRIGGER case_events_no_truncate BEFORE TRUNCATE ON case_events
    FOR EACH STATEMENT EXECUTE FUNCTION case_events_append_only();

-- Transitions: opened first and once; nothing after closed; acknowledged once; a disposition
-- needs an acknowledgement; closing needs a disposition. Messages start with
-- 'invalid case transition' so clients can tell them apart.
CREATE FUNCTION case_events_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    o case_events%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(72616002, (NEW.case_id % 2147483647)::int);
    SELECT * INTO o FROM case_events WHERE case_id = NEW.case_id AND event_type = 'opened';
    IF NEW.event_type = 'opened' THEN
        IF FOUND OR EXISTS (SELECT 1 FROM case_events WHERE case_id = NEW.case_id) THEN
            RAISE EXCEPTION 'invalid case transition: case % is already opened', NEW.case_id;
        END IF;
        RETURN NEW;
    END IF;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'invalid case transition: case % does not exist', NEW.case_id;
    END IF;
    IF (NEW.session_id, NEW.synthetic, NEW.asset_id, NEW.source_day, NEW.stretch)
       IS DISTINCT FROM (o.session_id, o.synthetic, o.asset_id, o.source_day, o.stretch) THEN
        RAISE EXCEPTION 'invalid case transition: event does not match case %''s session, '
                        'asset, day or run', NEW.case_id;
    END IF;
    IF EXISTS (SELECT 1 FROM case_events WHERE case_id = NEW.case_id
               AND event_type = 'closed') THEN
        RAISE EXCEPTION 'invalid case transition: case % is closed', NEW.case_id;
    END IF;
    IF NEW.event_type = 'acknowledged' AND EXISTS (SELECT 1 FROM case_events
            WHERE case_id = NEW.case_id AND event_type = 'acknowledged') THEN
        RAISE EXCEPTION 'invalid case transition: case % is already acknowledged', NEW.case_id;
    END IF;
    IF NEW.event_type = 'disposition' AND NOT EXISTS (SELECT 1 FROM case_events
            WHERE case_id = NEW.case_id AND event_type = 'acknowledged') THEN
        RAISE EXCEPTION 'invalid case transition: case % needs an acknowledgement before a '
                        'disposition', NEW.case_id;
    END IF;
    IF NEW.event_type = 'closed' AND NOT EXISTS (SELECT 1 FROM case_events
            WHERE case_id = NEW.case_id AND event_type = 'disposition') THEN
        RAISE EXCEPTION 'invalid case transition: case % needs a disposition before closing',
                        NEW.case_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER case_events_check_transition BEFORE INSERT ON case_events
    FOR EACH ROW EXECUTE FUNCTION case_events_transition();

-- Current case state, derived from the events only.
CREATE VIEW case_state AS
SELECT case_id,
       min(session_id)                                                  AS session_id,
       bool_or(synthetic)                                               AS synthetic,
       min(asset_id)                                                    AS asset_id,
       min(source_day)                                                  AS source_day,
       min(stretch)                                                     AS stretch,
       min(recorded_at) FILTER (WHERE event_type = 'opened')            AS opened_at,
       min(window_start) FILTER (WHERE event_type = 'evidence_added')   AS evidence_start,
       max(window_end) FILTER (WHERE event_type = 'evidence_added')     AS evidence_end,
       count(*) FILTER (WHERE event_type = 'evidence_added')            AS evidence_windows,
       count(*) FILTER (WHERE event_type = 'evidence_added' AND episode_start) AS episodes,
       array_agg(DISTINCT signal_name) FILTER (WHERE signal_name IS NOT NULL) AS signals,
       max(score) FILTER (WHERE event_type = 'evidence_added')          AS max_score,
       count(*) FILTER (WHERE event_type = 'note')                      AS notes,
       bool_or(event_type = 'acknowledged')                             AS acknowledged,
       (array_agg(disposition ORDER BY event_id DESC)
            FILTER (WHERE event_type = 'disposition'))[1]               AS disposition,
       (array_agg(reason ORDER BY event_id DESC)
            FILTER (WHERE event_type = 'disposition'))[1]               AS disposition_reason,
       bool_or(event_type = 'closed')                                   AS closed,
       CASE WHEN bool_or(event_type = 'closed') THEN 'closed'
            WHEN bool_or(event_type = 'disposition') THEN 'dispositioned'
            WHEN bool_or(event_type = 'acknowledged') THEN 'acknowledged'
            ELSE 'open' END                                             AS status,
       max(recorded_at)                                                 AS last_event_at
FROM case_events
GROUP BY case_id;
