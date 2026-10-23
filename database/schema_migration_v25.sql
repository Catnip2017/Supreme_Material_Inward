-- ============================================================
-- schema_migration_v25.sql — Vendor Name / Vendor Code split (Gate In tab).
--
-- BACKGROUND: previously "Vendor Name" was a single overloaded field --
-- it held the OCR'd seller name until the user clicked Fetch, at which
-- point it got overwritten with the resolved SAP vendor code (LIFNR),
-- since Gate In's SAP posting needs the code, not a name. That meant the
-- field showed a bare code like "200801" after Fetch, with no way to see
-- the actual vendor name again without a separate lookup (see MIGO 103's
-- own v20 workaround, get_supplier_by_code(), which reverse-resolves the
-- name from that stored code purely for display).
--
-- This adds a dedicated vendor_code column so Vendor Name can stay a real,
-- human-readable name and Vendor Code holds the SAP code that actually
-- gets posted -- see templates/tabs/gate_in.html and
-- services/rf_runner.py's execute_gate_in_sap().
--
-- Existing rows: vendor_code will be NULL for every gate_in_entries row
-- that predates this migration (their vendor_name still holds a bare
-- code from the old overloaded-field behavior). No backfill here --
-- app.py's view_detail() resolves this per-record at display time via
-- get_supplier_by_code(), same pattern as MIGO 103, rather than rewriting
-- history.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v25.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS.
-- ============================================================

ALTER TABLE gate_in_entries ADD COLUMN IF NOT EXISTS vendor_code TEXT;

SELECT 'schema_migration_v25 applied — gate_in_entries.vendor_code added.' AS result;
