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
from config.config import config
from config.logger import get_logger

logger = get_logger(__name__)

# FIX (2026-08-24, client request): this server runs in UTC, not IST --
# datetime.now() here returns the SERVER's clock, which is UTC. Used
# bare, this fallback would compute "today" from UTC, silently landing
# on the WRONG calendar date for any request made between ~00:00 and
# 05:30 IST (same underlying bug as migo_103.html's client-side
# toISOString() default, fixed there separately).
#
# UPDATED (2026-08-31, Silent Hours feature): this used to hardcode its
# own "+5:30, always" offset directly in this file. Now delegates to
# config.Config.get_ist_now(), which reads the new UTC_TIMEZONE .env flag
# -- one shared answer to "is this server on UTC" instead of two
# independent places that could silently drift apart (the Silent Hours
# window check in config/config.py reads that same flag).
def _today_ist() -> str:
    """Today's date as YYYY-MM-DD in India time, computed from this
    server's clock via config.Config.get_ist_now()."""
    return config.get_ist_now().strftime("%Y-%m-%d")


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
        data.get("migoPostDate") or data.get("migo_post_date") or _today_ist(),
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


def save_migo_101_fields(history_id: int, data: dict) -> bool:
    """
    Save MIGO 101's own header fields + its own items/matching JSONB in one
    write. Unlike MIGO 105 (save_migo_105_fields + a separate
    update_migo_105_items_with_batches pass), MIGO 101 collects matching AND
    per-line batch together in one confirmed-pairs table (same UI flow as
    MIGO 103's confirmMatches(), just with a batch column added to the same
    row) -- so there is no second pass needed here.

    UPSERT, not a plain UPDATE (unlike save_migo_105_fields): MIGO 105 can
    safely assume upsert_migo_entry() already created this history_id's
    migo_entries row, because MIGO 105 requires MIGO 103 done first, and
    MIGO 103's own route calls upsert_migo_entry() before this point. MIGO
    101 has NO such guarantee -- its only dependency is Gate In, and while
    migo_entries is normally pre-seeded from OCR data before Gate In (see
    app.py's pre-populate step), a Silent Hours walk-in record can reach
    Gate In (and therefore MIGO 101) before any document/OCR data -- and
    therefore before migo_entries -- ever existed for it. Upserting here
    removes that whole class of "row doesn't exist yet" risk rather than
    relying on some other code path having created it first.
    """
    items = data.get("items_data") or data.get("items") or []
    if isinstance(items, (list, dict)):
        items = json.dumps(items)

    sql = """
        INSERT INTO migo_entries (
            history_id, migo_101_outbound_delivery, migo_101_doc_date, migo_101_post_date,
            migo_101_bill_of_lading, migo_101_gr_slip_no, migo_101_header_text,
            migo_101_storage_loc, migo_101_vendor_invoice, migo_101_remarks, migo_101_items_data
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (history_id) DO UPDATE SET
            migo_101_outbound_delivery = EXCLUDED.migo_101_outbound_delivery,
            migo_101_doc_date          = EXCLUDED.migo_101_doc_date,
            migo_101_post_date         = EXCLUDED.migo_101_post_date,
            migo_101_bill_of_lading    = EXCLUDED.migo_101_bill_of_lading,
            migo_101_gr_slip_no        = EXCLUDED.migo_101_gr_slip_no,
            migo_101_header_text       = EXCLUDED.migo_101_header_text,
            migo_101_storage_loc       = EXCLUDED.migo_101_storage_loc,
            migo_101_vendor_invoice    = EXCLUDED.migo_101_vendor_invoice,
            migo_101_remarks           = EXCLUDED.migo_101_remarks,
            migo_101_items_data        = EXCLUDED.migo_101_items_data,
            updated_at                 = CURRENT_TIMESTAMP
    """
    values = (
        history_id,
        data.get("outboundDeliveryNumber") or data.get("migo_101_outbound_delivery"),
        data.get("migo101DocDate")         or data.get("migo_101_doc_date"),
        data.get("migo101PostDate")        or data.get("migo_101_post_date") or _today_ist(),
        data.get("migo101BillOfLading")    or data.get("migo_101_bill_of_lading"),
        data.get("migo101GRSlipNo")        or data.get("migo_101_gr_slip_no"),
        data.get("migo101HeaderText")      or data.get("migo_101_header_text"),
        data.get("storageLocation")        or data.get("migo_101_storage_loc"),
        data.get("vendorInvoiceDetail")    or data.get("migo_101_vendor_invoice"),
        data.get("remarks101")             or data.get("migo_101_remarks"),
        items,
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                logger.info(f"MIGO 101 fields saved for history_id {history_id}")
                return True
    except Exception as e:
        logger.error(f"Failed to save MIGO 101 fields for history_id {history_id}: {e}")
        return False


def update_migo_101_rf_result(
    history_id: int,
    status: str = "success",
    error_message: Optional[str] = None
) -> bool:
    sql = """
        UPDATE migo_entries
        SET migo_101_rf_status   = %s,
            migo_101_rf_error    = %s,
            migo_101_executed_at = %s,
            updated_at           = CURRENT_TIMESTAMP
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (status, error_message, datetime.now(), history_id))
                logger.info(f"MIGO 101 RF result stored for history_id {history_id}: status={status}")
                return True
    except Exception as e:
        logger.error(f"Failed to update MIGO 101 RF result for history_id {history_id}: {e}")
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
                if row.get("migo_101_items_data") and isinstance(row["migo_101_items_data"], str):
                    try:
                        row["migo_101_items_data"] = json.loads(row["migo_101_items_data"])
                    except Exception:
                        row["migo_101_items_data"] = []
                return row
    except Exception as e:
        logger.error(f"Failed to fetch MIGO entry for history_id {history_id}: {e}")
        return None



def shape_invoice_items_for_migo(hsn_details) -> list:
    """
    Reshape invoice_data.hsn_details (raw OCR/goods-table field names) into
    the MIGO line-item schema used by the MIGO 103 "Invoice Line Items"
    table and by the bot payload:
      - material_code: SAP material code from invoice
      - short_text: human-readable description (consolidated; no more
        'material'/'mat_short_text')
      - hsn_sac
      - qty_expected, qty_actual (UI shows qty_actual as "Delivery Note Qty")
      - rate, amount
      - batch: empty by default; filled in MIGO 105

    This is the single place that defines the hsn_details -> MIGO item
    mapping. It's called both when pre-seeding migo_entries.items_data
    (map_ocr_to_migo, below) and when rendering the MIGO 103 tab's
    "Invoice Line Items (from OCR)" table live from the current
    invoice_data (see view_detail() in app.py) -- so the two never drift
    apart the way items_data and hsn_details could before.
    """
    hsn_list = hsn_details or []
    if isinstance(hsn_list, str):
        try:
            hsn_list = json.loads(hsn_list)
        except Exception:
            hsn_list = []
    if not isinstance(hsn_list, list):
        return []

    items = []
    for idx, hsn in enumerate(hsn_list, 1):
        if not isinstance(hsn, dict):
            continue
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
    return items


def map_ocr_to_migo(
    invoice_data: Optional[dict],
    ewaybill_data: Optional[dict],
    lr_data: Optional[dict]
) -> dict:
    """
    Map OCR-extracted data to MIGO 103 form fields.

    Per-line items use the shared schema built by
    shape_invoice_items_for_migo() -- see that function's docstring.
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

    # v7: EWB goods fallback removed — invoice_data.hsn_details is the
    # single source of goods lines. If invoice OCR failed, items stay
    # empty until the invoice data is corrected on the Extracted Data tab.
    data["items_data"] = shape_invoice_items_for_migo(
        invoice_data.get("hsn_details") if invoice_data else None
    )
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


# ============================================================
# DMS LINK ATTACH RESULTS — v18. Separate follow-up jobs (migo103_link,
# migo105_link), run as their own rf_queue steps AFTER the corresponding
# posting job succeeds, never embedded in it -- a DMS-link failure here
# must never be able to make MIGO 103 / MIGO 105's own posting look
# failed. See services/rf_queue_worker.py._process_migo103_link /
# _process_migo105_link.
# ============================================================

def update_migo103_link_result(
    history_id: int,
    status: str,
    error_message: Optional[str] = None
) -> bool:
    """
    status: 'skipped_no_link' | 'success' | 'failed'.
    'skipped_no_link' is set when MIGO 103 posted fine but no DMS link was
    available yet at enqueue time -- services/dms_links_import.py catches
    these up later once the link lands (see its v18 addition), so this is
    not a terminal/error state, just "not attempted yet, waiting on DMS".
    """
    sql = """
        UPDATE migo_entries
        SET migo103_link_status      = %s,
            migo103_link_error       = %s,
            migo103_link_executed_at = %s,
            updated_at                = CURRENT_TIMESTAMP
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (status, error_message, datetime.now(), history_id))
                logger.info(f"MIGO103 link-attach result stored for history_id {history_id}: status={status}")
                return True
    except Exception as e:
        logger.error(f"Failed to update MIGO103 link-attach result for history_id {history_id}: {e}")
        return False


def update_migo105_link_result(
    history_id: int,
    status: str,
    error_message: Optional[str] = None
) -> bool:
    """Same contract as update_migo103_link_result, for the MIGO 105 follow-up job."""
    sql = """
        UPDATE migo_entries
        SET migo105_link_status      = %s,
            migo105_link_error       = %s,
            migo105_link_executed_at = %s,
            updated_at                = CURRENT_TIMESTAMP
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (status, error_message, datetime.now(), history_id))
                logger.info(f"MIGO105 link-attach result stored for history_id {history_id}: status={status}")
                return True
    except Exception as e:
        logger.error(f"Failed to update MIGO105 link-attach result for history_id {history_id}: {e}")
        return False


def update_migo101_link_result(
    history_id: int,
    status: str,
    error_message: Optional[str] = None
) -> bool:
    """Same contract as update_migo103_link_result, for the MIGO 101 follow-up job."""
    sql = """
        UPDATE migo_entries
        SET migo101_link_status      = %s,
            migo101_link_error       = %s,
            migo101_link_executed_at = %s,
            updated_at                = CURRENT_TIMESTAMP
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (status, error_message, datetime.now(), history_id))
                logger.info(f"MIGO101 link-attach result stored for history_id {history_id}: status={status}")
                return True
    except Exception as e:
        logger.error(f"Failed to update MIGO101 link-attach result for history_id {history_id}: {e}")
        return False