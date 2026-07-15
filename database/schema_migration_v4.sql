-- ============================================================
-- schema_migration_v4.sql
-- Adds approval workflow, OCR retry tracking, user email/roles,
-- and notifications table. Drops record locking columns.
--
-- Apply with:
--   psql -U material_user -d material_inward -f database\schema_migration_v4.sql
-- ============================================================

-- ============================================================
-- HISTORY TABLE — approval workflow + ocr status tracking
-- ============================================================
ALTER TABLE history ADD COLUMN IF NOT EXISTS approval_status VARCHAR(20) DEFAULT 'pending';
ALTER TABLE history ADD COLUMN IF NOT EXISTS approval_by      VARCHAR(255);
ALTER TABLE history ADD COLUMN IF NOT EXISTS approval_at      TIMESTAMP;
ALTER TABLE history ADD COLUMN IF NOT EXISTS hold_reason      TEXT;

ALTER TABLE history ADD COLUMN IF NOT EXISTS ocr_status       VARCHAR(20) DEFAULT 'success';
ALTER TABLE history ADD COLUMN IF NOT EXISTS ocr_retry_count  INTEGER DEFAULT 0;
ALTER TABLE history ADD COLUMN IF NOT EXISTS ocr_failed_path  TEXT;

-- Backfill: any record where Gate In already done auto-approved
UPDATE history SET approval_status = 'approved' WHERE gate_in = 1 AND approval_status = 'pending';

-- Drop legacy locking columns
ALTER TABLE history DROP COLUMN IF EXISTS locked_by;
ALTER TABLE history DROP COLUMN IF EXISTS locked_at;

CREATE INDEX IF NOT EXISTS idx_history_approval_status ON history(approval_status);
CREATE INDEX IF NOT EXISTS idx_history_ocr_status      ON history(ocr_status);

-- ============================================================
-- USERS TABLE — email + step roles + email notification toggle
-- ============================================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS email                       VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_notifications_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS step_roles                  VARCHAR(255) DEFAULT 'all';

-- ============================================================
-- NOTIFICATIONS TABLE — in-app notifications
-- ============================================================
CREATE TABLE IF NOT EXISTS notifications (
    id           SERIAL PRIMARY KEY,
    user_target  VARCHAR(255),                                 -- specific user, null = role broadcast
    role_target  VARCHAR(50),                                  -- step_role: gate_in / migo_103 / migo_105 / miro / all
    history_id   INTEGER REFERENCES history(id) ON DELETE CASCADE,
    title        VARCHAR(255),
    message      TEXT,
    type         VARCHAR(20),                                  -- approve / hold / gate_in / migo_103 / migo_105 / miro / ocr_failed
    is_read      BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notif_user_unread ON notifications(user_target, is_read) WHERE is_read = FALSE;
CREATE INDEX IF NOT EXISTS idx_notif_role        ON notifications(role_target);
CREATE INDEX IF NOT EXISTS idx_notif_created     ON notifications(created_at);

SELECT 'schema_migration_v4 applied successfully' AS result;