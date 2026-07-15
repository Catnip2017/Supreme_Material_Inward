"""
services/dms_upload_runner.py — Nightly trigger for dms_upload.robot.

Run by Windows Task Scheduler (same pattern as services/dms_scheduler.py),
scheduled to run AFTER dms_scheduler.py has staged the night's consolidated
PDFs (cover page + metadata sidecar written, dms_status='staged').

This is NOT part of the RF queue — dms_upload.robot processes the whole
DMS_STAGING_FOLDER in one batch (bulk upload), not one history record at a
time, so it's triggered as its own scheduled step, same as dms_scheduler.py.

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
from datetime import datetime
from dotenv import dotenv_values

# Ensure project root is on path when called directly by Task Scheduler
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.config import config
from database.db_operations import get_staged_dms_records, set_dms_status
from database.connection import init_pool
from services.robot_lock import acquire_robot_lock, release_robot_lock, is_robot_locked

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [dms_upload_runner] %(message)s",
)
logger = logging.getLogger(__name__)

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
            env={**os.environ, **dotenv_values()},
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


if __name__ == "__main__":
    init_pool()
    run_dms_upload()
