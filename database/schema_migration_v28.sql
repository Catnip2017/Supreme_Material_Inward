-- ============================================================
-- schema_migration_v28.sql — Silent Hours Gate-In (walk-in entries).
--
-- BACKGROUND (2026-08-31, client request): outside business hours
-- (weekdays after 17:00 IST, or any time on Saturday/Sunday -- see
-- config.Config.is_silent_hours()), Gate Security is allowed to post a
-- Gate In directly, without the record having gone through Documents ->
-- OCR -> Extracted Data approval first. The truck's actual paperwork is
-- physically on hand and gets uploaded/OCR'd later, during business
-- hours, same as any other record from that point on.
--
-- History ID is only ever consumed once the guard actually clicks Save
-- (see app.py's /api/save_gatein_silent_hours) -- there is no earlier
-- "reserve a blank record" step, so a walk-in the guard never gets
-- around to starting never shows up as a stray row here at all.
--
-- silent_hours_entry is a PERMANENT origin marker, set once at Save and
-- never cleared -- per client instruction, every viewer of the History
-- tab should be able to see a small "this started as a Silent-Hours
-- entry" indicator on the record forever, regardless of how far it's
-- since progressed (that's a separate, transient "documents still
-- pending" status, computed at query time from migo_103/invoice/GST
-- state -- not stored here, see app.py's history-list query).
--
-- silent_hours_created_by / silent_hours_created_at record who saved it
-- and when, so /api/post_gatein_silent_hours can enforce "only the same
-- guard who saved this entry may post it to SAP" (client-approved --
-- Save and Post are done by the same person, moments apart).
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v28.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS.
-- ============================================================

ALTER TABLE history ADD COLUMN IF NOT EXISTS silent_hours_entry SMALLINT DEFAULT 0;
ALTER TABLE history ADD COLUMN IF NOT EXISTS silent_hours_created_by VARCHAR(255);
ALTER TABLE history ADD COLUMN IF NOT EXISTS silent_hours_created_at TIMESTAMP;

SELECT 'schema_migration_v28 applied — history.silent_hours_entry* added.' AS result;
