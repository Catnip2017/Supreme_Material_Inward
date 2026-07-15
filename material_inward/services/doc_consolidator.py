"""
services/doc_consolidator.py — PDF consolidation after MIGO 103.

Merges invoice + ewaybill + lr PDFs into one consolidated PDF.
Saved to DMS_STAGING_FOLDER (set in .env as DMS_STAGING_FOLDER).
Path is stored in history.consolidated_doc_path via set_dms_status.

PyMuPDF (fitz) is already in requirements.txt — no new dependency.
"""

import os
import fitz  # PyMuPDF — already in requirements.txt
from typing import Optional

from config.config import config
from config.logger import get_logger

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


def consolidate_documents(history_id: int, details: dict) -> Optional[str]:
    """
    Merge the three source PDFs for a history record into one file.

    Args:
        history_id : History record ID — used for output filename
        details    : Output of get_history_details_by_id() containing
                     invoice_data, ewaybill_data, lr_data (each with filename)

    Returns:
        Absolute path of the merged PDF, or None on failure.
    """
    staging_folder = getattr(config, "DMS_STAGING_FOLDER",
                             r"C:\material_inward\dms_staging")
    os.makedirs(staging_folder, exist_ok=True)

    # Collect source paths in document order
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

    if not source_paths:
        logger.error(
            f"Consolidate: no source files found for history_id={history_id}"
        )
        return None

    output_path = os.path.join(
        staging_folder, f"h{history_id}_consolidated.pdf"
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
