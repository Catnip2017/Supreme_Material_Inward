"""
database/gst_operations.py
CRUD operations for the gst_approval table.

All functions follow the same pattern as gatein_operations.py:
  - history_id is the FK linking back to history.id
  - get_connection() from the shared pool
  - logger from config.logger
"""

from datetime import datetime
from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


# ── Upsert (insert or update) ─────────────────────────────────────────────────

def upsert_gst_approval(history_id: int, data: dict) -> bool:
    """
    Insert or update the gst_approval row for this history_id.
    Called by gst_runner after both bots complete.
    data keys match the table columns (all optional).
    """
    sql = """
        INSERT INTO gst_approval (
            history_id,
            einvoice_status,   einvoice_screenshot,
            gstin_status,      legal_name,          taxpayer_type,
            gstr3b_last_filed, gstr3b_tax_period,   gstr3b_status,
            gstr1_last_filed,  gstr1_tax_period,    gstr1_status,
            taxpayer_screenshot,
            bot_error,         checked_at
        ) VALUES (
            %s,
            %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s,
            %s, %s
        )
        ON CONFLICT (history_id) DO UPDATE SET
            einvoice_status     = EXCLUDED.einvoice_status,
            einvoice_screenshot = EXCLUDED.einvoice_screenshot,
            gstin_status        = EXCLUDED.gstin_status,
            legal_name          = EXCLUDED.legal_name,
            taxpayer_type       = EXCLUDED.taxpayer_type,
            gstr3b_last_filed   = EXCLUDED.gstr3b_last_filed,
            gstr3b_tax_period   = EXCLUDED.gstr3b_tax_period,
            gstr3b_status       = EXCLUDED.gstr3b_status,
            gstr1_last_filed    = EXCLUDED.gstr1_last_filed,
            gstr1_tax_period    = EXCLUDED.gstr1_tax_period,
            gstr1_status        = EXCLUDED.gstr1_status,
            taxpayer_screenshot = EXCLUDED.taxpayer_screenshot,
            bot_error           = EXCLUDED.bot_error,
            checked_at          = EXCLUDED.checked_at
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    history_id,
                    data.get("einvoice_status"),   data.get("einvoice_screenshot"),
                    data.get("gstin_status"),      data.get("legal_name"),       data.get("taxpayer_type"),
                    data.get("gstr3b_last_filed"), data.get("gstr3b_tax_period"), data.get("gstr3b_status"),
                    data.get("gstr1_last_filed"),  data.get("gstr1_tax_period"),  data.get("gstr1_status"),
                    data.get("taxpayer_screenshot"),
                    data.get("bot_error"),         data.get("checked_at", datetime.now()),
                ))
            conn.commit()
        logger.info(f"[gst_ops] upserted gst_approval for history_id={history_id}")
        return True
    except Exception as e:
        logger.error(f"[gst_ops] upsert failed for history_id={history_id}: {e}")
        return False


# ── Read ──────────────────────────────────────────────────────────────────────

def get_gst_approval(history_id: int) -> Optional[dict]:
    """Return the gst_approval row for this history_id, or None if not found."""
    sql = "SELECT * FROM gst_approval WHERE history_id = %s"
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (history_id,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[gst_ops] get failed for history_id={history_id}: {e}")
        return None


# ── Approve ───────────────────────────────────────────────────────────────────

def approve_gst(history_id: int, approved_by: str) -> bool:
    """Mark GST approval as approved."""
    sql = """
        UPDATE gst_approval
        SET approval_status = 'approved',
            approval_by     = %s,
            approval_at     = %s,
            hold_reason     = NULL
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (approved_by, datetime.now(), history_id))
            conn.commit()
        # Also mark gst_check step done on history table
        _mark_gst_check_done(history_id)
        logger.info(f"[gst_ops] approved history_id={history_id} by {approved_by}")
        return True
    except Exception as e:
        logger.error(f"[gst_ops] approve failed for history_id={history_id}: {e}")
        return False


# ── Hold ──────────────────────────────────────────────────────────────────────

def hold_gst(history_id: int, held_by: str, reason: str = "") -> bool:
    """Place GST approval on hold. reason is optional."""
    sql = """
        UPDATE gst_approval
        SET approval_status = 'hold',
            approval_by     = %s,
            approval_at     = %s,
            hold_reason     = %s
        WHERE history_id = %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (held_by, datetime.now(), reason or None, history_id))
            conn.commit()
        logger.info(f"[gst_ops] hold history_id={history_id} by {held_by} reason={reason!r}")
        return True
    except Exception as e:
        logger.error(f"[gst_ops] hold failed for history_id={history_id}: {e}")
        return False


# ── Re-run reset ─────────────────────────────────────────────────────────────

def reset_gst_for_rerun(history_id: int) -> bool:
    """
    Reset gst_approval back to clean state so bots can re-run.
    Clears all bot results and resets approval_status to pending.
    Also rolls back gst_check on history so Gate In is re-locked.
    """
    sql_approval = (
        "UPDATE gst_approval "
        "SET einvoice_status = NULL, einvoice_screenshot = NULL, "
        "    gstin_status = NULL, legal_name = NULL, taxpayer_type = NULL, "
        "    gstr3b_last_filed = NULL, gstr3b_tax_period = NULL, gstr3b_status = NULL, "
        "    gstr1_last_filed = NULL, gstr1_tax_period = NULL, gstr1_status = NULL, "
        "    taxpayer_screenshot = NULL, approval_status = 'pending', "
        "    approval_by = NULL, approval_at = NULL, hold_reason = NULL, "
        "    bot_error = NULL, checked_at = NULL "
        "WHERE history_id = %s"
    )
    sql_history = (
        "UPDATE history "
        "SET gst_check = 0, gst_check_done_at = NULL, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = %s"
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_approval, (history_id,))
                cur.execute(sql_history,  (history_id,))
            conn.commit()
        logger.info(f"[gst_ops] reset for rerun history_id={history_id}")
        return True
    except Exception as e:
        logger.error(f"[gst_ops] reset_gst_for_rerun failed id={history_id}: {e}")
        return False


# ── Internal helpers ──────────────────────────────────────────────────────────

def _mark_gst_check_done(history_id: int) -> None:
    """Set history.gst_check = 1 and gst_check_done_at = now."""
    sql = (
        "UPDATE history "
        "SET gst_check = 1, gst_check_done_at = %s, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = %s"
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (datetime.now(), history_id))
            conn.commit()
        logger.info(f"[gst_ops] history.gst_check=1 for id={history_id}")
    except Exception as e:
        logger.error(f"[gst_ops] _mark_gst_check_done failed id={history_id}: {e}")
