"""
database/notifications_operations.py — In-app notifications CRUD.

When ENABLE_INAPP_NOTIFICATIONS=false, all functions early-return cleanly
without DB hits.
"""

from datetime import datetime
from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.config import config
from config.logger import get_logger

logger = get_logger(__name__)


def create_notification(
    history_id: int,
    title: str,
    message: str,
    notification_type: str,
    user_target: Optional[str] = None,
    role_target: Optional[str] = None,
) -> bool:
    """
    Create a new notification.

    user_target: specific username (optional)
    role_target: step_role (gate_in / migo_103 / migo_105 / miro / all) — broadcast to users with matching role
    notification_type: approve / hold / gate_in / migo_103 / migo_105 / miro / ocr_failed
    """
    if not config.ENABLE_INAPP_NOTIFICATIONS:
        return False

    sql = """
        INSERT INTO notifications (
            user_target, role_target, history_id, title, message, type
        ) VALUES (%s, %s, %s, %s, %s, %s)
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    user_target, role_target, history_id,
                    title, message, notification_type
                ))
                logger.info(
                    f"Notification created — history={history_id} "
                    f"target_user={user_target} target_role={role_target} type={notification_type}"
                )
                return True
    except Exception as e:
        logger.error(f"Failed to create notification: {e}")
        return False


def get_unread_for_user(username: str, user_step_roles: str) -> list:
    """
    Get unread notifications visible to this user:
      - user_target = this username, OR
      - role_target matches one of user's step_roles, OR
      - role_target = 'all'

    user_step_roles: comma-separated string from users.step_roles
    """
    if not config.ENABLE_INAPP_NOTIFICATIONS:
        return []

    # Build the role match conditions
    user_roles = [r.strip().lower() for r in (user_step_roles or "").split(",") if r.strip()]
    if not user_roles or "all" in user_roles:
        # User has 'all' roles — see everything
        sql = """
            SELECT id, history_id, title, message, type, role_target, user_target,
                created_at, is_read
            FROM notifications
            WHERE is_read = FALSE
            ORDER BY created_at DESC
            LIMIT 50
        """
        params = ()
    else:
        sql = """
            SELECT id, history_id, title, message, type, role_target, user_target,
                created_at, is_read
            FROM notifications
            WHERE is_read = FALSE
            AND (
                user_target = %s
                OR role_target = 'all'
                OR role_target = ANY(%s)
            )
            ORDER BY created_at DESC
            LIMIT 50
        """
        params = (username, user_roles)
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                result = []
                for r in rows:
                    r = dict(r)
                    if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
                        r["created_at"] = r["created_at"].isoformat()
                    result.append(r)
                return result
    except Exception as e:
        logger.error(f"Failed to fetch notifications for {username}: {e}")
        return []


def mark_as_read(notification_id: int) -> bool:
    if not config.ENABLE_INAPP_NOTIFICATIONS:
        return True
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notifications SET is_read = TRUE WHERE id = %s",
                    (notification_id,)
                )
                return True
    except Exception as e:
        logger.error(f"Failed to mark notification {notification_id} read: {e}")
        return False


def mark_all_as_read_for_user(username: str) -> int:
    """Mark all unread notifications targeted at this user as read."""
    if not config.ENABLE_INAPP_NOTIFICATIONS:
        return 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE notifications
                    SET is_read = TRUE
                    WHERE user_target = %s AND is_read = FALSE
                    """,
                    (username,)
                )
                return cur.rowcount
    except Exception as e:
        logger.error(f"Failed to mark all read for {username}: {e}")
        return 0


def cleanup_old_notifications(days: int = 7) -> int:
    """Delete all notifications older than `days`. Called by daily cleanup task."""
    sql = """
        DELETE FROM notifications
        WHERE created_at < NOW() - INTERVAL '%s days'
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (days,))
                count = cur.rowcount
                if count > 0:
                    logger.info(f"Deleted {count} notification(s) older than {days} days")
                return count
    except Exception as e:
        logger.error(f"Failed to cleanup notifications: {e}")
        return 0
    
def mark_all_read(username: str, user_step_roles: str = "all") -> bool:
    if not config.ENABLE_INAPP_NOTIFICATIONS:
        return True
    user_roles = [r.strip().lower() for r in (user_step_roles or "").split(",") if r.strip()]
    if not user_roles:
        user_roles = ["all"]
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE notifications SET is_read=TRUE 
                       WHERE is_read=FALSE AND (
                           user_target = %s OR role_target = 'all' OR role_target = ANY(%s)
                       )""",
                    (username, user_roles)
                )
        return True
    except Exception as e:
        logger.error(f"mark_all_read failed: {e}")
        return False