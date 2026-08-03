"""
database/po_operations.py — PO line items CRUD operations.

Stores line items fetched from SAP ME23N via po_fetch.robot.
Called after Gate In completes successfully.
Data persists in DB so MIGO user (different user / different day) can access it.
"""

from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


def save_po_line_items(history_id: int, items: list) -> bool:
    """
    Save PO line items fetched from SAP for a given history_id.
    Replaces any existing rows — re-fetch replaces old data.

    Each item dict, as actually emitted by po_fetch.robot's RESULT:PO_DATA:
    JSON (see po_fetch.robot ~line 251), has keys:
        item_no, material_code, short_text, qty, rate, amount, hsn_sac

    FIX: this used to read item.get("material", "") / item.get("po_qty", "")
    -- key names that don't exist in the robot's actual output (it emits
    "material_code" / "qty"), so every fetch silently stored empty strings
    for material and quantity regardless of what SAP returned. DB column
    names (material, po_qty) are unchanged -- only the dict keys read FROM
    the robot's JSON are corrected here.
    """
    delete_sql = "DELETE FROM po_line_items WHERE history_id = %s"
    insert_sql = """
        INSERT INTO po_line_items (
            history_id, item_no, material, short_text,
            po_qty, rate, amount, hsn_sac
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(delete_sql, (history_id,))
                for item in items:
                    cur.execute(insert_sql, (
                        history_id,
                        item.get("item_no", ""),
                        item.get("material_code", "") or item.get("material", ""),
                        item.get("short_text", ""),
                        item.get("qty", "") or item.get("po_qty", ""),
                        item.get("rate", ""),
                        item.get("amount", ""),
                        item.get("hsn_sac", ""),
                    ))
                logger.info(f"Saved {len(items)} PO line item(s) for history_id={history_id}")
                return True
    except Exception as e:
        logger.error(f"Failed to save PO line items for history_id={history_id}: {e}")
        return False


def get_po_line_items(history_id: int) -> list:
    """
    Fetch all PO line items for a given history_id.
    Returns list of dicts, empty list if none found.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                     SELECT item_no, material, short_text,
                    po_qty, rate, amount, hsn_sac, fetched_at
                    FROM po_line_items
                    WHERE history_id = %s
                    ORDER BY id ASC
                    """,
                    (history_id,)
                )
                rows = cur.fetchall()
                result = []
                for row in rows:
                    r = dict(row)
                    if r.get("fetched_at") and hasattr(r["fetched_at"], "isoformat"):
                        r["fetched_at"] = r["fetched_at"].isoformat()
                    # FIX: material_code/qty re-keying used to only happen
                    # inside the fetched_at branch above -- moved out here
                    # so migo_103.html (which reads item.material_code /
                    # item.qty) always gets populated fields regardless of
                    # whether fetched_at happens to be a datetime object.
                    r['material_code'] = r.get('material', '')
                    r['qty'] = r.get('po_qty', '')
                    result.append(r)
                return result
    except Exception as e:
        logger.error(f"Failed to fetch PO line items for history_id={history_id}: {e}")
        return []