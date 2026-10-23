-- ============================================================
-- schema_migration_v10.sql — Formalizes manual supplier_master fixes
-- applied directly against production on 2026-07-16 during the initial
-- VENDOR_MASTER_SYNC data load.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v10.sql
--
-- Safe to re-run: every statement uses IF NOT EXISTS or is a no-op ALTER
-- TYPE if the column is already the target width.
--
-- BACKGROUND:
--   schema_migration_v8.sql declared supplier_master with CREATE TABLE IF
--   NOT EXISTS, but the real table had already been created manually
--   (via a hand-run CREATE TABLE, before v8 was written) without the
--   last_synced_at/created_at columns v8's CREATE TABLE text described --
--   so running v8 against that pre-existing table was a no-op and those
--   two columns never actually got added. Separately, the very first
--   real-data import (4211 rows from the SAP zvend_address export) hit
--   `value too long for type character varying(20)` on a foreign vendor's
--   permanent_account_number field (a free-text TIN reference, not a PAN,
--   30+ characters), which prompted widening several columns beyond what
--   v8 originally declared. This migration formalizes both fixes into the
--   versioned schema history so a fresh install matches production, and
--   updates database/schema.sql to include supplier_master (previously
--   absent from the base schema entirely -- it only existed via v8).
-- ============================================================


-- ============================================================
-- 1. Add the tracking columns v8's CREATE TABLE declared but that never
--    actually landed on the pre-existing production table.
-- ============================================================
ALTER TABLE supplier_master ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE supplier_master ADD COLUMN IF NOT EXISTS created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;


-- ============================================================
-- 2. Widen columns that proved too narrow against real SAP export data.
--    supplier/tax_number_3/postal_code/rg: real-world values observed so
--    far all fit comfortably under 20, but SAP code fields in general
--    aren't guaranteed to stay that short, so widened defensively.
--    permanent_account_number: the one that actually broke the import --
--    domestic vendors have a 10-char PAN, but foreign vendors get a
--    free-text tax reference instead (observed: 'TIN NO-1997/2413/960
--    (706/993)', 30 characters) -- widened generously to VARCHAR(100).
-- ============================================================
ALTER TABLE supplier_master ALTER COLUMN supplier                 TYPE VARCHAR(50);
ALTER TABLE supplier_master ALTER COLUMN tax_number_3              TYPE VARCHAR(50);
ALTER TABLE supplier_master ALTER COLUMN permanent_account_number  TYPE VARCHAR(100);
ALTER TABLE supplier_master ALTER COLUMN postal_code               TYPE VARCHAR(50);
ALTER TABLE supplier_master ALTER COLUMN rg                        TYPE VARCHAR(50);


-- ============================================================
-- Verify
-- ============================================================
SELECT 'schema_migration_v10 applied — supplier_master tracking columns + widened column types.' AS result;
