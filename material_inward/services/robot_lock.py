"""
services/robot_lock.py — Cross-process file-based robot execution lock.

Material Inward acquires this lock before every _run_rf_script call and
releases it in a finally block (even on failure/timeout).

The Password Reset app (separate process, port 5001) checks this lock
before starting its own robots, giving Material Inward full priority over
shared desktop resources: clipboard, keyboard focus, and screen.

Why file-based (not DB):
  - Password Reset has no access to Material Inward's PostgreSQL.
  - A plain file works across any two processes on the same Windows machine.
  - No dependency, no network, no race condition at file-system level.

Lock file: C:\\material_inward\\robot.lock
  Contents: "<script_name>\\n<unix_timestamp>"  (human-readable in Explorer)
"""

import os
import time

from config.logger import get_logger

logger = get_logger(__name__)

LOCK_FILE = r"C:\material_inward\robot.lock"
_LOCK_DIR  = os.path.dirname(LOCK_FILE)

# How long Material Inward will politely wait if Password Reset holds the lock.
# After this timeout it forcefully takes over — Material Inward is always priority.
_YIELD_MAX_SECONDS  = 300   # 5 minutes
_YIELD_POLL_SECONDS = 30

# Staleness ceiling: if a lock file is older than this, whoever wrote it almost
# certainly crashed/was killed without releasing it (app crash, forced kill,
# server restart mid-run) rather than genuinely still running — no normal
# robot run on this system takes anywhere near this long. Treat it as
# abandoned and clear it rather than blocking everything indefinitely.
_STALE_LOCK_SECONDS = 20 * 60  # 20 minutes


def _lock_age_seconds() -> float:
    try:
        with open(LOCK_FILE) as f:
            lines = f.read().split('\n')
        timestamp = float(lines[1].strip()) if len(lines) > 1 else 0
        return time.time() - timestamp
    except Exception:
        # Can't read a timestamp — treat as stale rather than blocking forever.
        return _STALE_LOCK_SECONDS + 1


def _clear_stale_lock_if_needed() -> None:
    if not os.path.exists(LOCK_FILE):
        return
    age = _lock_age_seconds()
    if age >= _STALE_LOCK_SECONDS:
        try:
            with open(LOCK_FILE) as f:
                holder = f.read().split('\n')[0].strip()
        except Exception:
            holder = "unknown"
        logger.warning(
            f"[RobotLock] Lock held by '{holder}' is {age:.0f}s old "
            f"(> {_STALE_LOCK_SECONDS}s ceiling) — treating as abandoned and clearing it."
        )
        release_robot_lock()


def acquire_robot_lock(label: str = "") -> bool:
    """
    Create the lock file, stamped with label (script name) and timestamp.
    If the lock is currently held by Password Reset, wait up to 5 min before
    forcefully taking over — Material Inward always has priority.
    A lock older than _STALE_LOCK_SECONDS is treated as abandoned (crashed
    holder) and cleared immediately, regardless of who holds it.
    Returns True on success. Non-fatal on failure.
    """
    try:
        os.makedirs(_LOCK_DIR, exist_ok=True)

        _clear_stale_lock_if_needed()

        # If Password Reset currently holds the lock, wait politely before taking over
        if os.path.exists(LOCK_FILE):
            try:
                with open(LOCK_FILE) as f:
                    holder = f.read().split('\n')[0].strip()
            except Exception:
                holder = "unknown"

            if holder == "password_reset":
                logger.warning(
                    f"[RobotLock] Lock held by Password Reset. "
                    f"Waiting up to {_YIELD_MAX_SECONDS // 60} min before taking over..."
                )
                start = time.time()
                while os.path.exists(LOCK_FILE):
                    elapsed = time.time() - start
                    if elapsed >= _YIELD_MAX_SECONDS:
                        logger.warning(
                            f"[RobotLock] Forcefully taking over after {_YIELD_MAX_SECONDS}s "
                            "— Material Inward has priority."
                        )
                        break
                    logger.info(f"[RobotLock] Still waiting for Password Reset... {int(elapsed)}s elapsed.")
                    time.sleep(_YIELD_POLL_SECONDS)

        with open(LOCK_FILE, "w") as f:
            f.write(f"{label}\n{time.time():.0f}")
        logger.info(f"[RobotLock] Acquired — {label or 'unlabelled'}")
        return True
    except Exception as e:
        logger.warning(f"[RobotLock] Could not acquire (non-fatal): {e}")
        return False


def release_robot_lock() -> None:
    """Delete the lock file. Safe to call even if it does not exist."""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            logger.info("[RobotLock] Released")
    except Exception as e:
        logger.warning(f"[RobotLock] Could not release: {e}")


def is_robot_locked() -> bool:
    """
    Return True if the lock file currently exists AND isn't stale.
    A stale lock (older than _STALE_LOCK_SECONDS) is cleared automatically
    and reported as unlocked, rather than blocking callers (e.g.
    dms_upload_runner.py's desktop-wait check) on an abandoned lock forever.
    """
    if not os.path.exists(LOCK_FILE):
        return False
    _clear_stale_lock_if_needed()
    return os.path.exists(LOCK_FILE)
