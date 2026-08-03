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
    Search supplier_master two ways at once, merged into one ranked list:
      1. Fuzzy name match (trigram similarity, requires pg_trgm -- see
         schema_migration_v8.sql), against name_1 first, falling back to
         name for suppliers where name_1 is blank.
      2. Vendor-code prefix match against the `supplier` column itself --
         covers the case where a guard/user remembers/types part of the
         6-digit SAP code directly instead of the vendor's name. Code
         matches are ranked first (score forced to 1.0) since a typed code
         is a much stronger signal than a fuzzy name guess.

    Returns a list of dicts:
      [{supplier, name_1, name, city, district, postal_code,
        street_2, street_3, street_4, street_5, building, floor, score}]
    ordered by closest match first. Deliberately does NOT auto-pick a single
    result even if only one row comes back -- the caller (Gate In tab) still
    shows it as a one-item pick-list so the disambiguating city/district (and,
    for same-name/same-postal-code duplicates, the full address) is visible
    before the user commits to it.
    """
    query = (query or "").strip()
    if not query:
        return []

    address_cols = "street_2, street_3, street_4, street_5, building, floor"

    sql = f"""
        SELECT supplier, name_1, name, city, district, postal_code,
               {address_cols}, score
        FROM (
            SELECT
                supplier, name_1, name, city, district, postal_code,
                {address_cols},
                GREATEST(
                    similarity(COALESCE(name_1, ''), %s),
                    similarity(COALESCE(name, ''), %s)
                ) AS score
            FROM supplier_master
            WHERE similarity(COALESCE(name_1, ''), %s) > %s
               OR similarity(COALESCE(name, ''), %s)   > %s

            UNION ALL

            SELECT
                supplier, name_1, name, city, district, postal_code,
                {address_cols},
                1.0 AS score
            FROM supplier_master
            WHERE supplier ILIKE %s
        ) matches
        ORDER BY score DESC, supplier
        LIMIT %s
    """
    code_prefix = query + "%"
    params = (
        query, query,               # GREATEST(...) similarity args
        query, MIN_SIMILARITY,      # name_1 filter
        query, MIN_SIMILARITY,      # name filter
        code_prefix,                # supplier code prefix match
        limit,
    )

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]
                # UNION ALL can duplicate a row if it matches both by name
                # and by code prefix -- dedupe by supplier code, keeping the
                # first (highest-scored) occurrence.
                seen, deduped = set(), []
                for r in rows:
                    if r["supplier"] in seen:
                        continue
                    seen.add(r["supplier"])
                    deduped.append(r)
                return deduped
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
