-- Step 5b: reads as of the session cursor.
--
-- case_state_visible is case_state as the replay's cursor has reached it: a case appears only
-- once the cursor reaches its first evidence window (window_end <= cursor_at); its evidence
-- is what lies at or before the cursor; its human events (acknowledgements, notes,
-- dispositions, closing) stay with it, whole. A rewound (pending) session shows no cases.
-- Nothing is deleted: rows beyond the cursor reappear as the replay passes them.
CREATE OR REPLACE VIEW case_state_visible AS
WITH ev AS (
    SELECT e.*, s.cursor_at AS session_cursor
    FROM case_events e JOIN replay_sessions s USING (session_id)
    WHERE e.event_type <> 'evidence_added' OR e.window_end <= s.cursor_at
)
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
       min(related_case_id) FILTER (WHERE event_type = 'opened')        AS related_case_id,
       min(session_cursor)                                              AS as_of
FROM ev
GROUP BY case_id
HAVING bool_or(event_type = 'evidence_added');

GRANT SELECT ON case_state_visible TO pumpcopilot_api;
