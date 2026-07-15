-- ============================================================
-- schema_migration_v3.sql
-- Adds po_line_items table for PO fetch bot results.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v3.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS po_line_items (
    id              SERIAL PRIMARY KEY,
    history_id      INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    item_no         VARCHAR(10),
    material        VARCHAR(50),
    short_text      TEXT,
    po_qty          VARCHAR(30),
    unit            VARCHAR(20),
    delivery_date   VARCHAR(20),
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_po_line_items_history ON po_line_items(history_id);

SELECT 'po_line_items table created/verified.' AS result;

-- Add po_fetch step support to rf_queue (no schema change needed,
-- step column is VARCHAR — just documenting valid values here)
-- Valid steps: gate_in | po_fetch | migo_103 | migo_105 | miro