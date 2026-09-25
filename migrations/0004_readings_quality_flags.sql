-- 0004: readings carry the quality flags of the telemetry row they start on, so features can
-- exclude stale- and spike-flagged readings without joining back to telemetry.
ALTER TABLE readings ADD COLUMN quality_flags text[] NOT NULL DEFAULT '{}';

UPDATE readings r
SET quality_flags = t.quality_flags
FROM telemetry t
WHERE t.observed_at = r.observed_at AND t.provenance_hash = r.provenance_hash;
