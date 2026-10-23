-- ============================================================
-- schema_migration_v13.sql — Partial-document scenarios on Extracted Data
-- (goods delivery mode, E-Way Bill exemption reasons, and unrecognized
-- "Extras" files from the folder watcher).
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v13.sql
--
-- Safe to re-run: uses ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT EXISTS.
--
-- BACKGROUND:
--   folder_watcher.py previously required all 3 documents (Invoice,
--   E-Way Bill, LR) before it would even create a history record. That
--   rule is being relaxed so a record can be created with just 1 or 2 of
--   the 3 present. When a document is genuinely missing (not just
--   late-arriving), the reviewer (Compliance Officer, on the Extracted
--   Data tab) must record WHY:
--
--     - LR missing (Invoice + E-Way Bill present) -> goods_delivery_mode:
--       one of utility_van / vendor_vehicle / hand_delivery / courier.
--       Picking 'courier' makes the LR tab available for manual entry
--       (no scanned LR document exists for that case).
--
--     - E-Way Bill missing (Invoice + LR present) -> ewb_exemption_reasons:
--       one or more of value_exemption / distance_exemption /
--       other_exemption (comma-separated).
--
--     - Invoice only -> both of the above are captured together.
--
--   Both choices are permanent once saved (no later editing -- same as
--   every other Compliance-owned field once the record moves past
--   review), and each write also appends a fixed line of explanatory
--   text into history_remarks.remark_text so the reason is visible in
--   the existing Remarks panel without the reviewer typing anything by
--   hand. See database/scenario_operations.py.
-- ============================================================


-- ============================================================
-- 1. history: goods delivery mode + EWB exemption reasons.
--    NULL = not applicable / not yet chosen. Once non-NULL, the save
--    endpoints in app.py refuse further changes (permanent lock).
-- ============================================================
ALTER TABLE history ADD COLUMN IF NOT EXISTS goods_delivery_mode VARCHAR(30);
ALTER TABLE history ADD COLUMN IF NOT EXISTS goods_delivery_mode_by VARCHAR(255);
ALTER TABLE history ADD COLUMN IF NOT EXISTS goods_delivery_mode_at TIMESTAMP;

ALTER TABLE history ADD COLUMN IF NOT EXISTS ewb_exemption_reasons VARCHAR(255);
ALTER TABLE history ADD COLUMN IF NOT EXISTS ewb_exemption_reasons_by VARCHAR(255);
ALTER TABLE history ADD COLUMN IF NOT EXISTS ewb_exemption_reasons_at TIMESTAMP;


-- ============================================================
-- 2. history_extras -- files picked up by folder_watcher.py in a group
--    whose filename suffix didn't match INV/EWB/LR at all. These are
--    never renamed into the OCR pipeline; they're just attached to the
--    record for reference and shown under the "Extras" banner on the
--    Extracted Data tab (view/download only).
-- ============================================================
CREATE TABLE IF NOT EXISTS history_extras (
    id                SERIAL PRIMARY KEY,
    history_id        INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    filename          VARCHAR(500) NOT NULL,   -- stored filename (as saved under UPLOAD_FOLDER)
    original_filename VARCHAR(500),            -- filename as it arrived in the watch folder
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_history_extras_history_id
    ON history_extras(history_id);


SELECT 'schema_migration_v13 applied — goods_delivery_mode, ewb_exemption_reasons, history_extras added.' AS result;
