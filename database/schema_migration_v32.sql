-- ============================================================
-- schema_migration_v32.sql -- MIGO 101 and MIGO 105 reversal tracking.
--
-- Same shape as schema_migration_v27.sql (MIGO 103 reversal): the shared
-- reversal bot (migo_reversal.robot, SAP action A03) cancels an existing
-- posting by creating a NEW SAP document that references the original, so
-- these columns record "this step was later reversed" on top of the
-- original posting's own record. 4 columns per step:
--   migo_1xx_reversed (flag), migo_1xx_reversal_doc_number,
--   migo_1xx_reversed_at, migo_1xx_reversed_by
--
-- As with MIGO 103, a successful reversal immediately auto-resets the step
-- (database/admin_operations.py reset_migo_101_step / reset_migo_105_step,
-- which clear these columns again) so the record can be posted again right
-- away. The columns matter while that reset is pending, or if it ever fails
-- (record stays visibly "reversed" and blocked until an admin resets it).
--
-- RUN THIS ON THE DATABASE *BEFORE* DEPLOYING THE NEW APP FILES. The History
-- list query selects these columns, so the app errors without them.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v32.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS. Existing rows are not touched.
-- ============================================================

ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_101_reversed SMALLINT DEFAULT 0;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_101_reversal_doc_number TEXT;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_101_reversed_at TIMESTAMP;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_101_reversed_by VARCHAR(255);

ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_105_reversed SMALLINT DEFAULT 0;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_105_reversal_doc_number TEXT;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_105_reversed_at TIMESTAMP;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_105_reversed_by VARCHAR(255);

SELECT 'schema_migration_v32 applied -- history.migo_101_reversed* and migo_105_reversed* added.' AS result;
