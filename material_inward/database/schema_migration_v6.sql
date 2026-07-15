-- ============================================================
-- schema_migration_v6.sql  —  GST Approval tab
-- Run ONCE on the production database.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v6.sql
--
-- Safe to re-run: all statements use IF NOT EXISTS / DO $$ blocks.
-- ============================================================


-- ============================================================
-- 1. HISTORY TABLE — add gst_check step flag
--    Follows the same pattern as gate_in, migo_103, migo_105, miro.
--    gst_check = 1 when GST approval is granted.
-- ============================================================
ALTER TABLE history ADD COLUMN IF NOT EXISTS gst_check         INT       DEFAULT 0;
ALTER TABLE history ADD COLUMN IF NOT EXISTS gst_check_done_at TIMESTAMP DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_history_gst_check ON history(gst_check);


-- ============================================================
-- 2. GST_APPROVAL TABLE
--    One row per history_id (UNIQUE constraint enforced).
--    history_id FK -> history.id (same pattern as gate_in_entries).
-- ============================================================
CREATE TABLE IF NOT EXISTS gst_approval (
    id                    SERIAL PRIMARY KEY,
    history_id            INT NOT NULL REFERENCES history(id) ON DELETE CASCADE,

    -- ── Site 1: einvoice1.gst.gov.in ──────────────────────────────────────
    einvoice_status       VARCHAR(150),       -- "NOT ENABLED for E-Invoice" / "ENABLED..."
    einvoice_screenshot   TEXT,               -- absolute file path (kept 10 days)

    -- ── Site 2: services.gst.gov.in ───────────────────────────────────────
    gstin_status          VARCHAR(50),        -- Active / Cancelled / Suspended
    legal_name            VARCHAR(200),
    taxpayer_type         VARCHAR(100),
    gstr3b_last_filed     VARCHAR(20),        -- "15/05/2026"
    gstr3b_tax_period     VARCHAR(50),        -- "April 2026-2027"
    gstr3b_status         VARCHAR(50),        -- "Filed"
    gstr1_last_filed      VARCHAR(20),
    gstr1_tax_period      VARCHAR(50),
    gstr1_status          VARCHAR(50),
    taxpayer_screenshot   TEXT,               -- absolute file path (kept 10 days)

    -- ── Approval workflow ─────────────────────────────────────────────────
    -- Values: pending | approved | hold
    -- No "denied" — only hold (with optional reason) or approve.
    approval_status       VARCHAR(20) NOT NULL DEFAULT 'pending',
    approval_by           VARCHAR(100),
    approval_at           TIMESTAMP,
    hold_reason           TEXT,               -- optional — can be NULL

    -- ── Bot run metadata ──────────────────────────────────────────────────
    bot_error             TEXT,               -- populated if either bot failed
    checked_at            TIMESTAMP,          -- when bots ran
    created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_gst_approval_history UNIQUE (history_id)
);

CREATE INDEX IF NOT EXISTS idx_gst_approval_history_id    ON gst_approval(history_id);
CREATE INDEX IF NOT EXISTS idx_gst_approval_status        ON gst_approval(approval_status);
CREATE INDEX IF NOT EXISTS idx_gst_approval_gstin_status  ON gst_approval(gstin_status);



-- ============================================================
-- 3. EXEMPT RECORDS ALREADY PAST GATE IN
--    Records that completed Gate In before this migration
--    should not be blocked by the new gst_check gate.
-- ============================================================
UPDATE history
SET gst_check         = 1,
    gst_check_done_at = CURRENT_TIMESTAMP
WHERE gate_in = 1
  AND (gst_check IS NULL OR gst_check = 0);


-- ============================================================
-- Verify
-- ============================================================
SELECT 'schema_migration_v6 applied successfully' AS result;
