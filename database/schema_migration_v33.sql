-- ============================================================
-- schema_migration_v33.sql -- MIGO 103 waiting list, "X is on it" markers
-- and per-user drafts.
--
-- Two small NEW tables (nothing existing is altered):
--
--   step_drafts  one saved, half-filled form per (record, step, user).
--                Written by the browser while a user fills MIGO 103 and
--                restored when they come back to that invoice. Deleted
--                automatically when the step posts, or after 14 days.
--   step_claims  "this user has this invoice's MIGO 103 tab open" heartbeat
--                (refreshed every ~20s by the browser, treated as stale after
--                90s). Purely informational -- it never blocks anyone.
--
-- Plus one index on rf_queue(status, step): the waiting list asks "which
-- records have an active MIGO 103 job" every few seconds, and the worker's
-- own "next pending job" lookup benefits from it too.
--
-- RUN THIS ON THE DATABASE *BEFORE* DEPLOYING THE NEW APP FILES.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v33.sql
--
-- Safe to re-run: everything is IF NOT EXISTS. Existing rows are not touched.
-- ============================================================

CREATE TABLE IF NOT EXISTS step_drafts (
    history_id  INTEGER     NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    step        VARCHAR(20) NOT NULL,
    username    TEXT        NOT NULL,
    data        JSONB       NOT NULL,
    updated_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (history_id, step, username)
);
CREATE INDEX IF NOT EXISTS idx_step_drafts_updated_at ON step_drafts(updated_at);

CREATE TABLE IF NOT EXISTS step_claims (
    history_id    INTEGER     NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    step          VARCHAR(20) NOT NULL,
    username      TEXT        NOT NULL,
    heartbeat_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (history_id, step, username)
);
CREATE INDEX IF NOT EXISTS idx_step_claims_heartbeat ON step_claims(heartbeat_at);

CREATE INDEX IF NOT EXISTS idx_rf_queue_status_step ON rf_queue(status, step);
