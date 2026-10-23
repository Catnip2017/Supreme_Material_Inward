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
#
# PERFORMANCE FIX (2026-09-29, client "Issue No.38" -- PO 4100035692 took ~9
# min end to end): this used to be a flat 300s wait polled every 30s. In the
# real run (application log, 14:45:32 -> 14:50:32) PO Fetch sat idle for the
# FULL 300s behind 'password_reset:58' and then "forcefully" took over anyway
# -- so the wait bought no protection, it just added 5 minutes to a user-facing
# request (over half of the 9m13s total). Polling every 30s also meant that
# when the holder DID finish, Material Inward could sit up to 30s longer
# before noticing. Now: poll every 2s (notice a released lock almost
# immediately) and only yield for 60s before taking over. Both are overridable
# from .env WITHOUT a code change:
#     ROBOT_LOCK_YIELD_MAX_SECONDS=300     (restores the old 5 min patience)
#     ROBOT_LOCK_YIELD_POLL_SECONDS=30     (restores the old poll interval)
# Read at call time (not import time) so a value in .env is honoured
# regardless of import order.
_DEFAULT_YIELD_MAX_SECONDS  = 60
_DEFAULT_YIELD_POLL_SECONDS = 2


def _env_seconds(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "").strip())
        return value if value > 0 else default
    except (ValueError, AttributeError):
        return default


def _yield_max_seconds() -> float:
    return _env_seconds("ROBOT_LOCK_YIELD_MAX_SECONDS", _DEFAULT_YIELD_MAX_SECONDS)


def _yield_poll_seconds() -> float:
    return _env_seconds("ROBOT_LOCK_YIELD_POLL_SECONDS", _DEFAULT_YIELD_POLL_SECONDS)


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


def acquire_robot_lock(label: str = "", wait_for_external: bool = False) -> bool:
    """
    Create the lock file, stamped with label (script name) and timestamp.
    If the lock is currently held by some other process (Password Reset,
    ecosystem_dashboard, or anything else outside Material Inward), wait up
    to 5 min before forcefully taking over — Material Inward always has
    priority. A lock older than _STALE_LOCK_SECONDS is treated as abandoned
    (crashed holder) and cleared immediately, regardless of who holds it.
    Returns True on success. Non-fatal on failure.

    wait_for_external (2026-09-29, client instruction): the wait for an
    outside holder is now OPT-IN. Only the DMS upload bot needs it (it drives
    a real native file dialog + Edge browser that need desktop focus, see
    dms_upload_runner.py); the SAP GUI-scripting bots (PO Fetch, MIGO, ZGRN,
    ...) do not, and waiting for Password Reset made user-facing steps sit
    idle (5 min in the Issue No.38 run). Default False = never wait. The lock
    file is still written either way, so Password Reset / the ecosystem
    dashboard can still see Material Inward running.
    """
    try:
        os.makedirs(_LOCK_DIR, exist_ok=True)

        _clear_stale_lock_if_needed()

        # CORRECTED (2026-09-08): this used to only ever wait for a holder
        # literally named "password_reset" -- fine back when Password Reset
        # was the only other process sharing this lock, but
        # ecosystem_dashboard started using this exact same lock
        # file/protocol on 2026-07-27 to coordinate its own scheduled SAP
        # GUI runs (ME5A, ZSDOB, VENDOR_MASTER_SYNC, etc. -- and Password
        # Reset itself now lives inside ecosystem_dashboard too) and was
        # never accounted for here. Its holder label is the process name
        # (e.g. "ME5A"), never "password_reset", so that exact-string check
        # let Material Inward sail straight through without waiting --
        # overwriting the lock file mid-run and driving SAP GUI on the same
        # shared desktop at the same time as whatever ecosystem_dashboard
        # job was still active. ecosystem_dashboard's own copy of this lock
        # already waits for ANY other holder correctly; this brings
        # Material Inward's side to match. Uses our own refcount (not the
        # label text) to tell "one of Material Inward's own already-running
        # scripts" apart from "someone else entirely" -- refcount > 0 means
        # whatever is in LOCK_FILE right now is one of ours (only the first
        # of our concurrent scripts ever writes it, see below), so this
        # still never makes Material Inward's own concurrent scripts wait
        # on each other. Only when our own refcount is 0 but the file still
        # exists does it mean an outside process holds it.
        if wait_for_external and os.path.exists(LOCK_FILE) and _read_refcount() == 0:
            try:
                with open(LOCK_FILE) as f:
                    holder = f.read().split('\n')[0].strip()
            except Exception:
                holder = "unknown"

            yield_max  = _yield_max_seconds()
            yield_poll = _yield_poll_seconds()
            logger.warning(
                f"[RobotLock] Lock held by '{holder}' (outside Material Inward). "
                f"Waiting up to {int(yield_max)}s before taking over..."
            )
            start = time.time()
            last_progress_log = -1
            while os.path.exists(LOCK_FILE):
                elapsed = time.time() - start
                if elapsed >= yield_max:
                    logger.warning(
                        f"[RobotLock] Forcefully taking over after {int(yield_max)}s "
                        "— Material Inward has priority."
                    )
                    break
                # Progress line every ~10s only -- polling is now every 2s and
                # a line per poll would flood the log.
                if int(elapsed) // 10 != last_progress_log:
                    last_progress_log = int(elapsed) // 10
                    logger.info(f"[RobotLock] Still waiting for '{holder}'... {int(elapsed)}s elapsed.")
                time.sleep(yield_poll)
            else:
                logger.info(
                    f"[RobotLock] '{holder}' released the lock after {int(time.time() - start)}s -- proceeding."
                )

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
