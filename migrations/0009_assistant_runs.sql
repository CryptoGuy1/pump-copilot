-- 0009: the copilot assistant's run log (Step 6A). Append-only: one row per question, with
-- what was asked (as a hash), which provider and model answered, the context hash, the raw
-- output, the check result and why an answer was rejected, and how long it took.

CREATE TABLE assistant_runs (
    run_id            bigserial   PRIMARY KEY,
    case_id           bigint      NOT NULL,
    question_sha256   text        NOT NULL,
    provider          text        NOT NULL,   -- the provider asked (template, fake, anthropic)
    model             text,
    served            text        NOT NULL CHECK (served IN ('assistant', 'template')),
    context_sha256    text        NOT NULL,
    raw_output        jsonb,                  -- what the provider returned, if anything
    check_passed      boolean     NOT NULL,   -- did the provider's answer pass the checker
    rejection_reasons text[]      NOT NULL DEFAULT '{}',
    fallback_reason   text,
    latency_ms        double precision NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX assistant_runs_case ON assistant_runs (case_id, run_id);

CREATE FUNCTION assistant_runs_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'assistant_runs is append-only: % is not allowed', TG_OP;
END $$;
CREATE TRIGGER assistant_runs_no_update BEFORE UPDATE OR DELETE ON assistant_runs
    FOR EACH ROW EXECUTE FUNCTION assistant_runs_append_only();
CREATE TRIGGER assistant_runs_no_truncate BEFORE TRUNCATE ON assistant_runs
    FOR EACH STATEMENT EXECUTE FUNCTION assistant_runs_append_only();

GRANT SELECT, INSERT ON assistant_runs TO pumpcopilot_api;
GRANT USAGE ON SEQUENCE assistant_runs_run_id_seq TO pumpcopilot_api;
