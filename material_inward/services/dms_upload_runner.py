"""
services/dms_upload_runner.py — Scheduled trigger for dms_upload.robot.

Run by Windows Task Scheduler on its own interval (e.g. every 15-30 min --
not tied to any single Gate In event). Staging (consolidate + sidecar,
dms_status='staged') now happens immediately after each Gate In posts (see
rf_queue_worker.py._process_gate_in(), v16) rather than in a nightly batch,
so this script's own docstring previously describing a fixed "stage at
9:30pm, upload at 10pm" nightly relationship is stale -- dms_scheduler.py
is now only a defensive fallback (see its own updated docstring) that
should normally find zero records. This script just needs to run
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

Two safety measures added on top of the basic "run the robot" flow:

  1. Desktop-lock coordination — dms_upload.robot drives a real native
     Windows file-open dialog (via file_dialog.py/pywinauto) and an Edge
     browser, both of which need real desktop/window focus. If a SAP robot
     (Gate In / MIGO / MIRO / etc.) happens to still be running at trigger
     time, colliding on the same desktop would corrupt both runs. Before
     starting, this script waits for services.robot_lock's lock file to be
     clear (regardless of which script holds it — not just "password_reset"
     as robot_lock.py's own cross-app logic does), then holds the lock itself
     for the duration of the upload run.

  2. Staged-only quarantine — dms_upload.robot's native "select all files in
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
from services.robot_lock import acquire_robot_lock, release_robot_lock, is_robot_locked
from services.dms_links_import import run_dms_links_import
from config.logger import get_logger

# v20 FIX: was its own logging.basicConfig() with a hardcoded tag -- see
# services/dms_links_import.py's comment for why that's unsafe (root
# logger race across imports). Switched to the shared get_logger().
logger = get_logger(__name__)

TIMEOUT_SECONDS = 1800  # 30 min — bulk upload of a whole folder can take a while

# How long to wait for a SAP robot that's already running to finish before
# giving up and skipping this run entirely (better than colliding on desktop).
_DESKTOP_WAIT_MAX_SECONDS = 600   # 10 min
_DESKTOP_WAIT_POLL_SECONDS = 15

HOLD_SUBDIR = "_not_yet_staged"  # under DMS_STAGING_FOLDER — temp quarantine


def _wait_for_desktop_free() -> bool:
    """
    Wait for robot_lock's lock file to be clear, regardless of holder.
    Unlike robot_lock.acquire_robot_lock() (which only yields to a holder
    named "password_reset"), this waits for ANY in-progress automation —
    needed because dms_upload_runner runs as its own OS process outside the
    RF queue's single worker thread, so it's the one new source of
    same-desktop concurrency this app didn't have before.
    """
    if not is_robot_locked():
        return True
    logger.warning("[DMS Upload] Desktop lock held by another automation — waiting...")
    start = time.time()
    while is_robot_locked():
        elapsed = time.time() - start
        if elapsed >= _DESKTOP_WAIT_MAX_SECONDS:
            logger.error(
                f"[DMS Upload] Desktop still locked after {_DESKTOP_WAIT_MAX_SECONDS}s — "
                "skipping this run rather than risking a collision."
            )
            return False
        time.sleep(_DESKTOP_WAIT_POLL_SECONDS)
    logger.info("[DMS Upload] Desktop free — proceeding.")
    return True


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
    logger.info("DMS upload trigger started")

    staged_records = get_staged_dms_records()
    if not staged_records:
        logger.info("No staged records — nothing to upload")
        return

    script_path = os.path.join(config.RF_SCRIPTS_PATH, "dms_upload.robot")
    if not os.path.exists(script_path):
        logger.error(f"dms_upload.robot not found at {script_path}")
        return

    if not _wait_for_desktop_free():
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
# focus for the few seconds it's open. _wait_for_desktop_free() below
# already exists to handle exactly that handoff (it predates this change —
# written for back when this ran as an independent Task Scheduler process,
# before v18 temporarily folded it into the RF queue) and is unaffected by
# this change: run_dms_upload() still calls it before touching anything.
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

_batch_lock = threading.Lock()


def trigger_dms_upload_for(history_id: int) -> None:
    """
    Fire-and-forget: starts run_dms_upload() on its own background thread
    (daemon, same pattern as gst_runner.py's per-record threads) and
    returns immediately. Does not go through rf_queue — no job_id, no
    polling; the outcome is only in the logs and in this record's own
    dms_status, same as it always was for the Task-Scheduler-driven path.
    """
    def _run():
        if not _batch_lock.acquire(blocking=False):
            logger.info(
                f"[DMS Upload] Batch already running — history_id={history_id}'s "
                "staged file will be picked up by that run (or the next trigger)."
            )
            return
        try:
            run_dms_upload()
        except Exception as e:
            logger.error(
                f"[DMS Upload] Batch run crashed (triggered by history_id={history_id}): {e}",
                exc_info=True
            )
        finally:
            _batch_lock.release()

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


if __name__ == "__main__":
    init_pool()
    run_dms_upload()
