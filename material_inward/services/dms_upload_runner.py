"""
services/dms_upload_runner.py — Scheduled trigger for dms_upload.robot.

RUN CADENCE (updated 2026-09-03): run_dms_upload() now runs on an HOURLY
cadence from an in-process scheduler started by app.py at Flask startup --
see start_dms_hourly_scheduler() near the bottom of this file. It no
longer needs to be scheduled via Windows Task Scheduler at all (Task
Scheduler's only remaining job on this machine, per client confirmation,
is dms_scheduler.py's separate NIGHTLY defensive fallback -- a different
script, a different failure mode -- see that module's own docstring).
Every automatic per-Gate-In/per-PO-Fetch trigger (the old
trigger_dms_upload_for() call sites in rf_queue_worker.py) has also been
removed -- staging (consolidate + sidecar, dms_status='staged') still
happens immediately after each Gate In posts, same as before, but the
actual upload batch now only runs on the hourly cadence, plus on-demand
via the existing manual /api/dms_upload/retry/<id> endpoint. This script's
own `python dms_upload_runner.py` entry point at the bottom still works
unchanged, for a manual one-off run if ever needed.

Staging (consolidate + sidecar, dms_status='staged') happens immediately
after each Gate In posts (see rf_queue_worker.py._process_gate_in(),
v16) rather than in a nightly batch. This script just needs to run
frequently enough, independently, to sweep up whatever's accumulated in
DMS_STAGING_FOLDER since its last run -- how fresh the DMS-hosted copies
and their sharing links are is entirely a function of how often this is
scheduled, not of any per-record trigger.

This is NOT part of the RF queue — dms_upload.robot processes the whole
DMS_STAGING_FOLDER in one batch (bulk upload), not one history record at a
time, so it's triggered as its own scheduled step, same as dms_scheduler.py.

v16: after a successful upload batch, this also runs
services/dms_links_import.py's import in the same process -- the DB is
never more stale than the upload cadence itself; there's no second
schedule to keep in sync separately.

One safety measure remains on top of the basic "run the robot" flow
(a second one — pre-emptively waiting for any other automation to finish
before starting — was REMOVED 2026-09-03 per client instruction; see the
note above _batch_lock below for why):

  1. Staged-only quarantine — dms_upload.robot's native "select all files in
     folder" step (via the Windows Open dialog) uploads literally every PDF
     physically sitting in DMS_STAGING_FOLDER at that moment, with no
     awareness of database state. If a MIGO 103 completes between the 9:30
     PM staging run and the 10:00 PM upload run, its consolidated PDF lands
     in DMS_STAGING_FOLDER as dms_status='pending' (no cover page yet, no
     sidecar) — and would otherwise get swept up and uploaded anyway,
     leaving that record's dms_status permanently stuck on 'pending' even
     though the file is already gone. To prevent this, before running the
     robot we move any PDF that is NOT in the "staged" snapshot (checked
     against get_staged_dms_records() + presence of its _meta.json sidecar)
     into a temporary holding folder, run the robot against a folder that
     only contains genuinely staged files, then move anything held back out
     again afterwards so tomorrow's staging run picks it up normally.

Uses sys.executable (not a bare "python") for both this script and the robot
subprocess, so it always runs with the same interpreter it was launched
with — required when running under a venv via Task Scheduler, since Task
Scheduler does not activate the venv or adjust PATH the way start_server.bat
does for the main app.
"""

import os
import sys
import time
import shutil
import subprocess
import logging
import threading
from datetime import datetime
from dotenv import dotenv_values

# Ensure project root is on path when called directly by Task Scheduler
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.config import config
from database.db_operations import (
    get_staged_dms_records, set_dms_status, get_history_details_by_id
)
from database.connection import init_pool
from services.robot_lock import acquire_robot_lock, release_robot_lock
from services.dms_links_import import run_dms_links_import
from config.logger import get_logger

# v20 FIX: was its own logging.basicConfig() with a hardcoded tag -- see
# services/dms_links_import.py's comment for why that's unsafe (root
# logger race across imports). Switched to the shared get_logger().
logger = get_logger(__name__)

TIMEOUT_SECONDS = 1800  # 30 min — bulk upload of a whole folder can take a while

HOLD_SUBDIR = "_not_yet_staged"  # under DMS_STAGING_FOLDER — temp quarantine

# MULTI-BOT FIX (2026-09-03): moved up from next to trigger_dms_upload_for()
# (further below) so run_dms_upload() itself can guard with it -- see that
# function's docstring. Used to only be acquired by trigger_dms_upload_for()'s
# own wrapper, which meant a caller that invoked run_dms_upload() directly
# (the new hourly scheduler, and the __main__ entry point) bypassed it
# entirely -- two such calls overlapping would both list/move the same
# DMS_STAGING_FOLDER at once. Non-reentrant (plain threading.Lock), so it is
# only ever acquired in exactly one place now (run_dms_upload() itself) --
# never nested.
_batch_lock = threading.Lock()

# REMOVED (2026-09-03, client instruction): a _wait_for_desktop_free()
# pre-check used to sit here, waiting for services.robot_lock's lock file
# to be completely clear (regardless of holder) before starting, since
# this script drives a real native Windows file-open dialog and an Edge
# browser that need real desktop focus. Removed because it was checking
# the wrong thing since this session's robot_lock.py rewrite: that lock is
# now a shared refcount that MULTIPLE Material-Inward bots are meant to
# hold at once (the whole point of the multi-bot concurrency work), so
# "is_robot_locked()" only ever meant "at least one bot of any kind is
# currently running somewhere" -- it was pointlessly delaying every hourly
# DMS run (up to 10 minutes) any time an unrelated SAP job happened to be
# in flight for a completely different user, with no real desktop-collision
# protection to show for it. acquire_robot_lock()/release_robot_lock()
# below (around the actual subprocess run) are unchanged -- this only
# removed the "wait before even starting" step.
#
# Genuinely still possible (accepted per client decision): if a bot like
# ZGRN print or MIRO happens to need real native/keyboard focus at the
# exact same moment this script's own file dialog or browser is on
# screen, the two could interfere with each other. Both windows are
# brief and this overlap is rare in practice; the client has chosen to
# accept it rather than keep the wait.


def _quarantine_unstaged_files(staged_records: list) -> list:
    """
    Move any PDF in DMS_STAGING_FOLDER that is NOT a genuinely staged file
    (present in staged_records AND has a _meta.json sidecar) into a temp
    holding folder, so the robot's "select all" step can't sweep it up.
    Returns the list of (original_path, held_path) tuples moved, so they can
    be restored afterwards.
    """
    staging_root = config.DMS_STAGING_FOLDER
    if not os.path.isdir(staging_root):
        return []

    staged_basenames = {
        os.path.basename(rec["consolidated_doc_path"])
        for rec in staged_records
        if rec.get("consolidated_doc_path")
    }

    hold_dir = os.path.join(staging_root, HOLD_SUBDIR)
    os.makedirs(hold_dir, exist_ok=True)

    moved = []
    for name in os.listdir(staging_root):
        full = os.path.join(staging_root, name)
        if not os.path.isfile(full) or not name.lower().endswith(".pdf"):
            continue

        sidecar = os.path.splitext(full)[0] + "_meta.json"
        is_staged = name in staged_basenames and os.path.exists(sidecar)

        if not is_staged:
            dest = os.path.join(hold_dir, name)
            try:
                shutil.move(full, dest)
                moved.append((full, dest))
                logger.info(
                    f"[DMS Upload] Quarantined not-yet-staged file: {name} "
                    "(no sidecar / not in staged snapshot — will be picked up "
                    "by tomorrow's staging run)"
                )
            except Exception as e:
                logger.error(f"[DMS Upload] Failed to quarantine {full}: {e}")

    return moved


def _restore_quarantined_files(moved: list) -> None:
    for original_path, held_path in moved:
        try:
            if os.path.exists(held_path):
                shutil.move(held_path, original_path)
        except Exception as e:
            logger.error(
                f"[DMS Upload] Failed to restore quarantined file "
                f"{held_path} -> {original_path}: {e}"
            )


def run_dms_upload() -> None:
    """Runs the full staged-files batch upload against DMS_STAGING_FOLDER.

    Safe to call from anywhere -- the hourly scheduler, the manual retry
    endpoint (via trigger_dms_upload_for), or a direct `python
    dms_upload_runner.py` run -- because this self-guards with
    _batch_lock: a caller that finds a run already in progress just
    returns immediately rather than starting a second one that would list
    /move the same folder at the same time. Nothing is lost either way --
    the run already in progress (or the next call, hourly at the latest)
    picks up anything currently staged."""
    if not _batch_lock.acquire(blocking=False):
        logger.info(
            "[DMS Upload] Batch already running elsewhere — skipping this "
            "call (the run already in progress will pick up anything "
            "currently staged)."
        )
        return
    try:
        _run_dms_upload_locked()
    finally:
        _batch_lock.release()


def _run_dms_upload_locked() -> None:
    logger.info("DMS upload trigger started")

    staged_records = get_staged_dms_records()
    if not staged_records:
        logger.info("No staged records — nothing to upload")
        return

    script_path = os.path.join(config.RF_SCRIPTS_PATH, "dms_upload.robot")
    if not os.path.exists(script_path):
        logger.error(f"dms_upload.robot not found at {script_path}")
        return

    quarantined = _quarantine_unstaged_files(staged_records)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(config.RF_OUTPUT_PATH, f"dms_upload_{timestamp}")
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        sys.executable, "-m", "robot",
        "--outputdir", output_dir,
        "--loglevel", "DEBUG",
        "--nostatusrc",
        script_path,
    ]

    logger.info(
        f"Running dms_upload.robot — {len(staged_records)} staged record(s) pending. "
        f"Logs: {output_dir}"
    )

    acquire_robot_lock("dms_upload.robot")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            # FIX: dotenv_values() with no argument searches for .env from
            # the current working directory upward -- unreliable under
            # Task Scheduler (see config/config.py's identical fix and
            # comment). Anchored to _ROOT (this file's own project-root
            # location, computed at the top of this module) instead, so
            # the subprocess always gets the real .env values regardless
            # of what cwd this script happened to be launched with.
            env={**os.environ, **dotenv_values(os.path.join(_ROOT, ".env"))},
        )
    except subprocess.TimeoutExpired:
        logger.error(f"dms_upload.robot timed out after {TIMEOUT_SECONDS}s")
        _restore_quarantined_files(quarantined)
        return
    except Exception as e:
        logger.error(f"Unexpected error running dms_upload.robot: {e}", exc_info=True)
        _restore_quarantined_files(quarantined)
        return
    finally:
        release_robot_lock()

    _restore_quarantined_files(quarantined)

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    logger.debug(f"RF output (first 2000 chars): {output[:2000]}")

    if "RESULT:DMS_UPLOAD_STATUS:SUCCESS" not in output:
        logger.error(
            f"dms_upload.robot did not report SUCCESS (exit code {result.returncode}). "
            f"Logs: {output_dir}"
        )
        return

    # A record is "uploaded" once its PDF has moved out of the staging root
    # (dms_upload.robot moves processed files into DMS_STAGING_FOLDER\uploaded).
    updated = skipped = 0
    for rec in staged_records:
        path = rec.get("consolidated_doc_path")
        if path and not os.path.exists(path):
            set_dms_status(rec["id"], "uploaded")
            updated += 1
        else:
            logger.warning(
                f"history_id={rec['id']}: PDF still present at {path!r} after "
                f"upload run — not marked uploaded, check robot logs at {output_dir}"
            )
            skipped += 1

    logger.info(
        f"DMS upload trigger complete — uploaded={updated} "
        f"still_pending={skipped} total_checked={len(staged_records)}"
    )

    # v16: pull whatever links dms_upload.robot just wrote into
    # DMS_LINKS_EXCEL_PATH straight into the DB -- no separate schedule to
    # keep in sync. Best-effort: an import failure here doesn't undo the
    # upload itself (files are already moved/marked 'uploaded' above); it's
    # logged for the next run (or a manual `python services/
    # dms_links_import.py`) to pick up, since the import is idempotent.
    try:
        run_dms_links_import()
    except Exception as e:
        logger.error(f"DMS links import failed after upload: {e}", exc_info=True)


# ============================================================
# ASYNC TRIGGER (ADDED 2026-08-14) — replaces rf_queue-chained "dms_upload"
# step. See rf_queue_worker.py's _enqueue_dms_upload(), which now calls
# trigger_dms_upload_for() below directly instead of enqueue_rf_job().
#
# Why this is safe to pull out of the RF queue's single worker thread:
# dms_upload.robot is SeleniumLibrary (browser) automation against the
# Contentverse website, not SAP GUI scripting — it does not need to wait
# behind SAP jobs for the bulk of its run. The one exception is the native
# Windows "Choose File" dialog (file_dialog.py) that a browser's own
# upload button triggers — that's a real OS-level dialog outside the page
# DOM, unreachable by Selenium, so it still needs real desktop/keyboard
# focus for the few seconds it's open. A pre-check used to wait here for
# that handoff -- REMOVED 2026-09-03 per client instruction, see the note
# above _batch_lock further up this file for why.
#
# What IS new here: run_dms_upload() processes the WHOLE staging folder in
# one batch (not scoped to one history_id), so two concurrent triggers
# (e.g. two records finishing Gate In back to back) must not both run the
# batch at the same time — both would list/move the same folder. Unlike
# gst_runner.py's per-history_id threads (safe to run in parallel, each
# owns its own browser session and GSTIN), DMS needs a single in-process
# gate. _batch_lock (non-blocking) provides that: a skipped trigger is not
# a lost upload — the file(s) stay in DMS_STAGING_FOLDER and get picked up
# by the run that's currently in progress, the next trigger, or
# dms_scheduler.py's own defensive periodic sweep.
# ============================================================

def trigger_dms_upload_for(history_id: int) -> None:
    """
    Fire-and-forget: starts run_dms_upload() on its own background thread
    (daemon, same pattern as gst_runner.py's per-record threads) and
    returns immediately. Does not go through rf_queue — no job_id, no
    polling; the outcome is only in the logs and in this record's own
    dms_status, same as it always was for the Task-Scheduler-driven path.

    MULTI-BOT FIX (2026-09-03): no longer does its own _batch_lock
    acquire/release here -- run_dms_upload() now self-guards with that
    same lock (see its docstring), so every caller gets the same
    never-overlap guarantee automatically without each one having to
    remember to apply it.
    """
    def _run():
        try:
            run_dms_upload()
        except Exception as e:
            logger.error(
                f"[DMS Upload] Batch run crashed (triggered by history_id={history_id}): {e}",
                exc_info=True
            )

        # Report this specific record's outcome — matches what
        # rf_queue_worker.py's old _process_dms_upload() used to log.
        try:
            details = get_history_details_by_id(history_id) or {}
            dms_status = (details.get("history") or {}).get("dms_status")
            if dms_status == "uploaded":
                logger.info(f"[DMS Upload] Complete — history_id={history_id}")
            else:
                logger.warning(
                    f"[DMS Upload] Batch ran but history_id={history_id} is still "
                    f"dms_status={dms_status!r} afterward."
                )
        except Exception as e:
            logger.error(
                f"[DMS Upload] Post-batch status check failed for history_id={history_id}: {e}"
            )

    threading.Thread(target=_run, daemon=True, name=f"DMSUpload-{history_id}").start()


# ============================================================
# IN-PROCESS HOURLY SCHEDULER (ADDED 2026-09-03)
#
# Replaces the per-event trigger above (every Gate In / PO Fetch
# completion used to call trigger_dms_upload_for() -- see rf_queue_worker.py,
# where those 3 call sites are now removed) with a single hourly cadence
# running inside the main Flask app itself, instead of a separate Windows
# Task Scheduler entry.
#
# Why this is safe and correct: run_dms_upload() already processes the
# WHOLE staging folder as one idempotent batch (skips anything not
# genuinely staged, skips files already uploaded) -- that design predates
# this change and was built for exactly this kind of periodic sweep, it
# just didn't have anything calling it on an hourly cadence on its own.
# Client confirmation (2026-09-03): Windows Task Scheduler was originally
# meant only for dms_scheduler.py's separate NIGHTLY defensive fallback
# (catching files that somehow never got staged at all -- a different
# failure mode, see that module's own docstring) -- not for this upload
# step. dms_scheduler.py is completely untouched by this change and keeps
# running on its own existing nightly schedule.
#
# Started once at app startup (see app.py, same pattern as
# rf_queue_worker.start_worker()) -- a daemon thread with its own sleep
# loop, living for the process lifetime. Deliberately a fixed delay AFTER
# each run finishes (not a wall-clock "every :00" cron) so two runs can
# never overlap even if one happens to take a while -- matches
# _batch_lock's existing non-overlap guarantee for the per-event trigger
# path, just simpler since there's only ever one caller now.
# ============================================================

_DMS_HOURLY_INTERVAL_SECONDS = 60 * 60  # 1 hour, per client instruction
_DMS_INITIAL_DELAY_SECONDS = 120        # let the app finish starting up first


def _dms_hourly_loop() -> None:
    logger.info(
        f"[DMS Upload] Hourly in-process scheduler started — first run in "
        f"{_DMS_INITIAL_DELAY_SECONDS}s, then every "
        f"{_DMS_HOURLY_INTERVAL_SECONDS // 60} min after each run completes."
    )
    time.sleep(_DMS_INITIAL_DELAY_SECONDS)
    while True:
        try:
            run_dms_upload()
        except Exception as e:
            logger.error(f"[DMS Upload] Hourly run crashed: {e}", exc_info=True)
        time.sleep(_DMS_HOURLY_INTERVAL_SECONDS)


def start_dms_hourly_scheduler() -> threading.Thread:
    """Call once at app startup (see app.py). Returns the daemon thread."""
    thread = threading.Thread(target=_dms_hourly_loop, daemon=True, name="DMSHourlyScheduler")
    thread.start()
    logger.info("DMS hourly upload scheduler thread started.")
    return thread


if __name__ == "__main__":
    init_pool()
    run_dms_upload()
