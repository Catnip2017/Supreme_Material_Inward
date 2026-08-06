"""
database/admin_operations.py — Record Admin operations.

Backs the new "record_admin" permission (schema_migration_v21.sql) --
lets a SuperAdmin grant specific users the ability to delete a history
record entirely, reset an individual workflow step, or revert an
approval, from a new /admin/records page in the app instead of raw SQL
run by hand against production.

Every mutating function here logs to admin_action_log first (capturing a
snapshot of what's about to change) and is deliberately narrow --
each one does exactly the same UPDATE/DELETE statements that were being
handed out as manual SQL, nothing more, so behavior matches exactly what
was already reviewed and used successfully for history_id=39's cleanup.
"""

from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


def _snapshot_invoice_number(cur, history_id: int) -> str:
    """Best-effort context for the audit log -- never raises, never blocks
    the actual action if it fails."""
    try:
        cur.execute(
            "SELECT COALESCE(inv.invoice_number, h.invoice_number) AS invoice_number "
            "FROM history h LEFT JOIN invoice_data inv ON inv.id = h.id WHERE h.id = %s",
            (history_id,)
        )
        row = cur.fetchone()
        return (row[0] or "") if row else ""
    except Exception:
        return ""


def log_admin_action(history_id: int, action: str, performed_by: str, details: str = "") -> None:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO admin_action_log (history_id, action, details, performed_by)
                       VALUES (%s, %s, %s, %s)""",
                    (history_id, action, details, performed_by)
                )
        logger.info(f"[RecordAdmin] {performed_by} performed '{action}' on history_id={history_id}")
    except Exception as e:
        # Logging failure must never block the actual action -- the caller
        # already did its own cur.execute inside the same transaction where
        # relevant; this is a best-effort accountability trail, not a gate.
        logger.error(f"Failed to write admin_action_log for history_id={history_id} action={action}: {e}")


def get_admin_action_log(limit: int = 100) -> list:
    sql = """
        SELECT id, history_id, action, details, performed_by, performed_at
        FROM admin_action_log
        ORDER BY performed_at DESC
        LIMIT %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (limit,))
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if r.get("performed_at") and hasattr(r["performed_at"], "isoformat"):
                        r["performed_at"] = r["performed_at"].isoformat()
                return rows
    except Exception as e:
        logger.error(f"Failed to fetch admin_action_log: {e}")
        return []


def find_records_for_admin(query: str, limit: int = 25) -> list:
    """
    Lookup for the Record Admin page's search box -- matches by history_id
    (if query is numeric), invoice number, or PO number. Returns enough
    status info to render the confirmation card before any destructive
    action is taken.
    """
    query = (query or "").strip()
    if not query:
        return []

    conditions = ["COALESCE(inv.invoice_number, h.invoice_number, '') ILIKE %s",
                  "COALESCE(inv.po_number, h.po_number, '') ILIKE %s"]
    params = [f"%{query}%", f"%{query}%"]
    if query.isdigit():
        conditions.insert(0, "h.id = %s")
        params.insert(0, int(query))

    sql = f"""
        SELECT
            h.id, h.gate_in, h.migo_103, h.migo_105, h.miro,
            h.approval_status, h.gst_check,
            COALESCE(inv.invoice_number, h.invoice_number) AS invoice_number,
            COALESCE(inv.po_number, h.po_number)            AS po_number,
            h.created_at
        FROM history h
        LEFT JOIN invoice_data inv ON inv.id = h.id
        WHERE {" OR ".join(conditions)}
        ORDER BY h.created_at DESC
        LIMIT %s
    """
    params.append(limit)
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
                        r["created_at"] = r["created_at"].isoformat()
                return rows
    except Exception as e:
        logger.error(f"find_records_for_admin failed for query {query!r}: {e}")
        return []


def delete_history_record(history_id: int, performed_by: str) -> bool:
    """
    Deletes the history row and everything that cascades from it (every
    FK to history.id across the schema is ON DELETE CASCADE -- invoice_data,
    ewaybill_data, lr_data, gate_in_entries, migo_entries, miro_entries,
    gst_approval, notifications, rf_queue, history_extras,
    dms_document_links, history_remarks). Logged BEFORE the delete so the
    invoice-number snapshot is still available to capture.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                invoice_number = _snapshot_invoice_number(cur, history_id)
                cur.execute("DELETE FROM history WHERE id = %s", (history_id,))
                deleted = cur.rowcount > 0
        if deleted:
            log_admin_action(
                history_id, "delete_record", performed_by,
                details=f"invoice_number={invoice_number!r}"
            )
            logger.info(f"[RecordAdmin] history_id={history_id} deleted by {performed_by}")
        return deleted
    except Exception as e:
        logger.error(f"delete_history_record failed for history_id={history_id}: {e}")
        return False


def reset_gate_in_step(history_id: int, performed_by: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE history
                       SET gate_in = 0, gatein_done_at = NULL, gate_in_number = NULL,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (history_id,)
                )
                cur.execute("DELETE FROM gate_in_entries WHERE history_id = %s", (history_id,))
                cur.execute(
                    "DELETE FROM rf_queue WHERE history_id = %s AND step = 'gate_in'",
                    (history_id,)
                )
        log_admin_action(history_id, "reset_gate_in", performed_by)
        return True
    except Exception as e:
        logger.error(f"reset_gate_in_step failed for history_id={history_id}: {e}")
        return False


def reset_migo_103_step(history_id: int, performed_by: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE history
                       SET migo_103 = 0, migo_103_done_at = NULL, material_doc_number = NULL,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (history_id,)
                )
                cur.execute(
                    """UPDATE migo_entries
                       SET material_doc_number = NULL, migo_103_rf_status = 'pending',
                           migo_103_rf_error = NULL, migo_103_executed_at = NULL,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE history_id = %s""",
                    (history_id,)
                )
                cur.execute(
                    "DELETE FROM rf_queue WHERE history_id = %s AND step = 'migo_103'",
                    (history_id,)
                )
        log_admin_action(history_id, "reset_migo_103", performed_by)
        return True
    except Exception as e:
        logger.error(f"reset_migo_103_step failed for history_id={history_id}: {e}")
        return False


def reset_migo_105_step(history_id: int, performed_by: str) -> bool:
    # Deliberately does NOT touch migo_entries.material_doc_number -- that
    # column is shared with MIGO 103 (see schema.sql's comment on it) and
    # is still needed as MIGO 105's own posting input.
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE history
                       SET migo_105 = 0, migo_105_done_at = NULL, migo_105_doc_number = NULL,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (history_id,)
                )
                cur.execute(
                    """UPDATE migo_entries
                       SET migo_105_storage_loc = NULL, migo_105_batch = NULL,
                           migo_105_rf_status = 'pending', migo_105_rf_error = NULL,
                           migo_105_executed_at = NULL, updated_at = CURRENT_TIMESTAMP
                       WHERE history_id = %s""",
                    (history_id,)
                )
                cur.execute(
                    "DELETE FROM rf_queue WHERE history_id = %s AND step = 'migo_105'",
                    (history_id,)
                )
        log_admin_action(history_id, "reset_migo_105", performed_by)
        return True
    except Exception as e:
        logger.error(f"reset_migo_105_step failed for history_id={history_id}: {e}")
        return False


def reset_miro_step(history_id: int, performed_by: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE history
                       SET miro = 0, miro_done_at = NULL, miro_fi_doc_number = NULL,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (history_id,)
                )
                cur.execute(
                    """UPDATE miro_entries
                       SET rf_status = 'pending', rf_error_message = NULL,
                           rf_executed_at = NULL, updated_at = CURRENT_TIMESTAMP
                       WHERE history_id = %s""",
                    (history_id,)
                )
                cur.execute(
                    "DELETE FROM rf_queue WHERE history_id = %s AND step = 'miro'",
                    (history_id,)
                )
        log_admin_action(history_id, "reset_miro", performed_by)
        return True
    except Exception as e:
        logger.error(f"reset_miro_step failed for history_id={history_id}: {e}")
        return False


def revert_extracted_data_approval(history_id: int, performed_by: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE history
                       SET approval_status = 'pending', approval_by = NULL,
                           approval_at = NULL, hold_reason = NULL,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (history_id,)
                )
        log_admin_action(history_id, "revert_extracted_data_approval", performed_by)
        return True
    except Exception as e:
        logger.error(f"revert_extracted_data_approval failed for history_id={history_id}: {e}")
        return False


def revert_gst_approval(history_id: int, performed_by: str) -> bool:
    # Two places -- history.gst_check (the flag other queries/badges read)
    # and the separate gst_approval table row (its own approval_status/
    # approval_by/approval_at/hold_reason) -- see schema_migration_v6.sql.
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE history
                       SET gst_check = 0, gst_check_done_at = NULL,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (history_id,)
                )
                cur.execute(
                    """UPDATE gst_approval
                       SET approval_status = 'pending', approval_by = NULL,
                           approval_at = NULL, hold_reason = NULL
                       WHERE history_id = %s""",
                    (history_id,)
                )
        log_admin_action(history_id, "revert_gst_approval", performed_by)
        return True
    except Exception as e:
        logger.error(f"revert_gst_approval failed for history_id={history_id}: {e}")
        return False
