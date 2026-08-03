"""
services/credential_cache.py — In-memory SAP credential cache for
per-user LDAP login (v16).

BACKGROUND: every SAP posting used to run under one shared spl_rpa
service account regardless of which real person triggered it — an audit
trail gap. Per client confirmation, each LDAP user's AD password is also
their personal SAP GUI login (same username, same password, both
systems). So an LDAP-authenticated user's RF jobs should now log into
SAP as that person, not as spl_rpa.

CRITICAL SECURITY CONSTRAINT — repeated per explicit client instruction:
this password must NEVER be written to any database table, any log
file, or any RF/robot output. It is allowed to exist in exactly two
places, both in-memory only, both defined below:

  1. SESSION_CACHE — captured once at login, used only to authorize
     brand-new job submissions while the user is actively working
     (60-minute idle timeout — see touch_session_credential). This is
     what /login populates and app.py's enqueue wrapper reads.

  2. JOB_CACHE — a copy attached to one specific rf_queue job id at the
     moment it's enqueued, deliberately decoupled from the session's
     lifetime from that point on. A job that's already queued must
     still complete correctly even if the submitting user's session
     later times out or they log out before the queue worker gets to
     it. Cleared the instant that job finishes (success or failure).

Neither cache is persisted to disk or DB. A Flask app restart wipes
both entirely — by design, not an oversight. See rf_queue_worker.py for
how a job that needed a credential but lost it to a restart is meant to
fail cleanly with a clear error, rather than silently falling back to
the shared spl_rpa account. There is NO fallback path to spl_rpa for an
LDAP-submitted job anywhere in this design.

auth_type='local' users (test accounts) are never routed through this
cache at all — rf_runner.py/robot scripts keep using the existing
SAP_USERNAME/SAP_PASSWORD .env values for them unconditionally, exactly
as before this change.
"""

import threading
import time
from typing import Optional, Tuple

from config.logger import get_logger

logger = get_logger(__name__)

SESSION_TTL_SECONDS = 60 * 60  # 60 minutes idle timeout, per client decision

_lock = threading.Lock()

# username -> (password, last_touched_epoch)
_session_cache: dict = {}

# job_id (int, rf_queue.id) -> (username, password)
_job_cache: dict = {}


def store_session_credential(username: str, password: str) -> None:
    """Call once, right after a successful LDAP login (see app.py /login)."""
    if not username or not password:
        return
    with _lock:
        _session_cache[username] = (password, time.time())
    logger.info(f"[cred_cache] session credential stored for {username}")


def touch_session_credential(username: str) -> None:
    """Refresh the idle-timeout clock. Safe to call unconditionally on every
    authenticated request (local users / anyone with no cached entry are a
    no-op) so the 60-minute window is idle time since last activity, not
    absolute time since login."""
    if not username:
        return
    with _lock:
        entry = _session_cache.get(username)
        if entry:
            _session_cache[username] = (entry[0], time.time())


def get_session_credential(username: str) -> Optional[str]:
    """Returns the cached password if still within the TTL, else None (and
    evicts the stale entry). Used only at the moment a NEW job is about to
    be enqueued — see app.py's _enqueue_sap_job()."""
    if not username:
        return None
    with _lock:
        entry = _session_cache.get(username)
        if not entry:
            return None
        password, last_touched = entry
        if time.time() - last_touched > SESSION_TTL_SECONDS:
            del _session_cache[username]
            logger.info(f"[cred_cache] session credential expired for {username}")
            return None
        return password


def clear_session_credential(username: str) -> None:
    """Call on logout."""
    if not username:
        return
    with _lock:
        _session_cache.pop(username, None)


def attach_job_credential(job_id, username: str, password: str) -> None:
    """Call right after a job is successfully enqueued into rf_queue — copies
    the credential into a separate cache keyed by job id, so the queue
    worker can use it whenever it actually runs the job, independent of
    whether the login session that submitted it is still valid by then."""
    if not job_id or not username or not password:
        return
    with _lock:
        _job_cache[job_id] = (username, password)


def get_job_credential(job_id) -> Optional[Tuple[str, str]]:
    """Returns (username, password) for a queued job, or None if it was
    never attached (local auth_type job — expected) or is no longer
    available (app restarted while it sat in the queue — see
    rf_queue_worker.py's handling, which must fail the job cleanly in
    that case rather than fall back to spl_rpa)."""
    if not job_id:
        return None
    with _lock:
        return _job_cache.get(job_id)


def clear_job_credential(job_id) -> None:
    """Call once a job finishes executing (success or failure) — that job's
    credential has done its work, wipe it immediately rather than let it
    linger in memory."""
    if not job_id:
        return
    with _lock:
        _job_cache.pop(job_id, None)
