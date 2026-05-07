"""
database/user_operations.py — User management operations.
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
    """
    Verify credentials. Returns user dict on success, None on failure.
    """
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
                        "username": user["username"],
                        "role": user["role"],
                        "name": user["name"]
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
                    "SELECT id, username, role, name, created_at, updated_at "
                    "FROM users ORDER BY created_at DESC"
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        return []


def add_user(username: str, password: str, role: str, name: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                if cur.fetchone():
                    logger.warning(f"User already exists: {username}")
                    return False
                cur.execute(
                    "INSERT INTO users (username, password, role, name) VALUES (%s, %s, %s, %s)",
                    (username, hash_password(password), role, name)
                )
                logger.info(f"User created: {username}")
                return True
    except Exception as e:
        logger.error(f"Error adding user {username}: {e}")
        return False


def update_user(username: str, password: str, role: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                if not cur.fetchone():
                    logger.warning(f"User not found for update: {username}")
                    return False
                cur.execute(
                    "UPDATE users SET password = %s, role = %s, updated_at = CURRENT_TIMESTAMP "
                    "WHERE username = %s",
                    (hash_password(password), role, username)
                )
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
