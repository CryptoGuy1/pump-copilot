-- Step 7a: worker heartbeats carry no host name.
--
-- A replay worker is now identified by a short random id (worker-3f9a) instead of
-- host:pid, and the host column is dropped, so /health never shows the machine's name.
-- Heartbeats are liveness status only, so the rows written under the old host:pid ids are
-- removed rather than renamed; a running worker writes a fresh row within a second.
DELETE FROM worker_heartbeats WHERE worker_id LIKE '%:%';
ALTER TABLE worker_heartbeats DROP COLUMN host;
