-- ============================================================
-- schema_migration_v12.sql — Record-wide Remarks & Comments
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v12.sql
--
-- Safe to re-run: uses CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--
-- BACKGROUND:
--   Client-requested: a single "Remark" set by the Compliance Officer while
--   reviewing Extracted Data / GST Approval, visible to every role across
--   every tab as the record moves through the pipeline (Gate In, MIGO 103,
--   MIGO 105, MIRO) -- plus the ability for each role to add their own
--   comment on that remark. Comments are attributed to the ROLE that made
--   them (e.g. "Stores Officer"), not the individual username, per client
--   instruction. Each role's comment thread shows only their most recent
--   comment (posting again overwrites what's shown), but every comment is
--   still stored as its own row (INSERT-only, never UPDATE/DELETE) so the
--   full history survives in the database for audit purposes even though
--   only the latest per role is ever displayed.
-- ============================================================


-- ============================================================
-- 1. history_remarks -- one row per history record, the single root
--    Remark. Only the Compliance role (or a SuperAdmin with edit rights)
--    can set/edit this -- see app.py's POST /api/remarks/<history_id>,
--    gated the same way as every other Compliance-owned field.
-- ============================================================
CREATE TABLE IF NOT EXISTS history_remarks (
    history_id      INTEGER PRIMARY KEY REFERENCES history(id) ON DELETE CASCADE,
    remark_text     TEXT,
    updated_by_role VARCHAR(50),
    updated_by      VARCHAR(255),   -- username, kept for internal audit only --
                                     -- never returned by the API/shown in the UI,
                                     -- which deliberately shows role, not username.
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================
-- 2. history_comments -- append-only log, one row per comment. Any role
--    with edit access to the record can post one; the UI only ever shows
--    the most recent row per (history_id, role), but nothing is ever
--    updated or deleted here -- see get_comments() in
--    database/remarks_operations.py for the "latest per role" query.
-- ============================================================
CREATE TABLE IF NOT EXISTS history_comments (
    id              SERIAL PRIMARY KEY,
    history_id      INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    role            VARCHAR(50) NOT NULL,   -- e.g. compliance / gate_in / migo_103 /
                                             -- migo_105 / miro / SuperAdmin
    comment_text    TEXT NOT NULL,
    created_by      VARCHAR(255),   -- username, internal audit only, same as above
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_history_comments_history_role_created
    ON history_comments(history_id, role, created_at DESC);


-- ============================================================
-- Verify
-- ============================================================
SELECT 'schema_migration_v12 applied — history_remarks + history_comments created.' AS result;
