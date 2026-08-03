-- ============================================================
-- schema_migration_v9.sql — Add IRN (Invoice Reference Number)
-- to invoice_data, displayed/edited from the GST Approval tab.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v9.sql
--
-- Safe to re-run.
--
-- DESIGN:
--   * IRN is extracted from the Invoice document during OCR
--     (services/extract.py) and stored on invoice_data.irn.
--   * It is NOT displayed/edited on the Extracted Data > Invoice tab --
--     it is shown and edited only on the GST Approval tab, inside the
--     E-Invoice Status container, editable until GST approval_status
--     (gst_approval.approval_status, a separate flag from the main
--     document approval) becomes 'approved'.
--   * Plain additive column -- no deprecation/staging needed (unlike
--     schema_migration_v7.sql, which staged a column removal).
-- ============================================================

ALTER TABLE invoice_data ADD COLUMN IF NOT EXISTS irn TEXT;

SELECT 'schema_migration_v9 applied — invoice_data.irn column added.' AS result;
