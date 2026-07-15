"""
database/migo_operations.py — MIGO 103 and MIGO 105 operations.

v4 changes:
- map_ocr_to_migo: drops 'material' and 'mat_short_text' (duplicates),
  uses single 'short_text' field
- qty_actual JSON key kept for robot compatibility (UI renames the column header only)
"""

import json
from datetime import datetime
from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


def upsert_migo_entry(history_id: int, data: dict) -> bool:
    items = data.get("items_data") or data.get("items") or []
    if isinstance(items, (list, dict)):
        items = json.dumps(items)

    sql = """
        INSERT INTO migo_entries (
            history_id, migo_po_number, migo_doc_date, migo_post_date,
            migo_delivery_note, migo_bill_of_lading, migo_gr_slip_no,
            migo_header_text, migo_remarks, items_data
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (history_id) DO UPDATE SET
            migo_po_number      = EXCLUDED.migo_po_number,
            migo_doc_date       = EXCLUDED.migo_doc_date,
            migo_post_date      = EXCLUDED.migo_post_date,
            migo_delivery_note  = EXCLUDED.migo_delivery_note,
            migo_bill_of_lading = EXCLUDED.migo_bill_of_lading,
            migo_gr_slip_no     = EXCLUDED.migo_gr_slip_no,
            migo_header_text    = EXCLUDED.migo_header_text,
            migo_remarks        = EXCLUDED.migo_remarks,
            items_data          = EXCLUDED.items_data,
            updated_at          = CURRENT_TIMESTAMP
    """
    values = (
        history_id,
        data.get("migoPoNumber") or data.get("migo_po_number") or data.get("purchaseOrder"),
        data.get("migoDocDate") or data.get("migo_doc_date"),
        data.get("migoPostDate") or data.get("migo_post_date") or datetime.now().strftime("%Y-%m-%d"),
        data.get("migoDeliveryNote") or data.get("migo_delivery_note"),
        data.get("migoBillOfLading") or data.get("migo_bill_of_lading"),
        data.get("migoGRSlipNo") or data.get("migo_gr_slip_no"),
        data.get("migoHeaderText") or data.get("migo_header_text"),
        data.get("migoRemarks") or data.get("migo_remarks"),
        items
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                logger.info(f"MIGO entry upserted for history_id {history_id}")
                return True
    except Exception as e:
        logger.error(f"Failed to upsert MIGO for history_id {history_id}: {e}")
        return False


def update_migo_103_rf_result(
    history_id: int,
    material_doc_number: str,
    status: str = "success",
    error_message: Optional[str] = None
) -> bool:
    sql = """
        UPDATE migo_entries
        SET material_doc_number  = %s,
            migo_103_rf_status   = %s,
            migo_103_rf_error    = %s,
            migo_103_executed_at = %s,
            updated_at           = CURRENT_TIMESTAMP
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    material_doc_number, status, error_message,
                    datetime.now(), history_id
                ))
                logger.info(
                    f"MIGO 103 RF result stored for history_id {history_id}: "
                    f"MaterialDoc={material_doc_number}"
                )
                return True
    except Exception as e:
        logger.error(f"Failed to update MIGO 103 RF result for history_id {history_id}: {e}")
        return False


def save_migo_105_fields(history_id: int, data: dict) -> bool:
    """
    Save MIGO 105 specific header fields.
    Per-line batches now live inside items_data (updated by upsert_migo_entry).
    Global migo_105_batch column kept for legacy but unused by new flow.
    """
    sql = """
        UPDATE migo_entries
        SET migo_105_storage_loc    = %s,
            migo_105_batch          = %s,
            migo_105_vendor_invoice = %s,
            migo_105_remarks        = %s,
            updated_at              = CURRENT_TIMESTAMP
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    data.get("storageLocation") or data.get("migo_105_storage_loc"),
                    data.get("batch") or data.get("migo_105_batch"),
                    data.get("vendorInvoiceDetail") or data.get("migo_105_vendor_invoice"),
                    data.get("remarks105") or data.get("migo_105_remarks"),
                    history_id
                ))
                logger.info(f"MIGO 105 fields saved for history_id {history_id}")
                return True
    except Exception as e:
        logger.error(f"Failed to save MIGO 105 fields for history_id {history_id}: {e}")
        return False


def update_migo_105_items_with_batches(history_id: int, batches_by_line: list) -> bool:
    """
    Update items_data JSONB to include per-line batch values entered in MIGO 105 form.
    batches_by_line: [{"line": 1, "batch": "..."}, {"line": 2, "batch": "..."}, ...]
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT items_data FROM migo_entries WHERE history_id = %s",
                    (history_id,)
                )
                row = cur.fetchone()
                if not row or not row.get("items_data"):
                    logger.warning(f"No items_data found for history_id {history_id}")
                    return False

                items = row["items_data"]
                if isinstance(items, str):
                    items = json.loads(items)
                if not isinstance(items, list):
                    return False

                # Build a map of line -> batch from the input
                batch_map = {}
                for b in batches_by_line:
                    line_num = b.get("line")
                    if line_num is not None:
                        batch_map[int(line_num)] = b.get("batch", "")

                # Apply batches to existing items
                for item in items:
                    line_num = item.get("line")
                    if line_num is not None and int(line_num) in batch_map:
                        item["batch"] = batch_map[int(line_num)]

                cur.execute(
                    "UPDATE migo_entries SET items_data = %s, updated_at = CURRENT_TIMESTAMP WHERE history_id = %s",
                    (json.dumps(items), history_id)
                )
                logger.info(f"Per-line batches saved for history_id {history_id}")
                return True
    except Exception as e:
        logger.error(f"Failed to update batches for history_id {history_id}: {e}")
        return False


def update_migo_105_rf_result(
    history_id: int,
    status: str = "success",
    error_message: Optional[str] = None
) -> bool:
    sql = """
        UPDATE migo_entries
        SET migo_105_rf_status   = %s,
            migo_105_rf_error    = %s,
            migo_105_executed_at = %s,
            updated_at           = CURRENT_TIMESTAMP
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (status, error_message, datetime.now(), history_id))
                logger.info(f"MIGO 105 RF result stored for history_id {history_id}: status={status}")
                return True
    except Exception as e:
        logger.error(f"Failed to update MIGO 105 RF result for history_id {history_id}: {e}")
        return False


def get_migo_entry(history_id: int) -> Optional[dict]:
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM migo_entries WHERE history_id = %s",
                    (history_id,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                row = dict(row)
                if row.get("items_data") and isinstance(row["items_data"], str):
                    try:
                        row["items_data"] = json.loads(row["items_data"])
                    except Exception:
                        row["items_data"] = []
                return row
    except Exception as e:
        logger.error(f"Failed to fetch MIGO entry for history_id {history_id}: {e}")
        return None



def map_ocr_to_migo(
    invoice_data: Optional[dict],
    ewaybill_data: Optional[dict],
    lr_data: Optional[dict]
) -> dict:
    """
    Map OCR-extracted data to MIGO 103 form fields.

    Per-line items use clean schema:
      - material_code: SAP material code from invoice
      - short_text: human-readable description (consolidated; no more 'material'/'mat_short_text')
      - hsn_sac
      - qty_expected, qty_actual (UI shows qty_actual as "Delivery Note Qty")
      - rate, amount
      - batch: empty by default; filled in MIGO 105
    """
    data = {}

    if invoice_data:
        data["migoDocDate"]      = invoice_data.get("invoice_date") or ""
        data["migoPostDate"]     = datetime.now().strftime("%Y-%m-%d")
        data["migoDeliveryNote"] = invoice_data.get("invoice_number") or ""

    data["migoPoNumber"] = (
        (invoice_data.get("po_number") if invoice_data else "") or
        (ewaybill_data.get("po_number") if ewaybill_data else "") or
        (lr_data.get("po_number") if lr_data else "") or ""
    )

    if lr_data:
        data["migoBillOfLading"] = lr_data.get("lr_number") or ""
    elif ewaybill_data:
        data["migoBillOfLading"] = ewaybill_data.get("transport_doc_no") or ""

    if ewaybill_data:
        data["migoGRSlipNo"] = ewaybill_data.get("ewaybill_number") or ""

    items = []
    if invoice_data and invoice_data.get("hsn_details"):
        hsn_list = invoice_data["hsn_details"]
        if isinstance(hsn_list, str):
            try:
                hsn_list = json.loads(hsn_list)
            except Exception:
                hsn_list = []
        for idx, hsn in enumerate(hsn_list, 1):
            items.append({
                "line":          idx,
                "material_code": hsn.get("material_code") or "",
                "short_text":    hsn.get("description") or "",
                "hsn_sac":       hsn.get("hsn_sac") or "",
                "qty_expected":  hsn.get("quantity") or "",
                "qty_actual":    hsn.get("quantity") or "",  # pre-fill = qty_expected (delivery note qty)
                "unit":          hsn.get("unit") or "",
                "rate":          hsn.get("rate") or "",
                "amount":        hsn.get("taxable_value") or "",
                "batch":         "",
            })
    elif ewaybill_data:
        items.append({
            "line":          1,
            "material_code": "",
            "short_text":    ewaybill_data.get("goods_description") or "",
            "hsn_sac":       ewaybill_data.get("hsn_code") or "",
            "qty_expected":  ewaybill_data.get("quantity") or "",
            "qty_actual":    ewaybill_data.get("quantity") or "",
            "unit":          "",
            "rate":          "",
            "amount":        "",
            "batch":         "",
        })

    data["items_data"] = items
    return data

def save_miro_doc_number(history_id: int, miro_doc_number: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE migo_entries SET miro_doc_number = %s WHERE history_id = %s",
                    (miro_doc_number, history_id)
                )
        logger.info(f"MIRO doc number saved for history_id={history_id}: {miro_doc_number}")
        return True
    except Exception as e:
        logger.error(f"save_miro_doc_number failed: {e}")
        return False