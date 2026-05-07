"""
services/folder_watcher.py — Watch a local folder for incoming invoice PDFs.

Naming convention:
    VendorName_INV2024001_invoice.pdf
    VendorName_INV2024001_eway.pdf
    VendorName_INV2024001_lr.pdf

Group key = everything before the doc type keyword (e.g. VendorName_INV2024001).
After OCR, batch folder moves to processed/<invoice_number>/.

Set INTAKE_METHOD=folder in .env to activate.
Mail poller kept intact — switch INTAKE_METHOD=mail to revert.
"""

import os
import time
import shutil
import threading

from config.config import config
from config.logger import get_logger
from database.connection import init_pool
from database.db_operations import (
    create_history_record, save_invoice_to_db,
    save_ewaybill_to_db, save_lr_to_db
)
from database.gatein_operations import upsert_gatein_entry, map_ocr_to_gatein
from database.migo_operations import upsert_migo_entry, map_ocr_to_migo
from database.miro_operations import upsert_miro_entry, map_ocr_to_miro
from services.extract import process_document

logger = get_logger(__name__)

WATCH_FOLDER     = os.getenv("WATCH_FOLDER", r"C:\material_inward\incoming")
PROCESSED_FOLDER = os.path.join(WATCH_FOLDER, "processed")
FAILED_FOLDER    = os.path.join(WATCH_FOLDER, "failed")

# How long a file must be unmodified before we consider it fully uploaded
STABLE_SECONDS = 60


def _detect_doc_type(filename: str):
    name = filename.lower()
    if config.INVOICE_KEYWORD in name:  return "invoice"
    if config.EWAYBILL_KEYWORD in name: return "ewaybill"
    if config.LR_KEYWORD in name:       return "lr"
    return None


def _get_group_key(filename: str) -> str:
    """
    Extract group key = everything before the doc type keyword.
    VendorName_INV2024001_invoice.pdf → VendorName_INV2024001
    """
    name_lower = filename.lower()
    for keyword in [config.INVOICE_KEYWORD, config.EWAYBILL_KEYWORD, config.LR_KEYWORD]:
        if keyword in name_lower:
            idx = name_lower.index(keyword)
            return filename[:idx].rstrip("_- ").lower()
    return filename.lower()


def _is_stable(file_path: str) -> bool:
    """File must not have been modified in last STABLE_SECONDS."""
    modified_ago = time.time() - os.path.getmtime(file_path)
    return modified_ago >= STABLE_SECONDS


def _group_files(folder: str) -> dict:
    """
    Scan folder, group PDFs by prefix key.
    Only include groups where ALL files present are stable.
    Returns: {"VendorName_INV2024001": {"invoice": path, "eway": path, "lr": path}}
    """
    groups = {}
    for filename in os.listdir(folder):
        if not filename.lower().endswith(".pdf"):
            continue
        doc_type = _detect_doc_type(filename)
        if not doc_type:
            continue
        file_path = os.path.join(folder, filename)
        key = _get_group_key(filename)
        if key not in groups:
            groups[key] = {"files": {}, "stable": True}
        groups[key]["files"][doc_type] = file_path
        # If any file in group is not stable, skip whole group this cycle
        if not _is_stable(file_path):
            groups[key]["stable"] = False

    # Return only stable groups that have at least an invoice
    return {
        key: data["files"]
        for key, data in groups.items()
        if data["stable"] and "invoice" in data["files"]
    }


def _process_batch(group_key: str, batch: dict) -> None:
    """
    Run OCR on a batch of files, save to DB, move to processed/<invoice_number>/.
    """
    history_id = create_history_record()
    if not history_id:
        logger.error(f"Failed to create history record for group: {group_key}")
        return

    os.makedirs(PROCESSED_FOLDER, exist_ok=True)
    os.makedirs(FAILED_FOLDER, exist_ok=True)

    extracted = {"invoice": None, "ewaybill": None, "lr": None}
    processed_files = []
    failed_files = []

    for doc_type, file_path in batch.items():
        filename = os.path.basename(file_path)
        safe_name = f"h{history_id}_{filename}"
        dest = os.path.join(config.UPLOAD_FOLDER, safe_name)

        try:
            shutil.copy2(file_path, dest)
            data = process_document(doc_type, dest, safe_name)
            if data:
                data["filename"] = safe_name
                extracted[doc_type] = data
                processed_files.append(file_path)
                logger.info(f"OCR OK: {doc_type} → history_id={history_id}")
            else:
                failed_files.append(file_path)
                logger.warning(f"OCR empty: {doc_type} for group={group_key}")
        except Exception as e:
            failed_files.append(file_path)
            logger.error(f"OCR error {doc_type} group={group_key}: {e}")

    # Save to DB
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

    # Determine destination folder name — use invoice number if extracted
    invoice_number = (inv or {}).get("invoice_number", "")
    safe_name = "".join(c for c in invoice_number if c.isalnum() or c in "-_")
    if not safe_name:
        safe_name = f"history_{history_id}"

    # Move processed files to processed/<invoice_number>/
    dest_folder = os.path.join(PROCESSED_FOLDER, safe_name)
    if os.path.exists(dest_folder):
        dest_folder = f"{dest_folder}_{history_id}"
    os.makedirs(dest_folder, exist_ok=True)

    for file_path in processed_files:
        try:
            shutil.move(file_path, os.path.join(dest_folder, os.path.basename(file_path)))
        except Exception as e:
            logger.error(f"Failed to move processed file {file_path}: {e}")

    # Move failed files to failed/<invoice_number>/
    if failed_files:
        fail_folder = os.path.join(FAILED_FOLDER, safe_name)
        if os.path.exists(fail_folder):
            fail_folder = f"{fail_folder}_{history_id}"
        os.makedirs(fail_folder, exist_ok=True)
        for file_path in failed_files:
            try:
                shutil.move(file_path, os.path.join(fail_folder, os.path.basename(file_path)))
            except Exception as e:
                logger.error(f"Failed to move failed file {file_path}: {e}")

    logger.info(f"Batch complete — history_id={history_id} → processed/{safe_name}")


def _poll_loop(interval: int = 30) -> None:
    """Poll WATCH_FOLDER every interval seconds."""
    logger.info(f"Folder watcher started — watching: {WATCH_FOLDER}")
    os.makedirs(WATCH_FOLDER, exist_ok=True)
    while True:
        try:
            groups = _group_files(WATCH_FOLDER)
            for group_key, batch in groups.items():
                logger.info(f"Processing group: {group_key} — {list(batch.keys())}")
                _process_batch(group_key, batch)
        except Exception as e:
            logger.error(f"Folder watcher error: {e}")
        time.sleep(interval)


def start_folder_watcher() -> threading.Thread:
    """Start folder watcher as a background daemon thread."""
    t = threading.Thread(
        target=_poll_loop,
        args=(30,),
        daemon=True,
        name="FolderWatcher"
    )
    t.start()
    return t