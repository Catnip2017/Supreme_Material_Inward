-- ============================================================
-- MATERIAL INWARD PROCESS — PostgreSQL Schema
-- Run this file once to create all tables in your database.
-- Command: psql -U your_db_user -d material_inward -f schema.sql
-- ============================================================

-- Enable UUID extension (optional, for future use)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- USERS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(255) UNIQUE NOT NULL,
    password    VARCHAR(255) NOT NULL,          -- bcrypt hashed
    role        VARCHAR(50)  NOT NULL,           -- Admin / User
    name        VARCHAR(255) NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- HISTORY TABLE — Master record for each material inward event
-- One row per material inward. All other tables FK to this.
-- ============================================================
CREATE TABLE IF NOT EXISTS history (
    id                      SERIAL PRIMARY KEY,
    invoice_number          TEXT,
    ewaybill_number         TEXT,
    lr_number               TEXT,
    po_number               TEXT,                   -- from subject line parsing
    mail_subject            TEXT,                   -- original mail subject
    mail_received_at        TIMESTAMP,              -- when the mail was picked up

    -- Workflow status flags
    gate_in                 SMALLINT DEFAULT 0,     -- 0=pending, 1=done
    migo_103                SMALLINT DEFAULT 0,
    migo_105                SMALLINT DEFAULT 0,
    miro                    SMALLINT DEFAULT 0,

    -- Generated SAP numbers
    gate_in_number          TEXT,                   -- GIN from SAP after Gate In RF
    material_doc_number     TEXT,                   -- Material doc from MIGO 103 RF
    bill_number             TEXT,                   -- Invoice number used as MIRO ref

    -- Approval timestamps
    gatein_done_at          TIMESTAMP,
    migo_103_done_at        TIMESTAMP,
    migo_105_done_at        TIMESTAMP,
    miro_done_at            TIMESTAMP,

    -- Record locking (prevents concurrent edits)
    locked_by               VARCHAR(255),           -- username who has record open
    locked_at               TIMESTAMP,

    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- INVOICE DATA TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS invoice_data (
    id                      INTEGER PRIMARY KEY REFERENCES history(id) ON DELETE CASCADE,
    filename                TEXT,
    invoice_number          TEXT,
    invoice_date            TEXT,
    po_number               TEXT,
    buyer_name              TEXT,
    buyer_address           TEXT,
    buyer_gstin             TEXT,
    ship_to_name            TEXT,
    ship_to_address         TEXT,
    ship_to_state           TEXT,
    ship_to_code            TEXT,
    bill_to_state           TEXT,
    bill_to_code            TEXT,
    seller_name             TEXT,
    seller_address          TEXT,
    seller_gstin            TEXT,
    company_pan             TEXT,
    payment_terms           TEXT,
    amount_in_words         TEXT,
    total_taxable_amount    TEXT,
    cgst_rate               TEXT,
    cgst_amount             TEXT,
    sgst_rate               TEXT,
    sgst_amount             TEXT,
    igst_rate               TEXT,
    igst_amount             TEXT,
    total_tax_amount        TEXT,
    total_amount            TEXT,
    grand_total             TEXT,
    hsn_details             JSONB,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- EWAYBILL DATA TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS ewaybill_data (
    id                      INTEGER PRIMARY KEY REFERENCES history(id) ON DELETE CASCADE,
    filename                TEXT,
    ewaybill_number         TEXT,
    generated_date          TEXT,
    validity_date           TEXT,
    invoice_number          TEXT,
    invoice_date            TEXT,
    po_number               TEXT,
    goods_description       TEXT,
    hsn_code                TEXT,
    quantity                TEXT,
    value_of_goods          TEXT,
    dispatch_from           TEXT,
    dispatch_to             TEXT,
    total_taxable_amount    TEXT,
    total_invoice_amount    TEXT,
    transport_mode          TEXT,
    vehicle_number          TEXT,
    transporter_name        TEXT,
    transporter_gstin       TEXT,
    transport_doc_no        TEXT,
    transport_doc_date      TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- LR DATA TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS lr_data (
    id                      INTEGER PRIMARY KEY REFERENCES history(id) ON DELETE CASCADE,
    filename                TEXT,
    lr_number               TEXT,
    lr_date                 TEXT,
    consignor_name          TEXT,
    consignee_name          TEXT,
    vehicle_number          TEXT,
    material_description    TEXT,
    quantity                TEXT,
    weight                  TEXT,
    delivery_address        TEXT,
    from_location           TEXT,
    to_location             TEXT,
    transporter_name        TEXT,
    freight_amount          TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- GATE IN ENTRIES TABLE
-- Stores all Gate In form fields + generated GIN number
-- ============================================================
CREATE TABLE IF NOT EXISTS gate_in_entries (
    id                      SERIAL PRIMARY KEY,
    history_id              INTEGER UNIQUE NOT NULL REFERENCES history(id) ON DELETE CASCADE,

    -- Form fields (pre-filled from OCR, editable by user)
    gate_in_date            TEXT,
    gate_in_time            TEXT,
    vendor_name             TEXT,
    transporter             TEXT,
    truck_no                TEXT,
    driver_name             TEXT,
    license_no              TEXT,
    num_persons             TEXT,
    container_no            TEXT,
    category                TEXT,
    material                TEXT,
    challan_no              TEXT,
    challan_qty             TEXT,
    boe_no                  TEXT,
    purchase_order          TEXT,
    gate_pass_no            TEXT,
    note                    TEXT,
    weight_option           TEXT,

    -- Generated by SAP after RF execution
    gate_in_number          TEXT,

    -- RF execution status
    rf_status               VARCHAR(50) DEFAULT 'pending',  -- pending / success / failed
    rf_error_message        TEXT,
    rf_executed_at          TIMESTAMP,

    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- MIGO ENTRIES TABLE
-- Covers both MIGO 103 and MIGO 105 in one table
-- 103 fields and 105 fields are separate columns
-- ============================================================
CREATE TABLE IF NOT EXISTS migo_entries (
    id                      SERIAL PRIMARY KEY,
    history_id              INTEGER UNIQUE NOT NULL REFERENCES history(id) ON DELETE CASCADE,

    -- === MIGO 103 fields ===
    migo_doc_date           TEXT,               -- Document date (from invoice date)
    migo_post_date          TEXT,               -- Posting date (current date)
    migo_delivery_note      TEXT,               -- Invoice no / challan num
    migo_bill_of_lading     TEXT,               -- LR no / LR name
    migo_gr_slip_no         TEXT,               -- BOE number
    migo_header_text        TEXT,               -- Gate In number goes here
    migo_remarks            TEXT,               -- Manually filled by user
    items_data              JSONB,              -- Line items from invoice HSN details

    -- Generated by SAP after MIGO 103 RF execution
    material_doc_number     TEXT,               -- Captured from SAP status bar after 103

    migo_103_rf_status      VARCHAR(50) DEFAULT 'pending',
    migo_103_rf_error       TEXT,
    migo_103_executed_at    TIMESTAMP,

    -- === MIGO 105 fields ===
    -- material_doc_number is shared (pre-filled from 103 result above)
    migo_105_storage_loc    TEXT,               -- Storage location (user fills manually)
    migo_105_batch          TEXT,               -- Vendor batch number (user fills)
    migo_105_vendor_invoice TEXT,               -- Invoice amount total (from invoice_data)
    migo_105_remarks        TEXT,               -- Additional remarks for 105

    migo_105_rf_status      VARCHAR(50) DEFAULT 'pending',
    migo_105_rf_error       TEXT,
    migo_105_executed_at    TIMESTAMP,

    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- MIRO ENTRIES TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS miro_entries (
    id                      SERIAL PRIMARY KEY,
    history_id              INTEGER UNIQUE NOT NULL REFERENCES history(id) ON DELETE CASCADE,

    -- Form fields
    miro_transaction        TEXT DEFAULT '1',   -- 1 = Invoice
    miro_diff_posting       TEXT,
    miro_invoice_date       TEXT,
    miro_posting_date       TEXT,               -- Always current date
    miro_reference          TEXT,               -- Invoice number (bill number)
    miro_amount             TEXT,               -- Grand total from invoice
    miro_tax_amount         TEXT,
    miro_tax_code           TEXT,
    miro_text               TEXT,
    miro_purchase_order     TEXT,
    items_data              JSONB,

    -- RF execution status
    rf_status               VARCHAR(50) DEFAULT 'pending',
    rf_error_message        TEXT,
    rf_executed_at          TIMESTAMP,

    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- ============================================================
-- STORAGE LOCATIONS MASTER TABLE (Admin managed, Plant 1010)
-- ============================================================
CREATE TABLE IF NOT EXISTS storage_locations (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(10) UNIQUE NOT NULL,
    description VARCHAR(100) NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO storage_locations (code, description) VALUES
    ('1010', 'Central Stores'),
    ('1011', 'Project Stores'),
    ('1012', 'Engg Yard'),
    ('1013', 'Old Kitchen'),
    ('1014', 'Open Yard'),
    ('1015', 'N.Tanker UL Area'),
    ('1016', 'Cylindersheld XPS'),
    ('1017', 'Initiator Shed'),
    ('1018', 'Open Yard T/C'),
    ('1019', 'Scrap Yard (Bill)'),
    ('502A', 'SM(B)-02FBS02A'),
    ('CFTA', 'Capital Asset'),
    ('LITS', 'Lost in Transit'),
    ('OBSS', 'Obsolete Str&Spa'),
    ('PROJ', 'Project Store 1'),
    ('PYRD', 'Project Yard'),
    ('RECD', 'Recondition'),
    ('REJR', 'Rejectilretn.'),
    ('SCRP', 'Scrap'),
    ('WDIF', 'Weighment Differ'),
    ('ZSIT', 'Stock in Transit')
ON CONFLICT (code) DO NOTHING;

-- RF EXECUTION QUEUE TABLE
-- Ensures only one RF script runs at a time.
-- Flask background worker picks next pending job and executes it.
-- ============================================================
CREATE TABLE IF NOT EXISTS rf_queue (
    id              SERIAL PRIMARY KEY,
    history_id      INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    step            VARCHAR(20) NOT NULL,   -- gate_in / migo_103 / migo_105 / miro
    status          VARCHAR(20) DEFAULT 'pending', -- pending / running / done / failed
    payload         JSONB,                  -- form data passed to RF script
    result          JSONB,                  -- result returned from RF script
    error_message   TEXT,
    attempts        INTEGER DEFAULT 0,
    queued_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rf_queue_status   ON rf_queue(status);
CREATE INDEX IF NOT EXISTS idx_rf_queue_history  ON rf_queue(history_id);

-- ============================================================
-- INDEXES for performance
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_history_invoice_number    ON history(invoice_number);
CREATE INDEX IF NOT EXISTS idx_history_po_number         ON history(po_number);
CREATE INDEX IF NOT EXISTS idx_history_created_at        ON history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_locked_by         ON history(locked_by);
CREATE INDEX IF NOT EXISTS idx_gate_in_history_id        ON gate_in_entries(history_id);
CREATE INDEX IF NOT EXISTS idx_migo_history_id           ON migo_entries(history_id);
CREATE INDEX IF NOT EXISTS idx_miro_history_id           ON miro_entries(history_id);
CREATE INDEX IF NOT EXISTS idx_invoice_number            ON invoice_data(invoice_number);

-- ============================================================
-- Default admin user (change password immediately after setup)
-- Password: Admin@123 (bcrypt hashed)
-- ============================================================
INSERT INTO users (username, password, role, name)
VALUES (
    'admin@catnip.com',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMqJqhsVV6YRQZQ1b8fKgHlR9.',
    'Admin',
    'Administrator'
)
ON CONFLICT (username) DO NOTHING;
