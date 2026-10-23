-- ============================================================
-- schema_migration_v23.sql — adds a Row Status column to
-- po_line_items, for the "Active"/"Deleted" flag po_fetch.robot now
-- reads per line item off SAP ME23N's grid deletion-indicator icon
-- (btnMEPO1211-STATUSICON, read via sap_helpers.py's
-- get_delflag_status() -- IconName 'B_DELE' = deleted).
--
-- A deleted PO line still shows a row in the grid, so without this flag
-- it was indistinguishable from a genuine active line with similar
-- data -- reported as line items appearing to "repeat". po_fetch.robot's
-- RESULT:PO_DATA: JSON now includes "row_status" per item ("Active" or
-- "Deleted"); po_line_items has no matching column yet.
--
-- Client decision (2026-08-12): a Deleted line still appears on the
-- MIGO 103 PO Items table (so nothing looks like it silently vanished)
-- but is greyed out / struck through and cannot be selected for
-- Invoice-PO pairing.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v23.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS.
-- ============================================================

ALTER TABLE po_line_items ADD COLUMN IF NOT EXISTS row_status VARCHAR(20) DEFAULT 'Active';

SELECT 'schema_migration_v23 applied — po_line_items.row_status added.' AS result;
