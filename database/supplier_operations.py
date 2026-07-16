"""
database/supplier_operations.py — Vendor/supplier code lookup.

Reads supplier_master (see database/schema_migration_v8.sql), populated by
the standalone VENDOR_MASTER_SYNC bot (outside this app, see
Material Inward/VENDOR_MASTER_SYNC/). Used by the Gate In tab's
"Fetch Vendor Code" button and its live type-ahead search.
"""

import psycopg2.extras
from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)

# similarity() returns 0..1 -- below this, a match is probably noise rather
# than a real candidate (typos/abbreviations still usually score well above
# this). Tune if real vendor names are producing too many/few candidates.
MIN_SIMILARITY = 0.25


def search_suppliers(query: str, limit: int = 10) -> list:
    """
    Fuzzy-search supplier_master by name, ranked by trigram similarity
    (requires the pg_trgm extension -- see schema_migration_v8.sql).
    Matches against name_1 first (primary SAP name field), falling back to
    name for suppliers where name_1 is blank.

    Returns a list of dicts:
      [{supplier, name_1, name, city, district, postal_code, score}]
    ordered by closest match first. Deliberately does NOT auto-pick a single
    result even if only one row comes back -- the caller (Gate In tab) still
    shows it as a one-item pick-list so the disambiguating city/district is
    visible before the user commits to it.
    """
    query = (query or "").strip()
    if not query:
        return []

    sql = """
        SELECT
            supplier, name_1, name, city, district, postal_code,
            GREATEST(
                similarity(COALESCE(name_1, ''), %s),
                similarity(COALESCE(name, ''), %s)
            ) AS score
        FROM supplier_master
        WHERE similarity(COALESCE(name_1, ''), %s) > %s
           OR similarity(COALESCE(name, ''), %s)   > %s
        ORDER BY score DESC
        LIMIT %s
    """
    params = (query, query, query, MIN_SIMILARITY, query, MIN_SIMILARITY, limit)

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"search_suppliers failed for query={query!r}: {e}")
        return []


def get_supplier_by_code(supplier_code: str) -> dict:
    """Direct lookup by exact SAP vendor code (e.g. to re-display a
    previously-saved vendor_code's name/location without a fresh search)."""
    if not supplier_code:
        return {}
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT supplier, name_1, name, city, district FROM supplier_master WHERE supplier = %s",
                    (supplier_code,)
                )
                row = cur.fetchone()
                return dict(row) if row else {}
    except Exception as e:
        logger.error(f"get_supplier_by_code failed for {supplier_code!r}: {e}")
        return {}
