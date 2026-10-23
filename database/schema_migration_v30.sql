-- Tax Classification tab (client request, 2026-09-23): a single mandatory
-- compliance attestation that must be set before MIRO can proceed (see
-- app.py's _check_step_allowed(), "miro" branch). Mutually exclusive by
-- design -- the CHECK constraint below only allows 'eligible' or
-- 'ineligible' (or NULL, meaning "not yet classified"), matching the
-- "single classification decision" shape confirmed for this feature,
-- rather than two independent yes/no flags that could both end up set or
-- both unset.
--
-- No dependency on any other tab/step (client-confirmed -- same "not
-- dependent on any other tabs" shape MIGO 101 already has): Compliance
-- can set this any time relative to Gate In/MIGO 101/103/105. Only MIRO
-- itself is blocked until it's set.
--
-- Stored directly on history (not a separate table) since it's a single
-- value per record with no repeating rows -- same shape as the existing
-- gst_check / migo_103 / migo_105 flag columns already on this table.

ALTER TABLE history ADD COLUMN IF NOT EXISTS tax_classification VARCHAR(20)
    CHECK (tax_classification IN ('eligible', 'ineligible'));
ALTER TABLE history ADD COLUMN IF NOT EXISTS tax_classification_marked_by VARCHAR(100);
ALTER TABLE history ADD COLUMN IF NOT EXISTS tax_classification_marked_at TIMESTAMP;
