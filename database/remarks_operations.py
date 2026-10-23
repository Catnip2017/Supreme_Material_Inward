"""
database/remarks_operations.py
CRUD operations for the record-wide Remarks & Comments feature
(history_remarks + history_comments -- see schema_migration_v12.sql).

Design recap:
  - history_remarks: one row per history_id, the single root "Remark".
    Write-once: only ever set ONE time, by the Compliance role (or a
    SuperAdmin with edit rights) -- see app.py's POST /api/remarks/<history_id>,
    which now rejects the request if a remark already exists for that
    history_id. upsert_remark() itself still performs an INSERT ... ON
    CONFLICT (kept as-is, harmless) but the write-once rule is enforced
    one layer up, in the route, per this codebase's established
    convention that the route layer decides permission/lock rules and
    the db layer just executes the write.
  - history_comments: append-only log. Every add_comment() call INSERTs
    a new row -- never UPDATEs or DELETEs one. get_comments() below
    returns the FULL chronological history (oldest first), each with the
    posting username, so every subsequent user can see who said what.

history_remarks.updated_by / history_comments.created_by have always
stored the posting username. Comments now surface it (client requirement:
show the individual person, not just their role) -- see get_comments().
"""

from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


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


# ── Comments (append-only log, full chronological history) ──────────────────

def get_comments(history_id: int) -> list:
    """
    Return EVERY comment ever posted on this record, oldest first, each
    tagged with the role it was posted as AND the username of the person
    who posted it. Comments can never be edited or removed -- this is a
    plain, complete, append-only history (no DISTINCT ON collapsing).
    Each item: {role, username, comment_text, created_at}.
    """
    sql = """
        SELECT role, created_by AS username, comment_text, created_at
        FROM history_comments
        WHERE history_id = %s
        ORDER BY created_at ASC
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (history_id,))
                return [dict(r) for r in cur.fetchall()]
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
