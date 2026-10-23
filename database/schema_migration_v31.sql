-- Tax Classification becomes a checklist (client request, 2026-10-08):
-- "Eligible / Verified by GST" and "Ineligible" can now BOTH be ticked on
-- the same record (e.g. an invoice with some eligible and some ineligible
-- lines), and at least one must be ticked before MIRO can proceed.
--
-- schema_migration_v30.sql made this column mutually exclusive with a CHECK
-- constraint that only allows 'eligible' or 'ineligible'. Both-ticked is
-- stored as the single value 'eligible,ineligible' (same column, no new
-- column, no data migration -- every existing 'eligible' / 'ineligible'
-- value stays valid as-is), so that constraint has to be widened.
--
-- RUN THIS ON PROD *BEFORE* deploying the new app.py / templates. Until it
-- is run, saving with BOTH boxes ticked fails ("DB update failed"); saving
-- with one box ticked keeps working either way.
--
-- Safe to re-run: it drops whatever CHECK constraint currently mentions
-- tax_classification (found by definition, not by guessing its auto-
-- generated name) and re-adds the widened one. Existing rows are not
-- touched.

DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'history'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%tax_classification%'
    LOOP
        EXECUTE format('ALTER TABLE history DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

ALTER TABLE history ADD CONSTRAINT history_tax_classification_check
    CHECK (tax_classification IN ('eligible', 'ineligible', 'eligible,ineligible'));
