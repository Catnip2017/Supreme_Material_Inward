-- ============================================================
-- schema_migration_v16.sql — three independent additions:
--
--   A) gate_in_entries.submitted_by — records which logged-in user's
--      submission actually produced a successful (or attempted) Gate In
--      posting. Needed for the zgatein_update PO-backfill redesign
--      (client wants the update attributed to the SAME person who did
--      the original Gate In, not whoever happens to trigger MIGO 103).
--      This migration only adds the column; the app-side flow that
--      *uses* it for a pending-PO-update panel is still under design
--      and NOT part of this migration.
--
--   B) gst_approval.auto_retry_exhausted — persists the "stop
--      auto-retrying, wait for a manual Re-run" decision that
--      services/gst_runner.py previously tracked only in an in-memory
--      dict (_attempts). That dict is wiped on every app restart, so a
--      record that had already exhausted its 5 auto-retries would
--      silently start auto-retrying again -- every 5s poll, forever --
--      the moment anyone opened its view page after a restart. This
--      column makes the "stop retrying automatically" decision durable.
--
--   C) dms_document_links — one row per consolidated PDF successfully
--      uploaded to Contentverse by dms_bot.robot, holding the sharing
--      link dms_bot/excel_writer.py currently only writes to
--      document_links.xlsx. A new import script reads that Excel file
--      and upserts rows here (matched by filename), so the app's
--      Documents tab can show/link to the hosted copy.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v16.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT EXISTS.
-- ============================================================

-- A. gate_in_entries.submitted_by
ALTER TABLE gate_in_entries ADD COLUMN IF NOT EXISTS submitted_by VARCHAR(255);

-- B. gst_approval.auto_retry_exhausted
ALTER TABLE gst_approval ADD COLUMN IF NOT EXISTS auto_retry_exhausted BOOLEAN NOT NULL DEFAULT FALSE;

-- C. dms_document_links
CREATE TABLE IF NOT EXISTS dms_document_links (
    id              SERIAL PRIMARY KEY,
    history_id      INTEGER REFERENCES history(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,          -- matches history.consolidated_doc_path's basename
    document_link   TEXT NOT NULL,          -- Contentverse sharing URL from document_links.xlsx
    imported_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_dms_document_links_filename UNIQUE (filename)
);

CREATE INDEX IF NOT EXISTS idx_dms_document_links_history_id ON dms_document_links(history_id);

SELECT 'schema_migration_v16 applied — gate_in_entries.submitted_by, gst_approval.auto_retry_exhausted, dms_document_links added.' AS result;
