"""
database/miro_operations.py — MIRO CRUD operations.
"""

import json
from datetime import datetime
from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


def _clean_ocr(value) -> str:
    """Convert None, 'None', 'N/A', null etc to empty string."""
    if value is None:
        return ""
    val = str(value).strip()
    if val.lower() in {"none", "null", "n/a", "na", "-", "--", "not available", "not found", ""}:
        return ""
    return val



def upsert_miro_entry(history_id: int, data: dict) -> bool:
    """
    Insert or update miro_entries for a given history_id.
    Called after OCR for pre-filling and when user saves the MIRO form.
    """
    items = data.get("items_data") or data.get("items") or []
    if isinstance(items, (list, dict)):
        items = json.dumps(items)

    sql = """
        INSERT INTO miro_entries (
            history_id, miro_transaction, miro_diff_posting,
            miro_invoice_date, miro_posting_date, miro_reference,
            miro_amount, miro_tax_amount, miro_tax_code,
            miro_text, miro_purchase_order, items_data
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (history_id) DO UPDATE SET
            miro_transaction    = EXCLUDED.miro_transaction,
            miro_invoice_date   = EXCLUDED.miro_invoice_date,
            miro_posting_date   = EXCLUDED.miro_posting_date,
            miro_reference      = EXCLUDED.miro_reference,
            miro_amount         = EXCLUDED.miro_amount,
            miro_tax_amount     = EXCLUDED.miro_tax_amount,
            miro_tax_code       = EXCLUDED.miro_tax_code,
            miro_text           = EXCLUDED.miro_text,
            miro_purchase_order = EXCLUDED.miro_purchase_order,
            items_data          = EXCLUDED.items_data,
            updated_at          = CURRENT_TIMESTAMP
    """
    values = (
        history_id,
        data.get("miroTransaction", "1"),
        data.get("miroDiffPosting", ""),
        data.get("miroInvoiceDate") or data.get("miro_invoice_date"),
        data.get("miroPostingDate") or datetime.now().strftime("%Y-%m-%d"),
        data.get("miroReference") or data.get("miro_reference"),
        data.get("miroAmount") or data.get("miro_amount"),
        data.get("miroTaxAmount") or data.get("miro_tax_amount"),
        data.get("miroTaxCode") or data.get("miro_tax_code", "V0"),
        data.get("miroText") or data.get("miro_text"),
        data.get("miroPurchaseOrder") or data.get("miro_purchase_order"),
        items
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                logger.info(f"MIRO entry upserted for history_id {history_id}")
                return True
    except Exception as e:
        logger.error(f"Failed to upsert MIRO for history_id {history_id}: {e}")
        return False


def update_miro_rf_result(
    history_id: int,
    status: str = "success",
    error_message: Optional[str] = None
) -> bool:
    """
    Record the outcome of MIRO RF execution.
    """
    sql = """
        UPDATE miro_entries
        SET rf_status        = %s,
            rf_error_message = %s,
            rf_executed_at   = %s,
            updated_at       = CURRENT_TIMESTAMP
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (status, error_message, datetime.now(), history_id))
                logger.info(f"MIRO RF result stored for history_id {history_id}: status={status}")
                return True
    except Exception as e:
        logger.error(f"Failed to update MIRO RF result for history_id {history_id}: {e}")
        return False


def get_miro_entry(history_id: int) -> Optional[dict]:
    """
    Fetch the miro_entries record for a given history_id.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM miro_entries WHERE history_id = %s",
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
        logger.error(f"Failed to fetch MIRO entry for history_id {history_id}: {e}")
        return None


def map_ocr_to_miro(
    invoice_data: Optional[dict],
    ewaybill_data: Optional[dict],
    lr_data: Optional[dict]
) -> dict:
    """
    Map OCR-extracted data to MIRO form fields.
    miro_reference = invoice number (bill number used in SAP MIRO reference field).
    miro_posting_date = always today.
    """
    data = {}

    if invoice_data:
        data["miroInvoiceDate"]   = invoice_data.get("invoice_date") or ""
        data["miroPostingDate"]   = datetime.now().strftime("%Y-%m-%d")
        data["miroReference"]     = invoice_data.get("invoice_number") or ""
        data["miroAmount"]        = invoice_data.get("grand_total") or ""
        data["miroTaxAmount"]     = invoice_data.get("total_tax_amount") or ""
        data["miroPurchaseOrder"] = invoice_data.get("po_number") or ""

        # Determine tax code
        if invoice_data.get("cgst_amount") or invoice_data.get("sgst_amount"):
            data["miroTaxCode"] = "V1"   # CGST + SGST
        elif invoice_data.get("igst_amount"):
            data["miroTaxCode"] = "V2"   # IGST
        else:
            data["miroTaxCode"] = "V0"   # No tax

    if ewaybill_data and not data.get("miroPurchaseOrder"):
        data["miroPurchaseOrder"] = ewaybill_data.get("po_number") or ""

    data["miroTransaction"] = "1"

    # Build line items from invoice HSN details
    items = []
    if invoice_data and invoice_data.get("hsn_details"):
        hsn_list = invoice_data["hsn_details"]
        if isinstance(hsn_list, str):
            try:
                hsn_list = json.loads(hsn_list)
            except Exception:
                hsn_list = []
        for idx, hsn in enumerate(hsn_list, 1):
            tax_code = "V0"
            if hsn.get("CGST Amount") or hsn.get("SGST Amount"):
                tax_code = "V1"
            elif hsn.get("IGST Amount"):
                tax_code = "V2"
            items.append({
                "item": idx,
                "po_text": hsn.get("description") or "",
                "quantity": hsn.get("quantity") or "",
                "amount": hsn.get("taxable_value") or "",
                "hsn_sac": hsn.get("hsn_sac") or "",
                "tax_code": tax_code
            })

    data["items_data"] = items
    return data