-- ============================================================
-- schema_migration_v2.sql
-- Run this ONLY if you already ran the original schema.sql
-- and need to add the new rf_queue table.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v2.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS rf_queue (
    id              SERIAL PRIMARY KEY,
    history_id      INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    step            VARCHAR(20) NOT NULL,
    status          VARCHAR(20) DEFAULT 'pending',
    payload         JSONB,
    result          JSONB,
    error_message   TEXT,
    attempts        INTEGER DEFAULT 0,
    queued_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rf_queue_status  ON rf_queue(status);
CREATE INDEX IF NOT EXISTS idx_rf_queue_history ON rf_queue(history_id);

-- Reset any stuck jobs from previous runs (safety measure)
UPDATE rf_queue SET status = 'failed', error_message = 'Reset on migration'
WHERE status = 'running';

SELECT 'rf_queue table created/verified.' AS result;

-- Add migo_po_number column (run if upgrading from previous version)
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_po_number TEXT;
SELECT 'migo_po_number column added.' AS result;