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
from services.credential_cache import (
    get_job_credential, clear_job_credential
)
from database.db_operations import update_history_step, get_history_details_by_id, set_dms_status
from services.doc_consolidator import consolidate_documents, write_staging_sidecar
from database.gatein_operations import update_gatein_rf_result, get_gatein_entry
from database.pending_po_operations import upsert_pending_po_update, mark_pending_po_resolved
from database.notifications_operations import create_notification
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

        update_gatein_rf_result(
            history_id, gin, status="success",
            submitted_by=payload.get("_submitted_by_username")
        )
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

        # v16: DMS staging moved here (post-Gate-In) from immediately-
        # after-OCR (v14/folder_watcher.py) -- per updated client decision,
        # so the consolidated filename can include the vendor code, which
        # isn't known/resolved until Gate In (see gate_in.html's vendor
        # lookup/verify flow against supplier_master -- the vendorName
        # field holds the resolved SAP vendor code once picked from the
        # type-ahead, not just free text). Naming:
        # {invoice_number}_{vendor_code}_{DD_MM_YY}.pdf.
        #
        # invoice_number here is OCR's extracted value (invoice_data.
        # invoice_number), not folder_watcher.py's filename-derived
        # group_key it preferred for the raw-file rename -- that value
        # never left folder_watcher.py's local scope and was never
        # persisted anywhere, so it isn't retrievable here. Using OCR's
        # value instead is safe at THIS point in the flow specifically
        # because Gate In cannot run until Compliance has approved the
        # record (see app.py._check_step_allowed), so a human has already
        # reviewed the extracted invoice_number by now -- unlike
        # immediately after OCR, when nobody had looked at it yet.
        #
        # Deliberately best-effort, same as the v14 version was: a
        # failure here does not affect Gate In's own success/failure or
        # anything downstream, it's logged for manual follow-up.
        try:
            def _safe_for_filename(value: str) -> str:
                value = (value or "").strip()
                cleaned = "".join(c for c in value if c.isalnum() or c in "-_")
                return cleaned or "NA"

            date_stamp      = datetime.now().strftime("%d_%m_%y")
            invoice_no_part = _safe_for_filename(inv.get("invoice_number"))
            vendor_code_part = _safe_for_filename(payload.get("vendorName"))
            # FIX: prefix with history_id (guaranteed unique) so two
            # invoices that sanitize to the same string (e.g. "INV/001" and
            # "INV-001" both stripping to "INV001") can never collide and
            # silently overwrite each other's consolidated PDF on disk. The
            # h{id} prefix is stripped back off before it's ever shown
            # inside Contentverse or written to the DMS links Excel --
            # see dms_upload.robot's Generate And Save Document Link --
            # so the client-visible document name is still exactly
            # invoice_vendorcode_date, as requested. Only the physical
            # staging filename (and DB's consolidated_doc_path, and the
            # Excel row used to match it back to this history_id) carries
            # the prefix.
            output_filename = f"h{history_id}_{invoice_no_part}_{vendor_code_part}_{date_stamp}.pdf"

            lr_data = details.get("lr_data") or {}
            consolidated_path = consolidate_documents(
                history_id,
                {"invoice_data": inv, "ewaybill_data": eway, "lr_data": lr_data},
                output_filename=output_filename
            )
            if consolidated_path:
                write_staging_sidecar(
                    history_id, consolidated_path,
                    invoice_number=inv.get("invoice_number", ""),
                    po_number=inv.get("po_number", ""),
                )
                set_dms_status(history_id, "staged", consolidated_path)
                logger.info(
                    f"DMS staged after Gate In — history_id={history_id}: {consolidated_path}"
                )
            else:
                logger.warning(
                    f"DMS consolidation returned None for history_id={history_id} — not staged"
                )
        except Exception as exc:
            logger.error(
                f"DMS consolidation error for history_id={history_id}: {exc}",
                exc_info=True
            )

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
                # v16: po_fetch always runs under the shared spl_rpa/.env
                # SAP login regardless of who triggered the parent Gate In
                # (read-only PO lookup, not an attributable posting) -- no
                # credential propagation here, by explicit client decision.
                # See config/.env comments and execute_po_fetch_sap().
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
            error_message=result.get("error"),
            submitted_by=payload.get("_submitted_by_username")
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
    # v17: SAP confirmed posting order between zgatein_update and MIGO 103
    # does not matter, so this no longer runs zgatein_update inline/
    # synchronously before MIGO 103 (v16's design). Instead, for a
    # without_po record, capturing a real PO number here just logs a
    # pending_po_updates row targeted at whoever originally submitted this
    # Gate In (gate_in_entries.submitted_by) -- they trigger the actual
    # zgatein_update job themselves, later, under their own live session
    # (see app.py's /api/pending_po_updates/<id>/run), so it's never
    # attributed to the MIGO 103 submitter. MIGO 103 itself always
    # proceeds immediately regardless -- see execute_migo_103_sap below --
    # its own PO_NUMBER field comes straight from this payload, not from
    # whether the Gate In record has been backfilled yet.
    details      = get_history_details_by_id(history_id)
    inv          = details.get("invoice_data") or {}
    history_rec  = details.get("history") or {}
    po_flow_type = (history_rec.get("po_flow_type") or "truck_with_po").strip()

    if "without_po" in po_flow_type:
        gatein_entry   = get_gatein_entry(history_id) or {}
        gate_in_number = gatein_entry.get("gate_in_number", "")
        po_number = (
            payload.get("purchaseOrder")       or  # actual key sent by migo_103.html's submit payload
            payload.get("migoPoNumber")        or  # same field, sent twice under this alias -- see _doSubmitMigo103()
            payload.get("purchaseOrderNumber") or  # kept as a fallback in case any other caller uses this name
            payload.get("po_number")           or
            history_rec.get("po_number")       or
            inv.get("po_number")               or
            ""
        )
        if gate_in_number and po_number:
            requested_by    = payload.get("_submitted_by_username")
            target_username = gatein_entry.get("submitted_by")
            try:
                upsert_pending_po_update(
                    history_id, po_number, gate_in_number,
                    requested_by, target_username
                )
                create_notification(
                    history_id=history_id,
                    title="PO Number Ready for Gate In Update",
                    message=(
                        f"MIGO 103 captured PO {po_number} for this record -- "
                        f"the Gate In entry (GIN {gate_in_number}) still needs "
                        f"it backfilled. Update it from the Pending PO Updates "
                        f"panel on the History page."
                    ),
                    notification_type="po_update_pending",
                    user_target=target_username,
                    # Fallback broadcast to any gate_in-role user if we
                    # don't know who specifically submitted this Gate In
                    # (record predates submitted_by tracking).
                    role_target=None if target_username else "gate_in",
                )
                logger.info(
                    f"Pending PO update logged — history_id={history_id} "
                    f"GIN={gate_in_number} po={po_number} target={target_username!r}"
                )
            except Exception as e:
                # Best-effort -- never let this block MIGO 103's own posting.
                logger.error(
                    f"Failed to log pending PO update for history_id={history_id}: {e}",
                    exc_info=True
                )
        else:
            logger.warning(
                f"Pending PO update NOT logged — history_id={history_id} "
                f"gate_in_number={gate_in_number!r} po_number={po_number!r}"
            )

    result = execute_migo_103_sap(payload)
    if result.get("success"):
        mat_doc = result["material_doc_number"]
        update_migo_103_rf_result(history_id, mat_doc, status="success")
        update_history_step(history_id, "migo_103", generated_number=mat_doc)

        send_migo_103_notification(
            material_doc_number=mat_doc,
            history_id=history_id,
            invoice_number=inv.get("invoice_number")
        )
        logger.info(f"MIGO 103 complete — history_id={history_id} MatDoc={mat_doc}")

        # PDF consolidation + DMS staging never happens here. v14 ran it
        # right after OCR (folder_watcher.py._process_batch()); v16 moved
        # it again, to right after Gate In posts successfully (see
        # _process_gate_in() above), so the consolidated filename can
        # include the vendor code -- not known until Gate In. See
        # doc_consolidator.py's docstring for the full history. Left
        # deliberately NOT re-run here even as a backstop, to avoid
        # double-consolidating (and overwriting) a file dms_upload.robot
        # may have already picked up by the time MIGO 103 completes.
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

    v17: no longer called synchronously from _process_migo_103. Enqueued
    as its own standalone job by app.py's /api/pending_po_updates/<id>/run
    route, triggered by the Gate In record's original submitter clicking
    "Update PO" on the History page's Pending PO Updates panel -- so this
    now runs under THEIR live session credential (via the normal
    _enqueue_sap_job/credential_cache path), not the MIGO 103 submitter's.

    On success, also writes po_number back to history.po_number and marks
    the corresponding pending_po_updates row resolved either way.
    """
    result = execute_update_gatein_po_sap(payload)
    po_number      = payload.get("po_number", "")
    gate_in_number = payload.get("gate_in_number", "")
    resolved_by    = payload.get("_submitted_by_username")

    if result.get("success"):
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
        mark_pending_po_resolved(history_id, True, resolved_by=resolved_by)
    else:
        logger.warning(
            f"update_gatein_po did not succeed for history_id={history_id}: "
            f"{result.get('error')}"
        )
        mark_pending_po_resolved(
            history_id, False, resolved_by=resolved_by,
            error_message=result.get("error")
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

            # v16: attach this job's per-user SAP credential (LDAP users
            # only -- see credential_cache.py + app.py's _enqueue_sap_job).
            # payload["_submitted_by_auth_type"] == "ldap" is written into
            # rf_queue.payload at enqueue time (not sensitive, just a
            # marker) and survives an app restart even though the actual
            # credential never does -- that's what lets us tell "local job,
            # never had one, fine" apart from "LDAP job that lost its
            # credential to a restart, must NOT fall back to spl_rpa."
            cred = get_job_credential(job_id)
            if cred:
                payload["_sap_username"], payload["_sap_password"] = cred
            elif payload.get("_submitted_by_auth_type") == "ldap":
                error_msg = (
                    "SAP credential unavailable (the app restarted while this "
                    "job was queued). Please resubmit — no fallback to the "
                    "shared account is used for LDAP-authenticated jobs."
                )
                logger.error(f"job_id={job_id} step={step}: {error_msg}")
                complete_rf_job(job_id, False, {"error": error_msg})
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
                # v16: this job's credential (if any) has done its work --
                # never let it linger in memory past this point.
                clear_job_credential(job_id)

        except Exception as e:
            logger.error(f"Unexpected error in RF worker loop: {e}", exc_info=True)
            time.sleep(POLL_INTERVAL_SECONDS)



def start_worker() -> threading.Thread:
    thread = threading.Thread(target=_worker_loop, daemon=True, name="RFQueueWorker")
    thread.start()
    logger.info("RF Queue Worker thread started.")
    return thread