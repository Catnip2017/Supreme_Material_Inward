"""
database/scenario_operations.py — Partial-document scenario handling.

Backs the Extracted Data tab's handling of records that don't have all 3
documents (Invoice + E-Way Bill + LR) -- see schema_migration_v13.sql.

Three pieces:
  - goods_delivery_mode: set once when LR is missing (Invoice + E-Way Bill
    present), or together with ewb_exemption_reasons when only Invoice is
    present. One of utility_van / vendor_vehicle / hand_delivery / courier.
  - ewb_exemption_reasons: set once when E-Way Bill is missing (Invoice +
    LR present), or together with goods_delivery_mode when only Invoice
    is present. One or more of value_exemption / distance_exemption /
    other_exemption, stored comma-separated.
  - history_extras: unrecognized files folder_watcher.py picked up in a
    group (filename suffix didn't match INV/EWB/LR), attached to the
    record for reference only.

Both goods_delivery_mode and ewb_exemption_reasons are write-once from the
API's point of view (see app.py's guard checks before calling these) --
the functions here don't themselves enforce that, they just persist
whatever they're given, same division of responsibility as the rest of
this codebase (route layer decides permission/lock rules, db layer just
executes the write).
"""

from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)

DELIVERY_MODE_LABELS = {
    "utility_van":     "Utility Van",
    "vendor_vehicle":  "Vendor Own Vehicle",
    "hand_delivery":   "Hand Delivery",
    "courier":         "Courier",
}

EWB_EXEMPTION_LABELS = {
    "value_exemption":    "Value Exemption",
    "distance_exemption": "Distance Exemption",
    "other_exemption":    "Other Exemptions",
}

# Fixed Remarks text auto-appended for each choice -- client-approved
# wording. Delivery-mode text is keyed by the single value chosen;
# exemption text is looked up per reason and joined if more than one
# is selected.
_DELIVERY_MODE_REMARK = {
    "utility_van":    "Delivery via company utility van — no LR applicable.",
    "vendor_vehicle": "Goods delivered via vendor's own vehicle — no LR applicable.",
    "hand_delivery":  "Hand-delivered consignment — no LR applicable.",
    "courier":        "Delivered via courier — LR to be filled manually from courier consignment note.",
}

_EWB_EXEMPTION_REMARK = {
    "value_exemption":    "E-Way Bill not generated — invoice value below the statutory threshold.",
    "distance_exemption": "E-Way Bill not generated — transport distance below the statutory threshold.",
    "other_exemption":    "E-Way Bill not generated — other statutory exemption applies (see remarks).",
}


def delivery_mode_remark_text(mode: str) -> str:
    return _DELIVERY_MODE_REMARK.get(mode, "")


def ewb_exemption_remark_text(reasons) -> str:
    """reasons: list/tuple of reason keys (order preserved, de-duped)."""
    seen = []
    for r in reasons or []:
        if r in _EWB_EXEMPTION_REMARK and r not in seen:
            seen.append(r)
    return " ".join(_EWB_EXEMPTION_REMARK[r] for r in seen)


# ── Reads ─────────────────────────────────────────────────────────────────

def get_history_extras(history_id: int, doc_type: Optional[str] = None) -> list:
    """
    doc_type: filter to 'extra' (genuinely unrecognized filenames, view-only)
    or 'others' (the deliberate 4th document type, merged into the DMS-bound
    consolidated PDF by doc_consolidator.py). None = both, unfiltered
    (matches pre-v14 behavior for existing callers).
    """
    sql = (
        "SELECT id, filename, original_filename, doc_type, created_at "
        "FROM history_extras WHERE history_id = %s"
    )
    params = [history_id]
    if doc_type:
        sql += " AND doc_type = %s"
        params.append(doc_type)
    sql += " ORDER BY id"
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[scenario_ops] get_history_extras failed for history_id={history_id}: {e}")
        return []


# ── Writes ───────────────────────────────────────────────────────────────

def set_goods_delivery_mode(history_id: int, mode: str, username: str) -> bool:
    if mode not in DELIVERY_MODE_LABELS:
        logger.error(f"[scenario_ops] invalid goods_delivery_mode '{mode}' for history_id={history_id}")
        return False
    sql = """
        UPDATE history
        SET goods_delivery_mode = %s,
            goods_delivery_mode_by = %s,
            goods_delivery_mode_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (mode, username, history_id))
            conn.commit()
        logger.info(f"[scenario_ops] goods_delivery_mode={mode} saved for history_id={history_id} by {username}")
        return True
    except Exception as e:
        logger.error(f"[scenario_ops] set_goods_delivery_mode failed for history_id={history_id}: {e}")
        return False


def set_ewb_exemption_reasons(history_id: int, reasons: list, username: str) -> bool:
    cleaned = [r for r in (reasons or []) if r in EWB_EXEMPTION_LABELS]
    if not cleaned:
        logger.error(f"[scenario_ops] no valid ewb_exemption_reasons for history_id={history_id}")
        return False
    value = ",".join(cleaned)
    sql = """
        UPDATE history
        SET ewb_exemption_reasons = %s,
            ewb_exemption_reasons_by = %s,
            ewb_exemption_reasons_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (value, username, history_id))
            conn.commit()
        logger.info(f"[scenario_ops] ewb_exemption_reasons={value} saved for history_id={history_id} by {username}")
        return True
    except Exception as e:
        logger.error(f"[scenario_ops] set_ewb_exemption_reasons failed for history_id={history_id}: {e}")
        return False


def add_history_extra(
    history_id: int,
    filename: str,
    original_filename: Optional[str] = None,
    doc_type: str = "extra"
) -> bool:
    """
    doc_type: 'extra' (default) = genuinely unrecognized filename, view-only,
    never touches DMS. 'others' = the deliberate 4th document type (_OTH
    suffix), merged into the DMS-bound consolidated PDF by doc_consolidator.py.
    """
    sql = """
        INSERT INTO history_extras (history_id, filename, original_filename, doc_type)
        VALUES (%s, %s, %s, %s)
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (history_id, filename, original_filename or filename, doc_type))
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"[scenario_ops] add_history_extra failed for history_id={history_id}: {e}")
        return False


def append_remark(history_id: int, text: str, role: str, username: str) -> bool:
    """
    Append a system-generated line to the record-wide Remark, used by the
    delivery-mode / EWB-exemption save endpoints so the reviewer doesn't
    have to type the explanation by hand. Reuses history_remarks (see
    database/remarks_operations.py) -- appends rather than overwrites, so
    it composes safely with anything already typed there, and with the
    other scenario picker firing on the same record (Invoice-only case
    writes two lines, one per picker).
    """
    from database.remarks_operations import get_remark, upsert_remark
    existing = get_remark(history_id)
    existing_text = (existing or {}).get("remark_text") or ""
    combined = (existing_text + ("\n" if existing_text else "") + text).strip()
    return upsert_remark(history_id, combined, role, username)
