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
        item_no, material_code, short_text, qty, rate, amount, hsn_sac,
        uom, open_qty

    FIX: this used to read item.get("material", "") / item.get("po_qty", "")
    -- key names that don't exist in the robot's actual output (it emits
    "material_code" / "qty"), so every fetch silently stored empty strings
    for material and quantity regardless of what SAP returned. DB column
    names (material, po_qty) are unchanged -- only the dict keys read FROM
    the robot's JSON are corrected here.

    FIX (2026-08-07): po_fetch.robot was updated to also scrape UOM
    (emitted as "uom") but this function never read it, and the DB column
    it belongs in (po_line_items.unit -- already existed, added back in
    schema_migration_v3.sql, just never wired up) was missing from the
    INSERT column list. So every fetch silently dropped UOM even though
    both the robot output and the DB column already existed. Added here;
    matching read added to get_po_line_items() below. Falls back to
    item.get("unit") too in case any caller ever emits that key instead.

    FIX (2026-08-07, same day): po_fetch.robot separately gained a second
    new value, "open_qty" (SAP's outstanding/undelivered quantity for
    that PO line, read from ME23N's Delivery tab -- see po_fetch.robot's
    STEP 2b). Unlike UOM, there was no dormant column for this one --
    schema_migration_v22.sql adds po_line_items.open_qty fresh. View-only
    on the MIGO 103 tab's PO table (client decision) -- doesn't feed into
    the Invoice/PO matching or mismatch-highlighting logic at all, just
    stored and displayed.

    FIX (2026-08-12): po_fetch.robot now also reads each line's SAP
    deletion-indicator icon (sap_helpers.py's get_delflag_status()) and
    emits "row_status": "Active" or "Deleted" per item -- a deleted PO
    line still shows a grid row, so without this it was indistinguishable
    from a genuine line with similar data (reported as items appearing to
    "repeat"). schema_migration_v23.sql adds po_line_items.row_status,
    defaulting existing rows to 'Active'. View-only like open_qty --
    migo_103.html greys it out and blocks pairing, doesn't touch the
    mismatch-highlighting logic.
    """
    delete_sql = "DELETE FROM po_line_items WHERE history_id = %s"
    insert_sql = """
        INSERT INTO po_line_items (
            history_id, item_no, material, short_text,
            po_qty, rate, amount, hsn_sac, unit, open_qty, row_status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        item.get("uom", "") or item.get("unit", ""),
                        item.get("open_qty", ""),
                        item.get("row_status", "") or "Active",
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
                    po_qty, rate, amount, hsn_sac, unit, open_qty, row_status, fetched_at
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
                    # unit / open_qty: no re-keying needed -- DB columns are
                    # already named to match what migo_103.html reads
                    # (item.get('unit','') / item.get('open_qty','')),
                    # unlike material_code/qty above.
                    # row_status: defensive default for rows saved before
                    # schema_migration_v23.sql (or any NULL edge case) --
                    # treat as Active rather than leaving migo_103.html to
                    # guess what an empty value means.
                    r['row_status'] = r.get('row_status') or 'Active'
                    result.append(r)
                return result
    except Exception as e:
        logger.error(f"Failed to fetch PO line items for history_id={history_id}: {e}")
        return []