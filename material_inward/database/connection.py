"""
database/connection.py — PostgreSQL connection pool.
All database modules import get_connection() from here.
Uses psycopg2 with a simple connection pool for thread safety.
"""

import psycopg2
import psycopg2.pool
import psycopg2.extras
from contextlib import contextmanager
from config.config import config
from config.logger import get_logger

logger = get_logger(__name__)

# Global connection pool — initialized once at startup
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def init_pool() -> None:
    """
    Initialize the connection pool. Call this once at application startup.
    """
    global _pool
    try:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=config.DB_MIN_CONNECTIONS,
            maxconn=config.DB_MAX_CONNECTIONS,
            host=config.DB_HOST,
            port=config.DB_PORT,
            dbname=config.DB_NAME,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            connect_timeout=10
        )
        logger.info("PostgreSQL connection pool initialized successfully.")
    except psycopg2.Error as e:
        logger.critical(f"FATAL: Failed to initialize DB connection pool: {e}")
        raise


def close_pool() -> None:
    """
    Close all connections in the pool. Call at application shutdown.
    """
    global _pool
    if _pool:
        _pool.closeall()
        logger.info("PostgreSQL connection pool closed.")


@contextmanager
def get_connection():
    """
    Context manager that checks out a connection from the pool,
    yields it, then returns it to the pool.

    Usage:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(...)

    Automatically commits on success, rolls back on exception.
    """
    if _pool is None:
        init_pool()

    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error — transaction rolled back: {e}")
        raise
    finally:
        _pool.putconn(conn)


def test_connection() -> bool:
    """
    Test that the database is reachable. Returns True on success.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        logger.info("Database connection test passed.")
        return True
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return False
