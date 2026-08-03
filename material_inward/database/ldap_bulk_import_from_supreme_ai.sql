-- ============================================================
-- ldap_bulk_import_from_supreme_ai.sql — One-time bulk copy of active
-- usernames from the client's supreme_ai database into Material
-- Inward's own users table, as unprovisioned auth_type='ldap' rows.
--
-- Command (run from the material_inward app root):
--   psql -h localhost -U postgres -d material_inward -f database\ldap_bulk_import_from_supreme_ai.sql
--
-- REQUIRES schema_migration_v15.sql to have already run (adds
-- users.auth_type and makes users.password nullable -- this script
-- inserts NULL passwords for every row, which fails on v14-or-earlier
-- schema).
--
-- Safe to re-run: username has a UNIQUE constraint (see schema.sql),
-- so ON CONFLICT DO NOTHING skips anyone already present rather than
-- erroring or duplicating.
--
-- v2: dropped the status = '1' filter from the first version of this
-- script. Checked supreme_ai.users directly (see
-- diagnose_supreme_ai_users.sql) -- status=0 is the default for 346 of
-- 354 rows, status=1 for only 8, and neither status nor user_type
-- means anything to Material Inward's own access control (that's
-- entirely role/step_roles in this table, assigned separately by a
-- SuperAdmin -- see IMPORTANT note below). So there's no reason to
-- filter on either column here -- pull every row with a real
-- username, let the "No Roles Assigned" gate handle the rest.
--
-- IMPORTANT: every row lands with role='User' and step_roles=''  --
-- i.e. NO real access. A freshly-copied user hits the "No Roles
-- Assigned" block page (see app.py _no_roles_assigned()) the moment
-- they try to log in, until a SuperAdmin manually assigns real
-- step_roles via User Management. Bulk-copying usernames must never
-- bulk-grant access.
--
-- This is a one-time, manually-run administrative copy -- Material
-- Inward's application code itself never queries supreme_ai at
-- runtime (see services/ldap_auth.py), by explicit client decision.
-- Running this script does not change that; it's a DBA action outside
-- the app, same category as the schema migrations.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS dblink;

INSERT INTO users (username, password, role, name, auth_type, step_roles)
SELECT user_name, NULL, 'User', COALESCE(name, user_name), 'ldap', ''
FROM dblink(
  'host=localhost port=5432 dbname=supreme_ai user=postgres password=Supreme@2026',
  'SELECT user_name, name FROM users WHERE user_name IS NOT NULL AND user_name <> '''''
) AS src(user_name text, name text)
ON CONFLICT (username) DO NOTHING;

SELECT username, name, role, step_roles, auth_type
FROM users
WHERE auth_type = 'ldap'
ORDER BY name;
