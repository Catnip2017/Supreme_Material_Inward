-- ============================================================
-- schema_migration_v14.sql — 4th incoming document type ("Others"),
-- folder watcher timing, and DMS staging reorder.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v14.sql
--
-- Safe to re-run: uses ADD COLUMN IF NOT EXISTS.
--
-- BACKGROUND:
--   folder_watcher.py now recognizes a 4th incoming file type, suffix
--   _OTH (configurable via OTHERS_KEYWORD), alongside _INV/_EWB/_LR. It is
--   NOT run through OCR, but IS merged into the consolidated PDF that gets
--   staged for DMS upload, alongside Invoice/E-Way Bill/LR. This is
--   different from a genuinely unrecognized filename (any suffix that
--   doesn't match one of the 4 known keywords), which is still just
--   attached for reference/viewing only, per schema_migration_v13.sql's
--   history_extras table.
--
--   Both cases are stored in the same history_extras table (added in
--   v13) -- this migration just adds a column to tell them apart, since
--   doc_consolidator.py needs to know which history_extras rows for a
--   given history_id should be merged into the DMS-bound PDF (doc_type =
--   'others') versus left out of it entirely (doc_type = 'extra', the
--   default, preserving v13's existing behavior for anything already in
--   this table).
-- ============================================================

ALTER TABLE history_extras ADD COLUMN IF NOT EXISTS doc_type VARCHAR(20) DEFAULT 'extra';

CREATE INDEX IF NOT EXISTS idx_history_extras_doc_type
    ON history_extras(history_id, doc_type);

SELECT 'schema_migration_v14 applied — history_extras.doc_type added (extra | others).' AS result;
