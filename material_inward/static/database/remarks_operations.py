"""
database/remarks_operations.py
CRUD operations for the record-wide Remarks & Comments feature
(history_remarks + history_comments -- see schema_migration_v12.sql).

Design recap:
  - history_remarks: one row per history_id, the single root "Remark",
    only ever edited by the Compliance role (or a SuperAdmin with edit
    rights) -- see app.py's POST /api/remarks/<history_id>. Upserted
    in place, same as any other single-value field.
  - history_comments: append-only log. Every add_comment() call INSERTs
    a new row -- never UPDATEs or DELETEs one. get_comments() below only
    ever returns the most recent row per (history_id, role), so from the
    UI's point of view a role "overwrites" its own comment by posting
    again, but the full history stays in the table for audit purposes.

Both tables intentionally store the posting username (updated_by /
created_by) for internal audit, but neither value is ever returned by
these functions to keep that decision centralized -- the client
requirement is that comments show the ROLE, not the person, so the API
layer (app.py) and the UI never see the username at all.
"""

from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)

# Display order for comments -- mirrors the pipeline order used throughout
# the rest of the app (and user_management.html's ROLE_LABELS), so the
# comment list reads top-to-bottom in the same order the record actually
# moves through the workflow, regardless of which role commented most
# recently.
_ROLE_ORDER = ["compliance", "gate_in", "migo_103", "migo_105", "miro", "SuperAdmin"]


def _role_sort_key(role: str) -> int:
    try:
        return _ROLE_ORDER.index(role)
    except ValueError:
        return len(_ROLE_ORDER)  # unknown roles sort last, rather than erroring


# ── Remark (single value per record) ───────────────────────────────────────

def get_remark(history_id: int) -> Optional[dict]:
    """Return {remark_text, updated_by_role, updated_at} or None if never set."""
    sql = (
        "SELECT remark_text, updated_by_role, updated_at "
        "FROM history_remarks WHERE history_id = %s"
    )
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (history_id,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[remarks_ops] get_remark failed for history_id={history_id}: {e}")
        return None


def upsert_remark(history_id: int, remark_text: str, role: str, username: str) -> bool:
    """Insert or overwrite the single Remark for this record."""
    sql = """
        INSERT INTO history_remarks (history_id, remark_text, updated_by_role, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (history_id) DO UPDATE SET
            remark_text     = EXCLUDED.remark_text,
            updated_by_role = EXCLUDED.updated_by_role,
            updated_by      = EXCLUDED.updated_by,
            updated_at      = CURRENT_TIMESTAMP
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (history_id, remark_text, role, username))
            conn.commit()
        logger.info(f"[remarks_ops] remark saved for history_id={history_id} by role={role}")
        return True
    except Exception as e:
        logger.error(f"[remarks_ops] upsert_remark failed for history_id={history_id}: {e}")
        return False


# ── Comments (append-only log, latest-per-role display) ─────────────────────

def get_comments(history_id: int) -> list:
    """
    Return the most recent comment per role for this record, ordered to
    match the pipeline sequence (compliance -> gate_in -> migo_103 ->
    migo_105 -> miro -> SuperAdmin), regardless of posting order.
    Each item: {role, comment_text, created_at}. Username is never included.
    """
    # DISTINCT ON (role) + ORDER BY role, created_at DESC picks exactly one
    # row per role -- the newest one -- straight from Postgres, without
    # needing a window function or a second query.
    sql = """
        SELECT DISTINCT ON (role) role, comment_text, created_at
        FROM history_comments
        WHERE history_id = %s
        ORDER BY role, created_at DESC
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (history_id,))
                rows = [dict(r) for r in cur.fetchall()]
                rows.sort(key=lambda r: _role_sort_key(r["role"]))
                return rows
    except Exception as e:
        logger.error(f"[remarks_ops] get_comments failed for history_id={history_id}: {e}")
        return []


def add_comment(history_id: int, role: str, comment_text: str, username: str) -> bool:
    """
    Always INSERTs a new row -- never updates an existing one. A role
    "overwriting" its own comment is purely a read-side effect of
    get_comments() only surfacing the latest row per role; the previous
    comment(s) stay in the table untouched, for audit purposes.
    """
    sql = """
        INSERT INTO history_comments (history_id, role, comment_text, created_by, created_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (history_id, role, comment_text, username))
            conn.commit()
        logger.info(f"[remarks_ops] comment added for history_id={history_id} role={role}")
        return True
    except Exception as e:
        logger.error(f"[remarks_ops] add_comment failed for history_id={history_id}: {e}")
        return False
