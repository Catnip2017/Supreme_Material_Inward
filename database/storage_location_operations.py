"""
database/storage_location_operations.py
Admin-managed storage location master table operations.
"""

from typing import Optional
import psycopg2.extras
from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


def get_all_storage_locations(active_only: bool = True) -> list:
    """Fetch all storage locations. active_only=True for dropdown, False for admin view."""
    sql = "SELECT id, code, description, is_active FROM storage_locations"
    if active_only:
        sql += " WHERE is_active = TRUE"
    sql += " ORDER BY code"
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"Failed to fetch storage locations: {e}")
        return []


def add_storage_location(code: str, description: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO storage_locations (code, description) VALUES (%s, %s)",
                    (code.strip().upper(), description.strip())
                )
        logger.info(f"Storage location added: {code}")
        return True
    except Exception as e:
        logger.error(f"Failed to add storage location {code}: {e}")
        return False


def update_storage_location(code: str, description: str, is_active: bool) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE storage_locations SET description=%s, is_active=%s, updated_at=CURRENT_TIMESTAMP WHERE code=%s",
                    (description.strip(), is_active, code)
                )
        logger.info(f"Storage location updated: {code}")
        return True
    except Exception as e:
        logger.error(f"Failed to update storage location {code}: {e}")
        return False
