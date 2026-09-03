"""
services/robot_lock.py — Cross-process file-based robot execution lock.

Material Inward acquires this lock before every _run_rf_script call and
releases it in a finally block (even on failure/timeout).

The Password Reset app (separate process, port 5001) checks this lock
before starting its own robots, giving Material Inward full priority over
shared desktop resources: clipboard, keyboard focus, and screen.
dms_upload_runner.py and ZGRN's print step (rf_runner.py) also wait on
this same lock before doing their own real desktop-level automation, for
the same reason.

Why file-based (not DB):
  - Password Reset has no access to Material Inward's PostgreSQL.
  - A plain file works across any two processes on the same Windows machine.
  - No dependency, no network, no race condition at file-system level.

Lock file: C:\\material_inward\\robot.lock
  Contents: "<script_name>\\n<unix_timestamp>"  (human-readable in Explorer)

MULTI-BOT FIX (2026-09-03): acquire_robot_lock()/release_robot_lock() used
to be a plain single-holder overwrite/delete -- fine when Material Inward
only ever ran one robot script at a time, broken now that several can run
concurrently (up to 6 windows for spl_rpa). Concretely: script A acquires
(writes the file), script B starts concurrently and immediately overwrites
it (no wait -- the "wait if held" branch below only ever applies to a
holder literally named "password_reset"), then script A finishes first and
calls release_robot_lock(), which used to delete the file unconditionally
-- even though script B is still actively running. That falsely tells
Password Reset / dms_upload_runner.py / ZGRN's own wait loop "the desktop
is free" while script B is still mid-automation, letting a second
automation start on top of it.

Fixed with a small refcount file (C:\\material_inward\\robot_lock_refcount.txt,
same directory-creation-as-mutex pattern already used in
robot_scripts/sap_coordination.resource) layered UNDER the existing shared
lock file -- NOT by changing the shared file's own format, since Password
Reset's own copy of this protocol (a separate codebase this session can't
see or edit) only ever checks os.path.exists() on it and, when IT is the
holder, writes its own "password_reset\\n<timestamp>" into it -- both of
which still work completely unchanged. The refcount just decides WHEN this
process's side actually creates/removes that shared file: only the first
Material Inward script to start creates it, and only the last one still
running removes it.
"""

import os
import time

from config.logger import get_logger

logger = get_logger(__name__)

LOCK_FILE = r"C:\material_inward\robot.lock"
_LOCK_DIR  = os.path.dirname(LOCK_FILE)

# MULTI-BOT FIX (2026-09-03): tracks how many of THIS process's (Material
# Inward's) own robot runs are currently active, so release_robot_lock()
# only clears the shared LOCK_FILE when the last one finishes. Deliberately
# a separate file from LOCK_FILE itself -- see module docstring for why.
_REFCOUNT_FILE     = r"C:\material_inward\robot_lock_refcount.txt"
_REFCOUNT_LOCK_DIR = r"C:\material_inward\robot_lock_refcount.lock"

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


def _acquire_refcount_mutex() -> bool:
    """Directory creation is atomic on Windows -- used as a simple mutex
    around reading/writing _REFCOUNT_FILE. Short retry budget since this
    only ever guards a quick file read+write, never a full login sequence
    (unlike sap_coordination.resource's equivalent, which holds its lock
    across a whole SAP login)."""
    os.makedirs(_LOCK_DIR, exist_ok=True)
    for _ in range(50):  # ~10s
        try:
            os.mkdir(_REFCOUNT_LOCK_DIR)
            return True
        except FileExistsError:
            time.sleep(0.2)
        except Exception:
            return False
    logger.warning("[RobotLock] Could not acquire refcount mutex after retries -- proceeding anyway to avoid deadlock.")
    return False


def _release_refcount_mutex() -> None:
    try:
        os.rmdir(_REFCOUNT_LOCK_DIR)
    except Exception:
        pass


def _read_refcount() -> int:
    try:
        with open(_REFCOUNT_FILE) as f:
            return max(0, int(f.read().strip() or "0"))
    except Exception:
        return 0


def _write_refcount(value: int) -> None:
    try:
        with open(_REFCOUNT_FILE, "w") as f:
            f.write(str(max(0, value)))
    except Exception as e:
        logger.warning(f"[RobotLock] Could not write refcount (non-fatal): {e}")


def _reset_refcount() -> None:
    """Called when a stale lock is cleared -- an abandoned/crashed lock
    means whatever refcount was tracked for it is meaningless too, so start
    clean rather than leaving a phantom count that release_robot_lock()
    could never work back down to 0 from."""
    _acquire_refcount_mutex()
    try:
        _write_refcount(0)
    finally:
        _release_refcount_mutex()


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
        _reset_refcount()
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
                logger.info("[RobotLock] Released (stale)")
        except Exception as e:
            logger.warning(f"[RobotLock] Could not release stale lock: {e}")


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

        # MULTI-BOT FIX (2026-09-03): only the FIRST of Material Inward's own
        # concurrently-running scripts actually (re)writes the shared file --
        # see module docstring. Subsequent concurrent acquires just bump the
        # refcount so release_robot_lock() knows not to clear it out from
        # under whoever else is still active.
        _acquire_refcount_mutex()
        try:
            count = _read_refcount()
            _write_refcount(count + 1)
            if count == 0:
                with open(LOCK_FILE, "w") as f:
                    f.write(f"{label}\n{time.time():.0f}")
            logger.info(f"[RobotLock] Acquired — {label or 'unlabelled'} (active count: {count + 1})")
        finally:
            _release_refcount_mutex()
        return True
    except Exception as e:
        logger.warning(f"[RobotLock] Could not acquire (non-fatal): {e}")
        return False


def release_robot_lock() -> None:
    """Decrements this process's active-script refcount; only actually
    deletes the shared lock file once the count reaches 0 (i.e. this was
    the last of Material Inward's own scripts still running). Safe to call
    even if the file/refcount does not exist."""
    try:
        _acquire_refcount_mutex()
        try:
            count = max(0, _read_refcount() - 1)
            _write_refcount(count)
            if count == 0:
                if os.path.exists(LOCK_FILE):
                    os.remove(LOCK_FILE)
                    logger.info("[RobotLock] Released (last active script)")
            else:
                logger.info(f"[RobotLock] Released one of Material Inward's own scripts (still {count} active) -- lock file left in place.")
        finally:
            _release_refcount_mutex()
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
