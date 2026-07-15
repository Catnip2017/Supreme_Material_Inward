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
from database.db_operations import update_history_step, get_history_details_by_id, set_dms_status
from database.gatein_operations import update_gatein_rf_result, get_gatein_entry
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
    execute_update_gatein_po_sap,
)
from services.doc_consolidator import consolidate_documents
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
        inv     = details.get("invoice_data") or {}
        eway    = details.get("ewaybill_data") or {}

        send_gate_in_notification(
            gate_in_number=gin,
            history_id=history_id,
            invoice_number=inv.get("invoice_number"),
            po_number=inv.get("po_number")
        )
        logger.info(f"Gate In complete — history_id={history_id} GIN={gin}")

        # Determine po_flow_type — set in DB by app.py save_gatein before enqueue
        history_rec  = details.get("history") or {}
        po_flow_type = (history_rec.get("po_flow_type") or "truck_with_po").strip()

        # Only enqueue po_fetch for flows that already have a PO number
        if po_flow_type in ("truck_with_po", "hand_with_po"):
            gatein_entry = get_gatein_entry(history_id) or {}
            po_number = (
                gatein_entry.get("purchase_order") or
                payload.get("purchaseOrder")       or
                inv.get("po_number")               or
                eway.get("po_number")              or
                ""
            )
            if po_number:
                po_job_id = enqueue_rf_job(
                    history_id, "po_fetch",
                    {"po_number": po_number, "history_id": history_id}
                )
                if po_job_id:
                    logger.info(
                        f"PO fetch enqueued — history_id={history_id} "
                        f"job_id={po_job_id} po={po_number}"
                    )
                else:
                    logger.warning(
                        f"PO fetch already queued for history_id={history_id}"
                    )
            else:
                logger.warning(
                    f"No PO number found for history_id={history_id} "
                    f"po_flow_type={po_flow_type}"
                )
        else:
            # without_po flows: PO will be fetched manually from MIGO 103 screen
            logger.info(
                f"Gate In done — po_flow_type={po_flow_type}, "
                f"skipping auto po_fetch for history_id={history_id}"
            )
    else:
        update_gatein_rf_result(
            history_id, "", status="failed",
            error_message=result.get("error")
        )
        # ── Notify admin that Gate In needs manual check ──
        from database.notifications_operations import create_notification
        create_notification(
            history_id=history_id,
            title="Gate In Failed — Manual Check Required",
            message=result.get("error", "Gate In did not capture a GIN from SAP."),
            notification_type="ocr_failed",
            role_target="gate_in"
        )
    return result

# Fix:
def _process_po_fetch(history_id: int, payload: dict) -> dict:
    result = execute_po_fetch_sap(payload)
    if result.get("success"):
        po_items = result.get("po_items", [])
        save_po_line_items(history_id, po_items)
        logger.info(
            f"PO fetch complete — history_id={history_id} "
            f"{len(po_items)} line(s)."
        )
        if not po_items:
            # SAP returned no lines — log it clearly so user knows
            logger.warning(
                f"PO fetch returned 0 items for history_id={history_id} "
                f"po={payload.get('po_number')} — PO may have no open lines."
            )
    else:
        logger.warning(
            f"PO fetch failed for history_id={history_id}: "
            f"{result.get('error')}"
        )
    # Return actual success/failure so complete_rf_job records it correctly
    return result

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

        details    = get_history_details_by_id(history_id)
        inv        = details.get("invoice_data") or {}
        history_rec  = details.get("history") or {}
        po_flow_type = (history_rec.get("po_flow_type") or "truck_with_po").strip()

        send_migo_103_notification(
            material_doc_number=mat_doc,
            history_id=history_id,
            invoice_number=inv.get("invoice_number")
        )
        logger.info(f"MIGO 103 complete — history_id={history_id} MatDoc={mat_doc}")

        # ── PDF Consolidation ────────────────────────────────────────────
        try:
            consolidated_path = consolidate_documents(history_id, details)
            if consolidated_path:
                set_dms_status(history_id, "pending", consolidated_path)
                logger.info(
                    f"PDF consolidated for history_id={history_id}: {consolidated_path}"
                )
            else:
                logger.warning(
                    f"PDF consolidation returned None for history_id={history_id}"
                )
        except Exception as exc:
            logger.error(
                f"PDF consolidation error for history_id={history_id}: {exc}",
                exc_info=True
            )

        # ── Without-PO flows: update the SAP Gate In entry with the PO ──
        if "without_po" in po_flow_type:
            gatein_entry = get_gatein_entry(history_id) or {}
            gate_in_number = gatein_entry.get("gate_in_number", "")
            po_number = (
                payload.get("purchaseOrderNumber") or
                payload.get("po_number")           or
                history_rec.get("po_number")       or
                inv.get("po_number")               or
                ""
            )
            if gate_in_number and po_number:
                ug_job_id = enqueue_rf_job(
                    history_id, "update_gatein_po",
                    {
                        "gate_in_number": gate_in_number,
                        "po_number":      po_number,
                        "history_id":     history_id,
                    }
                )
                if ug_job_id:
                    logger.info(
                        f"update_gatein_po enqueued — history_id={history_id} "
                        f"GIN={gate_in_number} po={po_number}"
                    )
            else:
                logger.warning(
                    f"update_gatein_po NOT enqueued — history_id={history_id} "
                    f"gate_in_number={gate_in_number!r} po_number={po_number!r}"
                )
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

    mat_doc = (
        payload.get("material_doc_number_override") or  # user edited
        migo_entry.get("material_doc_number", "") or    # from DB
        ""
    )
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
        update_history_step(history_id, "migo_105", generated_number=migo_105_doc or None)

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
        update_history_step(history_id, "miro", generated_number=fi_doc or None)
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



def _process_update_gatein_po(history_id: int, payload: dict) -> dict:
    """
    Update the SAP Gate In entry with the fetched PO number.
    Called after MIGO 103 for without_po flows.
    On success, also writes po_number back to history.po_number.
    """
    result = execute_update_gatein_po_sap(payload)
    if result.get("success"):
        po_number      = payload.get("po_number", "")
        gate_in_number = payload.get("gate_in_number", "")
        # Persist the resolved PO number into history for display / downstream steps
        try:
            from database.connection import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE history SET po_number = %s, updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = %s AND (po_number IS NULL OR po_number = '' OR po_number = 'NA')",
                        (po_number, history_id)
                    )
                conn.commit()
            logger.info(
                f"update_gatein_po done — history_id={history_id} "
                f"GIN={gate_in_number} po={po_number}"
            )
        except Exception as e:
            logger.error(
                f"update_gatein_po: failed to update history.po_number "
                f"for history_id={history_id}: {e}",
                exc_info=True
            )
    else:
        logger.warning(
            f"update_gatein_po did not succeed for history_id={history_id}: "
            f"{result.get('error')}"
        )
    return result


STEP_HANDLERS = {
    "gate_in":            _process_gate_in,
    "po_fetch":           _process_po_fetch,
    "po_list_fetch":      _process_po_list_fetch,
    "migo_103":           _process_migo_103,
    "migo_105":           _process_migo_105,
    "miro":               _process_miro,
    "update_gatein_po":   _process_update_gatein_po,
}


def _worker_loop() -> None:
    logger.info("RF Queue Worker started.")
    reset_stuck_running_jobs(minutes=STUCK_JOB_TIMEOUT_MINUTES)
    logger.warning(
    "Worker started — resetting any jobs stuck in 'running' "
    f"longer than {STUCK_JOB_TIMEOUT_MINUTES} minutes."
)
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

            try:
                result = handler(history_id, payload)
                success = result.get("success", False)
            except Exception as e:
                logger.error(
                    f"Handler crashed for job_id={job_id} step={step}: {e}",
                    exc_info=True
                )
                result = {"success": False, "error": str(e)}
                success = False
            finally:
                complete_rf_job(job_id, success, result)

        except Exception as e:
            logger.error(f"Unexpected error in RF worker loop: {e}", exc_info=True)
            time.sleep(POLL_INTERVAL_SECONDS)



def start_worker() -> threading.Thread:
    thread = threading.Thread(target=_worker_loop, daemon=True, name="RFQueueWorker")
    thread.start()
    logger.info("RF Queue Worker thread started.")
    return thread