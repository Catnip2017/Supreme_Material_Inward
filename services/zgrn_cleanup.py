"""
ZGRN certificate PDF cleanup (ADDED 2026-10-08, client request).

Every ZGRN Print leaves PDFs on disk that nothing ever removed:
  1. SAP's own download folder (config.ZGRN_MIP_FOLDER, C:\\zgrn_print) --
     "<material doc>_GRN.pdf" / "<material doc>_GCD.pdf".
  2. The per-record copy rf_queue_worker._process_zgrn_print() makes into
     config.UPLOAD_FOLDER ("h<history_id>_<name>.pdf") so the app can serve
     it, tracked in history_extras (doc_type 'zgrn_grn' / 'zgrn_cgd').

Once a day this deletes the FILES in both places that are older than
RETENTION_DAYS (30) -- decided purely by each file's AGE, never by when the
app started, so restarting the app (even many times a day) can never delete
anything recent.

WHEN it runs: only inside a quiet nightly window (RUN_WINDOW_START_HOUR to
RUN_WINDOW_END_HOUR, server local time), and at most once per calendar date.
The date of the last run is stamped in a small file (STAMP_FILE) so a restart
neither repeats the run nor triggers one outside the window. If the app is
down during the window that day, the cleanup is simply skipped until the
next night.

The history_extras rows are deliberately left alone:
the Tax Classification / MIGO 103 tabs check whether the file still exists
on disk and offer Print again if it is gone, so a user returning after two
months can regenerate the certificate by clicking Print.

Safety: only filenames matching "<digits>_GRN.pdf" / "<digits>_GCD.pdf" are
touched in the SAP folder, and only files the DB lists as zgrn_* are
touched in UPLOAD_FOLDER. Nothing else (invoice/e-way/LR/Others PDFs, DMS
files) is ever deleted by this module. Every step is best-effort: an error
on one file is logged and skipped, never raised.
"""

import os
import re
import time
import threading
from datetime import datetime

from config.config import config
from config.logger import get_logger
from database.scenario_operations import list_zgrn_extras_older_than

logger = get_logger(__name__)

RETENTION_DAYS = 30
RUN_WINDOW_START_HOUR = 2             # 02:00 server local time ...
RUN_WINDOW_END_HOUR = 4               # ... up to (not including) 04:00
_CHECK_INTERVAL_SECONDS = 30 * 60     # how often the loop looks at the clock
STAMP_FILE = os.path.join(config.APP_ROOT, "zgrn_cleanup_last_run.txt")

_SAP_FILE_RE = re.compile(r"^\d+_(GRN|GCD)\.pdf$", re.IGNORECASE)


def _delete_old_sap_folder_files(cutoff_ts: float) -> int:
    folder = config.ZGRN_MIP_FOLDER
    removed = 0
    try:
        names = os.listdir(folder)
    except OSError as e:
        logger.warning(f"[ZGRN cleanup] cannot read {folder}: {e}")
        return 0
    for name in names:
        if not _SAP_FILE_RE.match(name):
            continue
        path = os.path.join(folder, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff_ts:
                os.remove(path)
                removed += 1
        except OSError as e:
            logger.warning(f"[ZGRN cleanup] could not remove {path}: {e}")
    return removed


def _delete_old_upload_copies() -> int:
    removed = 0
    for row in list_zgrn_extras_older_than(RETENTION_DAYS):
        name = row.get("filename") or ""
        # filename comes from our own DB, but never follow a path out of
        # UPLOAD_FOLDER even so.
        if not name or name != os.path.basename(name):
            continue
        path = os.path.join(config.UPLOAD_FOLDER, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
        except OSError as e:
            logger.warning(f"[ZGRN cleanup] could not remove {path}: {e}")
    return removed


def run_zgrn_cleanup() -> None:
    cutoff_ts = time.time() - RETENTION_DAYS * 24 * 60 * 60
    sap_removed = _delete_old_sap_folder_files(cutoff_ts)
    upload_removed = _delete_old_upload_copies()
    logger.info(
        f"[ZGRN cleanup] done -- removed {sap_removed} file(s) from "
        f"{config.ZGRN_MIP_FOLDER} and {upload_removed} copy(ies) from the "
        f"uploads folder (older than {RETENTION_DAYS} days). DB rows kept."
    )


def _read_last_run_date() -> str:
    try:
        with open(STAMP_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_last_run_date(date_str: str) -> None:
    try:
        with open(STAMP_FILE, "w", encoding="utf-8") as f:
            f.write(date_str)
    except OSError as e:
        logger.warning(f"[ZGRN cleanup] could not write {STAMP_FILE}: {e}")


def _cleanup_loop() -> None:
    while True:
        try:
            now = datetime.now()
            today = now.date().isoformat()
            in_window = RUN_WINDOW_START_HOUR <= now.hour < RUN_WINDOW_END_HOUR
            if in_window and _read_last_run_date() != today:
                run_zgrn_cleanup()
                # Stamp the date even if some files failed to delete (each
                # failure is logged) so one bad file can't make this retry
                # every 30 minutes all night.
                _write_last_run_date(today)
        except Exception as e:
            logger.error(f"[ZGRN cleanup] run crashed: {e}", exc_info=True)
        time.sleep(_CHECK_INTERVAL_SECONDS)


def start_zgrn_cleanup() -> threading.Thread:
    """Call once at app startup (see app.py). Returns the daemon thread."""
    thread = threading.Thread(target=_cleanup_loop, daemon=True, name="ZgrnCleanup")
    thread.start()
    logger.info(
        f"ZGRN cleanup thread started (once per date, {RUN_WINDOW_START_HOUR:02d}:00-"
        f"{RUN_WINDOW_END_HOUR:02d}:00, {RETENTION_DAYS}-day retention; last run: "
        f"{_read_last_run_date() or 'never'})."
    )
    return thread
