"""
database/rf_queue_operations.py — RF Execution Queue operations.

Guarantees only one RF script runs at a time across all users.
Flow:
  1. User clicks Save & Post → job inserted into rf_queue with status=pending
  2. Background worker checks queue every 5 seconds
  3. If no job is currently running, picks the oldest pending job
  4. Sets it to running, executes RF, stores result, sets done/failed
  5. Flask API polls /api/queue_status/<job_id> to get result
"""

import json
from datetime import datetime
from typing import Optional
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger

logger = get_logger(__name__)


def enqueue_rf_job(history_id: int, step: str, payload: dict) -> Optional[int]:
    """
    Add an RF job to the queue. Returns the job ID.
    If a job for this history_id + step already exists with status pending/running,
    returns None to prevent duplicates.
    """
    # Check for duplicate — prevent double-click resubmission
    check_sql = """
        SELECT id FROM rf_queue
        WHERE history_id = %s AND step = %s AND status IN ('pending', 'running')
    """
    insert_sql = """
        INSERT INTO rf_queue (history_id, step, status, payload)
        VALUES (%s, %s, 'pending', %s)
        RETURNING id
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(check_sql, (history_id, step))
                existing = cur.fetchone()
                if existing:
                    logger.warning(
                        f"Duplicate RF job blocked — history_id={history_id} "
                        f"step={step} already queued/running (job_id={existing[0]})"
                    )
                    return None

                cur.execute(insert_sql, (
                    history_id, step, json.dumps(payload)
                ))
                job_id = cur.fetchone()[0]
                logger.info(f"RF job enqueued — job_id={job_id} history_id={history_id} step={step}")
                return job_id
    except Exception as e:
        logger.error(f"Failed to enqueue RF job for history_id={history_id} step={step}: {e}")
        return None


def claim_next_pending_job() -> Optional[dict]:
    """
    Atomically claim the next pending job for execution.
    Uses SELECT FOR UPDATE SKIP LOCKED to safely handle concurrency.
    Returns the job dict or None if queue is empty or a job is already running.
    """
    # First check if anything is currently running
    check_running_sql = """
        SELECT id FROM rf_queue WHERE status = 'running' LIMIT 1
    """
    claim_sql = """
        UPDATE rf_queue
        SET status = 'running', started_at = %s, attempts = attempts + 1
        WHERE id = (
            SELECT id FROM rf_queue
            WHERE status = 'pending'
            ORDER BY queued_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, history_id, step, payload, attempts
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(check_running_sql)
                if cur.fetchone():
                    return None  # Something already running — wait

                cur.execute(claim_sql, (datetime.now(),))
                row = cur.fetchone()
                if not row:
                    return None

                job = dict(row)
                if isinstance(job.get("payload"), str):
                    try:
                        job["payload"] = json.loads(job["payload"])
                    except Exception:
                        job["payload"] = {}

                logger.info(
                    f"Claimed RF job — job_id={job['id']} "
                    f"history_id={job['history_id']} step={job['step']}"
                )
                return job
    except Exception as e:
        logger.error(f"Failed to claim next RF job: {e}")
        return None


def complete_rf_job(job_id: int, success: bool, result: dict) -> bool:
    """
    Mark a job as done or failed and store the result.
    """
    status = "done" if success else "failed"
    sql = """
        UPDATE rf_queue
        SET status = %s, result = %s, error_message = %s, completed_at = %s
        WHERE id = %s
    """
    error_msg = result.get("error") if not success else None
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    status,
                    json.dumps(result),
                    error_msg,
                    datetime.now(),
                    job_id
                ))
                logger.info(f"RF job {job_id} marked as {status}")
                return True
    except Exception as e:
        logger.error(f"Failed to complete RF job {job_id}: {e}")
        return False


def get_job_status(job_id: int) -> Optional[dict]:
    """
    Get the current status and result of a queued job.
    Called by frontend polling /api/queue_status/<job_id>.
    """
    sql = "SELECT id, history_id, step, status, result, error_message, queued_at, started_at, completed_at FROM rf_queue WHERE id = %s"
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (job_id,))
                row = cur.fetchone()
                if not row:
                    return None
                job = dict(row)
                # Serialize datetime fields
                for key in ["queued_at", "started_at", "completed_at"]:
                    if job.get(key) and hasattr(job[key], "isoformat"):
                        job[key] = job[key].isoformat()
                if isinstance(job.get("result"), str):
                    try:
                        job["result"] = json.loads(job["result"])
                    except Exception:
                        pass
                return job
    except Exception as e:
        logger.error(f"Failed to get job status for job_id={job_id}: {e}")
        return None


def reset_stuck_running_jobs(minutes: int = 15) -> int:
    """
    Reset jobs that have been stuck in 'running' state too long.
    This handles crashed RF processes. Returns count of jobs reset.
    """
    sql = """
        UPDATE rf_queue
        SET status = 'failed',
            error_message = 'Job timed out — RF process may have crashed. Please retry.',
            completed_at = %s
        WHERE status = 'running'
        AND started_at < NOW() - INTERVAL '%s minutes'
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (datetime.now(), minutes))
                count = cur.rowcount
                if count > 0:
                    logger.warning(f"Reset {count} stuck RF job(s) older than {minutes} minutes.")
                return count
    except Exception as e:
        logger.error(f"Failed to reset stuck RF jobs: {e}")
        return 0
