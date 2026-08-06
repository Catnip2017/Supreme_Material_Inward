-- ============================================================
-- diagnose_supreme_ai_users.sql — one-off read-only look at
-- supreme_ai.users to figure out the right filter for
-- ldap_bulk_import_from_supreme_ai.sql (the first attempt used
-- status = '1' and only returned 7 of ~350 expected rows).
--
-- Command (run directly against supreme_ai, not material_inward):
--   psql -h localhost -U postgres -d supreme_ai -f database\diagnose_supreme_ai_users.sql
--
-- Read-only. Does not touch material_inward at all.
-- ============================================================

-- Total rows in the table
SELECT COUNT(*) AS total_rows FROM users;

-- How many have a usable, non-blank username
SELECT COUNT(*) AS rows_with_username
FROM users
WHERE user_name IS NOT NULL AND user_name <> '';

-- Breakdown of every distinct status value and how many rows have it
SELECT status, COUNT(*) AS row_count
FROM users
GROUP BY status
ORDER BY row_count DESC;

-- Breakdown of every distinct user_type value and how many rows have it
SELECT user_type, COUNT(*) AS row_count
FROM users
GROUP BY user_type
ORDER BY row_count DESC;

-- Sample of 20 rows so we can see actual values side by side
SELECT name, user_name, user_type, status
FROM users
ORDER BY name
LIMIT 20;
