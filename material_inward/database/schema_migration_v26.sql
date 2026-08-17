-- ============================================================
-- schema_migration_v26.sql — adds a Category classification to history,
-- captured on the Extracted Data tab and used to pre-fill Gate In's
-- existing "Category" dropdown (Material Details section).
--
-- BACKGROUND (2026-08-13 client discussion):
--   Gate In's Category field (A-Stores / B-Tankfarm / C-Scrap /
--   D-Despatch / E-Sales Return / F-Job Work / G-Despatch Scrap) has
--   always been picked manually by Gate Security with no default, every
--   time. A new, simpler 3-option picker (Stores / Sales Return / Job
--   Work) is added to the top of Extracted Data -- compulsory, defaults
--   to "Stores", and unlike goods_delivery_mode/ewb_exemption_reasons
--   (schema_migration_v13.sql) it is NOT write-once -- Compliance can
--   change it at any time while the record is still editable.
--
--   Gate In's Category dropdown still shows all 7 original options and
--   stays a normal editable field -- this value only supplies the
--   PRESELECTED default (mapped stores->A, sales_return->E,
--   job_work->F) the first time Gate In loads for a record that hasn't
--   already saved its own category. Manually changing it in Gate In
--   after that is allowed, gated by a confirm prompt (app.py /
--   templates/tabs/gate_in.html handle the mapping + confirm; nothing
--   about that lives in the DB layer).
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v26.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS.
-- ============================================================

ALTER TABLE history ADD COLUMN IF NOT EXISTS category VARCHAR(20) DEFAULT 'stores';
ALTER TABLE history ADD COLUMN IF NOT EXISTS category_by VARCHAR(255);
ALTER TABLE history ADD COLUMN IF NOT EXISTS category_at TIMESTAMP;

SELECT 'schema_migration_v26 applied — history.category added.' AS result;
