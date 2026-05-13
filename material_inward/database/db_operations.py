"""
database/db_operations.py — Core database operations for history and documents.
All functions use the shared connection pool.

v4 changes:
- Removed: lock_record, unlock_record, unlock_stale_locks
- Added: set_approval_status, set_hold_status, set_ocr_status,
        increment_ocr_retry, get_ocr_failed_path
"""

import json
from datetime import datetime
from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# HISTORY — CREATE / READ / UPDATE
# ============================================================

def create_history_record(
    invoice_number: Optional[str] = None,
    ewaybill_number: Optional[str] = None,
    lr_number: Optional[str] = None,
    po_number: Optional[str] = None,
    mail_subject: Optional[str] = None,
    mail_received_at: Optional[datetime] = None
) -> Optional[int]:
    sql = """
        INSERT INTO history (
            invoice_number, ewaybill_number, lr_number,
            po_number, mail_subject, mail_received_at
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    invoice_number, ewaybill_number, lr_number,
                    po_number, mail_subject, mail_received_at
                ))
                history_id = cur.fetchone()[0]
                logger.info(f"Created history record ID: {history_id}")
                return history_id
    except Exception as e:
        logger.error(f"Failed to create history record: {e}")
        return None


def get_history_by_id(history_id: int) -> Optional[dict]:
    sql = "SELECT * FROM history WHERE id = %s"
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (history_id,))
                result = cur.fetchone()
                return dict(result) if result else None
    except Exception as e:
        logger.error(f"Failed to fetch history ID {history_id}: {e}")
        return None


def get_history_details_by_id(history_id: int) -> dict:
    result = {
        "history": None,
        "invoice_data": None,
        "ewaybill_data": None,
        "lr_data": None
    }
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM history WHERE id = %s", (history_id,))
                h = cur.fetchone()
                result["history"] = dict(h) if h else None

                cur.execute("SELECT * FROM invoice_data WHERE id = %s", (history_id,))
                inv = cur.fetchone()
                if inv:
                    inv = dict(inv)
                    if inv.get("hsn_details") and isinstance(inv["hsn_details"], str):
                        try:
                            inv["hsn_details"] = json.loads(inv["hsn_details"])
                        except Exception:
                            inv["hsn_details"] = []
                    result["invoice_data"] = inv

                cur.execute("SELECT * FROM ewaybill_data WHERE id = %s", (history_id,))
                eway = cur.fetchone()
                result["ewaybill_data"] = dict(eway) if eway else None

                cur.execute("SELECT * FROM lr_data WHERE id = %s", (history_id,))
                lr = cur.fetchone()
                result["lr_data"] = dict(lr) if lr else None

    except Exception as e:
        logger.error(f"Failed to fetch history details for ID {history_id}: {e}")
    return result


def get_all_history() -> list:
    sql = """
        SELECT
            h.id,
            COALESCE(h.invoice_number, inv.invoice_number)   AS invoice_number,
            COALESCE(h.ewaybill_number, eway.ewaybill_number) AS ewaybill_number,
            COALESCE(h.lr_number, lr.lr_number)              AS lr_number,
            COALESCE(h.po_number, inv.po_number)             AS po_number,
            h.mail_subject,
            h.gate_in,
            h.migo_103,
            h.migo_105,
            h.miro,
            h.gate_in_number,
            h.material_doc_number,
            h.approval_status,
            h.approval_by,
            h.approval_at,
            h.hold_reason,
            h.ocr_status,
            h.ocr_retry_count,
            h.created_at,
            h.mail_received_at,
            CASE
                WHEN h.miro = 1     THEN 'MIRO Done'
                WHEN h.migo_105 = 1 THEN 'MIGO 105 Done'
                WHEN h.migo_103 = 1 THEN 'MIGO 103 Done'
                WHEN h.gate_in = 1  THEN 'Gate In Done'
                ELSE 'Pending'
            END AS status
        FROM history h
        LEFT JOIN invoice_data  inv  ON inv.id  = h.id
        LEFT JOIN ewaybill_data eway ON eway.id = h.id
        LEFT JOIN lr_data       lr   ON lr.id   = h.id
        ORDER BY h.created_at DESC
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Failed to fetch all history: {e}")
        return []


def update_history_step(
    history_id: int,
    step: str,
    generated_number: Optional[str] = None
) -> bool:
    now = datetime.now()
    valid_steps = {
        "gate_in":   ("gate_in",  "gatein_done_at",   "gate_in_number"),
        "migo_103":  ("migo_103", "migo_103_done_at", "material_doc_number"),
        "migo_105":  ("migo_105", "migo_105_done_at", None),
        "miro":      ("miro",     "miro_done_at",     None),
    }
    if step not in valid_steps:
        logger.error(f"Invalid step '{step}' passed to update_history_step")
        return False

    flag_col, time_col, num_col = valid_steps[step]

    if generated_number and num_col:
        sql = f"""
            UPDATE history
            SET {flag_col} = 1, {time_col} = %s, {num_col} = %s, updated_at = %s
            WHERE id = %s
        """
        params = (now, generated_number, now, history_id)
    else:
        sql = f"""
            UPDATE history
            SET {flag_col} = 1, {time_col} = %s, updated_at = %s
            WHERE id = %s
        """
        params = (now, now, history_id)

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                logger.info(f"History {history_id} step '{step}' marked done.")
                return True
    except Exception as e:
        logger.error(f"Failed to update history step '{step}' for ID {history_id}: {e}")
        return False


# ============================================================
# APPROVAL WORKFLOW
# ============================================================

def set_approval_status(history_id: int, approved_by: str) -> bool:
    """Mark a record as approved by a user."""
    sql = """
        UPDATE history
        SET approval_status = 'approved',
            approval_by     = %s,
            approval_at     = %s,
            hold_reason     = NULL,
            updated_at      = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (approved_by, datetime.now(), history_id))
                logger.info(f"Record {history_id} approved by {approved_by}")
                return True
    except Exception as e:
        logger.error(f"Failed to approve record {history_id}: {e}")
        return False


def set_hold_status(history_id: int, held_by: str, reason: str) -> bool:
    """Place a record on hold with a reason."""
    sql = """
        UPDATE history
        SET approval_status = 'hold',
            approval_by     = %s,
            approval_at     = %s,
            hold_reason     = %s,
            updated_at      = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (held_by, datetime.now(), reason, history_id))
                logger.info(f"Record {history_id} put on hold by {held_by}: {reason}")
                return True
    except Exception as e:
        logger.error(f"Failed to hold record {history_id}: {e}")
        return False


# ============================================================
# OCR STATUS TRACKING
# ============================================================

def set_ocr_status(history_id: int, status: str, failed_path: Optional[str] = None) -> bool:
    """Update OCR status: 'success' or 'failed'."""
    sql = """
        UPDATE history
        SET ocr_status      = %s,
            ocr_failed_path = %s,
            updated_at      = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (status, failed_path, history_id))
                logger.info(f"Record {history_id} ocr_status set to {status}")
                return True
    except Exception as e:
        logger.error(f"Failed to update ocr_status for {history_id}: {e}")
        return False


def increment_ocr_retry(history_id: int) -> int:
    """Increment ocr_retry_count, return new count."""
    sql = """
        UPDATE history
        SET ocr_retry_count = ocr_retry_count + 1,
            updated_at      = CURRENT_TIMESTAMP
        WHERE id = %s
        RETURNING ocr_retry_count
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (history_id,))
                count = cur.fetchone()[0]
                logger.info(f"Record {history_id} OCR retry count: {count}")
                return count
    except Exception as e:
        logger.error(f"Failed to increment retry count for {history_id}: {e}")
        return 0


def get_ocr_failed_path(history_id: int) -> Optional[str]:
    """Get the failed folder path for a record (used for retry)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ocr_failed_path FROM history WHERE id = %s", (history_id,))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.error(f"Failed to fetch ocr_failed_path for {history_id}: {e}")
        return None


# ============================================================
# INVOICE / EWAYBILL / LR — SAVE / UPDATE
# ============================================================

def save_invoice_to_db(history_id: int, data: dict) -> bool:
    hsn = data.get("hsn_details")
    if isinstance(hsn, (list, dict)):
        hsn = json.dumps(hsn)

    insert_sql = """
        INSERT INTO invoice_data (
            id, filename, invoice_number, invoice_date, po_number,
            buyer_name, buyer_address, buyer_gstin,
            ship_to_name, ship_to_address, ship_to_state, ship_to_code,
            bill_to_state, bill_to_code,
            seller_name, seller_address, seller_gstin,
            company_pan, payment_terms, amount_in_words,
            total_taxable_amount, cgst_rate, cgst_amount,
            sgst_rate, sgst_amount, igst_rate, igst_amount,
            total_tax_amount, total_amount, grand_total, hsn_details
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (id) DO UPDATE SET
            filename = EXCLUDED.filename,
            invoice_number = EXCLUDED.invoice_number,
            invoice_date = EXCLUDED.invoice_date,
            po_number = EXCLUDED.po_number,
            buyer_name = EXCLUDED.buyer_name,
            buyer_address = EXCLUDED.buyer_address,
            buyer_gstin = EXCLUDED.buyer_gstin,
            ship_to_name = EXCLUDED.ship_to_name,
            ship_to_address = EXCLUDED.ship_to_address,
            ship_to_state = EXCLUDED.ship_to_state,
            ship_to_code = EXCLUDED.ship_to_code,
            bill_to_state = EXCLUDED.bill_to_state,
            bill_to_code = EXCLUDED.bill_to_code,
            seller_name = EXCLUDED.seller_name,
            seller_address = EXCLUDED.seller_address,
            seller_gstin = EXCLUDED.seller_gstin,
            company_pan = EXCLUDED.company_pan,
            payment_terms = EXCLUDED.payment_terms,
            amount_in_words = EXCLUDED.amount_in_words,
            total_taxable_amount = EXCLUDED.total_taxable_amount,
            cgst_rate = EXCLUDED.cgst_rate,
            cgst_amount = EXCLUDED.cgst_amount,
            sgst_rate = EXCLUDED.sgst_rate,
            sgst_amount = EXCLUDED.sgst_amount,
            igst_rate = EXCLUDED.igst_rate,
            igst_amount = EXCLUDED.igst_amount,
            total_tax_amount = EXCLUDED.total_tax_amount,
            total_amount = EXCLUDED.total_amount,
            grand_total = EXCLUDED.grand_total,
            hsn_details = EXCLUDED.hsn_details,
            updated_at = CURRENT_TIMESTAMP
    """
    values = (
        history_id,
        data.get("filename"), data.get("invoice_number"), data.get("invoice_date"), data.get("po_number"),
        data.get("buyer_name"), data.get("buyer_address"), data.get("buyer_gstin"),
        data.get("ship_to_name"), data.get("ship_to_address"), data.get("ship_to_state"), data.get("ship_to_code"),
        data.get("bill_to_state"), data.get("bill_to_code"),
        data.get("seller_name"), data.get("seller_address"), data.get("seller_gstin"),
        data.get("company_pan"), data.get("payment_terms"), data.get("amount_in_words"),
        data.get("total_taxable_amount"), data.get("cgst_rate"), data.get("cgst_amount"),
        data.get("sgst_rate"), data.get("sgst_amount"), data.get("igst_rate"), data.get("igst_amount"),
        data.get("total_tax_amount"), data.get("total_amount"), data.get("grand_total"), hsn
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(insert_sql, values)
                logger.info(f"Invoice data saved for history_id {history_id}")
                return True
    except Exception as e:
        logger.error(f"Failed to save invoice for history_id {history_id}: {e}")
        return False


def save_ewaybill_to_db(history_id: int, data: dict) -> bool:
    sql = """
        INSERT INTO ewaybill_data (
            id, filename, ewaybill_number, generated_date, validity_date,
            invoice_number, invoice_date, po_number, goods_description, hsn_code,
            quantity, value_of_goods, dispatch_from, dispatch_to,
            total_taxable_amount, total_invoice_amount, transport_mode,
            vehicle_number, transporter_name, transporter_gstin,
            transport_doc_no, transport_doc_date
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s
        )
        ON CONFLICT (id) DO UPDATE SET
            filename = EXCLUDED.filename,
            ewaybill_number = EXCLUDED.ewaybill_number,
            generated_date = EXCLUDED.generated_date,
            validity_date = EXCLUDED.validity_date,
            invoice_number = EXCLUDED.invoice_number,
            invoice_date = EXCLUDED.invoice_date,
            po_number = EXCLUDED.po_number,
            goods_description = EXCLUDED.goods_description,
            hsn_code = EXCLUDED.hsn_code,
            quantity = EXCLUDED.quantity,
            value_of_goods = EXCLUDED.value_of_goods,
            dispatch_from = EXCLUDED.dispatch_from,
            dispatch_to = EXCLUDED.dispatch_to,
            total_taxable_amount = EXCLUDED.total_taxable_amount,
            total_invoice_amount = EXCLUDED.total_invoice_amount,
            transport_mode = EXCLUDED.transport_mode,
            vehicle_number = EXCLUDED.vehicle_number,
            transporter_name = EXCLUDED.transporter_name,
            transporter_gstin = EXCLUDED.transporter_gstin,
            transport_doc_no = EXCLUDED.transport_doc_no,
            transport_doc_date = EXCLUDED.transport_doc_date,
            updated_at = CURRENT_TIMESTAMP
    """
    values = (
        history_id, data.get("filename"), data.get("ewaybill_number"),
        data.get("generated_date"), data.get("validity_date"),
        data.get("invoice_number"), data.get("invoice_date"), data.get("po_number"),
        data.get("goods_description"), data.get("hsn_code"),
        data.get("quantity"), data.get("value_of_goods"),
        data.get("dispatch_from"), data.get("dispatch_to"),
        data.get("total_taxable_amount"), data.get("total_invoice_amount"),
        data.get("transport_mode"), data.get("vehicle_number"),
        data.get("transporter_name"), data.get("transporter_gstin"),
        data.get("transport_doc_no"), data.get("transport_doc_date")
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                logger.info(f"E-way Bill data saved for history_id {history_id}")
                return True
    except Exception as e:
        logger.error(f"Failed to save ewaybill for history_id {history_id}: {e}")
        return False


def save_lr_to_db(history_id: int, data: dict) -> bool:
    sql = """
        INSERT INTO lr_data (
            id, filename, lr_number, lr_date, consignor_name,
            consignee_name, vehicle_number, material_description,
            quantity, weight, delivery_address, from_location,
            to_location, transporter_name, freight_amount
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (id) DO UPDATE SET
            filename = EXCLUDED.filename,
            lr_number = EXCLUDED.lr_number,
            lr_date = EXCLUDED.lr_date,
            consignor_name = EXCLUDED.consignor_name,
            consignee_name = EXCLUDED.consignee_name,
            vehicle_number = EXCLUDED.vehicle_number,
            material_description = EXCLUDED.material_description,
            quantity = EXCLUDED.quantity,
            weight = EXCLUDED.weight,
            delivery_address = EXCLUDED.delivery_address,
            from_location = EXCLUDED.from_location,
            to_location = EXCLUDED.to_location,
            transporter_name = EXCLUDED.transporter_name,
            freight_amount = EXCLUDED.freight_amount,
            updated_at = CURRENT_TIMESTAMP
    """
    values = (
        history_id, data.get("filename"), data.get("lr_number"), data.get("lr_date"),
        data.get("consignor_name"), data.get("consignee_name"), data.get("vehicle_number"),
        data.get("material_description"), data.get("quantity"), data.get("weight"),
        data.get("delivery_address"), data.get("from_location"), data.get("to_location"),
        data.get("transporter_name"), data.get("freight_amount")
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                logger.info(f"LR data saved for history_id {history_id}")
                return True
    except Exception as e:
        logger.error(f"Failed to save LR for history_id {history_id}: {e}")
        return False


# ============================================================
# HISTORY SEARCH
# ============================================================

def get_history_search(
    search: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    per_page: int = 10
) -> dict:
    conditions = []
    params = []

    if search:
        conditions.append("""(
            COALESCE(inv.invoice_number, h.invoice_number, '') ILIKE %s OR
            COALESCE(eway.ewaybill_number, h.ewaybill_number, '') ILIKE %s OR
            COALESCE(lr.lr_number, h.lr_number, '') ILIKE %s OR
            COALESCE(inv.po_number, h.po_number, '') ILIKE %s OR
            COALESCE(h.gate_in_number, '') ILIKE %s
        )""")
        like = f"%{search}%"
        params.extend([like, like, like, like, like])

    if status == "pending":
        conditions.append("h.gate_in = 0")
    elif status == "in_progress":
        conditions.append("h.gate_in = 1 AND h.miro = 0")
    elif status == "completed":
        conditions.append("h.miro = 1")

    if date_from:
        conditions.append("h.created_at::date >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("h.created_at::date <= %s")
        params.append(date_to)

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    base_sql = f"""
        FROM history h
        LEFT JOIN invoice_data  inv  ON inv.id  = h.id
        LEFT JOIN ewaybill_data eway ON eway.id = h.id
        LEFT JOIN lr_data       lr   ON lr.id   = h.id
        {where_clause}
    """

    count_sql = f"SELECT COUNT(*) {base_sql}"
    data_sql = f"""
        SELECT
            h.id,
            COALESCE(h.invoice_number, inv.invoice_number)    AS invoice_number,
            COALESCE(h.ewaybill_number, eway.ewaybill_number) AS ewaybill_number,
            COALESCE(h.lr_number, lr.lr_number)               AS lr_number,
            COALESCE(h.po_number, inv.po_number)              AS po_number,
            h.gate_in, h.migo_103, h.migo_105, h.miro,
            h.gate_in_number, h.material_doc_number,
            h.approval_status, h.ocr_status,
            h.created_at,
            CASE
                WHEN h.miro = 1     THEN 'MIRO Done'
                WHEN h.migo_105 = 1 THEN 'MIGO 105 Done'
                WHEN h.migo_103 = 1 THEN 'MIGO 103 Done'
                WHEN h.gate_in = 1  THEN 'Gate In Done'
                ELSE 'Pending'
            END AS status
        {base_sql}
        ORDER BY h.created_at DESC
        LIMIT %s OFFSET %s
    """

    offset = (page - 1) * per_page

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(count_sql, params)
                total = cur.fetchone()["count"]

                cur.execute(data_sql, params + [per_page, offset])
                records = [dict(r) for r in cur.fetchall()]

                for r in records:
                    if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
                        r["created_at"] = r["created_at"].isoformat()

                return {
                    "records": records,
                    "total": total,
                    "page": page,
                    "per_page": per_page,
                    "total_pages": max(1, -(-total // per_page))
                }
    except Exception as e:
        logger.error(f"History search failed: {e}")
        return {"records": [], "total": 0, "page": 1, "per_page": per_page, "total_pages": 1}


# ============================================================
# TODAY'S COUNTS
# ============================================================

def get_today_counts() -> dict:
    sql = """
        SELECT
            COUNT(*)                                         AS total,
            COUNT(*) FILTER (WHERE gate_in = 0)              AS pending,
            COUNT(*) FILTER (WHERE gate_in = 1 AND miro = 0) AS in_progress,
            COUNT(*) FILTER (WHERE miro = 1)                 AS completed
        FROM history
        WHERE created_at::date = CURRENT_DATE
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                row = cur.fetchone()
                return dict(row) if row else {"total": 0, "pending": 0, "in_progress": 0, "completed": 0}
    except Exception as e:
        logger.error(f"Failed to get today counts: {e}")
        return {"total": 0, "pending": 0, "in_progress": 0, "completed": 0}