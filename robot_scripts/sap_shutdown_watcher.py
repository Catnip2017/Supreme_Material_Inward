"""
sap_shutdown_watcher.py

Standalone background watcher, spawned (detached) by
sap_bot_coordination.py's coordinated_close_sap_session() the moment the
shared SAP bot refcount hits 0. Runs completely independently of any
bot's own process -- its only job is to wait and see whether another bot
registers as active within SHUTDOWN_COOLDOWN_SECONDS, and only then
fully close saplogon.exe.

FIX 11 (2026-09-03): this wait-and-kill logic used to run INSIDE the
last bot's own Robot Framework [Teardown], blocking that bot's whole
process from exiting for up to 60s. Since rf_runner.py's
subprocess.run() -- and therefore the job's done/failed status, and
therefore the frontend's poll -- only resolves once the robot process
exits, this made whichever bot happened to be "last one out" report its
result up to a full minute late, even though its actual SAP work had
already finished (confirmed for real against a PO Fetch run that had
already fetched its data and closed its own window, but the frontend
kept showing "fetching" until saplogon.exe itself shut down a minute
later). Moving this wait out here, as a detached process, lets the
finishing bot's own process exit immediately after closing its own
window -- the shared "should SAP be fully shut down now" decision keeps
working exactly the same way, just off to the side where it can't block
anyone's UI.

Uses the SAME refcount file + lock directory as sap_bot_coordination.py
so both always agree on the live count -- deliberately duplicated here
(not imported) so this script has zero dependency on Robot Framework or
SapGuiLibrary and can run standalone via a bare `python` call, detached
from any calling process.

No arguments, no return value the caller needs -- logs to its own file
for traceability since it has no Robot Framework console to write to.
"""
import os
import subprocess
import time
from datetime import datetime

REFCOUNT_FILE = r"C:\temp\sap_bot_refcount.txt"
REFCOUNT_LOCK_DIR = r"C:\temp\sap_bot_refcount.lock"
WATCHER_LOG_FILE = r"C:\temp\sap_shutdown_watcher.log"

SHORT_LOCK_MAX_ATTEMPTS = 150        # 150 x 0.2s = 30s
SHORT_LOCK_POLL_INTERVAL = 0.2

# Must match sap_bot_coordination.py's own values -- both scripts watch
# the same shared state and need to agree on timing.
SHUTDOWN_COOLDOWN_SECONDS = 60
SHUTDOWN_COOLDOWN_POLL_INTERVAL = 3


def _log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
    try:
        os.makedirs(r"C:\temp", exist_ok=True)
        with open(WATCHER_LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _acquire_refcount_lock(max_attempts=SHORT_LOCK_MAX_ATTEMPTS, poll_interval=SHORT_LOCK_POLL_INTERVAL):
    os.makedirs(r"C:\temp", exist_ok=True)
    for _ in range(max_attempts):
        try:
            os.mkdir(REFCOUNT_LOCK_DIR)
            return
        except FileExistsError:
            time.sleep(poll_interval)
    _log("WARNING: could not acquire refcount lock after 30s of retries -- proceeding anyway to avoid a permanent deadlock.")


def _release_refcount_lock():
    try:
        os.rmdir(REFCOUNT_LOCK_DIR)
    except OSError:
        pass


def _read_counter():
    if not os.path.exists(REFCOUNT_FILE):
        return 0
    with open(REFCOUNT_FILE) as f:
        raw = f.read().strip()
    return int(raw) if raw else 0


def _active_count():
    _acquire_refcount_lock()
    try:
        return _read_counter()
    finally:
        _release_refcount_lock()


def _kill_saplogon(with_tree=False):
    args = ["taskkill", "/F", "/IM", "saplogon.exe"]
    if with_tree:
        args.append("/T")
    subprocess.run(args, capture_output=True)


def main():
    _log(
        f"Watcher started -- refcount was 0 at handoff. Watching for "
        f"{SHUTDOWN_COOLDOWN_SECONDS}s to confirm no other bot registers "
        "before shutting down saplogon.exe."
    )

    elapsed = 0
    while elapsed < SHUTDOWN_COOLDOWN_SECONDS:
        time.sleep(SHUTDOWN_COOLDOWN_POLL_INTERVAL)
        elapsed += SHUTDOWN_COOLDOWN_POLL_INTERVAL
        current = _active_count()
        if current > 0:
            _log(
                f"Another bot became active during the cooldown (refcount={current}) "
                "-- backing off. That bot (or a later one) will be responsible for "
                "the eventual shutdown."
            )
            return

    _log(
        f"Refcount stayed at 0 for the full {SHUTDOWN_COOLDOWN_SECONDS}s cooldown -- "
        "shutting down saplogon.exe fully."
    )
    _kill_saplogon(with_tree=False)
    time.sleep(2)
    _kill_saplogon(with_tree=True)
    _log("SAP process closed and terminated.")


if __name__ == "__main__":
    main()
