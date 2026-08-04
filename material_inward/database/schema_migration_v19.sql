-- ============================================================
-- schema_migration_v19.sql — adds a UOM (unit of measure) column to
-- lr_data, matching the UOM column already added to the MIGO 103 / MIGO
-- 105 invoice line-item tables (items_data JSON) earlier. LR is a flat
-- one-row-per-history table (not a JSON items list), so this needs an
-- actual column rather than a key inside an existing JSON blob.
--
-- Populated from the LR tab's new "UOM" field (extracted_data.html,
-- saveExtractedLr()) and stored/read via database/db_operations.py's
-- save_lr_to_db() / get_history_details_by_id() (the latter does
-- SELECT * FROM lr_data, so no code change was needed on the read side).
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v19.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS.
-- ============================================================

ALTER TABLE lr_data ADD COLUMN IF NOT EXISTS uom VARCHAR(20);

SELECT 'schema_migration_v19 applied — lr_data.uom added.' AS result;
