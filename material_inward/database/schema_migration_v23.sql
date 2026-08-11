-- ============================================================
-- schema_migration_v23.sql — adds outbound_delivery_number to
-- invoice_data.
--
-- Delivery Challan and Tax Invoice documents in the Stock Transfer
-- document family (Supreme Petrochem intra-company plant transfers)
-- print an "Outbound Delivery Number" that doesn't exist on a normal
-- third-party purchase invoice. Captured as its own field by
-- services/extract.py and shown on the Invoice tab's Basic Information
-- section (Extracted Data > Invoice), editable/saveable like any other
-- invoice field.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v23.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS.
-- ============================================================

ALTER TABLE invoice_data ADD COLUMN IF NOT EXISTS outbound_delivery_number TEXT;

SELECT 'schema_migration_v23 applied — invoice_data.outbound_delivery_number added.' AS result;
