-- ============================================================
-- schema_migration_v22.sql — adds an Open Quantity column to
-- po_line_items, for the "Open Qty" value po_fetch.robot now scrapes
-- from SAP ME23N's Delivery tab (field MEPO1320-OBMNG -- the
-- outstanding/undelivered quantity remaining on that PO line).
--
-- Unlike the earlier UOM fix (schema_migration_v3.sql already had a
-- dormant `unit` column that just wasn't wired up), this is a genuinely
-- new value with nowhere to live yet -- po_fetch.robot's RESULT:PO_DATA:
-- JSON now includes "open_qty" per item, but po_line_items has no
-- matching column.
--
-- View-only, PO side only (client decision, 2026-08-07): shown on the
-- MIGO 103 tab's "PO Line Items (from SAP)" table only -- the Invoice
-- table has no equivalent concept, and this column deliberately does
-- NOT participate in the Invoice-vs-PO mismatch-highlighting check
-- (nothing on the Invoice side to compare it against).
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v22.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS.
-- ============================================================

ALTER TABLE po_line_items ADD COLUMN IF NOT EXISTS open_qty VARCHAR(30);

SELECT 'schema_migration_v22 applied — po_line_items.open_qty added.' AS result;
