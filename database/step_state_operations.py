"""
database/step_state_operations.py -- per-user drafts, "X is on it" markers and
the "Waiting for MIGO 103" list.

Tables come from schema_migration_v33.sql (step_drafts, step_claims).

Everything here is best-effort UI support: a failure returns an empty/False
result and is logged, and never raises into a posting path.

Timestamps: the columns are plain TIMESTAMP (server-local, like the rest of
the schema), so all age maths below is done in SQL with LOCALTIMESTAMP rather
than mixing Python and DB clocks.
"""

import json
from datetime import datetime, timedelta
from typing import Optional

import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)

CLAIM_FRESH_SECONDS = 90          # heartbeat older than this = nobody is on it
DRAFT_MAX_AGE_DAYS = 14
DRAFT_MAX_BYTES = 200 * 1024
SUPPORTED_STEPS = ("migo_103", "migo_105")


def _iso(v):
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


# ------------------------------------------------------------------ drafts

def save_draft(history_id: int, step: str, username: str, data: dict) -> bool:
    if step not in SUPPORTED_STEPS or not username:
        return False
    try:
        raw = json.dumps(data)
        if len(raw.encode("utf-8")) > DRAFT_MAX_BYTES:
            logger.warning(f"Draft too large, not saved - history={history_id} step={step}")
            return False
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO step_drafts (history_id, step, username, data, updated_at)
                    VALUES (%s, %s, %s, %s::jsonb, LOCALTIMESTAMP)
                    ON CONFLICT (history_id, step, username)
                    DO UPDATE SET data = EXCLUDED.data, updated_at = LOCALTIMESTAMP
                    """,
                    (history_id, step, username, raw),
                )
                # Opportunistic housekeeping -- indexed on updated_at, cheap.
                cur.execute(
                    "DELETE FROM step_drafts WHERE updated_at < LOCALTIMESTAMP - (%s || ' days')::interval",
                    (str(DRAFT_MAX_AGE_DAYS),),
                )
        return True
    except Exception as e:
        logger.error(f"save_draft failed history={history_id} step={step}: {e}")
        return False


def get_draft(history_id: int, step: str, username: str) -> Optional[dict]:
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT data, updated_at FROM step_drafts
                    WHERE history_id = %s AND step = %s AND username = %s
                    """,
                    (history_id, step, username),
                )
                row = cur.fetchone()
                if not row:
                    return None
                data = row["data"]
                if isinstance(data, str):
                    data = json.loads(data)
                return {"data": data, "updated_at": _iso(row["updated_at"])}
    except Exception as e:
        logger.error(f"get_draft failed history={history_id} step={step}: {e}")
        return None


def get_other_drafts(history_id: int, step: str, username: str) -> list:
    """Who ELSE has an unposted draft for this record (names + age only,
    never their content)."""
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT username,
                           EXTRACT(EPOCH FROM (LOCALTIMESTAMP - updated_at))::int AS age_seconds
                    FROM step_drafts
                    WHERE history_id = %s AND step = %s AND username <> %s
                    ORDER BY updated_at DESC
                    """,
                    (history_id, step, username),
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"get_other_drafts failed history={history_id} step={step}: {e}")
        return []


def delete_draft(history_id: int, step: str, username: Optional[str] = None) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if username:
                    cur.execute(
                        "DELETE FROM step_drafts WHERE history_id=%s AND step=%s AND username=%s",
                        (history_id, step, username),
                    )
                else:
                    cur.execute(
                        "DELETE FROM step_drafts WHERE history_id=%s AND step=%s",
                        (history_id, step),
                    )
        return True
    except Exception as e:
        logger.error(f"delete_draft failed history={history_id} step={step}: {e}")
        return False


# ------------------------------------------------------------------ claims

def touch_claim(history_id: int, step: str, username: str) -> bool:
    if step not in SUPPORTED_STEPS or not username:
        return False
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO step_claims (history_id, step, username, heartbeat_at)
                    VALUES (%s, %s, %s, LOCALTIMESTAMP)
                    ON CONFLICT (history_id, step, username)
                    DO UPDATE SET heartbeat_at = LOCALTIMESTAMP
                    """,
                    (history_id, step, username),
                )
                cur.execute(
                    "DELETE FROM step_claims WHERE heartbeat_at < LOCALTIMESTAMP - INTERVAL '1 day'"
                )
        return True
    except Exception as e:
        logger.error(f"touch_claim failed history={history_id} step={step}: {e}")
        return False


def release_claim(history_id: int, step: str, username: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM step_claims WHERE history_id=%s AND step=%s AND username=%s",
                    (history_id, step, username),
                )
        return True
    except Exception as e:
        logger.error(f"release_claim failed history={history_id} step={step}: {e}")
        return False


def clear_step_state(history_id: int, step: str) -> bool:
    """Called once a step has posted: its drafts and claims are obsolete."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM step_drafts WHERE history_id=%s AND step=%s", (history_id, step))
                cur.execute("DELETE FROM step_claims WHERE history_id=%s AND step=%s", (history_id, step))
        return True
    except Exception as e:
        logger.error(f"clear_step_state failed history={history_id} step={step}: {e}")
        return False


# ------------------------------------------------------------ waiting list

def get_pending_migo_103(username: str, limit: int = 100, days: int = 30) -> list:
    """Records whose Gate In is posted and MIGO 103 is not yet (same
    definition as History's "Gate In Approved" bucket), oldest first.

    Per row: who else currently has it open (fresh claims only, excluding
    the caller), whether a MIGO 103 job is already pending/running for it,
    and whether the caller has their own saved draft."""
    sql = f"""
        WITH active AS (
            SELECT DISTINCT ON (history_id) history_id, status
            FROM rf_queue
            WHERE step = 'migo_103' AND status IN ('pending', 'running')
            ORDER BY history_id, id DESC
        )
        SELECT
            h.id,
            COALESCE(inv.invoice_number, h.invoice_number)                      AS invoice_number,
            COALESCE(sm.name_1, sm.name, gatein.vendor_name, inv.seller_name)   AS vendor_name,
            COALESCE(gatein.gate_in_number, h.gate_in_number)                   AS gate_in_number,
            h.gatein_done_at                                                    AS gatein_done_at,
            active.status                                                       AS posting_status,
            ARRAY(
                SELECT c.username FROM step_claims c
                WHERE c.history_id = h.id AND c.step = 'migo_103'
                  AND c.username <> %s
                  AND c.heartbeat_at > LOCALTIMESTAMP - INTERVAL '{CLAIM_FRESH_SECONDS} seconds'
                ORDER BY c.heartbeat_at DESC
            )                                                                   AS claimed_by,
            EXISTS (
                SELECT 1 FROM step_drafts d
                WHERE d.history_id = h.id AND d.step = 'migo_103' AND d.username = %s
            )                                                                   AS has_my_draft
        FROM history h
        LEFT JOIN invoice_data     inv    ON inv.id = h.id
        LEFT JOIN gate_in_entries  gatein ON gatein.history_id = h.id
        LEFT JOIN supplier_master  sm     ON sm.supplier = gatein.vendor_name
        LEFT JOIN active                  ON active.history_id = h.id
        WHERE h.gate_in = 1
          AND COALESCE(h.migo_103, 0) = 0
          AND COALESCE(h.migo_101, 0) = 0
          AND COALESCE(h.gatein_done_at, h.created_at) >= %s
        ORDER BY h.gatein_done_at ASC NULLS LAST, h.id ASC
        LIMIT %s
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # gatein_done_at is written by the app with Python's datetime.now()
                # (db_operations.update_history_step), so the waiting time and
                # the 30-day window are worked out the same way here in Python,
                # not with the database's clock/timezone.
                now = datetime.now()
                cutoff = now - timedelta(days=days)
                cur.execute(sql, (username, username, cutoff, limit))
                rows = []
                for r in cur.fetchall():
                    r = dict(r)
                    r["claimed_by"] = list(r.get("claimed_by") or [])
                    done = r.pop("gatein_done_at", None)
                    r["waiting_seconds"] = (
                        max(0, int((now - done).total_seconds())) if done is not None else None
                    )
                    rows.append(r)
                return rows
    except Exception as e:
        logger.error(f"get_pending_migo_103 failed: {e}")
        return []
