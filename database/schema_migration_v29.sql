-- ============================================================
-- schema_migration_v29.sql — MIGO 101 (Goods Receipt against an
-- Outbound Delivery).
--
-- Follows the exact same pattern already used for MIGO 105 in this
-- schema: the "headline" flag/timestamp/generated-doc-number columns
-- go on history (matching gate_in/migo_103/migo_105/miro), and the
-- step's own header fields + per-line item data + RF status/error/
-- executed_at go on migo_entries as their own migo_101_-prefixed
-- columns (matching migo_105_storage_loc/migo_105_batch/etc.).
--
-- IMPORTANT: migo_101_items_data is DELIBERATELY its own separate
-- JSONB column, NOT a reuse of migo_entries.items_data. That shared
-- items_data column is already load-bearing for MIGO 103 (OCR-derived
-- line items, written by upsert_migo_entry/map_ocr_to_migo) and MIGO
-- 105 (per-line batch patched onto the SAME rows by
-- update_migo_105_items_with_batches). MIGO 101 does its own
-- independent PO-line matching (confirmed pairs can legitimately
-- differ from 103's -- e.g. a partial delivery), so writing into the
-- shared items_data would silently corrupt 103/105's already-posted
-- data for this record. Keeping it separate costs one extra column
-- and avoids that entirely.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v29.sql
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS.
-- ============================================================

-- --- history: headline flag/timestamp/doc-number, same shape as
-- --- gate_in/migo_103/migo_105/miro ---
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_101             SMALLINT DEFAULT 0;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_101_done_at     TIMESTAMP;
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_101_doc_number  TEXT;

-- --- migo_entries: MIGO 101's own header fields, its own separate
-- --- items/matching JSONB, and its own RF status/error/executed_at
-- --- (same shape as the existing migo_105_* columns) ---
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_outbound_delivery  TEXT;               -- outbound delivery number entered/confirmed
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_doc_date           TEXT;               -- Document date, same TEXT convention as migo_doc_date (MIGO 103's own field)
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_post_date          TEXT;               -- Posting date, same TEXT convention as migo_post_date (MIGO 103's own field)
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_bill_of_lading     TEXT;               -- separate from migo_bill_of_lading (that one is MIGO 103's own field)
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_gr_slip_no        TEXT;               -- separate from migo_gr_slip_no (MIGO 103's own field); manual entry only, no auto-population
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_header_text       TEXT;               -- separate from migo_header_text (MIGO 103's own field)
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_storage_loc       TEXT;
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_vendor_invoice    TEXT;
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_remarks           TEXT;
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_items_data        JSONB;              -- 101's own confirmed/matched line items + per-line batch -- see note above, NOT the shared items_data
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_rf_status         VARCHAR(50) DEFAULT 'pending';
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_rf_error          TEXT;
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo_101_executed_at       TIMESTAMP;

-- --- migo101_link follow-up job (ADDED 2026-09-17): same shape and same
-- --- "migoNNN_link_*" naming convention (no underscore between "migo"
-- --- and the number) as the existing migo103_link_*/migo105_link_*
-- --- columns from schema_migration_v18.sql -- kept naming-consistent
-- --- with those siblings rather than with this file's own migo_101_*
-- --- fields above, since this is the same DMS-link-attach mechanism,
-- --- not one of MIGO 101's own posting fields. See
-- --- database/migo_operations.py's update_migo101_link_result() and
-- --- services/rf_queue_worker.py's _process_migo101_link(). ---
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo101_link_status       VARCHAR(50) DEFAULT 'pending';
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo101_link_error        TEXT;
ALTER TABLE migo_entries ADD COLUMN IF NOT EXISTS migo101_link_executed_at  TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_migo_entries_migo101_link_status ON migo_entries(migo101_link_status);

-- rf_queue.step is a plain VARCHAR with no CHECK constraint -- 'migo_101'
-- and 'migo101_link' are new valid values alongside gate_in / po_fetch /
-- migo_103 / migo_105 / miro / migo_103_reversal / update_gatein_po /
-- migo103_link / migo105_link / miro_link / zgrn_print. Documenting here
-- only, no schema change needed.

-- admin_action_log.action is also a plain VARCHAR(50) with no CHECK
-- constraint -- 'reset_migo_101' is a new valid value alongside
-- reset_gate_in / reset_migo_103 / reset_migo_105 / reset_miro / etc.
-- Documenting here only, no schema change needed.

SELECT 'schema_migration_v29 applied — MIGO 101 columns added to history + migo_entries.' AS result;
