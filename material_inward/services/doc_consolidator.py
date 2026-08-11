"""
services/doc_consolidator.py — PDF consolidation for DMS upload.

v14 changes:
- Trigger moved: this ran immediately after OCR/grouping succeeds
  (folder_watcher.py._process_batch), not after MIGO 103. It no longer
  needs MIGO 103 data at all -- it only ever merged raw source PDFs, never
  embedded GIN/Material Doc numbers itself (that was a separate step in
  dms_scheduler.py's cover page, now retired -- see write_staging_sidecar
  below and services/dms_scheduler.py's updated docstring).
- Merges a 4th source now too: the "Others" document (_OTH suffix,
  history_extras.doc_type='others') alongside invoice/eway/lr, when present.
- output_filename is caller-supplied instead of hardcoded
  h{history_id}_consolidated.pdf.

v16 changes:
- Trigger moved AGAIN: the v14 call site above (folder_watcher.py, right
  after OCR) was removed. This is now called from
  services/rf_queue_worker.py._process_gate_in(), right after Gate In
  posts successfully -- per updated client decision, so the vendor code
  (resolved during Gate In, not known at OCR time) can go into
  output_filename: {invoice_number}_{vendor_code}_{DD_MM_YY}.pdf. This
  function itself is unchanged -- it still only merges whatever source
  PDFs it's given and still doesn't care what step called it.

Saved to DMS_STAGING_FOLDER (set in .env as DMS_STAGING_FOLDER).
Path is stored in history.consolidated_doc_path via set_dms_status.

PyMuPDF (fitz) is already in requirements.txt — no new dependency.
"""

import json
import os
import fitz  # PyMuPDF — already in requirements.txt
from datetime import datetime
from typing import Optional

from config.config import config
from config.logger import get_logger
from database.scenario_operations import get_history_extras

logger = get_logger(__name__)


def _find_file(filename: str) -> str:
    """
    Search across all upload folders for a given filename.
    Mirrors the _find_file logic in app.py without importing from it
    (avoids circular imports).
    """
    if not filename:
        return ""
    for folder in [
        config.UPLOAD_FOLDER,
        config.UPLOAD_PROCESSED_FOLDER,
        config.UPLOAD_FAILED_FOLDER,
    ]:
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            return path
    return ""


def consolidate_documents(
    history_id: int,
    details: dict,
    output_filename: Optional[str] = None
) -> Optional[str]:
    """
    Merge the source PDFs for a history record into one file: invoice +
    ewaybill + lr (whichever are present -- partial-document scenarios are
    fine here, same tolerance as before), plus the "Others" document if one
    was attached for this record (history_extras.doc_type='others').

    Args:
        history_id      : History record ID
        details         : dict with invoice_data, ewaybill_data, lr_data
                           (each a dict with a "filename" key) -- either
                           straight from the in-memory OCR result
                           (folder_watcher.py) or from
                           get_history_details_by_id()
        output_filename : filename to save as, inside DMS_STAGING_FOLDER.
                           Falls back to the old h{history_id}_consolidated.pdf
                           naming if not given, for any other caller.

    Returns:
        Absolute path of the merged PDF, or None on failure.
    """
    # DMS_STAGING_FOLDER is always set now (derived from APP_ROOT in
    # config.py, follows IS_PRODUCTION) -- getattr fallback kept only as a
    # defensive no-op in case this is ever called against a stale config
    # module.
    staging_folder = getattr(config, "DMS_STAGING_FOLDER",
                             r"C:\material_inward\dms_staging")
    os.makedirs(staging_folder, exist_ok=True)

    # Collect source paths in document order: invoice, eway, lr, others.
    source_paths = []
    for doc_key in ("invoice_data", "ewaybill_data", "lr_data"):
        doc      = details.get(doc_key) or {}
        filename = doc.get("filename", "")
        if not filename:
            logger.info(
                f"Consolidate: no filename for {doc_key} "
                f"(history_id={history_id}) — skipping"
            )
            continue
        path = _find_file(filename)
        if path:
            source_paths.append(path)
            logger.debug(f"Consolidate source: {path}")
        else:
            logger.warning(
                f"Consolidate: file not found for {doc_key} "
                f"filename={filename!r} history_id={history_id}"
            )

    # v14: Others document, if one was attached to this record. Distinct
    # from genuinely-unrecognized extras (doc_type='extra') -- those are
    # view-only and deliberately excluded from the DMS-bound PDF.
    others_rows = get_history_extras(history_id, doc_type="others")
    for row in others_rows:
        filename = row.get("filename", "")
        path = _find_file(filename)
        if path:
            source_paths.append(path)
            logger.debug(f"Consolidate source (Others): {path}")
        else:
            logger.warning(
                f"Consolidate: Others file not found filename={filename!r} "
                f"history_id={history_id}"
            )

    if not source_paths:
        logger.error(
            f"Consolidate: no source files found for history_id={history_id}"
        )
        return None

    output_path = os.path.join(
        staging_folder,
        output_filename or f"h{history_id}_consolidated.pdf"
    )

    try:
        merged     = fitz.open()
        page_count = 0

        for src_path in source_paths:
            try:
                doc = fitz.open(src_path)
                merged.insert_pdf(doc)
                page_count += doc.page_count
                doc.close()
                logger.debug(f"Merged {src_path}")
            except Exception as e:
                logger.error(
                    f"Consolidate: failed to merge {src_path} "
                    f"for history_id={history_id}: {e}"
                )
                # Partial merge is better than no merge — continue

        if merged.page_count == 0:
            logger.error(
                f"Consolidate: merged PDF has 0 pages "
                f"for history_id={history_id}"
            )
            merged.close()
            return None

        merged.save(output_path)
        merged.close()
        logger.info(
            f"Consolidated PDF saved: {output_path} "
            f"({len(source_paths)} file(s), {page_count} page(s))"
        )
        return output_path

    except Exception as e:
        logger.error(
            f"Consolidate: unexpected error for history_id={history_id}: {e}",
            exc_info=True
        )
        return None


def write_staging_sidecar(
    history_id: int,
    consolidated_path: str,
    invoice_number: str = "",
    po_number: str = "",
) -> str:
    """
    v14: replaces dms_scheduler.py's write_metadata_sidecar() for the new
    immediately-after-OCR trigger point. Deliberately minimal -- no cover
    page, no GIN/Material Doc numbers (those don't exist yet at this stage,
    and per client decision a cover page embedding them "doesn't make sense"
    now that DMS staging no longer waits for MIGO 103). Also drops
    seller_name/invoice_date/etc that the old sidecar carried -- redundant
    with what's already visible on the invoice's own pages inside the
    consolidated PDF itself.

    This file's role is NOT display -- nothing in the Material Inward UI
    reads it. Its job is the same as before: services/dms_upload_runner.py
    uses its mere presence next to a PDF in DMS_STAGING_FOLDER as the
    "this one is actually ready to upload" signal, quarantining anything
    that doesn't have one. invoice_number/po_number are kept only as a
    light audit trail for anyone opening the sidecar directly on disk.
    """
    sidecar_path = os.path.splitext(consolidated_path)[0] + "_meta.json"
    metadata = {
        "history_id":       history_id,
        "invoice_number":   invoice_number or "",
        "po_number":        po_number or "",
        "consolidated_pdf": consolidated_path,
        "staged_at":        datetime.now().isoformat(),
    }
    try:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        logger.info(f"Staging sidecar written: {sidecar_path}")
    except Exception as e:
        logger.error(f"Failed to write staging sidecar {sidecar_path}: {e}")
    return sidecar_path
