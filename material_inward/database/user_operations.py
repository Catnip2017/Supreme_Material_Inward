"""
database/user_operations.py — User management operations.

v4 changes:
- Added: email, email_notifications_enabled, step_roles columns
- All add/update/get functions now handle these new fields
"""

from typing import Optional
import bcrypt
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


def verify_user(username: str, password: str) -> Optional[dict]:
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM users WHERE username = %s",
                    (username,)
                )
                user = cur.fetchone()
                if not user:
                    logger.warning(f"Login attempt for unknown user: {username}")
                    return None

                user = dict(user)
                if verify_password(password, user["password"]):
                    logger.info(f"User verified: {username}")
                    return {
                        "username":   user["username"],
                        "role":       user["role"],
                        "name":       user["name"],
                        "email":      user.get("email"),
                        "step_roles": user.get("step_roles") or "all",
                    }

                logger.warning(f"Invalid password for user: {username}")
                return None
    except Exception as e:
        logger.error(f"Error verifying user {username}: {e}")
        return None


def get_all_users() -> list:
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, username, role, name,
                           email, email_notifications_enabled, step_roles,
                           created_at, updated_at
                    FROM users
                    ORDER BY created_at DESC
                    """
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        return []


def get_users_for_step(step_role: str) -> list:
    """
    Get users who:
      - have email_notifications_enabled = TRUE
      - have a non-empty email
      - have step_roles matching this step OR 'all'

    Used by mail_service to send per-step notifications.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT username, name, email, step_roles
                    FROM users
                    WHERE email_notifications_enabled = TRUE
                      AND email IS NOT NULL
                      AND email <> ''
                      AND (
                          step_roles = 'all'
                          OR step_roles ILIKE %s
                      )
                    """,
                    (f"%{step_role}%",)
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching users for step {step_role}: {e}")
        return []


def add_user(
    username: str,
    password: str,
    role: str,
    name: str,
    email: str = "",
    email_notifications_enabled: bool = False,
    step_roles: str = "all"
) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                if cur.fetchone():
                    logger.warning(f"User already exists: {username}")
                    return False
                cur.execute(
                    """
                    INSERT INTO users (
                        username, password, role, name,
                        email, email_notifications_enabled, step_roles
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        username,
                        hash_password(password),
                        role,
                        name,
                        email or None,
                        bool(email_notifications_enabled),
                        step_roles or "all",
                    )
                )
                logger.info(f"User created: {username}")
                return True
    except Exception as e:
        logger.error(f"Error adding user {username}: {e}")
        return False


def update_user(
    username: str,
    password: Optional[str] = None,
    role: Optional[str] = None,
    email: Optional[str] = None,
    email_notifications_enabled: Optional[bool] = None,
    step_roles: Optional[str] = None
) -> bool:
    """
    Update a user. Only fields passed (not None) will be updated.
    For password, pass plain text — it will be hashed.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                if not cur.fetchone():
                    logger.warning(f"User not found for update: {username}")
                    return False

                set_parts = []
                values = []

                if password is not None:
                    set_parts.append("password = %s")
                    values.append(hash_password(password))
                if role is not None:
                    set_parts.append("role = %s")
                    values.append(role)
                if email is not None:
                    set_parts.append("email = %s")
                    values.append(email or None)
                if email_notifications_enabled is not None:
                    set_parts.append("email_notifications_enabled = %s")
                    values.append(bool(email_notifications_enabled))
                if step_roles is not None:
                    set_parts.append("step_roles = %s")
                    values.append(step_roles or "all")

                if not set_parts:
                    return True  # nothing to update

                set_parts.append("updated_at = CURRENT_TIMESTAMP")
                values.append(username)

                sql = f"UPDATE users SET {', '.join(set_parts)} WHERE username = %s"
                cur.execute(sql, values)
                logger.info(f"User updated: {username}")
                return True
    except Exception as e:
        logger.error(f"Error updating user {username}: {e}")
        return False


def delete_user(username: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE username = %s", (username,))
                logger.info(f"User deleted: {username}")
                return True
    except Exception as e:
        logger.error(f"Error deleting user {username}: {e}")
        return False