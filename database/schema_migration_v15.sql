-- ============================================================
-- schema_migration_v15.sql — LDAP login support for Material Inward's
-- own users table.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v15.sql
--
-- Safe to re-run: uses ADD COLUMN IF NOT EXISTS / conditional ALTER.
--
-- BACKGROUND:
--   Users used to be created exclusively with a bcrypt-hashed local
--   password, managed entirely inside this app (User Management tab).
--   Going forward, most real users authenticate against the client's
--   Active Directory instead (services/ldap_auth.py) -- a SuperAdmin
--   creates a username-only row (no password at all) via User
--   Management, and the actual credential check happens against AD at
--   login time. A handful of existing local/test accounts are kept
--   working exactly as before, for testing -- auth_type distinguishes
--   the two. This mirrors the same auth_type split already used by the
--   Ecosystem Dashboard's own user table for the same reason.
--
--   Material Inward does NOT read the client's supreme_ai database for
--   any of this (deliberately, per client decision) -- this table is
--   its own standalone source of truth for who's allowed to log in;
--   LDAP is used purely to verify a password, nothing else.
-- ============================================================

-- 1. auth_type: 'local' (existing bcrypt-hashed accounts, default -- kept
--    for testing) or 'ldap' (no local password stored at all, verified
--    against AD on every login).
ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_type VARCHAR(10) NOT NULL DEFAULT 'local';

-- 2. password becomes optional -- an 'ldap' row genuinely has none to store.
ALTER TABLE users ALTER COLUMN password DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_users_auth_type ON users(auth_type);

SELECT 'schema_migration_v15 applied — users.auth_type added, password now nullable.' AS result;
