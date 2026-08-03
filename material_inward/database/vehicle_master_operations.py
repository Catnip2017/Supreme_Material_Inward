"""
database/vehicle_master_operations.py — Vehicle master lookup.

Table columns: truck_number, driver_name,
               transporter_name, licence_number  (4 columns)

get_drivers_by_truck(truck_number):
    Returns all rows for the truck after normalising the number
    (strips spaces/hyphens, uppercases).
    len == 0 -> no record, user fills manually
    len == 1 -> auto-populate all fields
    len  > 1 -> show driver dropdown

bulk_insert_vehicle_master(rows):
    One-time data load from Excel.
    Each row dict must have keys matching column names.
"""

import re
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


def _normalize_truck(truck_number: str) -> str:
    """
    Strip spaces and hyphens, uppercase.
    'mh-06 bw9067' -> 'MH06BW9067'
    """
    if not truck_number:
        return ""
    return re.sub(r'[\s\-]', '', truck_number.strip().upper())


def get_drivers_by_truck(truck_number: str) -> list:
    """
    Lookup all driver records for a given truck number.
    Normalisation is applied both to the input and the stored value
    so formatting differences in the data do not break lookups.

    Returns list of dicts:
      [{driver_name, transporter_name, licence_number}]
    """
    normalized = _normalize_truck(truck_number)
    if not normalized:
        return []

    sql = """
        SELECT driver_name, transporter_name, licence_number
        FROM vehicle_master
        WHERE UPPER(REGEXP_REPLACE(truck_number, '[[:space:]-]', '', 'g')) = %s
        ORDER BY driver_name
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (normalized,))
                rows = cur.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"get_drivers_by_truck failed for '{truck_number}': {e}")
        return []


def bulk_insert_vehicle_master(rows: list) -> int:
    """
    Insert rows from Excel load.
    Silently skips exact duplicates (all 4 fields identical).
    Returns count inserted.

    Each row dict expected keys:
      truck_number, driver_name,
      transporter_name, licence_number
    """
    if not rows:
        return 0

    sql = """
        INSERT INTO vehicle_master
            (truck_number, driver_name, transporter_name, licence_number)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """
    inserted = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(sql, (
                        (row.get("truck_number")     or "").strip().upper(),
                        (row.get("driver_name")      or "").strip(),
                        (row.get("transporter_name") or "").strip(),
                        (row.get("licence_number")   or "").strip(),
                    ))
                    inserted += cur.rowcount
        logger.info(f"Vehicle master bulk insert: {inserted} row(s) added")
        return inserted
    except Exception as e:
        logger.error(f"bulk_insert_vehicle_master failed: {e}")
        return 0 