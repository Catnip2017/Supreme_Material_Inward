-- ============================================================
-- schema_migration_v7.sql — Shift Goods Information from
-- E-Way Bill to Invoice (single source of truth = hsn_details)
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v7.sql
--
-- Safe to re-run.
--
-- DESIGN (agreed):
--   * Goods info on the Invoice tab is rendered from the existing
--     invoice_data.hsn_details JSONB line items — NO new columns
--     are added to invoice_data (one copy of the data only).
--   * invoice_data.total_taxable_amount and grand_total already
--     exist and remain in the "Tax & Amount Information" section.
--   * The application no longer extracts, displays, or writes the
--     6 goods columns on ewaybill_data:
--         goods_description, hsn_code, quantity, value_of_goods,
--         total_taxable_amount, total_invoice_amount
--   * STAGED DROP: the columns are kept for a safety window so the
--     app can be rolled back without data loss. Run STAGE 2 below
--     only after the new flow is verified in production.
-- ============================================================


-- ============================================================
-- STAGE 1 — run now (no structural change)
-- ============================================================

-- 1a. Sanity check: confirm the deprecated columns still exist.
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'ewaybill_data'
  AND column_name IN (
      'goods_description', 'hsn_code', 'quantity', 'value_of_goods',
      'total_taxable_amount', 'total_invoice_amount'
  );

-- 1b. Document the deprecation on the columns themselves so anyone
--     inspecting the schema knows they are frozen.
COMMENT ON COLUMN ewaybill_data.goods_description    IS 'DEPRECATED v7 — goods info now lives in invoice_data.hsn_details. Frozen; scheduled for drop in Stage 2.';
COMMENT ON COLUMN ewaybill_data.hsn_code             IS 'DEPRECATED v7 — goods info now lives in invoice_data.hsn_details. Frozen; scheduled for drop in Stage 2.';
COMMENT ON COLUMN ewaybill_data.quantity             IS 'DEPRECATED v7 — goods info now lives in invoice_data.hsn_details. Frozen; scheduled for drop in Stage 2.';
COMMENT ON COLUMN ewaybill_data.value_of_goods       IS 'DEPRECATED v7 — goods info now lives in invoice_data.hsn_details. Frozen; scheduled for drop in Stage 2.';
COMMENT ON COLUMN ewaybill_data.total_taxable_amount IS 'DEPRECATED v7 — totals shown from invoice_data. Frozen; scheduled for drop in Stage 2.';
COMMENT ON COLUMN ewaybill_data.total_invoice_amount IS 'DEPRECATED v7 — totals shown from invoice_data. Frozen; scheduled for drop in Stage 2.';

SELECT 'schema_migration_v7 STAGE 1 applied — EWB goods columns frozen (not dropped).' AS result;


-- ============================================================
-- STAGE 2 — run ONLY after the new flow is verified
-- (recommended safety window: 2–4 weeks of production use).
-- Uncomment the block below and re-run this file, or execute
-- the statements manually.
--
-- Before running, optionally archive the old values:
--   CREATE TABLE ewaybill_goods_archive_v7 AS
--   SELECT id, goods_description, hsn_code, quantity, value_of_goods,
--          total_taxable_amount, total_invoice_amount
--   FROM ewaybill_data
--   WHERE goods_description IS NOT NULL
--      OR hsn_code IS NOT NULL
--      OR quantity IS NOT NULL
--      OR value_of_goods IS NOT NULL
--      OR total_taxable_amount IS NOT NULL
--      OR total_invoice_amount IS NOT NULL;
-- ============================================================

-- ALTER TABLE ewaybill_data DROP COLUMN IF EXISTS goods_description;
-- ALTER TABLE ewaybill_data DROP COLUMN IF EXISTS hsn_code;
-- ALTER TABLE ewaybill_data DROP COLUMN IF EXISTS quantity;
-- ALTER TABLE ewaybill_data DROP COLUMN IF EXISTS value_of_goods;
-- ALTER TABLE ewaybill_data DROP COLUMN IF EXISTS total_taxable_amount;
-- ALTER TABLE ewaybill_data DROP COLUMN IF EXISTS total_invoice_amount;

-- SELECT 'schema_migration_v7 STAGE 2 applied — EWB goods columns dropped.' AS result;
