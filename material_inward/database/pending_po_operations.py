"""
database/pending_po_operations.py — CRUD for pending_po_updates (v17).

See schema_migration_v17.sql for the background. One row per history_id
(UNIQUE constraint) -- a fresh MIGO 103 submission for the same record just
refreshes the existing row (new PO number/timestamp) rather than piling up
duplicates.
"""

from datetime import datetime
from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


def upsert_pending_po_update(
    history_id: int,
    po_number: str,
    gate_in_number: str,
    requested_by: Optional[str],
    target_username: Optional[str],
) -> bool:
    """
    Called from rf_queue_worker.py._process_migo_103 the moment a without_po
    record's MIGO 103 submission captures a real PO number. Best-effort --
    a failure here must never block MIGO 103's own posting (see caller).

    Resets status back to 'pending' on conflict -- if a prior attempt had
    already failed or (unusually) another PO number is entered again later,
    this always reflects the latest request, not stale resolved state.
    """
    sql = """
        INSERT INTO pending_po_updates
            (history_id, po_number, gate_in_number, requested_by,
             requested_at, target_username, status,
             resolved_by, resolved_at, error_message)
        VALUES (%s, %s, %s, %s, %s, %s, 'pending', NULL, NULL, NULL)
        ON CONFLICT (history_id) DO UPDATE SET
            po_number       = EXCLUDED.po_number,
            gate_in_number  = EXCLUDED.gate_in_number,
            requested_by    = EXCLUDED.requested_by,
            requested_at    = EXCLUDED.requested_at,
            target_username = EXCLUDED.target_username,
            status          = 'pending',
            resolved_by     = NULL,
            resolved_at     = NULL,
            error_message   = NULL
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    history_id, po_number, gate_in_number, requested_by,
                    datetime.now(), target_username
                ))
            conn.commit()
        logger.info(
            f"pending_po_updates upserted — history_id={history_id} "
            f"po={po_number} target={target_username}"
        )
        return True
    except Exception as e:
        logger.error(f"upsert_pending_po_update failed for history_id={history_id}: {e}")
        return False


def get_pending_po_updates_for_user(username: str, is_superadmin: bool = False) -> list:
    """
    Rows this user should see on the History page's Pending PO Updates
    panel: status IN ('pending','failed') AND (target_username = them OR
    target_username IS NULL -- legacy records with no captured submitter,
    treated as "anyone with gate_in access can pick this up"). A SuperAdmin
    sees every such row regardless of target, as a backstop.

    'failed' rows are included deliberately (not just 'pending') -- a
    transient SAP failure (session timeout, popup, network blip) must stay
    visible with a retry path, not vanish silently. See mark_pending_po_resolved.
    """
    sql = """
        SELECT p.id, p.history_id, p.po_number, p.gate_in_number,
               p.requested_by, p.requested_at, p.target_username,
               p.status, p.error_message,
               COALESCE(inv.invoice_number, h.invoice_number) AS invoice_number
        FROM pending_po_updates p
        JOIN history h ON h.id = p.history_id
        LEFT JOIN invoice_data inv ON inv.id = h.id
        WHERE p.status IN ('pending', 'failed')
    """
    params: tuple = ()
    if not is_superadmin:
        sql += " AND (p.target_username = %s OR p.target_username IS NULL)"
        params = (username,)
    sql += " ORDER BY p.requested_at ASC"

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"get_pending_po_updates_for_user failed for {username!r}: {e}")
        return []


def get_pending_po_update(history_id: int) -> Optional[dict]:
    """Single row lookup -- used by the /run route to fetch po_number/gate_in_number
    before enqueuing the zgatein_update job, and to enforce the target/visibility check.
    Includes 'failed' rows so a retry click after a transient failure works."""
    sql = "SELECT * FROM pending_po_updates WHERE history_id = %s AND status IN ('pending', 'failed')"
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (history_id,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"get_pending_po_update failed for history_id={history_id}: {e}")
        return None


def mark_pending_po_resolved(
    history_id: int,
    success: bool,
    resolved_by: Optional[str] = None,
    error_message: Optional[str] = None,
) -> bool:
    """
    Called from rf_queue_worker.py._process_update_gatein_po once the
    zgatein_update job completes (success or failure). A failed attempt is
    marked 'failed' (kept visible in the panel with a Retry action, see
    get_pending_po_updates_for_user) rather than deleted or hidden.

    WHERE is keyed on history_id alone (no status filter) -- pending_po_updates
    has a UNIQUE(history_id) constraint so there's always exactly one row per
    record. An earlier version filtered WHERE status='pending', which meant a
    *second* attempt after a first failure (status already 'failed') could
    never update the row again, regardless of outcome. Fixed in v17.1.
    """
    sql = """
        UPDATE pending_po_updates
        SET status        = %s,
            resolved_by   = %s,
            resolved_at   = %s,
            error_message = %s
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    "done" if success else "failed",
                    resolved_by, datetime.now(), error_message, history_id
                ))
            conn.commit()
        logger.info(
            f"pending_po_updates resolved — history_id={history_id} "
            f"success={success} by={resolved_by}"
        )
        return True
    except Exception as e:
        logger.error(f"mark_pending_po_resolved failed for history_id={history_id}: {e}")
        return False
