-- ============================================================
-- schema_migration_v21.sql — Record Admin: lets a SuperAdmin grant a new
-- "record_admin" permission (via the existing step_roles mechanism, same
-- as gate_in/migo_103/migo_105/miro/compliance) to specific users, who can
-- then delete a history record entirely, reset an individual workflow
-- step, or revert an approval -- all from a new /admin/records page
-- instead of raw SQL run by hand against production.
--
-- admin_action_log: audit trail for every destructive action taken from
-- that page. Deliberately has NO foreign key to history(id) -- every
-- other table in this schema cascade-deletes when its history row is
-- deleted (see schema.sql), which is exactly right for those tables, but
-- would be self-defeating here: the whole point of this table is to keep
-- a record of "someone deleted history_id=39" even after history_id=39
-- itself no longer exists. history_id is stored as a plain, unconstrained
-- integer for that reason.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v21.sql
--
-- Safe to re-run: CREATE TABLE IF NOT EXISTS.
-- ============================================================

CREATE TABLE IF NOT EXISTS admin_action_log (
    id            SERIAL PRIMARY KEY,
    history_id    INTEGER NOT NULL,      -- no FK, intentionally -- see note above
    action        VARCHAR(50) NOT NULL,  -- delete_record / reset_gate_in / reset_migo_103 /
                                          -- reset_migo_105 / reset_miro / revert_extracted_data_approval /
                                          -- revert_gst_approval
    details       TEXT,                  -- free-text context captured before the change
                                          -- (e.g. invoice number, prior status) so the log
                                          -- is still meaningful after the record is gone
    performed_by  VARCHAR(255) NOT NULL,
    performed_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_admin_action_log_history_id ON admin_action_log(history_id);
CREATE INDEX IF NOT EXISTS idx_admin_action_log_performed_at ON admin_action_log(performed_at);

SELECT 'schema_migration_v21 applied — admin_action_log added, record_admin permission ready.' AS result;
