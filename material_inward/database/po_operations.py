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

    Each item dict expected keys:
        item_no, material_code, short_text, qty, unit, hsn_sac
    """
    delete_sql = "DELETE FROM po_line_items WHERE history_id = %s"
    insert_sql = """
        INSERT INTO po_line_items (
            history_id, item_no, material_code, short_text,
            qty, rate, amount, hsn_sac
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
                        item.get("material_code", ""),
                        item.get("short_text", ""),
                        item.get("qty", ""),
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
                    SELECT item_no, material_code, short_text,
                           qty, rate, amount, hsn_sac, fetched_at
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
                    result.append(r)
                return result
    except Exception as e:
        logger.error(f"Failed to fetch PO line items for history_id={history_id}: {e}")
        return []