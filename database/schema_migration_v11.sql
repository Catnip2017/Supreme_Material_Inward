-- ============================================================
-- schema_migration_v11.sql  —  Role-based page/tab access control
-- Run ONCE on the production database.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v10.sql
--
-- Safe to re-run: uses IF NOT EXISTS / idempotent UPDATE statements.
--
-- DESIGN (agreed with client):
--   * "Admin" is renamed to "SuperAdmin" — handled by IT. A new admin_edit
--     BOOLEAN column controls whether a SuperAdmin can actually edit/act
--     (TRUE) or only view every tab read-only (FALSE). Only SuperAdmin
--     accounts have User Management access at all, regardless of
--     admin_edit.
--   * step_roles keeps its existing free-text comma-separated shape (no
--     new table), but gets one new possible value: "compliance" — the
--     bucket that owns Documents, Extracted Data (full edit), and GST
--     Approval. The four existing values (gate_in, migo_103, migo_105,
--     miro) are unchanged.
--   * The old "all" sentinel is retired for regular users (the "All
--     Steps" checkbox is removed from User Management) now that
--     SuperAdmin exists as the real "sees everything" tier. Existing
--     users who had step_roles = 'all' are migrated to hold all five
--     concrete roles explicitly, so nobody silently loses access they
--     had yesterday.
--   * A user can hold multiple roles at once (unchanged) — e.g.
--     "gate_in,migo_103" is valid for someone doing double duty.
-- ============================================================


-- ============================================================
-- 1. New column: admin_edit
--    Defaults to TRUE so existing Admin accounts (about to be renamed
--    to SuperAdmin below) keep full edit rights on cutover — nobody's
--    access silently narrows on deploy day.
-- ============================================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_edit BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN users.admin_edit IS
    'SuperAdmin only. TRUE = can edit/act on every tab + manage users. FALSE = view-only across every tab, cannot edit or act anywhere (including User Management itself). Ignored for role = ''User''.';


-- ============================================================
-- 2. Rename role 'Admin' -> 'SuperAdmin'
--    Existing Admin accounts become SuperAdmin with admin_edit = TRUE
--    (full power), matching what they could already do before this
--    migration — this is a rename, not a capability change.
-- ============================================================
UPDATE users SET role = 'SuperAdmin' WHERE role = 'Admin';


-- ============================================================
-- 3. Migrate the 'all' step_roles sentinel for ordinary users.
--    Now that SuperAdmin is the real "sees everything" tier, a plain
--    User should never be relying on the 'all' sentinel going forward
--    (the checkbox that produced it is being removed from the UI).
--    Expand it to the five concrete roles so existing users keep
--    exactly the access they have today.
-- ============================================================
UPDATE users
SET step_roles = 'compliance,gate_in,migo_103,migo_105,miro'
WHERE role = 'User'
  AND (step_roles IS NULL OR TRIM(LOWER(step_roles)) IN ('', 'all'));


-- ============================================================
-- Verify
-- ============================================================
SELECT 'schema_migration_v10 applied successfully' AS result;
SELECT username, role, admin_edit, step_roles FROM users ORDER BY role, username;
