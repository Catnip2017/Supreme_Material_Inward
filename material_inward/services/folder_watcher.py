"""
services/folder_watcher.py — Watch a local folder for incoming PDFs.

v4 changes:
- New folder structure: incoming/grouped/, ocr_done/, failed/
- Loose files in incoming/ root → moved into grouped/<group_key>/
- All 3 docs (invoice + eway + lr) required before OCR runs
- After OCR success → group folder moved to ocr_done/<invoice_number>/
- After OCR failure → group folder moved to failed/<group_key>_<timestamp>/
- 60-day orphan cleanup: incomplete groups in grouped/ moved to failed/orphan_*
- ocr_status set on history record

"""

import os
import time
import shutil
import threading
from datetime import datetime, timedelta

from config.config import config
from config.logger import get_logger
from database.db_operations import (
    create_history_record, save_invoice_to_db,
    save_ewaybill_to_db, save_lr_to_db,
    set_ocr_status
)
from database.gatein_operations import upsert_gatein_entry, map_ocr_to_gatein
from database.migo_operations import upsert_migo_entry, map_ocr_to_migo
from database.miro_operations import upsert_miro_entry, map_ocr_to_miro
from services.extract import process_document
from services.mail_service import send_ocr_failure_alert

logger = get_logger(__name__)

WATCH_FOLDER     = os.getenv("WATCH_FOLDER", r"C:\material_inward\incoming")
GROUPED_FOLDER   = os.path.join(WATCH_FOLDER, "grouped")
OCR_DONE_FOLDER  = os.path.join(os.path.dirname(WATCH_FOLDER), "ocr_done")
FAILED_FOLDER    = os.path.join(os.path.dirname(WATCH_FOLDER), "failed")

STABLE_SECONDS = 30      # File must be unmodified this long before being touched
ORPHAN_DAYS    = 60      #Must match DB_RETENTION_DAYS in app.py cleanup
POLL_INTERVAL  = 30      # Watcher cycle interval


# ============================================================
# UTILITIES
# ============================================================

def _detect_doc_type(filename: str):
    name = filename.lower()
    if config.INVOICE_KEYWORD in name:  return "invoice"
    if config.EWAYBILL_KEYWORD in name: return "ewaybill"
    if config.LR_KEYWORD in name:       return "lr"
    return None


def _get_group_key(filename: str) -> str:
    """
    Group key = everything before the doc type keyword.
    VendorName_INV2024001_invoice.pdf → vendorname_inv2024001
    """
    name_lower = filename.lower()
    for keyword in [config.INVOICE_KEYWORD, config.EWAYBILL_KEYWORD, config.LR_KEYWORD]:
        if keyword in name_lower:
            idx = name_lower.index(keyword)
            return filename[:idx].rstrip("_- ").lower()
    return filename.lower()


def _is_stable(file_path: str) -> bool:
    try:
        modified_ago = time.time() - os.path.getmtime(file_path)
        return modified_ago >= STABLE_SECONDS
    except Exception:
        return False


def _ensure_dirs():
    for folder in [WATCH_FOLDER, GROUPED_FOLDER, OCR_DONE_FOLDER, FAILED_FOLDER]:
        os.makedirs(folder, exist_ok=True)


# ============================================================
# STEP 1: SWEEP LOOSE FILES INTO GROUPED/
# ============================================================

def _sweep_loose_files():
    """Move stable loose files in WATCH_FOLDER root into grouped/<group_key>/."""
    try:
        for filename in os.listdir(WATCH_FOLDER):
            file_path = os.path.join(WATCH_FOLDER, filename)
            if not os.path.isfile(file_path):
                continue  # skip subfolders
            if not filename.lower().endswith(".pdf"):
                continue

            doc_type = _detect_doc_type(filename)
            if not doc_type:
                logger.warning(f"Unrecognized filename in incoming: {filename}")
                continue

            if not _is_stable(file_path):
                continue  # still being copied

            group_key = _get_group_key(filename)
            group_folder = os.path.join(GROUPED_FOLDER, group_key)
            os.makedirs(group_folder, exist_ok=True)

            dest_path = os.path.join(group_folder, filename)
            try:
                shutil.move(file_path, dest_path)
                logger.info(f"Grouped: {filename} → {group_key}/")
            except Exception as e:
                logger.error(f"Failed to move {filename} into group: {e}")
    except Exception as e:
        logger.error(f"Loose file sweep error: {e}")


# ============================================================
# STEP 2: PROCESS GROUPS THAT HAVE ALL 3 DOCS
# ============================================================

def _process_complete_groups():
    """Find groups with all 3 docs present and stable, then run OCR."""
    if not os.path.exists(GROUPED_FOLDER):
        return

    for group_key in os.listdir(GROUPED_FOLDER):
        group_folder = os.path.join(GROUPED_FOLDER, group_key)
        if not os.path.isdir(group_folder):
            continue

        # Map files in group by doc type
        files_by_type = {}
        all_stable = True

        for filename in os.listdir(group_folder):
            if not filename.lower().endswith(".pdf"):
                continue
            file_path = os.path.join(group_folder, filename)
            doc_type = _detect_doc_type(filename)
            if not doc_type:
                continue
            files_by_type[doc_type] = file_path
            if not _is_stable(file_path):
                all_stable = False

        # Require ALL 3 doc types
        if not all_stable:
            continue
        if not all(t in files_by_type for t in ["invoice", "ewaybill", "lr"]):
            continue

        logger.info(f"Group ready for OCR: {group_key}")
        _process_batch(group_key, group_folder, files_by_type)


def _process_batch(group_key: str, group_folder: str, files_by_type: dict):
    """Run OCR on a complete group, save to DB, move files appropriately."""
    history_id = create_history_record()
    if not history_id:
        logger.error(f"Failed to create history record for group: {group_key}")
        return

    extracted = {"invoice": None, "ewaybill": None, "lr": None}
    ocr_succeeded = True
    error_detail = None

    for doc_type, file_path in files_by_type.items():
        filename = os.path.basename(file_path)
        safe_name = f"h{history_id}_{filename}"
        upload_dest = os.path.join(config.UPLOAD_FOLDER, safe_name)

        try:
            shutil.copy2(file_path, upload_dest)
            data = process_document(doc_type, upload_dest, safe_name)
            if data:
                data["filename"] = safe_name
                extracted[doc_type] = data
                logger.info(f"OCR OK: {doc_type} → history_id={history_id}")
            else:
                ocr_succeeded = False
                error_detail = f"OCR returned no data for {doc_type}"
                logger.warning(error_detail)
                break
        except Exception as e:
            ocr_succeeded = False
            error_detail = f"OCR exception for {doc_type}: {e}"
            logger.error(error_detail, exc_info=True)
            break

    if ocr_succeeded:
        # Save extracted data to DB
        if extracted["invoice"]:
            save_invoice_to_db(history_id, extracted["invoice"])
        if extracted["ewaybill"]:
            save_ewaybill_to_db(history_id, extracted["ewaybill"])
        if extracted["lr"]:
            save_lr_to_db(history_id, extracted["lr"])

        inv  = extracted["invoice"]
        eway = extracted["ewaybill"]
        lr   = extracted["lr"]
        upsert_gatein_entry(history_id, map_ocr_to_gatein(inv, eway, lr))
        upsert_migo_entry(history_id, map_ocr_to_migo(inv, eway, lr))
        upsert_miro_entry(history_id, map_ocr_to_miro(inv, eway, lr))

        # Move group folder to ocr_done/<invoice_number>/
        invoice_number = (inv or {}).get("invoice_number", "")
        safe_inv = "".join(c for c in invoice_number if c.isalnum() or c in "-_") or f"history_{history_id}"

        dest_folder = os.path.join(OCR_DONE_FOLDER, safe_inv)
        if os.path.exists(dest_folder):
            dest_folder = f"{dest_folder}_{history_id}"

        try:
            shutil.move(group_folder, dest_folder)
            set_ocr_status(history_id, "success")
            logger.info(f"Batch complete — history_id={history_id} → ocr_done/{safe_inv}")
        except Exception as e:
            logger.error(f"Could not move group folder to ocr_done: {e}")
            set_ocr_status(history_id, "success", failed_path=group_folder)

    else:
        # OCR failed — move group to failed/<group_key>_<timestamp>/
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fail_dest = os.path.join(FAILED_FOLDER, f"{group_key}_{timestamp}")
        try:
            shutil.move(group_folder, fail_dest)
            set_ocr_status(history_id, "failed", failed_path=fail_dest)
            logger.warning(f"OCR failed for group {group_key} → moved to failed/")

            inv = extracted.get("invoice") or {}
            send_ocr_failure_alert(
                history_id=history_id,
                invoice_number=inv.get("invoice_number"),
                error_detail=error_detail
            )
        except Exception as e:
            logger.error(f"Could not move failed group to failed/: {e}")
            set_ocr_status(history_id, "failed", failed_path=group_folder)


# ============================================================
# STEP 3: ORPHAN CLEANUP
# ============================================================

def _cleanup_orphans():
    """
    Move incomplete groups in grouped/ older than ORPHAN_DAYS to failed/orphan_*.
    These are groups that never got all 3 docs (invoice + eway + lr).
    We do NOT call set_ocr_status here — if a history record existed for this
    group it was already cleaned by the DB retention job.
    """
    if not os.path.exists(GROUPED_FOLDER):
        return

    cutoff = datetime.now() - timedelta(days=ORPHAN_DAYS)

    for group_key in os.listdir(GROUPED_FOLDER):
        group_folder = os.path.join(GROUPED_FOLDER, group_key)
        if not os.path.isdir(group_folder):
            continue
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(group_folder))
            if mtime < cutoff:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                fail_dest = os.path.join(
                    FAILED_FOLDER, f"orphan_{group_key}_{timestamp}"
                )
                shutil.move(group_folder, fail_dest)
                logger.warning(
                    f"Orphan group cleaned: {group_key} "
                    f"({ORPHAN_DAYS}d old) → {fail_dest}"
                )
                # NOTE: No set_ocr_status call here intentionally.
                # group folders in grouped/ never had a history record
                # created for them yet (OCR never ran — they were incomplete).
        except Exception as e:
            logger.error(f"Orphan cleanup failed for {group_key}: {e}")
# ============================================================
# MAIN POLL LOOP
# ============================================================

def _poll_loop(interval: int = POLL_INTERVAL):
    logger.info(f"Folder watcher started — watching: {WATCH_FOLDER}")
    _ensure_dirs()

    last_orphan_check = time.time()

    while True:
        try:
            # Guard: if watch folder is a network drive that went offline
            if not os.path.exists(WATCH_FOLDER):
                logger.error(
                    f"Watch folder not accessible: {WATCH_FOLDER} — "
                    f"NAS drive may be disconnected. Retrying in {interval}s."
                )
                time.sleep(interval)
                continue

            _sweep_loose_files()           # ← was missing
            _process_complete_groups()
            
            # Run orphan cleanup once a day
            if time.time() - last_orphan_check > 86400:
                _cleanup_orphans()
                last_orphan_check = time.time()

        except Exception as e:
            logger.error(f"Folder watcher cycle error: {e}", exc_info=True)
        time.sleep(interval)


def start_folder_watcher() -> threading.Thread:
    t = threading.Thread(target=_poll_loop, daemon=True, name="FolderWatcher")
    t.start()
    return t