"""
services/dms_scheduler.py — Nightly DMS staging job.

Run by Windows Task Scheduler (NOT via the RF queue).
Example task command:
    python "C:\material_inward\app\services\dms_scheduler.py"

For each history record with dms_status='pending' and migo_103 done:
  1. Prepends a cover page (GIN + MIGO 103 mat doc) to the consolidated PDF.
  2. Writes a JSON metadata sidecar next to the PDF.
  3. Marks the record dms_status='staged' in PostgreSQL.

Future: when client requests MIRO numbers, add a second pass here for
dms_status='update_pending' triggered after miro=1.
"""

import json
import os
import sys
import logging
from datetime import datetime

import fitz  # PyMuPDF — already in requirements.txt

# Ensure project root is on path when called directly by Task Scheduler
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from database.db_operations import get_pending_dms_records, set_dms_status
from database.connection import init_pool
from config.config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [dms_scheduler] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Brand colours (RGB 0-1 scale) ─────────────────────────────────────────────
_BLUE  = (0.118, 0.361, 0.659)   # #1e5ca8
_DARK  = (0.173, 0.243, 0.314)   # #2c3e50
_GREY  = (0.4,   0.4,   0.4)
_WHITE = (1.0,   1.0,   1.0)
_LIGHT = (0.941, 0.949, 0.973)   # #f0f4ff


def _val(record: dict, *keys) -> str:
    """Return first non-empty value from record for given keys, else '—'."""
    for k in keys:
        v = record.get(k)
        if v:
            return str(v).strip()
    return "—"


def _fmt_date(val) -> str:
    if not val:
        return "—"
    if hasattr(val, "strftime"):
        return val.strftime("%d-%m-%Y")
    try:
        return datetime.fromisoformat(str(val)).strftime("%d-%m-%Y")
    except Exception:
        return str(val)


def _prepend_cover_page(record: dict, consolidated_path: str) -> None:
    """
    Create a cover page and prepend it to the consolidated PDF in-place.
    Only GIN and MIGO 103 material doc number shown (per client requirement).
    MIGO 105 / MIRO rows are commented out — uncomment when client requests.
    """
    cover = fitz.open()
    page  = cover.new_page(width=595, height=842)   # A4 portrait

    def rect(x0, y0, x1, y1, colour):
        page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=None, fill=colour)

    def text(x, y, content, size=11, colour=_DARK, bold=False):
        fontname = "hebo" if bold else "helv"
        page.insert_text(fitz.Point(x, y), content,
                         fontsize=size, color=colour, fontname=fontname)

    def separator(y):
        page.draw_line(fitz.Point(36, y), fitz.Point(559, y),
                       color=(0.9, 0.9, 0.9), width=0.5)

    # ── Header band ───────────────────────────────────────────────────────────
    rect(0, 0, 595, 72, _BLUE)
    text(36, 28, "SUPREME PETROCHEM LTD", size=14, colour=_WHITE, bold=True)
    text(36, 50, "Material Inward — Goods Receipt Record", size=10, colour=_WHITE)

    # ── Sub-header ────────────────────────────────────────────────────────────
    processed = _fmt_date(record.get("migo_103_done_at")) or datetime.now().strftime("%d-%m-%Y")
    rect(0, 72, 595, 92, _LIGHT)
    text(36,  86, f"Processed on: {processed}", size=9, colour=_GREY)
    text(420, 86, f"Record ID: h{record.get('id', '—')}", size=9, colour=_GREY)

    # ── Invoice Details section ────────────────────────────────────────────────
    y = 114
    rect(36, y, 559, y + 22, _BLUE)
    text(44, y + 15, "INVOICE DETAILS", size=10, colour=_WHITE, bold=True)
    y += 32

    for label, value in [
        ("Invoice Number",  _val(record, "invoice_number")),
        ("PO Number",       _val(record, "po_number")),
        ("Vendor / Seller", _val(record, "seller_name")),
        ("Invoice Date",    _fmt_date(record.get("invoice_date"))),
        ("Invoice Amount",  _val(record, "grand_total")),
    ]:
        text(44,  y + 11, label + ":", size=10, colour=_GREY)
        text(200, y + 11, value,        size=10, colour=_DARK, bold=True)
        separator(y + 17)
        y += 24

    # ── Document Numbers section ───────────────────────────────────────────────
    y += 16
    rect(36, y, 559, y + 22, _BLUE)
    text(44, y + 15, "GENERATED DOCUMENT NUMBERS", size=10, colour=_WHITE, bold=True)
    y += 32

    doc_rows = [
        ("Gate In Number",        _val(record, "gate_in_number"),      "✓ Done"),
        ("MIGO 103 Material Doc", _val(record, "material_doc_number"), "✓ Done"),
        # ── Uncomment below when client requests MIGO 105 / MIRO numbers ──
        # ("MIGO 105 Doc",        _val(record, "migo_105_doc_number"), "Pending"),
        # ("MIRO FI Doc",         _val(record, "miro_fi_doc_number"),  "Pending"),
    ]

    for label, value, status in doc_rows:
        text(44,  y + 11, label + ":", size=10, colour=_GREY)
        text(200, y + 11, value,        size=11, colour=_BLUE, bold=True)
        text(450, y + 11, status,        size=9,  colour=(0.1, 0.47, 0.24))
        separator(y + 17)
        y += 24

    # ── Contents note ─────────────────────────────────────────────────────────
    y += 20
    rect(36, y, 559, y + 18, _LIGHT)
    text(44, y + 13,
         "Documents included:  Invoice   |   E-Way Bill   |   Lorry Receipt",
         size=9, colour=_GREY)

    # ── Footer ────────────────────────────────────────────────────────────────
    rect(0, 812, 595, 842, _BLUE)
    text(36, 830,
         "System-generated by Material Inward Process — Supreme Petrochem Ltd",
         size=8, colour=_WHITE)
    text(450, 830, datetime.now().strftime("%d-%m-%Y %H:%M"), size=8, colour=_WHITE)

    # ── Prepend: cover first, then existing pages ─────────────────────────────
    main_doc = fitz.open(consolidated_path)
    cover.insert_pdf(main_doc)
    cover.save(consolidated_path + ".tmp")
    cover.close()
    main_doc.close()
    os.replace(consolidated_path + ".tmp", consolidated_path)
    logger.info(f"Cover page prepended to: {consolidated_path}")


def write_metadata_sidecar(record: dict, consolidated_path: str) -> str:
    """Write a JSON sidecar alongside the consolidated PDF for audit trail."""
    sidecar_path = os.path.splitext(consolidated_path)[0] + "_meta.json"
    metadata = {
        "history_id":          record.get("id"),
        "invoice_number":      record.get("invoice_number", ""),
        "po_number":           record.get("po_number", ""),
        "seller_name":         record.get("seller_name", ""),
        "invoice_date":        _fmt_date(record.get("invoice_date")),
        "gate_in_number":      record.get("gate_in_number", ""),
        "material_doc_number": record.get("material_doc_number", ""),
        "consolidated_pdf":    consolidated_path,
        "staged_at":           datetime.now().isoformat(),
        # DMS target folder path (for manual or automated upload into Contentverse)
        "dms_folder": (
            f"MIP Docs/"
            f"{datetime.now().strftime('%Y-%b')}/"
            f"{record.get('invoice_number') or ('h' + str(record.get('id', '')))}"
        ),
    }
    try:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        logger.info(f"Sidecar written: {sidecar_path}")
    except Exception as e:
        logger.error(f"Failed to write sidecar {sidecar_path}: {e}")
    return sidecar_path


def run_dms_staging() -> None:
    """Main entry point called by Windows Task Scheduler."""
    logger.info("DMS staging job started")
    try:
        records = get_pending_dms_records()
    except Exception as e:
        logger.error(f"Failed to fetch pending DMS records: {e}", exc_info=True)
        return

    if not records:
        logger.info("No pending DMS records — nothing to do")
        return

    logger.info(f"Processing {len(records)} pending record(s)")
    staged = skipped = errored = 0

    for rec in records:
        history_id        = rec.get("id")
        consolidated_path = rec.get("consolidated_doc_path", "")

        if not consolidated_path or not os.path.exists(consolidated_path):
            logger.warning(
                f"history_id={history_id}: consolidated PDF not found "
                f"({consolidated_path!r}) — skipping"
            )
            skipped += 1
            continue

        try:
            _prepend_cover_page(rec, consolidated_path)
            write_metadata_sidecar(rec, consolidated_path)
            set_dms_status(history_id, "staged")
            logger.info(
                f"history_id={history_id}: staged OK  "
                f"GIN={rec.get('gate_in_number')}  "
                f"MatDoc={rec.get('material_doc_number')}"
            )
            staged += 1
        except Exception as e:
            logger.error(
                f"history_id={history_id}: staging error: {e}", exc_info=True
            )
            errored += 1

    logger.info(
        f"DMS staging complete — staged={staged}, skipped={skipped}, errors={errored}"
    )


if __name__ == "__main__":
    init_pool()
    run_dms_staging()
