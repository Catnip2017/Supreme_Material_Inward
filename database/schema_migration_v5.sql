-- ============================================================
-- schema_migration_v5.sql
-- Run ONCE on the existing database.
--
-- Command:
--   psql -U material_user -d material_inward -f database\schema_migration_v5.sql
-- ============================================================

-- ============================================================
-- VEHICLE MASTER TABLE
-- 4 columns matching actual Excel: truck_number, driver_name,
-- transporter_name, licence_number
-- One truck can have multiple rows (multiple drivers)
-- ============================================================
CREATE TABLE IF NOT EXISTS vehicle_master (
    id                  SERIAL PRIMARY KEY,
    truck_number        VARCHAR(30)  NOT NULL,
    driver_name         VARCHAR(255),
    transporter_name    VARCHAR(255),
    licence_number      VARCHAR(60),
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_vehicle_driver UNIQUE (truck_number, driver_name)
);

CREATE INDEX IF NOT EXISTS idx_vehicle_truck_number ON vehicle_master(truck_number);

-- ============================================================
-- HISTORY TABLE — new columns
-- ============================================================

-- Delivery type + PO availability combined into one field
-- Values: truck_with_po | truck_without_po | hand_with_po | hand_without_po
ALTER TABLE history ADD COLUMN IF NOT EXISTS po_flow_type VARCHAR(30) DEFAULT 'truck_with_po';

-- DMS tracking (operational only, no UI)
ALTER TABLE history ADD COLUMN IF NOT EXISTS dms_status            VARCHAR(20) DEFAULT NULL;
ALTER TABLE history ADD COLUMN IF NOT EXISTS dms_staged_at         TIMESTAMP;
ALTER TABLE history ADD COLUMN IF NOT EXISTS consolidated_doc_path TEXT;

-- Document numbers that were missing from history table
-- migo_105_doc_number: the number from SAP status bar after MIGO 105
--   (same number passed into MIRO as reference — hence MIRO_DOC_NUMBER
--    in robot, but stored here by its origin step)
-- miro_fi_doc_number: FI document number from SAP after MIRO posts
ALTER TABLE history ADD COLUMN IF NOT EXISTS migo_105_doc_number TEXT;
ALTER TABLE history ADD COLUMN IF NOT EXISTS miro_fi_doc_number  TEXT;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_history_dms_status ON history(dms_status);
CREATE INDEX IF NOT EXISTS idx_history_po_flow    ON history(po_flow_type);

SELECT 'schema_migration_v5 applied successfully' AS result;

-- If vehicle_master already exists from an earlier run, add the unique constraint:
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_vehicle_driver'
    ) THEN
        ALTER TABLE vehicle_master
            ADD CONSTRAINT uq_vehicle_driver UNIQUE (truck_number, driver_name);
    END IF;
END$$;
