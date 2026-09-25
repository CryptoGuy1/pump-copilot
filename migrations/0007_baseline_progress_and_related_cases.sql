-- 0007: per-signal baseline progress on each replay session (for a "baseline forming" view),
-- and related_case_id: evidence that would have extended a closed case opens a new case that
-- points back to it.

ALTER TABLE replay_sessions ADD COLUMN baseline_progress jsonb;  -- replay.baseline_progress

ALTER TABLE case_events ADD COLUMN related_case_id bigint,
    ADD CONSTRAINT case_events_related_only_on_open
        CHECK (related_case_id IS NULL OR event_type = 'opened');

-- as in 0006, plus: a related case must exist and be closed
CREATE OR REPLACE FUNCTION case_events_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    o case_events%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(72616002, (NEW.case_id % 2147483647)::int);
    SELECT * INTO o FROM case_events WHERE case_id = NEW.case_id AND event_type = 'opened';
    IF NEW.event_type = 'opened' THEN
        IF FOUND OR EXISTS (SELECT 1 FROM case_events WHERE case_id = NEW.case_id) THEN
            RAISE EXCEPTION 'invalid case transition: case % is already opened', NEW.case_id;
        END IF;
        IF NEW.related_case_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM case_events
                WHERE case_id = NEW.related_case_id AND event_type = 'closed') THEN
            RAISE EXCEPTION 'invalid case transition: related case % is not a closed case',
                            NEW.related_case_id;
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

-- CREATE OR REPLACE VIEW may only add columns at the end
CREATE OR REPLACE VIEW case_state AS
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
       max(recorded_at)                                                 AS last_event_at,
       min(related_case_id) FILTER (WHERE event_type = 'opened')        AS related_case_id
FROM case_events
GROUP BY case_id;
