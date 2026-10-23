-- ============================================================
-- schema_migration_v27.sql — MIGO 103 reversal tracking.
--
-- BACKGROUND (2026-08-21): the new MIGO Reversal bot cancels an existing
-- MIGO 103 posting (SAP action A03) by creating a NEW SAP document that
-- references the original one -- it does NOT undo/delete the original
-- material document. So history.migo_103 / material_doc_number must stay
-- exactly as they are (that GR genuinely happened) -- this migration adds
-- separate columns to record that it was LATER reversed, without touching
-- the original posting's own record.
--
-- POLICY (assumed, per discussion -- confirm/flag if wrong): once
-- migo_103_reversed=1, MIGO 105 and MIRO are blocked for that record (see
-- app.py's api_run_migo_105/api_run_miro role/state guards) since the
-- goods are no longer actually sitting in blocked stock once reversed.
--
-- Reversal is only ever offered for records where migo_103=1 already (per
-- explicit confirmation) -- there is no "reverse an arbitrary doc number
-- not in our system" path.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v27.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS.
-- ============================================================

ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_103_reversed SMALLINT DEFAULT 0;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_103_reversal_doc_number TEXT;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_103_reversed_at TIMESTAMP;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_103_reversed_by VARCHAR(255);

SELECT 'schema_migration_v27 applied — history.migo_103_reversed* added.' AS result;
