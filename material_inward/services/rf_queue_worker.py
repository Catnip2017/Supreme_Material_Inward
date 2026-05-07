"""
services/rf_queue_worker.py — Background RF queue worker.

Runs as a daemon thread inside Flask.
Polls rf_queue every 5 seconds, picks the next pending job,
executes the RF script, stores the result.

Only ONE job runs at a time across all users — this prevents
two RF scripts from fighting over the spl_rpa SAP session.
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
from database.db_operations import update_history_step
from database.gatein_operations import update_gatein_rf_result
from database.migo_operations import (
    update_migo_103_rf_result,
    update_migo_105_rf_result,
    upsert_migo_entry,
    get_migo_entry        
)
from database.miro_operations import update_miro_rf_result
from services.rf_runner import (
    execute_gate_in_sap,
    execute_migo_103_sap,
    execute_migo_105_sap,
    execute_miro_sap,
    execute_po_fetch_sap,
    execute_po_list_fetch_sap

)
from services.mail_service import (
    send_gate_in_notification,
    send_migo_103_notification,
    send_migo_105_notification, 
    send_miro_completion_notification
)
from database.db_operations import get_history_details_by_id
from config.logger import get_logger

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 5
STUCK_JOB_TIMEOUT_MINUTES = 15


# def _process_gate_in(history_id: int, payload: dict) -> dict:
#     result = execute_gate_in_sap(payload)
#     if result.get("success"):
#         gin = result["gate_in_number"]
#         update_gatein_rf_result(history_id, gin, status="success")
#         update_history_step(history_id, "gate_in", generated_number=gin)
#         upsert_migo_entry(history_id, {"migoHeaderText": gin})

#         details = get_history_details_by_id(history_id)
#         inv = details.get("invoice_data") or {}
#         send_gate_in_notification(
#             gate_in_number=gin,
#             history_id=history_id,
#             invoice_number=inv.get("invoice_number"),
#             po_number=inv.get("po_number")
#         )
#         logger.info(f"Gate In complete — history_id={history_id} GIN={gin}")
#     else:
#         update_gatein_rf_result(
#             history_id, "", status="failed",
#             error_message=result.get("error")
#         )
#     return result

def _process_gate_in(history_id: int, payload: dict) -> dict:
    result = execute_gate_in_sap(payload)
    if result.get("success"):
        # gin = result["gate_in_number"]
        # update_gatein_rf_result(history_id, gin, status="success") //// use in Production
        gin = result.get("gate_in_number")
        if not gin:
            gin = f"GIN-DUMMY-{history_id}"  # REMOVE AT DEPLOYMENT
            logger.warning(f"GIN not captured — using dummy: {gin}")
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
 
        # Immediately enqueue PO fetch using the purchase_order saved in Gate In form.
        # This runs as the very next job in the queue (no user action needed).
        gatein_entry = details.get("gatein_data") or {}
        po_number = (
            gatein_entry.get("purchase_order") or
            payload.get("purchaseOrder") or
            inv.get("po_number") or ""
        )
        if po_number:
            po_job_id = enqueue_rf_job(
                history_id,
                "po_fetch",
                {"po_number": po_number, "history_id": history_id}
            )
            if po_job_id:
                logger.info(
                    f"PO fetch enqueued — history_id={history_id} "
                    f"PO={po_number} job_id={po_job_id}"
                )
            else:
                logger.warning(
                    f"PO fetch already queued for history_id={history_id} — skipping duplicate."
                )
        else:
            logger.warning(
                f"Gate In complete but no PO number found for history_id={history_id} "
                f"— PO fetch skipped."
            )
    else:
        update_gatein_rf_result(
            history_id, "", status="failed",
            error_message=result.get("error")
        )
    return result
 
def _process_po_fetch(history_id: int, payload: dict) -> dict:
    """
    Fetch PO line items from SAP ME23N and store in DB.
    Triggered automatically after Gate In succeeds.
    Non-critical — failure is logged but does NOT block MIGO.
    """
    result = execute_po_fetch_sap(payload)
    if result.get("success"):
        po_items = result.get("po_items", [])
        saved = save_po_line_items(history_id, po_items)
        if saved:
            logger.info(
                f"PO fetch complete — history_id={history_id} "
                f"{len(po_items)} line(s) saved."
            )
        else:
            logger.error(
                f"PO fetch ran but DB save failed — history_id={history_id}"
            )
    else:
        logger.warning(
            f"PO fetch failed for history_id={history_id}: {result.get('error')} "
            f"— MIGO will show empty PO table. User can still proceed."
        )
    # Always return success=True so queue doesn't block on PO fetch failure.
    # PO fetch is a verification aid, not a blocking step.
    return {"success": True, "error": result.get("error")}

def _process_po_list_fetch(history_id: int, payload: dict) -> dict:
    """
    Fetch list of pending POs for a vendor from SAP ME2N.
    Result is stored in rf_queue.result — frontend polls and reads it.
    Non-blocking — failure just returns empty list to frontend.
    """
    result = execute_po_list_fetch_sap(payload)
    if result.get("success"):
        logger.info(
            f"PO list fetch complete — history_id={history_id} "
            f"{len(result.get('po_list', []))} PO(s)."
        )
    else:
        logger.warning(
            f"PO list fetch failed — history_id={history_id}: {result.get('error')}"
        )
    # Always mark as success so queue doesn't stall
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
        update_migo_103_rf_result(
            history_id, "", status="failed",
            error_message=result.get("error")
        )
    return result


# def _process_migo_105(history_id: int, payload: dict) -> dict:
#     result = execute_migo_105_sap(payload)
#     if result.get("success"):
#         update_migo_105_rf_result(history_id, status="success")
#         update_history_step(history_id, "migo_105")
#         logger.info(f"MIGO 105 complete — history_id={history_id}")
#     else:
#         update_migo_105_rf_result(
#             history_id, status="failed",
#             error_message=result.get("error")
#         )
#     return result

def _process_migo_105(history_id: int, payload: dict) -> dict:
    # Fetch mat doc number saved by MIGO 103
    migo_entry = get_migo_entry(history_id)
    if not migo_entry:
        logger.error(f"MIGO 105 — no migo entry found for history_id={history_id}")
        update_migo_105_rf_result(history_id, status="failed", error_message="No MIGO entry found")
        return {"success": False, "error": "No MIGO entry found"}

    mat_doc = migo_entry.get("material_doc_number", "")
    if not mat_doc:
        logger.error(f"MIGO 105 — material_doc_number is empty for history_id={history_id}")
        update_migo_105_rf_result(history_id, status="failed", error_message="No material doc number from MIGO 103")
        return {"success": False, "error": "No material doc number from MIGO 103"}
    
    #    # ADD THESE TWO LINES:
    # items_data = migo_entry.get("items_data") or []
    # payload["items_data"] = items_data

    # # Inject into payload
    payload["material_doc_number"] = mat_doc
    logger.info(f"MIGO 105 — using mat_doc={mat_doc} for history_id={history_id}")

    result = execute_migo_105_sap(payload)
    if result.get("success"):
        update_migo_105_rf_result(history_id, status="success")
        update_history_step(history_id, "migo_105")
         # Send notification
        details = get_history_details_by_id(history_id)
        inv = details.get("invoice_data") or {}
        send_migo_105_notification(
            history_id=history_id,
            invoice_number=inv.get("invoice_number")
        )
        logger.info(f"MIGO 105 complete — history_id={history_id}")
    else:
        update_migo_105_rf_result(
            history_id, status="failed",
            error_message=result.get("error")
        )
    return result


def _process_miro(history_id: int, payload: dict) -> dict:
    result = execute_miro_sap(payload)
    if result.get("success"):
        update_miro_rf_result(history_id, status="success")
        update_history_step(history_id, "miro")

        details = get_history_details_by_id(history_id)
        inv = details.get("invoice_data") or {}
        send_miro_completion_notification(
            history_id=history_id,
            invoice_number=inv.get("invoice_number"),
            po_number=inv.get("po_number")
        )
        logger.info(f"MIRO complete — history_id={history_id}")
    else:
        update_miro_rf_result(
            history_id, status="failed",
            error_message=result.get("error")
        )
    return result


STEP_HANDLERS = {
    "gate_in":  _process_gate_in,
    "po_fetch": _process_po_fetch,
    "po_list_fetch":  _process_po_list_fetch,
    "migo_103": _process_migo_103,
    "migo_105": _process_migo_105,
    "miro":     _process_miro,
}


def _worker_loop() -> None:
    logger.info("RF Queue Worker started.")
    # Reset any jobs stuck from previous session immediately on startup
    reset_stuck_running_jobs(minutes=0)  # 0 = reset all running jobs
    last_stuck_check = datetime.now()
    ...

    while True:
        try:
            # Periodically reset stuck jobs
            now = datetime.now()
            if (now - last_stuck_check).total_seconds() > (STUCK_JOB_TIMEOUT_MINUTES * 60):
                reset_stuck_running_jobs(minutes=STUCK_JOB_TIMEOUT_MINUTES)
                last_stuck_check = now

            # Try to claim next pending job
            job = claim_next_pending_job()
            if not job:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            job_id    = job["id"]
            history_id = job["history_id"]
            step      = job["step"]
            payload   = job["payload"]

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
    """
    Start the RF queue worker as a daemon background thread.
    Call this once at Flask startup.
    """
    thread = threading.Thread(target=_worker_loop, daemon=True, name="RFQueueWorker")
    thread.start()
    logger.info("RF Queue Worker thread started.")
    return thread
