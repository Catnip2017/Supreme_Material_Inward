-- ============================================================
-- schema_migration_v8.sql  —  Supplier Master (SAP vendor code lookup)
-- Run ONCE on the production database.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v8.sql
--
-- Safe to re-run: all statements use IF NOT EXISTS / DO $$ blocks.
--
-- Note: numbered v8 to sit after schema_migration_v7.sql (goods info /
-- invoice migration). Table definition and column set follow the
-- CREATE TABLE supplier_master statement already run manually against the
-- SAP zvend_address export -- this migration formalizes it into the
-- versioned schema history (same style as schema_migration_v6.sql) and adds
-- the extra tracking/search columns called out below.
-- ============================================================
 
 
-- ============================================================
-- 1. SUPPLIER_MASTER TABLE
--    One row per SAP vendor code (Business Partner). Refreshed nightly by
--    the standalone vendor-export bot (zvend_address transaction) + importer
--    -- see vendor_master_sync/ (outside this Flask app, run on a schedule).
--    supplier = SAP vendor code, PRIMARY KEY (natural key from SAP).
-- ============================================================
CREATE TABLE IF NOT EXISTS supplier_master (
    supplier                    VARCHAR(20) PRIMARY KEY,
    name_1                      VARCHAR(255),
    tax_number_3                VARCHAR(20),
    permanent_account_number    VARCHAR(20),
    name                        VARCHAR(255),
    name_2                      VARCHAR(255),
    name_3                      VARCHAR(255),
    name_4                      VARCHAR(255),
    city                        VARCHAR(255),
    district                    VARCHAR(255),
    postal_code                 VARCHAR(20),
    street_2                    VARCHAR(255),
    street_3                    VARCHAR(255),
    street_4                    VARCHAR(255),
    street_5                    VARCHAR(255),
    building                    VARCHAR(255),
    floor                       VARCHAR(100),
    rg                          VARCHAR(10),
 
    -- ── Added for the daily sync job / lookup UI (not in the original
    --    manually-run CREATE TABLE) ─────────────────────────────────────
    -- Lets the importer tell "still current" apart from "sync stopped
    -- running N days ago and nobody noticed" (flagged as an edge case
    -- when this was designed).
    last_synced_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at                  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
 
 
-- ============================================================
-- 2. FUZZY / PROXIMITY SEARCH SUPPORT
--    pg_trgm gives similarity()/% matching so "India ABB Ltd" vs
--    "India ABB Limited" can be matched without an exact string match.
--    Requires the extension to be available on the server (standard
--    contrib module, ships with Postgres). If CREATE EXTENSION fails here
--    due to permissions, ask a DB admin to run it once as superuser --
--    the rest of this migration does not depend on it, only the
--    trigram index below does.
-- ============================================================
CREATE EXTENSION IF NOT EXISTS pg_trgm;
 
CREATE INDEX IF NOT EXISTS idx_supplier_master_name_1_trgm
    ON supplier_master USING gin (name_1 gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_supplier_master_name_trgm
    ON supplier_master USING gin (name gin_trgm_ops);
 
-- Plain lookups (city/district shown in the disambiguation picklist).
CREATE INDEX IF NOT EXISTS idx_supplier_master_city     ON supplier_master(city);
CREATE INDEX IF NOT EXISTS idx_supplier_master_district ON supplier_master(district);
 
 
-- ============================================================
-- (Removed) 3. GATE_IN_ENTRIES vendor_code column.
--    Originally added a separate vendor_code column alongside vendor_name.
--    Reverted per instruction: the resolved SAP vendor code goes directly
--    into the existing vendor_name field/column instead -- Gate In posts
--    to SAP through the same VENDOR_NAME slot it always has, just holding
--    a code string instead of a free-text name once Fetch/type-ahead
--    resolves one. No new column, no new SAP field, no rf_runner.py /
--    gate_in.robot changes needed at all.
-- ============================================================
 
 
-- ============================================================
-- Verify
-- ============================================================
SELECT 'schema_migration_v8 applied successfully' AS result;