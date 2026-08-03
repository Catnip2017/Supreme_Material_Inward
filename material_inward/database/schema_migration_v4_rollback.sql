-- ============================================================
-- ROLLBACK for schema_migration_v4.sql
-- Run only if you need to undo v4 changes
-- ============================================================

DROP TABLE IF EXISTS notifications;

ALTER TABLE users DROP COLUMN IF EXISTS email;
ALTER TABLE users DROP COLUMN IF EXISTS email_notifications_enabled;
ALTER TABLE users DROP COLUMN IF EXISTS step_roles;

ALTER TABLE history DROP COLUMN IF EXISTS approval_status;
ALTER TABLE history DROP COLUMN IF EXISTS approval_by;
ALTER TABLE history DROP COLUMN IF EXISTS approval_at;
ALTER TABLE history DROP COLUMN IF EXISTS hold_reason;
ALTER TABLE history DROP COLUMN IF EXISTS ocr_status;
ALTER TABLE history DROP COLUMN IF EXISTS ocr_retry_count;
ALTER TABLE history DROP COLUMN IF EXISTS ocr_failed_path;

ALTER TABLE history ADD COLUMN IF NOT EXISTS locked_by VARCHAR(255);
ALTER TABLE history ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP;

SELECT 'schema_migration_v4 rolled back' AS result;

-- psql -U material_user -d material_inward -f database\schema_migration_v4_rollback.sql