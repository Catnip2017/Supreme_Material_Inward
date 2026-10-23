-- ============================================================
-- schema_migration_v18.sql — DMS link attach tracking for MIGO 103,
-- MIGO 105, and MIRO (three separate follow-up RF jobs, NOT embedded in
-- the posting bots themselves -- client decision: a DMS link failure must
-- never be able to affect whether the underlying SAP posting is reported
-- as successful).
--
-- BACKGROUND: gate_in -> po_fetch -> dms_upload (new, chained in the same
-- rf_queue) now writes a Contentverse link into dms_document_links shortly
-- after every Gate In, keyed by history_id (see services/dms_links_import.py).
-- Once material_doc_number exists (after MIGO 103) and the link exists,
-- three separate follow-up jobs each attach that same link inside SAP
-- against the relevant document: migo103_link, migo105_link, miro_link --
-- all three post-only steps, run as their own rf_queue jobs, auto-enqueued
-- right after their corresponding primary posting job succeeds (or,
-- if the link wasn't there yet at that moment, caught up later by
-- services/dms_links_import.py once the link lands -- see that script's
-- v18 addition).
--
-- Each pair of columns mirrors the existing migo_103_rf_status /
-- migo_103_rf_error / migo_103_executed_at pattern already used for the
-- primary posting steps on the same tables.
--
-- status values: 'pending' (not attempted yet) | 'skipped_no_link'
-- (posting succeeded but no DMS link was available at enqueue time --
-- waiting for services/dms_links_import.py to catch it up) | 'success' |
-- 'failed'.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v18.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS.
-- ============================================================

-- migo_entries: covers both the migo103_link and migo105_link follow-up jobs
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo103_link_status       VARCHAR(50) DEFAULT 'pending';
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo103_link_error        TEXT;
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo103_link_executed_at  TIMESTAMP;

ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo105_link_status       VARCHAR(50) DEFAULT 'pending';
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo105_link_error        TEXT;
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo105_link_executed_at  TIMESTAMP;

-- miro_entries: covers the miro_link follow-up job
ALTER TABLE miro_entries ADD COLUMN IF NOT EXISTS miro_link_status          VARCHAR(50) DEFAULT 'pending';
ALTER TABLE miro_entries ADD COLUMN IF NOT EXISTS miro_link_error           TEXT;
ALTER TABLE miro_entries ADD COLUMN IF NOT EXISTS miro_link_executed_at     TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_migo_entries_migo103_link_status ON migo_entries(migo103_link_status);
CREATE INDEX IF NOT EXISTS idx_migo_entries_migo105_link_status ON migo_entries(migo105_link_status);
CREATE INDEX IF NOT EXISTS idx_miro_entries_miro_link_status    ON miro_entries(miro_link_status);

SELECT 'schema_migration_v18 applied — migo103_link / migo105_link / miro_link status tracking added.' AS result;
