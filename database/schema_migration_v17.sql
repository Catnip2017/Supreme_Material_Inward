-- ============================================================
-- schema_migration_v17.sql — Pending PO Updates (zgatein_update
-- decoupled from MIGO 103).
--
-- BACKGROUND: for a without_po Gate In, MIGO 103's own posting no longer
-- waits on zgatein_update backfilling the PO onto the SAP Gate In record
-- first (confirmed with SAP: posting order between the two doesn't
-- matter). Instead, the moment MIGO 103 captures a real PO number for
-- such a record, a pending_po_updates row is created here, targeted at
-- whoever originally submitted that Gate In (gate_in_entries.submitted_by,
-- added in v16) -- they see it as a "Pending PO Updates" item on the
-- History page and trigger the zgatein_update job themselves, under their
-- own live session credential, whenever they get to it. See
-- database/pending_po_operations.py.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v17.sql
--
-- Safe to re-run: CREATE TABLE IF NOT EXISTS.
-- ============================================================

CREATE TABLE IF NOT EXISTS pending_po_updates (
    id              SERIAL PRIMARY KEY,
    history_id      INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,

    po_number       TEXT NOT NULL,
    gate_in_number  TEXT NOT NULL,      -- snapshotted at request time

    requested_by    VARCHAR(255),       -- MIGO 103 submitter's username
    requested_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Who should see/action this -- gate_in_entries.submitted_by at
    -- request time. NULL for records that predate that column being
    -- populated; treated as "any gate_in-role user" in the app (see
    -- get_pending_po_updates_for_user()).
    target_username VARCHAR(255),

    status          VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending | done | failed
    resolved_by     VARCHAR(255),
    resolved_at     TIMESTAMP,
    error_message   TEXT,

    CONSTRAINT uq_pending_po_updates_history UNIQUE (history_id)
);

CREATE INDEX IF NOT EXISTS idx_pending_po_updates_target  ON pending_po_updates(target_username, status);
CREATE INDEX IF NOT EXISTS idx_pending_po_updates_history ON pending_po_updates(history_id);

SELECT 'schema_migration_v17 applied — pending_po_updates table added.' AS result;
