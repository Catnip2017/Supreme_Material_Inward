"""
services/log_cleanup.py — Scheduled cleanup of RF run logs/screenshots.

Run by Windows Task Scheduler (NOT via the RF queue) — same pattern as
services/dms_scheduler.py.

Every RF run (gate_in, migo_103, migo_105, miro, zgatein_update, po_fetch,
po_list_fetch, dms_upload, ...) creates its own timestamped folder under
RF_OUTPUT_PATH containing log.html, report.html, output.xml, and
sap-screenshot_N.jpg / *.png files. Nothing ever deleted these — this script
does, on two different retention windows:

  - dms_upload_* folders   -> deleted once older than 3 days
  - everything else        -> deleted once older than 15 days

The script is idempotent: it always computes "now - retention_days" and
deletes whatever qualifies, regardless of how long it's been since the last
run. Safe to schedule at any cadence; recommended split below.

Windows Task Scheduler setup (two separate triggers, same script):
    Task "MaterialInward - Cleanup RF Logs" (every 15 days):
        python "C:\\material_inward\\app\\services\\log_cleanup.py" --target rf

    Task "MaterialInward - Cleanup DMS Bot Logs" (every 3 days):
        python "C:\\material_inward\\app\\services\\log_cleanup.py" --target dms

    (Or schedule a single daily run with --target all — the retention math
    is the same either way.)
"""

import argparse
import os
import shutil
import sys
import logging
from datetime import datetime, timedelta

# Ensure project root is on path when called directly by Task Scheduler
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [log_cleanup] %(message)s",
)
logger = logging.getLogger(__name__)

DMS_PREFIX = "dms_upload_"
DMS_RETENTION_DAYS = 3
RF_RETENTION_DAYS = 15

# Uploaded consolidated PDFs are business documents, not debug artifacts —
# kept far longer than screenshots. Set to 0 to disable this pass entirely.
DMS_UPLOADED_PDF_RETENTION_DAYS = 90


def _folder_age_days(path: str) -> float:
    mtime = os.path.getmtime(path)
    return (datetime.now() - datetime.fromtimestamp(mtime)).total_seconds() / 86400


def _delete_folder(path: str) -> bool:
    try:
        shutil.rmtree(path)
        return True
    except Exception as e:
        logger.error(f"Failed to delete folder {path}: {e}")
        return False


def cleanup_rf_output(
    retention_days: int,
    dms_only: bool = False,
    exclude_dms: bool = False,
) -> None:
    """
    Walk RF_OUTPUT_PATH and delete run folders older than retention_days.
    dms_only    -> only touch folders prefixed "dms_upload_"
    exclude_dms -> touch everything EXCEPT folders prefixed "dms_upload_"
    """
    root = config.RF_OUTPUT_PATH
    if not os.path.isdir(root):
        logger.warning(f"RF_OUTPUT_PATH does not exist: {root} — nothing to clean")
        return

    deleted = kept = 0
    for name in os.listdir(root):
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue

        is_dms = name.startswith(DMS_PREFIX)
        if dms_only and not is_dms:
            continue
        if exclude_dms and is_dms:
            continue

        try:
            age = _folder_age_days(full)
        except Exception as e:
            logger.error(f"Could not read age of {full}: {e}")
            continue

        if age >= retention_days:
            if _delete_folder(full):
                logger.info(f"Deleted ({age:.1f}d old): {full}")
                deleted += 1
        else:
            kept += 1

    logger.info(
        f"RF output cleanup done — root={root} "
        f"scope={'dms_upload_*' if dms_only else ('non-dms' if exclude_dms else 'all')} "
        f"retention={retention_days}d deleted={deleted} kept={kept}"
    )


def cleanup_uploaded_dms_pdfs(retention_days: int = DMS_UPLOADED_PDF_RETENTION_DAYS) -> None:
    """
    Optional housekeeping: DMS_STAGING_FOLDER/uploaded accumulates a copy of
    every consolidated PDF once it's been pushed to Contentverse. These are
    real business documents (not screenshots), so retention here is much
    longer and disabled by default cleanup cadence — call explicitly if wanted.
    """
    if retention_days <= 0:
        return
    uploaded_dir = os.path.join(config.DMS_STAGING_FOLDER, "uploaded")
    if not os.path.isdir(uploaded_dir):
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    deleted = 0
    for name in os.listdir(uploaded_dir):
        full = os.path.join(uploaded_dir, name)
        if os.path.isfile(full) and datetime.fromtimestamp(os.path.getmtime(full)) < cutoff:
            try:
                os.remove(full)
                deleted += 1
            except Exception as e:
                logger.error(f"Failed to delete {full}: {e}")
    logger.info(f"Uploaded DMS PDF cleanup — deleted={deleted} retention={retention_days}d")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cleanup RF run logs/screenshots and DMS bot artifacts."
    )
    parser.add_argument(
        "--target",
        choices=["rf", "dms", "all"],
        default="all",
        help=(
            "rf  = every non-dms_upload RF run folder, 15-day retention. "
            "dms = dms_upload_* run folders only, 3-day retention. "
            "all = both (default)."
        ),
    )
    args = parser.parse_args()

    logger.info(f"Log cleanup started — target={args.target}")

    if args.target in ("rf", "all"):
        cleanup_rf_output(retention_days=RF_RETENTION_DAYS, exclude_dms=True)

    if args.target in ("dms", "all"):
        cleanup_rf_output(retention_days=DMS_RETENTION_DAYS, dms_only=True)

    logger.info("Log cleanup finished")


if __name__ == "__main__":
    main()
