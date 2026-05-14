"""
services/rf_queue_worker.py — Background RF queue worker.

v4 changes:
- _process_migo_105 now injects items_data into payload so per-line batches
  flow into rf_runner -> migo_105.robot via ITEMS_JSON_BATCH.
- Approval notification fires on Gate In completion (in addition to existing emails).
"""

import threading
import time
from datetime import datetime
from database.po_operations import save_po_line_items

from database.rf_queue_operations import (
    claim_next_pending_job,
    complete_rf_job,
    reset_stuck_running_jobs,
    enqueue_rf_job
)
from database.db_operations import update_history_step, get_history_details_by_id
from database.gatein_operations import update_gatein_rf_result
from database.migo_operations import (
    update_migo_103_rf_result,
    update_migo_105_rf_result,
    upsert_migo_entry,
    get_migo_entry,
)
from database.miro_operations import update_miro_rf_result
from services.rf_runner import (
    execute_gate_in_sap,
    execute_migo_103_sap,
    execute_migo_105_sap,
    execute_miro_sap,
    execute_po_fetch_sap,
    execute_po_list_fetch_sap,
)
from services.mail_service import (
    send_gate_in_notification,
    send_migo_103_notification,
    send_migo_105_notification,
    send_miro_completion_notification
)
from config.logger import get_logger

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 5
STUCK_JOB_TIMEOUT_MINUTES = 15


def _process_gate_in(history_id: int, payload: dict) -> dict:
    result = execute_gate_in_sap(payload)
    if result.get("success"):
        gin = result.get("gate_in_number")
        if not gin:
            return {"success": False, "error": "GIN not captured from SAP."}

        update_gatein_rf_result(history_id, gin, status="success")
        update_history_step(history_id, "gate_in", generated_number=gin)
        upsert_migo_entry(history_id, {"migoHeaderText": gin})

        details = get_history_details_by_id(history_id)
        inv = details.get("invoice_data") or {}
        send_gate_in_notification(
            gate_in_number=gin,
            history_id=history_id,
            invoice_number=inv.get("invoice_number"),
            po_number=inv.get("po_number")
        )
        logger.info(f"Gate In complete — history_id={history_id} GIN={gin}")

        # Auto-enqueue PO fetch
        gatein_entry = details.get("gatein_data") or {}
        po_number = (
            gatein_entry.get("purchase_order") or
            payload.get("purchaseOrder") or
            inv.get("po_number") or ""
        )
        if po_number:
            po_job_id = enqueue_rf_job(
                history_id, "po_fetch",
                {"po_number": po_number, "history_id": history_id}
            )
            if po_job_id:
                logger.info(f"PO fetch enqueued — history_id={history_id} job_id={po_job_id}")
        else:
            logger.warning(f"No PO number found for history_id={history_id} — PO fetch skipped.")
    else:
        update_gatein_rf_result(history_id, "", status="failed", error_message=result.get("error"))
    return result


def _process_po_fetch(history_id: int, payload: dict) -> dict:
    result = execute_po_fetch_sap(payload)
    if result.get("success"):
        po_items = result.get("po_items", [])
        save_po_line_items(history_id, po_items)
        logger.info(f"PO fetch complete — history_id={history_id} {len(po_items)} line(s).")
    else:
        logger.warning(f"PO fetch failed for history_id={history_id}: {result.get('error')}")
    return {"success": True, "error": result.get("error")}


def _process_po_list_fetch(history_id: int, payload: dict) -> dict:
    result = execute_po_list_fetch_sap(payload)
    if result.get("success"):
        logger.info(f"PO list fetch complete — history_id={history_id} {len(result.get('po_list', []))} PO(s).")
    else:
        logger.warning(f"PO list fetch failed — history_id={history_id}: {result.get('error')}")
    return {"success": True, "po_list": result.get("po_list", []), "error": result.get("error")}


def _process_migo_103(history_id: int, payload: dict) -> dict:
    result = execute_migo_103_sap(payload)
    if result.get("success"):
        mat_doc = result["material_doc_number"]
        update_migo_103_rf_result(history_id, mat_doc, status="success")
        update_history_step(history_id, "migo_103", generated_number=mat_doc)

        details = get_history_details_by_id(history_id)
        inv = details.get("invoice_data") or {}
        send_migo_103_notification(
            material_doc_number=mat_doc,
            history_id=history_id,
            invoice_number=inv.get("invoice_number")
        )
        logger.info(f"MIGO 103 complete — history_id={history_id} MatDoc={mat_doc}")
    else:
        update_migo_103_rf_result(history_id, "", status="failed", error_message=result.get("error"))
    return result


def _process_migo_105(history_id: int, payload: dict) -> dict:
    """
    Inject material_doc_number AND items_data (with per-line batches) into payload
    before sending to rf_runner. This is what enables the per-line batch flow.
    """
    migo_entry = get_migo_entry(history_id)
    if not migo_entry:
        logger.error(f"MIGO 105 — no migo entry for history_id={history_id}")
        update_migo_105_rf_result(history_id, status="failed", error_message="No MIGO entry found")
        return {"success": False, "error": "No MIGO entry found"}

    mat_doc = migo_entry.get("material_doc_number", "")
    if not mat_doc:
        logger.error(f"MIGO 105 — material_doc_number empty for history_id={history_id}")
        update_migo_105_rf_result(history_id, status="failed", error_message="No material doc from MIGO 103")
        return {"success": False, "error": "No material doc number from MIGO 103"}

    # Inject mat_doc and items_data into payload before sending to bot
    payload["material_doc_number"] = mat_doc
    payload["items_data"] = migo_entry.get("items_data") or []
    logger.info(f"MIGO 105 — using mat_doc={mat_doc} with {len(payload['items_data'])} line(s)")

    result = execute_migo_105_sap(payload)
    if result.get("success"):
        migo_105_doc = result.get("miro_doc_number", "")
        update_migo_105_rf_result(history_id, status="success")
        update_history_step(history_id, "migo_105")

        details = get_history_details_by_id(history_id)
        inv = details.get("invoice_data") or {}
        send_migo_105_notification(
            history_id=history_id,
            invoice_number=inv.get("invoice_number"),
            migo_105_doc=migo_105_doc
        )
        logger.info(f"MIGO 105 complete — history_id={history_id} doc={migo_105_doc}")
    else:
        update_migo_105_rf_result(history_id, status="failed", error_message=result.get("error"))
    return result


def _process_miro(history_id: int, payload: dict) -> dict:
    result = execute_miro_sap(payload)
    if result.get("success"):
        fi_doc = result.get("fi_doc_number", "")
        update_miro_rf_result(history_id, status="success")
        update_history_step(history_id, "miro")
        details = get_history_details_by_id(history_id)
        inv = details.get("invoice_data") or {}
        send_miro_completion_notification(
            history_id=history_id,
            invoice_number=inv.get("invoice_number"),
            po_number=inv.get("po_number"),
            fi_doc_number=fi_doc
        )
        logger.info(f"MIRO complete — history_id={history_id} FI_DOC={fi_doc}")
    else:
        update_miro_rf_result(history_id, status="failed", error_message=result.get("error"))
    return result


STEP_HANDLERS = {
    "gate_in":       _process_gate_in,
    "po_fetch":      _process_po_fetch,
    "po_list_fetch": _process_po_list_fetch,
    "migo_103":      _process_migo_103,
    "migo_105":      _process_migo_105,
    "miro":          _process_miro,
}


def _worker_loop() -> None:
    logger.info("RF Queue Worker started.")
    reset_stuck_running_jobs(minutes=0)
    last_stuck_check = datetime.now()

    while True:
        try:
            now = datetime.now()
            if (now - last_stuck_check).total_seconds() > (STUCK_JOB_TIMEOUT_MINUTES * 60):
                reset_stuck_running_jobs(minutes=STUCK_JOB_TIMEOUT_MINUTES)
                last_stuck_check = now

            job = claim_next_pending_job()
            if not job:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            job_id     = job["id"]
            history_id = job["history_id"]
            step       = job["step"]
            payload    = job["payload"]

            logger.info(f"Worker processing job_id={job_id} history_id={history_id} step={step}")

            handler = STEP_HANDLERS.get(step)
            if not handler:
                logger.error(f"No handler for step '{step}' — job_id={job_id}")
                complete_rf_job(job_id, False, {"error": f"Unknown step: {step}"})
                continue

            result = handler(history_id, payload)
            complete_rf_job(job_id, result.get("success", False), result)

        except Exception as e:
            logger.error(f"Unexpected error in RF worker loop: {e}", exc_info=True)
            time.sleep(POLL_INTERVAL_SECONDS)



def start_worker() -> threading.Thread:
    thread = threading.Thread(target=_worker_loop, daemon=True, name="RFQueueWorker")
    thread.start()
    logger.info("RF Queue Worker thread started.")
    return thread