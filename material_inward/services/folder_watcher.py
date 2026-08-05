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

v5 changes (filename convention):
- Incoming files are named INVOICENO_<KEYWORD>.pdf, any case
  (e.g. 4500012345_INV.pdf / 4500012345_EWB.pdf / 4500012345_LR.pdf).
- Doc type is matched on the EXACT last underscore-segment, not a
  substring search anywhere in the filename (avoids false matches from
  invoice numbers that happen to contain "inv"/"ewb"/"lr" as a substring).
- group_key = the invoice-number portion of the filename itself --
  reliable and available before OCR even runs, unlike OCR's own
  invoice_number field (which can come back empty/garbled).
- On success, all 3 files AND the folder are renamed to
  <invoice_no>_<SUFFIX>_<DD_MM_YY> before moving to ocr_done/, using
  today's processing date. This filename-derived value is used for
  file/folder naming ONLY -- invoice_data.invoice_number in the database
  still comes from OCR, unchanged.

v13 changes (partial document scenarios -- see schema_migration_v13.sql):
- A group no longer has to have all 3 doc types before it's processed.
  It's processed once it is COMPLETE (all 3 present) OR once it has been
  STABLE (no new files landing) for GROUP_GRACE_SECONDS with at least an
  Invoice present. Invoice is always required -- it's the anchor document
  every downstream tab keys off (buyer/seller/GSTIN, etc.); a group with
  only an E-Way Bill and/or LR and no Invoice is left waiting indefinitely,
  same as before.
- Files whose filename suffix doesn't match INV/EWB/LR at all are no
  longer silently skipped -- they're copied into the upload folder,
  attached to the new history record via history_extras, and left out of
  the rename/move step (they're not part of the OCR pipeline). Reviewers
  see them under the "Extras" banner on the Extracted Data tab.
- Missing E-Way Bill and/or LR is not an error condition any more --
  gst_runner/rf_runner/map_ocr_to_* already tolerate empty
  ewaybill_data/lr_data (see the None-safety audit before this change).
  The Extracted Data tab prompts the reviewer for why the document is
  missing (goods_delivery_mode / ewb_exemption_reasons) before Approve
  is allowed.

v14 changes (4th document type, timing, DMS reorder -- see
schema_migration_v14.sql):
- STABLE_SECONDS / GROUP_GRACE_SECONDS: 30s -> 40s.
- New 4th recognized suffix, _OTH (config.OTHERS_KEYWORD): miscellaneous
  supporting docs, pre-merged by the sender into one PDF, at most one per
  group like the other 3. NOT run through OCR, but IS merged into the
  DMS-bound consolidated PDF (see doc_consolidator.py) -- distinct from a
  genuinely unrecognized filename (any suffix that doesn't match one of
  the 4 known keywords), which stays view-only, never touches DMS, exactly
  as v13 already handled it.
- "Complete" is now well-bounded: all 4 types present (invoice + ewaybill
  + lr + others) -> process immediately, no grace wait needed, since there
  is no possible 5th type that could still be inbound. Anything short of
  4 (but with an Invoice present) still waits out GROUP_GRACE_SECONDS of
  quiet before processing with whatever's actually present -- unchanged
  from v13 for that case.
- FIX: _sweep_loose_files() used to log a warning for an unrecognized
  loose file and just leave it there, forever, re-logging identically on
  every single poll cycle with no other signal -- the file was never
  moved anywhere so it just sat in the incoming root indefinitely. Now
  sends ONE email alert per unrecognized filename (not persisted anywhere
  -- an in-memory set, self-cleaning: rebuilt from what's actually present
  on disk every cycle, so a renamed/removed file's entry doesn't linger).
  The file itself is still never touched/moved -- if it's renamed to
  match the convention, the very next sweep picks it up normally.
- DMS staging trigger moved: consolidate_documents() + a staging sidecar
  now run immediately after OCR succeeds, right here in _process_batch(),
  instead of waiting for MIGO 103 (previously done in
  services/rf_queue_worker.py post-MIGO-103 -- see that file's updated
  comment). The files needed already exist locally in UPLOAD_FOLDER by
  this point (copied in for OCR), and the merge itself never actually
  depended on MIGO 103 data -- only the now-retired cover page did. See
  services/doc_consolidator.py and services/dms_scheduler.py.

v16 changes (DMS staging trigger moved again, per updated client
decision):
- REVERTED the v14 change above: DMS staging no longer happens here.
  Client now wants the consolidated filename to include the vendor code,
  which isn't known/resolved until Gate In (see gate_in.html's vendor
  lookup/verify flow) -- nowhere close to available at OCR time. Staging
  now happens in services/rf_queue_worker.py._process_gate_in(), right
  after Gate In posts successfully to SAP. See that file's comment for
  the new naming convention and why OCR's invoice_number is used there
  instead of this file's filename-derived group_key (which never left
  this module's local scope and isn't retrievable later anyway).
- This file still owns the entire intake pipeline (grouping, OCR,
  rename-to-ocr_done/failed) exactly as before -- only the DMS-specific
  tail end of _process_batch() moved out.
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
from services.mail_service import send_ocr_failure_alert, send_unrecognized_file_alert
from database.scenario_operations import add_history_extra

logger = get_logger(__name__)

WATCH_FOLDER     = os.getenv("WATCH_FOLDER", r"C:\material_inward\incoming")
GROUPED_FOLDER   = os.path.join(WATCH_FOLDER, "grouped")
OCR_DONE_FOLDER  = os.path.join(os.path.dirname(WATCH_FOLDER), "ocr_done")
FAILED_FOLDER    = os.path.join(os.path.dirname(WATCH_FOLDER), "failed")

STABLE_SECONDS = 40      # File must be unmodified this long before being touched
ORPHAN_DAYS    = 60      #Must match DB_RETENTION_DAYS in app.py cleanup
POLL_INTERVAL  = 30      # Watcher cycle interval

# v13: how long a group with at least an Invoice, but not all 4 doc types,
# waits (with no new files landing) before it's treated as "this is a
# partial-document scenario, not a straggler" and processed with whatever
# it has. Same window as STABLE_SECONDS -- deliberately short, per client
# decision, rather than a long separate grace window: a group that's gone
# quiet for STABLE_SECONDS is assumed final. This is a literal alias, not
# a separately-hardcoded value -- bumping STABLE_SECONDS updates both.
GROUP_GRACE_SECONDS = STABLE_SECONDS

# Uppercase suffix used when renaming files into ocr_done/ on success --
# matches the incoming INVOICENO_<SUFFIX>.pdf convention, just re-applied
# with a fresh date stamp. Keyed by the internal doc_type name.
DOC_TYPE_SUFFIX = {"invoice": "INV", "ewaybill": "EWB", "lr": "LR", "others": "OTH"}

# v14: tracks unrecognized loose filenames we've already alerted on, so
# _sweep_loose_files() sends one email per file, not one per 30s poll
# cycle forever. Self-cleaning by construction -- see _sweep_loose_files.
_notified_unrecognized = set()


# ============================================================
# UTILITIES
# ============================================================

def _detect_doc_type(filename: str):
    """
    Filenames arrive as INVOICENO_<KEYWORD>.pdf (any case), e.g.
    4500012345_INV.pdf / 4500012345_EWB.pdf / 4500012345_LR.pdf /
    4500012345_OTH.pdf.

    Matches the EXACT last underscore-segment (before the extension)
    against the configured keyword -- NOT a substring search anywhere in
    the filename. A substring search would misfire here: e.g. an invoice
    number like "SINV20240091_INV.pdf" contains "inv" inside "SINV" too,
    so a substring match could cut the group key at the wrong position.
    Anchoring to the exact final segment avoids that entirely.
    """
    stem = os.path.splitext(filename)[0]
    if "_" not in stem:
        return None
    suffix = stem.rsplit("_", 1)[1].strip().lower()
    if suffix == config.INVOICE_KEYWORD:  return "invoice"
    if suffix == config.EWAYBILL_KEYWORD: return "ewaybill"
    if suffix == config.LR_KEYWORD:       return "lr"
    if suffix == config.OTHERS_KEYWORD:   return "others"
    return None


def _get_group_key(filename: str) -> str:
    """
    Group key = the invoice-number portion of INVOICENO_<KEYWORD>.pdf --
    everything before the final underscore, lowercased for consistent
    folder naming. E.g. 4500012345_INV.pdf → 4500012345
    (rsplit on the LAST underscore only, so an invoice number that itself
    contains an underscore is still handled correctly.)
    """
    stem = os.path.splitext(filename)[0]
    if "_" not in stem:
        return filename.lower()
    return stem.rsplit("_", 1)[0].strip().lower()


def _is_stable(file_path: str) -> bool:
    try:
        modified_ago = time.time() - os.path.getmtime(file_path)
        return modified_ago >= STABLE_SECONDS
    except Exception:
        return False


def _ensure_dirs() -> bool:
    """
    Returns True if all watcher folders exist/were created, False if any
    couldn't be created (e.g. WATCH_FOLDER lives on a mapped/NAS drive that
    isn't currently mounted -- os.makedirs on a missing drive root raises
    FileNotFoundError: [WinError 3], same class of failure the main loop
    below already anticipates via the "NAS drive may be disconnected"
    check). Callers must not let this raise -- an unmounted drive at
    startup previously killed the whole FolderWatcher thread before it
    ever reached that retry logic.
    """
    try:
        for folder in [WATCH_FOLDER, GROUPED_FOLDER, OCR_DONE_FOLDER, FAILED_FOLDER]:
            os.makedirs(folder, exist_ok=True)
        return True
    except OSError as e:
        logger.error(
            f"Could not create watcher folder(s) under {WATCH_FOLDER}: {e} — "
            "NAS drive may be disconnected. Will keep retrying from the main loop."
        )
        return False


# ============================================================
# STEP 1: SWEEP LOOSE FILES INTO GROUPED/
# ============================================================

def _sweep_loose_files():
    """Move stable loose files in WATCH_FOLDER root into grouped/<group_key>/."""
    try:
        current_unrecognized = set()

        for filename in os.listdir(WATCH_FOLDER):
            file_path = os.path.join(WATCH_FOLDER, filename)
            if not os.path.isfile(file_path):
                continue  # skip subfolders
            if not filename.lower().endswith(".pdf"):
                continue

            doc_type = _detect_doc_type(filename)
            if not doc_type:
                # FIX (v14): used to just log a warning here and leave the
                # file exactly where it was, every single cycle, forever,
                # with no other signal anyone would ever see unless they
                # were tailing the log. Now alerts once per filename (not
                # persisted -- see _notified_unrecognized above) and still
                # deliberately does NOT move or touch the file: if it gets
                # renamed to match the convention, the next sweep just
                # picks it up like any other incoming file.
                current_unrecognized.add(filename)
                if filename not in _notified_unrecognized and _is_stable(file_path):
                    logger.warning(f"Unrecognized filename in incoming: {filename}")
                    try:
                        send_unrecognized_file_alert(filename)
                    except Exception as e:
                        logger.error(f"Failed to send unrecognized-file alert for {filename}: {e}")
                    _notified_unrecognized.add(filename)
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

        # Self-clean: drop tracking for any filename no longer actually
        # sitting in the watch folder root (renamed, removed, or grouped
        # some other way) -- keeps this set from growing unbounded and
        # lets a brand-new file that happens to reuse an old bad name get
        # re-alerted rather than silently swallowed forever.
        _notified_unrecognized.intersection_update(current_unrecognized)
    except Exception as e:
        logger.error(f"Loose file sweep error: {e}")


# ============================================================
# STEP 2: PROCESS GROUPS THAT ARE COMPLETE, OR STABLE-BUT-PARTIAL
# ============================================================

def _group_last_activity(group_folder: str, filenames) -> float:
    """Most recent mtime across every file in the group -- used to decide
    whether a partial group has gone quiet long enough to process."""
    latest = os.path.getmtime(group_folder)
    for filename in filenames:
        try:
            latest = max(latest, os.path.getmtime(os.path.join(group_folder, filename)))
        except OSError:
            continue
    return latest


def _process_complete_groups():
    """
    Find groups that are either complete (all 4 doc types: invoice,
    ewaybill, lr, others) or, per v13/v14, stable-but-partial (at least
    Invoice present, no new files landing for GROUP_GRACE_SECONDS) --
    then run OCR on whatever's present. Unrecognized-suffix files travel
    along as "extras" rather than being silently skipped.

    v14: "complete" now requires all 4 known types, not 3 -- safe to fire
    immediately in that case (no grace wait) because there is no possible
    5th type that could still be inbound, so nothing can arrive later that
    a fast path would miss. Anything short of 4 still waits out the grace
    period, same as v13.
    """
    if not os.path.exists(GROUPED_FOLDER):
        return

    for group_key in os.listdir(GROUPED_FOLDER):
        group_folder = os.path.join(GROUPED_FOLDER, group_key)
        if not os.path.isdir(group_folder):
            continue

        # Map recognized files by doc type; collect unrecognized ones as
        # extras. Duplicate files of an already-seen type just overwrite
        # in files_by_type (unchanged pre-v13 behavior) -- not treated as
        # extras, per client instruction that extras means "unrecognized
        # suffix only".
        files_by_type = {}
        extra_files = []
        all_names = []
        all_stable = True

        for filename in os.listdir(group_folder):
            if not filename.lower().endswith(".pdf"):
                continue
            all_names.append(filename)
            file_path = os.path.join(group_folder, filename)
            if not _is_stable(file_path):
                all_stable = False
            doc_type = _detect_doc_type(filename)
            if doc_type:
                files_by_type[doc_type] = file_path
            else:
                extra_files.append(file_path)

        if not all_stable:
            continue  # something in the group is still being copied

        complete = all(t in files_by_type for t in ["invoice", "ewaybill", "lr", "others"])

        if complete:
            logger.info(f"Group ready for OCR (complete, 4/4): {group_key}")
            _process_batch(group_key, group_folder, files_by_type, extra_files)
            continue

        # v13/v14: not complete -- process anyway once it's an
        # Invoice-anchored partial group that's gone quiet for
        # GROUP_GRACE_SECONDS. A group with no Invoice at all still waits
        # indefinitely, same as before, since Invoice is the anchor
        # document every downstream tab and workflow step keys off.
        if "invoice" not in files_by_type:
            continue

        last_activity = _group_last_activity(group_folder, all_names)
        if time.time() - last_activity < GROUP_GRACE_SECONDS:
            continue  # still might be waiting on a late-arriving doc

        present = sorted(files_by_type.keys())
        logger.info(f"Group ready for OCR (partial — {present}): {group_key}")
        _process_batch(group_key, group_folder, files_by_type, extra_files)


def _process_batch(group_key: str, group_folder: str, files_by_type: dict, extra_files: list = None):
    """Run OCR on a complete group, save to DB, move files appropriately."""
    history_id = create_history_record()
    if not history_id:
        logger.error(f"Failed to create history record for group: {group_key}")
        return

    # v13: copy unrecognized-suffix files into the record's uploads
    # (view/download only, via /view_document & /download_document --
    # same _find_file() lookup already used for invoice/eway/LR, no new
    # route needed). Copied regardless of whether OCR below succeeds --
    # the original stays in group_folder and travels with it either way
    # (to ocr_done/ or failed/), this copy is purely for UI access.
    for extra_path in (extra_files or []):
        extra_filename = os.path.basename(extra_path)
        safe_extra_name = f"h{history_id}_{extra_filename}"
        try:
            shutil.copy2(extra_path, os.path.join(config.UPLOAD_FOLDER, safe_extra_name))
            add_history_extra(history_id, safe_extra_name, extra_filename, doc_type="extra")
            logger.info(f"Extra file attached: {extra_filename} → history_id={history_id}")
        except Exception as e:
            logger.error(f"Failed to attach extra file {extra_filename} to history_id={history_id}: {e}")

    # v14: "Others" document (_OTH) -- not run through OCR, but IS a real
    # 4th document type (not a generic extra). Pulled out of files_by_type
    # before the OCR loop below so process_document() never sees it; kept
    # in files_by_type itself so the rename-on-success step further down
    # still renames it alongside invoice/eway/lr, same as always.
    others_path = files_by_type.get("others")
    if others_path:
        others_filename = os.path.basename(others_path)
        safe_others_name = f"h{history_id}_{others_filename}"
        try:
            shutil.copy2(others_path, os.path.join(config.UPLOAD_FOLDER, safe_others_name))
            add_history_extra(history_id, safe_others_name, others_filename, doc_type="others")
            logger.info(f"Others document attached: {others_filename} → history_id={history_id}")
        except Exception as e:
            logger.error(f"Failed to attach Others document {others_filename} to history_id={history_id}: {e}")

    ocr_types = {k: v for k, v in files_by_type.items() if k != "others"}

    extracted = {"invoice": None, "ewaybill": None, "lr": None}
    ocr_succeeded = True
    error_detail = None

    for doc_type, file_path in ocr_types.items():
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
            # Auto-start GST verification the moment OCR extracts a seller
            # GSTIN -- same trigger app.py's manual-upload path
            # (_run_ocr_and_save) already fires. This is the network-folder
            # intake pipeline (the primary/automated path -- see module
            # docstring), which previously saved invoice_data here and then
            # just stopped: nothing kicked off the GST bots until a human
            # happened to open the GST Approval tab and its 5s poll fired
            # trigger_async() for the first time. Best-effort/non-fatal, same
            # as the app.py side -- a GST-trigger failure must never affect
            # OCR intake succeeding.
            try:
                from services.gst_runner import trigger_async as _gst_trigger
                _gst_trigger(history_id)
            except Exception as _gst_err:
                logger.warning(f"GST auto-trigger failed for history_id={history_id} (non-fatal): {_gst_err}")
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

        # v16: DMS staging no longer happens here -- moved to
        # services/rf_queue_worker.py._process_gate_in(), post-Gate-In, so
        # the consolidated filename can include the vendor code (not known
        # until Gate In). See that file's comment for the new naming and
        # services/doc_consolidator.py's updated docstring.
        date_stamp = datetime.now().strftime("%d_%m_%y")
        safe_key = "".join(c for c in group_key if c.isalnum() or c in "-_") or f"history_{history_id}"

        # Rename the recognized files (invoice/eway/lr/others) + the folder
        # itself to <group_key>_<SUFFIX>_<DD_MM_YY>, then move to ocr_done/.
        # group_key comes straight from the incoming filename convention
        # (INVOICENO_INV.pdf etc.) -- it's guaranteed present and
        # well-formed the moment the file arrives, unlike OCR's
        # invoice_number field, which can come back empty or garbled if
        # OCR misreads the document. Deliberately NOT the same value as
        # invoice_data.invoice_number in the DB -- that stays whatever OCR
        # actually read off the document; this is file/folder naming only.
        for doc_type, old_path in files_by_type.items():
            suffix = DOC_TYPE_SUFFIX[doc_type]
            new_name = f"{safe_key}_{suffix}_{date_stamp}.pdf"
            new_path = os.path.join(group_folder, new_name)
            try:
                os.rename(old_path, new_path)
            except Exception as e:
                logger.error(f"Could not rename {old_path} → {new_name}: {e}")

        dest_folder = os.path.join(OCR_DONE_FOLDER, f"{safe_key}_{date_stamp}")
        if os.path.exists(dest_folder):
            dest_folder = f"{dest_folder}_{history_id}"

        try:
            shutil.move(group_folder, dest_folder)
            set_ocr_status(history_id, "success")
            logger.info(f"Batch complete — history_id={history_id} → ocr_done/{safe_key}_{date_stamp}")
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
    These are groups that never got all 4 docs (invoice + eway + lr + others)
    AND never even accumulated an Invoice (an Invoice-anchored partial group
    gets processed by the grace-period path in _process_complete_groups()
    long before this ever runs -- this is only for groups missing an
    Invoice entirely).
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
    # FIX: _ensure_dirs() used to be called unguarded here -- if WATCH_FOLDER
    # sits on a NAS/mapped drive that isn't mounted yet at process startup,
    # os.makedirs raised straight out of this line, before the thread ever
    # reached the retry-aware loop below, killing FolderWatcher entirely
    # instead of waiting for the drive to come back. Now non-fatal either way.
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

            # Drive is reachable -- make sure the subfolders exist too
            # (they may not have been created yet if it just came back
            # online after being missing at startup or mid-run).
            if not _ensure_dirs():
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
