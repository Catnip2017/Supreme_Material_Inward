"""
database/gatein_operations.py — Gate In CRUD operations.
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



def upsert_gatein_entry(history_id: int, data: dict) -> bool:
    """
    Insert or update a gate_in_entries record for the given history_id.
    Called both during auto-save (after OCR) and when user saves the form.
    """
    sql = """
        INSERT INTO gate_in_entries (
            history_id, gate_in_date, gate_in_time, vendor_name,
            transporter, truck_no, driver_name, license_no,
            num_persons, container_no, category, material,
            challan_no, challan_qty, boe_no, purchase_order,
            gate_pass_no, note, weight_option
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (history_id) DO UPDATE SET
            gate_in_date    = EXCLUDED.gate_in_date,
            gate_in_time    = EXCLUDED.gate_in_time,
            vendor_name     = EXCLUDED.vendor_name,
            transporter     = EXCLUDED.transporter,
            truck_no        = EXCLUDED.truck_no,
            driver_name     = EXCLUDED.driver_name,
            license_no      = EXCLUDED.license_no,
            num_persons     = EXCLUDED.num_persons,
            container_no    = EXCLUDED.container_no,
            category        = EXCLUDED.category,
            material        = EXCLUDED.material,
            challan_no      = EXCLUDED.challan_no,
            challan_qty     = EXCLUDED.challan_qty,
            boe_no          = EXCLUDED.boe_no,
            purchase_order  = EXCLUDED.purchase_order,
            gate_pass_no    = EXCLUDED.gate_pass_no,
            note            = EXCLUDED.note,
            weight_option   = EXCLUDED.weight_option,
            updated_at      = CURRENT_TIMESTAMP
    """
    values = (
        history_id,
        data.get("gateInDate") or data.get("gate_in_date"),
        data.get("gateInTime") or data.get("gate_in_time"),
        data.get("vendorName") or data.get("vendor_name"),
        data.get("transporter"),
        data.get("truckNo") or data.get("truck_no"),
        data.get("driverName") or data.get("driver_name"),
        data.get("licenseNo") or data.get("license_no"),
        data.get("numPersons") or data.get("num_persons"),
        data.get("containerNo") or data.get("container_no"),
        data.get("category"),
        data.get("material"),
        data.get("challanNo") or data.get("challan_no"),
        data.get("challanQty") or data.get("challan_qty"),
        data.get("boeNo") or data.get("boe_no"),
        data.get("purchaseOrder") or data.get("purchase_order"),
        data.get("gatePassNo") or data.get("gate_pass_no"),
        data.get("note"),
        data.get("weightOption") or data.get("weight_option")
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                logger.info(f"Gate In entry upserted for history_id {history_id}")
                return True
    except Exception as e:
        logger.error(f"Failed to upsert Gate In for history_id {history_id}: {e}")
        return False


def update_gatein_rf_result(
    history_id: int,
    gate_in_number: str,
    status: str = "success",
    error_message: Optional[str] = None
) -> bool:
    """
    Store the SAP-generated Gate In number after RF execution.
    Also updates the history table's gate_in_number column.
    """
    sql = """
        UPDATE gate_in_entries
        SET gate_in_number    = %s,
            rf_status         = %s,
            rf_error_message  = %s,
            rf_executed_at    = %s,
            updated_at        = CURRENT_TIMESTAMP
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    gate_in_number, status, error_message,
                    datetime.now(), history_id
                ))
                logger.info(f"Gate In RF result stored for history_id {history_id}: GIN={gate_in_number}")
                return True
    except Exception as e:
        logger.error(f"Failed to update Gate In RF result for history_id {history_id}: {e}")
        return False


def get_gatein_entry(history_id: int) -> Optional[dict]:
    """
    Fetch the gate_in_entries record for a given history_id.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM gate_in_entries WHERE history_id = %s",
                    (history_id,)
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"Failed to fetch Gate In entry for history_id {history_id}: {e}")
        return None


def _goods_from_invoice(invoice_data: Optional[dict]) -> tuple:
    """
    v7: goods info now comes from invoice_data.hsn_details (single source
    of truth) instead of the deprecated ewaybill goods columns.
    Returns (material, challan_qty):
      material    — first line description, with ' +N more' when multi-line
      challan_qty — sum of numeric line quantities (or first line's raw
                    value when quantities are not numeric)
    """
    if not invoice_data:
        return "", ""
    hsn_list = invoice_data.get("hsn_details") or []
    if isinstance(hsn_list, str):
        try:
            hsn_list = json.loads(hsn_list)
        except Exception:
            hsn_list = []
    if not isinstance(hsn_list, list) or not hsn_list:
        return "", ""

    first_desc = str(hsn_list[0].get("description") or "").strip()
    material = first_desc
    if len(hsn_list) > 1 and first_desc:
        material = f"{first_desc} +{len(hsn_list) - 1} more"

    total_qty = 0.0
    numeric = True
    for line in hsn_list:
        raw = str(line.get("quantity") or "").strip()
        if not raw:
            continue
        try:
            total_qty += float(raw.replace(",", ""))
        except ValueError:
            numeric = False
            break
    if numeric and total_qty:
        challan_qty = str(int(total_qty)) if total_qty == int(total_qty) else str(total_qty)
    else:
        challan_qty = str(hsn_list[0].get("quantity") or "").strip()

    return material, challan_qty


def map_ocr_to_gatein(
    invoice_data: Optional[dict],
    ewaybill_data: Optional[dict],
    lr_data: Optional[dict]
) -> dict:
    """
    Map OCR-extracted data to Gate In form fields.
    Called immediately after OCR to pre-fill the form.
    """
    from datetime import datetime
    data = {}

    if invoice_data:
        data["gateInDate"]     = invoice_data.get("invoice_date") or ""
        data["vendorName"]     = invoice_data.get("seller_name") or ""
        # data["challanNo"]      = invoice_data.get("invoice_number") or ""
        data["purchaseOrder"]  = (invoice_data.get("po_number") if invoice_data else "") or (ewaybill_data.get("po_number") if ewaybill_data else "") or (lr_data.get("po_number") if lr_data else "") or ""
        # v7: material + challan qty from invoice line items
        _material, _challan_qty = _goods_from_invoice(invoice_data)
        if _material:
            data["material"] = _material
        if _challan_qty:
            data["challanQty"] = _challan_qty

    if ewaybill_data:
        data["transporter"]    = ewaybill_data.get("transporter_name") or ""
        data["truckNo"]        = ewaybill_data.get("vehicle_number") or ""

    if lr_data:
        if not data.get("transporter"):
            data["transporter"] = lr_data.get("transporter_name") or ""
        if not data.get("truckNo"):
            data["truckNo"]     = lr_data.get("vehicle_number") or ""
        if not data.get("material"):
            data["material"]    = lr_data.get("material_description", "")

    data["numPersons"]  = "1"
    data["gateInTime"]  = datetime.now().strftime("%H:%M")

    return data