"""
app.py — Material Inward Process — Final Production Application.

v4 changes:
- Removed: lock_record / unlock_record routes (no more record locking)
- Added: /api/save_extracted_invoice, /api/save_extracted_eway, /api/save_extracted_lr
- Added: /api/approve, /api/hold
- Added: /api/rerun_ocr/<id>
- Added: /api/notifications/unread, /api/notifications/<id>/mark_read
- Added: /api/migo_matched_pairs/<id>
- Email-step gating for tabs
- 7-day notification cleanup, 60-day record cleanup (existing)
"""

import os
import shutil
import json
import threading
import time
import re
import hashlib
import zipfile
import io
from datetime import datetime, date
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, send_file, Response
)

from config.config import config
from config.logger import get_logger
from database.connection import init_pool, get_connection
from database.db_operations import (
    create_history_record, get_history_by_id,
    get_history_details_by_id, get_all_history,
    update_history_step,
    save_invoice_to_db, save_ewaybill_to_db, save_lr_to_db,
    get_history_search, get_today_counts,
    set_approval_status, set_hold_status,
    set_ocr_status, increment_ocr_retry, get_ocr_failed_path,
    set_dms_status, set_po_flow_type,
    get_dms_document_link
)
from database.scenario_operations import (
    get_history_extras, set_goods_delivery_mode, set_ewb_exemption_reasons,
    delivery_mode_remark_text, ewb_exemption_remark_text, append_remark,
    DELIVERY_MODE_LABELS, EWB_EXEMPTION_LABELS, add_history_extra,
    set_category, CATEGORY_LABELS, CATEGORY_TO_GATEIN_CODE,
    delete_history_extras_by_doctype
)
from database.vehicle_master_operations import get_drivers_by_truck
from database.supplier_operations import search_suppliers, get_supplier_by_code
from database.gatein_operations import (
    upsert_gatein_entry, get_gatein_entry, map_ocr_to_gatein
)
from database.migo_operations import (
    upsert_migo_entry, save_migo_105_fields,
    get_migo_entry, map_ocr_to_migo,
    update_migo_105_items_with_batches,
    shape_invoice_items_for_migo
)
from database.miro_operations import (
    upsert_miro_entry, get_miro_entry, map_ocr_to_miro
)
from database.po_operations import get_po_line_items
from database.rf_queue_operations import enqueue_rf_job, get_job_status
from database.user_operations import (
    verify_user, get_all_users, add_user, update_user, delete_user
)
from database.storage_location_operations import (
    get_all_storage_locations, add_storage_location, update_storage_location
)
from database.notifications_operations import (
    get_unread_for_user, mark_as_read, mark_all_as_read_for_user,
    cleanup_old_notifications, create_notification
)
from database.admin_operations import (
    find_records_for_admin, get_admin_action_log,
    delete_history_record, reset_gate_in_step,
    reset_migo_103_step, reset_migo_105_step, reset_miro_step,
    revert_extracted_data_approval, revert_gst_approval, revert_approval
)
from services.extract import process_document
from services.rf_queue_worker import start_worker
from services.mail_service import send_approval_notification
from services.credential_cache import (
    store_session_credential, get_session_credential,
    clear_session_credential, touch_session_credential,
    attach_job_credential
)

logger = get_logger(__name__)

# ============================================================
# APP SETUP
# ============================================================

app = Flask(__name__)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_FILE_SIZE_BYTES
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True

for folder in [config.UPLOAD_FOLDER, config.UPLOAD_PROCESSED_FOLDER, config.UPLOAD_FAILED_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Thumbnail cache — separate sibling folder, not scanned by _find_file() since
# that only checks UPLOAD_FOLDER/UPLOAD_PROCESSED_FOLDER/UPLOAD_FAILED_FOLDER
# directly. Rendered previews are cached here keyed by filename+mtime so a
# repeat page view doesn't re-rasterize the PDF every time.
THUMBNAIL_CACHE_FOLDER = os.path.join(config.UPLOAD_FOLDER, "_thumb_cache")
os.makedirs(THUMBNAIL_CACHE_FOLDER, exist_ok=True)

# ============================================================
# STARTUP
# ============================================================

with app.app_context():
    try:
        init_pool()
        logger.info("DB pool ready.")
    except Exception as e:
        logger.critical(f"DB pool failed: {e}")

_rf_worker = start_worker()  # noqa: F841

_intake_method = config.INTAKE_METHOD
if _intake_method == "folder":
    from services.folder_watcher import start_folder_watcher
    start_folder_watcher()
    logger.info("Intake: Folder watcher started.")
else:
    logger.info("Intake: Mail poller mode — run mail_poller.py via Task Scheduler.")


# ============================================================
# CLEANUP TASKS
# ============================================================

def _clear_old_records():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM history WHERE created_at < NOW() - INTERVAL '2 months'")
        logger.info("Old records cleared (>2 months).")
    except Exception as e:
        logger.error(f"Record cleanup error: {e}")


def _cleanup_loop():
    while True:
        time.sleep(86400)  # daily
        try:
            cleanup_old_notifications(days=7)

            # Only delete records that are fully DONE (miro=1)
            # and older than 60 days — matches folder watcher ORPHAN_DAYS
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            DELETE FROM history
                            WHERE miro = 1
                              AND created_at < NOW() - INTERVAL '60 days'
                            """
                        )
                        deleted = cur.rowcount
                        if deleted:
                            logger.info(
                                f"Cleanup: deleted {deleted} completed "
                                f"records older than 60 days."
                            )
            except Exception as e:
                logger.error(f"Record cleanup error: {e}")

        except Exception as e:
            logger.error(f"Cleanup loop error: {e}")


# ============================================================
# HEALTH CHECK — for watchdog.py / external monitoring only.
# Deliberately public (no @login_required) -- the watchdog isn't a logged-in
# user and can't be, and this endpoint returns nothing sensitive. Must stay
# fast and cheap: a real SELECT so a hung/exhausted DB pool is caught (that's
# the actual failure mode a process-alive-but-stuck check needs to catch),
# but nothing else -- no RF queue stats, no table scans. See watchdog.py's
# docstring for the full monitoring design (network/DB check, then this,
# then a restart decision).
# ============================================================

@app.route("/health")
def health_check():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return jsonify({"status": "ok", "db": True}), 200
    except Exception as e:
        logger.error(f"/health check failed: {e}")
        return jsonify({"status": "error", "db": False, "error": str(e)}), 503


# ============================================================
# DECORATORS / HELPERS
# ============================================================

def _no_roles_assigned() -> bool:
    """
    v15: True for a verified, logged-in user who isn't SuperAdmin and has
    no step_roles at all -- e.g. a freshly-created LDAP row a SuperAdmin
    hasn't assigned anything to yet. SuperAdmin always passes (sees
    everything regardless of step_roles, same as _has_role()).
    """
    return session.get("role") != "SuperAdmin" and not (session.get("step_roles") or "").strip()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        if _no_roles_assigned():
            return redirect(url_for("no_access"))
        # v16: refresh the SAP credential idle-timeout clock on every page
        # load — no-op for local users / anyone with no cached entry.
        touch_session_credential(session.get("username"))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return jsonify({
                "success": False,
                "error": "Session expired. Please log in again.",
                "session_expired": True
            }), 401
        if _no_roles_assigned():
            return jsonify({
                "success": False,
                "error": "No roles assigned to your account yet. Contact your SuperAdmin."
            }), 403
        # v16: see login_required() above.
        touch_session_credential(session.get("username"))
        return f(*args, **kwargs)
    return decorated


def login_required_view_only(f):
    """
    v17: Same as login_required, but deliberately skips the
    _no_roles_assigned() block. Use ONLY on read-only routes an
    unassigned (no step_roles, non-SuperAdmin) LDAP user should still be
    able to reach -- today that's the history list and a record's detail
    page. This does NOT expose anything extra once there: every
    workflow tab on view_detail's index.html render is separately gated
    by can_view_* / has_role() checks that already evaluate False for an
    unassigned user, so only the Documents tab (can_view_documents=True
    for everyone, per the v14 fix) actually renders for them. Do not
    apply this decorator to any route that performs an action or that
    should stay hidden from an unassigned user -- use login_required
    for those, as before.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        touch_session_credential(session.get("username"))
        return f(*args, **kwargs)
    return decorated


def api_login_required_view_only(f):
    """API counterpart of login_required_view_only -- see that docstring.
    Use only on read-only GET endpoints an unassigned user needs (e.g.
    history search), never on an action/mutation route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return jsonify({
                "success": False,
                "error": "Session expired. Please log in again.",
                "session_expired": True
            }), 401
        touch_session_credential(session.get("username"))
        return f(*args, **kwargs)
    return decorated


def _enqueue_sap_job(history_id, step: str, payload: dict):
    """
    v16: wraps enqueue_rf_job() with per-user SAP credential attachment.

    LDAP-authenticated users' RF jobs use their own cached SAP password
    (captured at login — see /login below) instead of the shared spl_rpa
    .env account. Local/test accounts are untouched: sap_username/
    sap_password stay None for them, and rf_runner.py/the robot scripts
    fall back to .env exactly as before this change.

    If an LDAP user's cached credential has expired (60-minute idle
    timeout) or was never captured, the submission is refused up front
    with a "log in again" error rather than queued with no credential —
    there is no fallback to spl_rpa for an LDAP-submitted job, ever.

    Returns (job_id, error_response). error_response is None on success;
    when set, the caller should return it directly. job_id is None if
    enqueue_rf_job() itself declined (duplicate already queued/running).
    """
    username  = session.get("username")
    auth_type = session.get("auth_type", "local")

    # v20: Gate In (and update_gatein_po — the zgatein_update PO-backfill
    # flow, same underlying GIN entry as Gate In, just triggered later) are
    # always posted by the shared spl_rpa/.env account, regardless of the
    # submitting user's own account auth_type — client decision. Only
    # MIGO 103 / MIGO 105 / MIRO still route through an LDAP user's own
    # cached SAP credential. This mirrors the same carve-out po_fetch
    # already had (see its own call site further down) — it just never
    # went through this shared wrapper in the first place.
    FORCE_LOCAL_STEPS = {"gate_in", "update_gatein_po"}
    if step in FORCE_LOCAL_STEPS:
        auth_type = "local"

    sap_username = None
    sap_password = None
    if auth_type == "ldap":
        sap_password = get_session_credential(username)
        if not sap_password:
            return None, (jsonify({
                "success": False,
                "error": "Your session has timed out. Please log in again to continue.",
                "session_expired": True
            }), 401)
        sap_username = username
        # Not sensitive — just a marker so rf_queue_worker.py can tell
        # "local job, no credential needed" apart from "LDAP job that
        # lost its credential to an app restart" once it's persisted in
        # rf_queue.payload (which survives a restart; the credential
        # itself never does).
        payload = dict(payload or {})
        payload["_submitted_by_auth_type"] = "ldap"

    job_id = enqueue_rf_job(history_id, step, payload)
    if job_id and sap_username and sap_password:
        attach_job_credential(job_id, sap_username, sap_password)
    return job_id, None


def admin_required(f):
    """User Management + storage-location mutation routes: SuperAdmin only,
    and only if admin_edit is True (a view-only SuperAdmin cannot edit
    anything anywhere, including here)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "SuperAdmin":
            return jsonify({"success": False, "error": "SuperAdmin access required"}), 403
        if not session.get("admin_edit"):
            return jsonify({"success": False, "error": "View-only admin access — editing disabled."}), 403
        return f(*args, **kwargs)
    return decorated


def _current_user() -> str:
    return session.get("username", "unknown")


def _is_admin() -> bool:
    # Kept for template/route compatibility -- "admin" now means SuperAdmin.
    return session.get("role") == "SuperAdmin"


def _is_superadmin() -> bool:
    return session.get("role") == "SuperAdmin"


def _admin_can_edit() -> bool:
    """True only for a SuperAdmin with admin_edit=True. Regular users are
    governed by _has_role()/_require_role_edit() instead."""
    return _is_superadmin() and bool(session.get("admin_edit"))


def _current_roles() -> set:
    raw = (session.get("step_roles") or "").strip().lower()
    if not raw:
        return set()
    return {r.strip() for r in raw.split(",") if r.strip()}


def _has_role(role_name: str) -> bool:
    """View permission: SuperAdmin (any admin_edit value) can always view
    every tab. Otherwise the user needs this specific role checked."""
    if _is_superadmin():
        return True
    return role_name in _current_roles()


def _require_role_edit(role_name: str):
    """
    Action-route guard. Returns None if the current session may perform
    this action, or a (response, status) tuple to return immediately if
    not. A SuperAdmin needs admin_edit=True to act (not just view); a
    regular user needs role_name in their step_roles.
    """
    if _is_superadmin():
        if _admin_can_edit():
            return None
        return jsonify({"success": False, "error": "View-only admin access — editing disabled."}), 403
    if role_name in _current_roles():
        return None
    return jsonify({"success": False, "error": "You do not have permission to perform this action."}), 403


def _extracted_data_view_state(history: dict) -> tuple:
    """
    Returns (can_view, can_edit) for the Extracted Data tab given the
    current session's role(s) and this record's workflow progress.

    Compliance Officer / SuperAdmin: always full view + edit (subject to
    the existing approval_status lock, handled separately in the
    template).

    Downstream roles get a staggered read-only reveal, one stage at a
    time, matching the SAP process order already enforced by
    _check_step_allowed(): Gate Security sees it once GST is approved;
    Stores Officer (103) once Gate In is done; Quality/Release (105)
    once MIGO 103 is done; Accounts Payable (MIRO) once MIGO 105 is
    done. None of these roles can ever edit it -- view only.
    """
    if _is_superadmin() or "compliance" in _current_roles():
        return True, _admin_can_edit() if _is_superadmin() else True

    roles = _current_roles()
    if "gate_in" in roles and history.get("gst_check"):
        return True, False
    if "migo_103" in roles and history.get("gate_in"):
        return True, False
    if "migo_105" in roles and history.get("migo_103"):
        return True, False
    if "miro" in roles and history.get("migo_105"):
        return True, False
    return False, False


def _check_step_allowed(history: dict, step: str) -> tuple:
    # FIX: duplicate-post guard -- always enforced regardless of
    # ENABLE_STEP_LOCKS. That toggle (currently false in prod) only
    # controls whether steps must happen IN ORDER; it was never meant to
    # allow the SAME step to be posted to SAP twice. Before this, nothing
    # server-side stopped a second POST to /save_gatein, /api/run_migo_103,
    # /api/run_migo_105, or /api/run_miro for a record whose step was
    # already done -- the frontend button also never disabled itself after
    # a successful post (see the matching JS fix), so a double-click, a
    # second tab, or a direct API call could post a duplicate GRN/invoice.
    step_label = {
        "gate_in": "Gate In", "migo_103": "MIGO 103",
        "migo_105": "MIGO 105", "miro": "MIRO",
    }.get(step)
    if step_label and history.get(step):
        return False, f"{step_label} has already been posted for this record."

    step_locks = os.getenv('ENABLE_STEP_LOCKS', 'true').lower() == 'true'
    if not step_locks:
        return True, ""
    if step == "gate_in":
        if (history.get("approval_status") or "pending") != "approved":
            return False, "Documents pending verification & approval."
        if not history.get("gst_check"):
            return False, "GST verification pending — approve on the GST Approval tab first."
    elif step == "migo_103":
        if not history.get("gate_in"):
            return False, "Awaiting Gate In completion."
    elif step == "migo_105":
        if not history.get("migo_103"):
            return False, "Awaiting MIGO 103 completion."
    elif step == "miro":
        if not history.get("migo_105"):
            return False, "Awaiting MIGO 105 completion."
    return True, ""


def _validate_required_fields(data: dict, required: list) -> str | None:
    """
    Server-side mandatory-field check for the four SAP-posting routes
    (save_gatein, run_migo_103, run_migo_105, run_miro).

    Added 2026-07-25: an audit found every posting route enforced its
    mandatory fields ONLY in browser JS (validateGateIn/validateMigo103/
    validateMigo105/validateMiro in the respective templates) -- there was
    no `required` HTML attribute anywhere (two of the four forms even set
    `novalidate`) and no re-check in Flask. A normal user going through the
    UI could never submit blank, but a direct POST to any of these routes
    (curl/Postman/devtools, or a future frontend regression) could reach
    execute_*_sap() and post to live SAP with any mandatory field empty,
    with nothing server-side to stop it.

    `required` is a list of (payload_key, human_label) tuples -- human_label
    matches the on-screen field label so the error reads the same as the
    client-side validation would have shown. Returns None if every field is
    present and non-blank after stripping whitespace, else one combined
    error string listing every missing field (not just the first one hit),
    so a direct-API caller doesn't have to resubmit repeatedly to discover
    each one individually.
    """
    missing = [label for key, label in required if not str(data.get(key) or "").strip()]
    if not missing:
        return None
    return f"Missing required field(s): {', '.join(missing)}."


def _move_file(src_path: str, dest_folder: str) -> str:
    os.makedirs(dest_folder, exist_ok=True)
    filename = os.path.basename(src_path)
    dest_path = os.path.join(dest_folder, filename)
    try:
        shutil.move(src_path, dest_path)
        return dest_path
    except Exception as e:
        logger.error(f"Failed to move {src_path}: {e}")
        return src_path


def _find_file(filename: str) -> str:
    for folder in [config.UPLOAD_FOLDER, config.UPLOAD_PROCESSED_FOLDER, config.UPLOAD_FAILED_FOLDER]:
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            return path
    return ""


def _auto_populate_form_tables(history_id: int) -> None:
    try:
        details = get_history_details_by_id(history_id)
        history = details.get("history") or {}
        # Re-populate only if nothing has been posted yet (Gate In not done)
        if history.get("gate_in"):
            return
        inv  = details.get("invoice_data")
        eway = details.get("ewaybill_data")
        lr   = details.get("lr_data")
        upsert_gatein_entry(history_id, map_ocr_to_gatein(inv, eway, lr))
        upsert_migo_entry(history_id, map_ocr_to_migo(inv, eway, lr))
        upsert_miro_entry(history_id, map_ocr_to_miro(inv, eway, lr))
        logger.info(f"Form tables populated for history_id={history_id}")
    except Exception as e:
        logger.warning(f"Auto-populate failed for history_id={history_id}: {e}")


# FIX (2026-08-11): manual-upload/G-drive parity. Maps our internal doctype
# names to the filename-suffix keyword _detect_doc_type() (folder_watcher.py)
# looks for. Manually-uploaded filenames are arbitrary (e.g. "scan1.pdf") and
# don't follow the <number>_INV.pdf convention G-drive intake produces, so
# when a manual upload's OCR fails and gets moved into its failed folder, it
# must be renamed to end in _INV/_EWB/_LR -- otherwise _detect_doc_type()
# returns None and /api/rerun_ocr's retry loop silently skips it forever.
_UPLOAD_DOCTYPE_KEYWORD = {
    "invoice":  config.INVOICE_KEYWORD,
    "ewaybill": config.EWAYBILL_KEYWORD,
    "lr":       config.LR_KEYWORD,
}


def _move_failed_upload(file_path: str, history_id: int, doctype: str) -> str:
    """
    Moves a manual-upload OCR failure into its own per-record subfolder
    (uploads/failed/h{history_id}/) instead of the old single shared
    UPLOAD_FAILED_FOLDER -- the shared folder meant /api/rerun_ocr's
    os.listdir(failed_path) could pick up ANOTHER record's failed file and
    reprocess it under the wrong history_id. Also renames the file so
    _detect_doc_type() can classify it on retry (see _UPLOAD_DOCTYPE_KEYWORD
    above). Returns the folder path, for set_ocr_status's failed_path.
    """
    failed_folder = os.path.join(config.UPLOAD_FAILED_FOLDER, f"h{history_id}")
    os.makedirs(failed_folder, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(file_path))
    keyword = _UPLOAD_DOCTYPE_KEYWORD.get(doctype, doctype).upper()
    if not stem.lower().endswith(f"_{keyword.lower()}"):
        stem = f"{stem}_{keyword}"
    dest_path = os.path.join(failed_folder, f"{stem}{ext}")
    try:
        shutil.move(file_path, dest_path)
    except Exception as e:
        logger.error(f"Failed to move failed upload {file_path} -> {dest_path}: {e}")
    return failed_folder


def _dedupe_watch_folder(original_filename: str, success: bool) -> None:
    """
    ADDED (2026-08-12): best-effort call into
    folder_watcher.claim_matching_incoming_file() -- see that function's
    docstring for the full scenario. `original_filename` must be the RAW
    filename the browser sent (file.filename), never the h{history_id}_
    prefixed local copy name -- when staff pick a document straight off
    the mapped scanner drive via the browser's file picker (rather than a
    different local copy), this raw name is what's still sitting in
    folder_watcher's WATCH_FOLDER/GROUPED_FOLDER, and matching against the
    prefixed name would never find it.

    Deliberately swallows every exception -- this is filing/housekeeping
    only and must never be able to turn a successful manual upload into a
    failed API response, or vice versa.
    """
    try:
        from services.folder_watcher import claim_matching_incoming_file
        claim_matching_incoming_file(original_filename, success)
    except Exception as e:
        logger.warning(f"WATCH_FOLDER dedup skipped for {original_filename!r}: {e}")


def _attach_others_document(history_id: int, safe_name: str, original_filename: str) -> None:
    """
    ADDED (2026-08-13): "Others" is meant to be at most one file per record
    (see folder_watcher.py's own "_OTH ... at most one per group like the
    other 3" [invoice/ewaybill/lr] rule) -- unlike those 3, which upsert a
    single DB row per history_id, history_extras had no equivalent
    protection: add_history_extra() is a pure INSERT, so re-uploading
    "Others" for a record that already has one (e.g. via /process_all,
    then again later via the Documents tab's Add/Replace panel) just piled
    up a second row instead of replacing the first -- confirmed as the
    cause of a record showing 2 attached Others files after what the user
    experienced as a single upload.

    Deletes any existing 'others' row(s) for this history_id (and their
    now-orphaned physical files under UPLOAD_FOLDER) before attaching the
    new one, so there's only ever one at a time -- true "replace", not
    "add". File deletion is best-effort/non-fatal: the DB row is the
    source of truth for what's attached, a leftover unreferenced file on
    disk is harmless clutter, not a correctness problem.
    """
    old_filenames = delete_history_extras_by_doctype(history_id, "others")
    for old_filename in old_filenames:
        try:
            old_path = os.path.join(config.UPLOAD_FOLDER, old_filename)
            if os.path.isfile(old_path):
                os.remove(old_path)
        except Exception as e:
            logger.warning(f"Could not remove superseded Others file {old_filename!r}: {e}")
    add_history_extra(history_id, safe_name, original_filename, doc_type="others")


def _run_ocr_and_save(doctype: str, file_path: str, filename: str, history_id: int, original_filename: str = None) -> bool:
    original_filename = original_filename or filename
    try:
        extracted = process_document(doctype, file_path, filename)
        if not extracted:
            failed_folder = _move_failed_upload(file_path, history_id, doctype)
            set_ocr_status(history_id, "failed", failed_path=failed_folder)
            _dedupe_watch_folder(original_filename, success=False)
            return False
        extracted["filename"] = filename
        if doctype == "invoice":
            save_invoice_to_db(history_id, extracted)
            # v27 (2026-08-14, client request): GST verification is now
            # strictly on-demand -- it no longer auto-starts when invoice
            # OCR completes. It only ever starts from an explicit user
            # action (the Run/Re-run button on GST Approval, or the bulk
            # "Run Selected" action on the GST Verification admin page).
            # See api_gst_run/api_gst_bulk_run below.
        elif doctype == "ewaybill":
            save_ewaybill_to_db(history_id, extracted)
        elif doctype == "lr":
            save_lr_to_db(history_id, extracted)
        _move_file(file_path, config.UPLOAD_PROCESSED_FOLDER)
        _dedupe_watch_folder(original_filename, success=True)
        return True
    except Exception as e:
        logger.error(f"OCR error for {doctype}: {e}", exc_info=True)
        failed_folder = _move_failed_upload(file_path, history_id, doctype)
        set_ocr_status(history_id, "failed", failed_path=failed_folder)
        _dedupe_watch_folder(original_filename, success=False)
        return False


# ============================================================
# CONTEXT PROCESSOR — globals available to all templates
# ============================================================

@app.context_processor
def inject_globals():
    return {
        "config": config,
        "enabled_steps": config._ENABLED_STEPS_RAW.lower(),
        "is_step_enabled": config.is_step_enabled,
        "is_admin": _is_admin(),
        "is_superadmin": _is_superadmin(),
        "admin_can_edit": _admin_can_edit(),
        "has_role": _has_role,
        "current_role": session.get("role", ""),
        "current_username": session.get("username", ""),
        # Used by templates/tabs/_remarks_panel.html to decide whether to
        # show a role-picker before posting a comment -- only needed when
        # the signed-in user holds more than one operational role.
        "current_roles_list": sorted(_current_roles()),
        "allow_user_upload": config.ALLOW_USER_UPLOAD,
        "show_dashboard_counts": config.SHOW_DASHBOARD_COUNTS,
        "enable_inapp_notifications": config.ENABLE_INAPP_NOTIFICATIONS,
    }


# ============================================================
# AUTH ROUTES
# ============================================================

@app.route("/")
def home():
    if "username" not in session:
        return redirect(url_for("login"))
    return redirect(url_for("history_page"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = verify_user(username, password)
        if user:
            session["username"]   = user["username"]
            session["role"]       = user["role"]
            session["name"]       = user["name"]
            session["step_roles"] = user.get("step_roles", "")
            session["admin_edit"] = bool(user.get("admin_edit", True))
            # v15: decides whether a queued RF job uses this person's own
            # SAP login or the shared spl_rpa/.env fallback.
            session["auth_type"]  = user.get("auth_type", "local")
            # v16: LDAP users' own password (== their personal SAP login,
            # confirmed by client) is cached in-memory only, never in the
            # session cookie or any DB table — see credential_cache.py.
            # Local/test accounts never go through this cache at all.
            if session["auth_type"] == "ldap":
                store_session_credential(username, password)
            logger.info(f"Login: {username} ({user['role']}, auth_type={session['auth_type']})")
            return redirect(url_for("history_page"))
        logger.warning(f"Failed login: {username}")
        return render_template("login.html", error="Invalid username or password")
    return render_template("login.html")


@app.route("/logout")
def logout():
    logger.info(f"Logout: {session.get('username')}")
    # v16: drop this user's cached SAP session credential immediately —
    # a already-queued job keeps its own separately-attached copy (see
    # credential_cache.py JOB_CACHE), so logging out mid-job is safe.
    clear_session_credential(session.get("username"))
    session.clear()
    return redirect(url_for("login"))


@app.route("/no_access")
def no_access():
    """v15: shown instead of any real page for a verified user with no
    step_roles and no SuperAdmin role -- see login_required()/
    _no_roles_assigned(). Deliberately not decorated with @login_required
    itself (that would redirect right back here)."""
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template("no_access.html", username=session.get("username"))


# ============================================================
# PAGE ROUTES
# ============================================================

@app.route("/history")
@login_required_view_only
def history_page():
    try:
        history_data = get_all_history()
    except Exception as e:
        logger.error(f"History load error: {e}")
        history_data = []
    today_counts = {}
    if config.SHOW_DASHBOARD_COUNTS:
        try:
            today_counts = get_today_counts()
        except Exception as e:
            logger.error(f"Today counts error: {e}")
    return render_template(
        "history.html",
        history_data=history_data,
        today_counts=today_counts,
        username=session.get("username"),
        role=session.get("role")
    )


@app.route("/api/history_search")
@api_login_required_view_only
def api_history_search():
    search   = request.args.get("search", "").strip()
    status   = request.args.get("status", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to", "").strip()
    page     = int(request.args.get("page", 1))
    return jsonify(get_history_search(
        search=search, status=status,
        date_from=date_from, date_to=date_to,
        page=page, per_page=20
    ))


@app.route("/change_my_password", methods=["POST"])
@api_login_required
def change_my_password():
    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password", "")
    new_password     = data.get("new_password", "")
    confirm_password = data.get("confirm_password", "")

    if not all([current_password, new_password, confirm_password]):
        return jsonify({"success": False, "error": "All fields required"}), 400
    if new_password != confirm_password:
        return jsonify({"success": False, "error": "New passwords do not match"}), 400
    if len(new_password) < 6:
        return jsonify({"success": False, "error": "Password must be at least 6 characters"}), 400

    user = verify_user(session.get("username"), current_password)
    if not user:
        return jsonify({"success": False, "error": "Current password is incorrect"}), 400

    if update_user(session.get("username"), password=new_password):
        return jsonify({"success": True, "message": "Password updated successfully"})
    return jsonify({"success": False, "error": "Failed to update password"}), 500


@app.route("/view/<int:history_id>")
@login_required_view_only
def view_detail(history_id):
    try:
        details = get_history_details_by_id(history_id)
        history = details.get("history")
        if not history:
            return redirect(url_for("history_page"))

        gatein_data = get_gatein_entry(history_id) or {}
        migo_data   = get_migo_entry(history_id)   or {}
        miro_data   = get_miro_entry(history_id)   or {}

        # FIX (2026-08-13): legacy fallback for gate_in_entries rows saved
        # before the Vendor Name / Vendor Code split (schema_migration_v25) --
        # those rows have vendor_code = NULL, with vendor_name still holding
        # the bare SAP code from the old overloaded-field design (Fetch used
        # to overwrite Vendor Name itself). Same reverse-lookup pattern MIGO
        # 103 already uses (see resolved_vendor_name below) so old records
        # display a real name instead of a bare code, without a data
        # migration/backfill script. Only kicks in when vendor_code is
        # genuinely empty -- any record saved through the new split already
        # has both fields populated correctly.
        if gatein_data and not gatein_data.get("vendor_code") and gatein_data.get("vendor_name"):
            _legacy_supplier = get_supplier_by_code(gatein_data["vendor_name"]) or {}
            if _legacy_supplier:
                gatein_data["vendor_code"] = gatein_data["vendor_name"]
                gatein_data["vendor_name"] = (
                    _legacy_supplier.get("name_1") or _legacy_supplier.get("name")
                    or gatein_data["vendor_name"]
                )

        if history.get("gate_in_number") and not migo_data.get("migo_header_text"):
            migo_data["migo_header_text"] = history["gate_in_number"]
        if history.get("material_doc_number"):
            migo_data["material_doc_number"] = history["material_doc_number"]

        # v20: GIN is zero-padded in SAP (e.g. "0000038656") -- Header Text
        # should show/post the number without the leading zeros. Strip here
        # (covers both a fresh fallback to history.gate_in_number above and
        # an already-saved migo_header_text from a prior draft/posting,
        # since _process_gate_in's upsert_migo_entry stores the raw padded
        # GIN as-is) so this is correct on every view regardless of source.
        # The actual SAP posting value gets the same treatment independently
        # in services/rf_runner.py's execute_migo_103_sap, as a backstop.
        if migo_data.get("migo_header_text"):
            _raw_header_text = str(migo_data["migo_header_text"])
            migo_data["migo_header_text"] = _raw_header_text.lstrip("0") or _raw_header_text

        po_data = get_po_line_items(history_id)

        # v20, simplified 2026-08-13: MIGO 103 wants a view-only vendor NAME
        # field once Gate In has happened. This used to need a
        # supplier_master re-lookup here because gatein_data.vendor_name
        # held the resolved SAP vendor CODE, not a name (Fetch overwrote the
        # one shared field). Now that Vendor Name/Vendor Code are separate
        # columns (schema_migration_v25), gatein_data.vendor_name is always
        # already the real name by the time this runs -- either saved
        # directly as one under the new split, or resolved by the legacy
        # fallback right after get_gatein_entry() above for older rows. No
        # lookup needed any more. View-only, deliberately not fed back into
        # any SAP posting payload -- purely informational so the user isn't
        # stuck reading the OCR seller_name fallback (invoice_data.seller_name)
        # once a real, verified vendor is known from Gate In.
        resolved_vendor_name = gatein_data.get("vendor_name") or None

        # v26: Gate In's Category dropdown pre-selects from Extracted
        # Data's simpler 3-option Category (defaults to "stores" if the
        # record predates this feature/history.category is somehow NULL)
        # -- but ONLY as a default. If this Gate In already has its own
        # saved category (a draft in progress, or an already-submitted
        # one), that real value always wins -- see the template's
        # fallback logic in templates/tabs/gate_in.html.
        gatein_category_default = CATEGORY_TO_GATEIN_CODE.get(
            (history.get("category") or "stores"), "A"
        )

        # v13: files folder_watcher.py couldn't recognize as INV/EWB/LR --
        # shown under the "Extras" banner on Extracted Data (view/download
        # only, see /view_document & /download_document, doctype='extra').
        history_extras = get_history_extras(history_id)

        # E-way Bill validity check — flags (does not block) an EWB whose
        # "Valid Upto" date has already passed as of today. validity_date is
        # already normalized to YYYY-MM-DD by services/extract.py, so this is
        # a plain date-only comparison (time-of-day on the EWB is not parsed;
        # EWB validity conventionally ends at 23:59 on the stated day, so a
        # date-only check is accurate for the workflow's purposes).
        ewb_expired = False
        ewaybill_data = details.get("ewaybill_data") or {}
        ewb_validity_raw = ewaybill_data.get("validity_date")
        if ewb_validity_raw:
            try:
                validity_dt = datetime.strptime(str(ewb_validity_raw), "%Y-%m-%d").date()
                ewb_expired = date.today() > validity_dt
            except ValueError:
                logger.warning(
                    f"history_id={history_id}: could not parse ewaybill validity_date "
                    f"'{ewb_validity_raw}' for expiry check."
                )

        # Role-based tab access -- see _has_role()/_extracted_data_view_state()
        # in the helpers section above. Documents + GST Approval are the
        # Compliance Officer's exclusive tabs (or SuperAdmin); Extracted
        # Data gets a staggered read-only reveal for downstream roles as
        # the record's workflow progresses; Gate In/MIGO/MIRO tabs are
        # each gated to their own role, on top of the existing system-wide
        # is_step_enabled() toggle (unchanged).
        can_view_extracted, can_edit_extracted = _extracted_data_view_state(history)
        # FIX (v14): Documents tab is now open to any authenticated user
        # (view/download only), regardless of role or step_roles -- it used
        # to be compliance-only, which is what was actually causing
        # documents to appear to "vanish" for other roles (a permanent
        # restriction, not something that changed after approval or a
        # rendering fix). Deleting a document is unaffected -- that's still
        # gated separately via _require_role_edit("compliance") wherever
        # /delete_document and /delete_all_documents check it.
        can_view_documents = True
        # v16: Contentverse sharing link, once dms_upload.robot has
        # uploaded this record's consolidated PDF and services/
        # dms_links_import.py has pulled the link back into the DB. None
        # until then -- documents.html shows nothing extra in that case.
        dms_document_link  = get_dms_document_link(history_id)
        can_view_gst       = _has_role("compliance")
        can_view_gate_in   = config.is_step_enabled("gate_in")   and _has_role("gate_in")
        can_view_migo_103  = config.is_step_enabled("migo_103")  and _has_role("migo_103")
        can_view_migo_105  = config.is_step_enabled("migo_105")  and _has_role("migo_105")
        can_view_miro      = config.is_step_enabled("miro")      and _has_role("miro")

        # MIGO 103's "Invoice Line Items (from OCR)" table is rendered live
        # from the current invoice_data.hsn_details, not from the one-time
        # migo_entries.items_data snapshot -- that snapshot goes stale the
        # moment Gate In posts (see _auto_populate_form_tables()'s
        # early-return), so any correction made to the Invoice tab's Goods
        # Information table afterward was previously never reflected here.
        # Reading it live means this table always shows whatever is
        # currently saved (and, once GST is approved, exactly what was
        # approved, since Extracted Data is locked from then on).
        invoice_line_items = shape_invoice_items_for_migo(
            (details.get("invoice_data") or {}).get("hsn_details")
        )

        # First tab this user is allowed to see, in pipeline order -- used
        # to mark the initial active nav button/pane so a downstream-only
        # role (e.g. Gate Security) doesn't land on a blank "Documents"
        # pane they can't view.
        # FIX (2026-08-13): this used to check "documents" first -- but
        # can_view_documents is unconditionally True for every logged-in
        # user (v14), so it always won and the whole role-based landing
        # tab was dead code; everyone landed on Documents regardless of
        # role. Now: land on the first tab, in pipeline order, that this
        # user's role(s) cover AND that isn't already done for this
        # record yet -- e.g. a Compliance+Gate Security+Stores user whose
        # Extracted Data review is already approved skips straight to
        # Gate In, and once Gate In is posted, to MIGO 103. GST Approval
        # is deliberately excluded from this "next pending step" chain --
        # it's a standalone, on-demand tab now, not a pipeline gate (see
        # the Extracted Data redesign notes). Falls through to a second
        # pass (any tab they can at least view, same order, Documents
        # last) for a user with nothing left pending in any role they
        # hold -- e.g. every one of their steps is already done, or they
        # hold no operational role at all.
        default_tab_id = None
        for _tab_id, _visible, _pending in (
            ("extracted", can_view_extracted, (history.get("approval_status") or "pending") != "approved"),
            ("gateIn",    can_view_gate_in,   not history.get("gate_in")),
            ("migo103",   can_view_migo_103,  not history.get("migo_103")),
            ("migo105",   can_view_migo_105,  not history.get("migo_105")),
            ("miro",      can_view_miro,      not history.get("miro")),
        ):
            if _visible and _pending:
                default_tab_id = _tab_id
                break

        if not default_tab_id:
            for _tab_id, _visible in (
                ("extracted",   can_view_extracted),
                ("gstApproval", can_view_gst),
                ("gateIn",      can_view_gate_in),
                ("migo103",     can_view_migo_103),
                ("migo105",     can_view_migo_105),
                ("miro",        can_view_miro),
                ("documents",   can_view_documents),
            ):
                if _visible:
                    default_tab_id = _tab_id
                    break

        return render_template(
            "index.html",
            history=history,
            history_id=history_id,
            invoice_data=details.get("invoice_data"),
            ewaybill_data=details.get("ewaybill_data"),
            ewb_expired=ewb_expired,
            lr_data=details.get("lr_data"),
            history_extras=history_extras,
            gatein_data=gatein_data,
            resolved_vendor_name=resolved_vendor_name,
            gatein_category_default=gatein_category_default,
            migo_data=migo_data,
            invoice_line_items=invoice_line_items,
            miro_data=miro_data,
            po_data=po_data,
            username=session.get("username"),
            role=session.get("role"),
            from_history=True,
            can_view_documents=can_view_documents,
            dms_document_link=dms_document_link,
            can_view_extracted=can_view_extracted,
            can_edit_extracted=can_edit_extracted,
            can_view_gst=can_view_gst,
            can_view_gate_in=can_view_gate_in,
            can_view_migo_103=can_view_migo_103,
            can_view_migo_105=can_view_migo_105,
            can_view_miro=can_view_miro,
            default_tab_id=default_tab_id
        )
    except Exception as e:
        logger.error(f"view_detail error {history_id}: {e}", exc_info=True)
        return redirect(url_for("history_page"))


@app.route("/new_entry")
@login_required
def new_entry():
    if not _is_superadmin() and "compliance" not in _current_roles() and not config.ALLOW_USER_UPLOAD:
        return redirect(url_for("history_page"))
    session.pop("current_history_id", None)
    return render_template(
        "index.html",
        history=None, history_id=None,
        invoice_data=None, ewaybill_data=None, lr_data=None,
        gatein_data=None, migo_data=None, miro_data=None,
        username=session.get("username"),
        role=session.get("role"),
        from_history=False
    )


@app.route("/user_management")
@login_required
def user_management():
    # View access: SuperAdmin only, regardless of admin_edit (a view-only
    # SuperAdmin can still see the user list, just can't create/edit/
    # delete -- those mutating routes are separately gated by
    # @admin_required, which also checks admin_edit).
    if not _is_superadmin():
        return redirect(url_for("history_page"))
    users = get_all_users()
    storage_locations = get_all_storage_locations(active_only=False)
    return render_template(
        "user_management.html",
        users=users,
        storage_locations=storage_locations,
        username=session.get("username"),
        current_username=session.get("username"),
        role=session.get("role"),
        admin_can_edit=_admin_can_edit()
    )


# ============================================================
# RECORD ADMIN — delete a record entirely, reset an individual step, or
# revert an approval, from the UI instead of raw SQL run by hand.
#
# Permission model matches every other step-based action in this app
# (gate_in/migo_103/migo_105/miro/compliance): "record_admin" is just
# another value a SuperAdmin can add to a user's step_roles via the User
# Management page (see user_management.html/user_operations.py) --
# _has_role("record_admin") gates viewing this page, _require_role_edit(
# "record_admin") gates every actual mutating call below, same idiom used
# throughout the rest of app.py (e.g. save_gatein()/run_migo_103()). A
# SuperAdmin can always view (per _has_role's own rule) and can act only
# if admin_edit=True, same as everywhere else.
#
# Every mutating route here is deliberately POST-only, requires an
# explicit {"confirm": true} in the JSON body (belt-and-suspenders on top
# of whatever confirmation the frontend already does -- these are
# destructive, hard-to-undo actions on a production system), and logs to
# admin_action_log via database/admin_operations.py before/around the
# actual change so there's an audit trail independent of the record being
# acted on.
# ============================================================

@app.route("/admin/records")
@login_required
def admin_records_page():
    if not _has_role("record_admin"):
        return redirect(url_for("history_page"))
    return render_template(
        "admin_records.html",
        username=session.get("username"),
        role=session.get("role"),
        can_edit=_is_superadmin() and _admin_can_edit() or (not _is_superadmin() and "record_admin" in _current_roles()),
        recent_actions=get_admin_action_log(limit=50)
    )


@app.route("/api/admin/records/search")
@api_login_required
def api_admin_records_search():
    if not _has_role("record_admin"):
        return jsonify({"success": False, "error": "Permission denied."}), 403
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"success": True, "records": []})
    return jsonify({"success": True, "records": find_records_for_admin(query)})


def _record_admin_action(action_fn, history_id: int):
    """Shared body for every mutating route below -- checks permission,
    requires an explicit confirm flag, calls the given admin_operations
    function, and returns a consistent JSON response."""
    blocked = _require_role_edit("record_admin")
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"success": False, "error": "Confirmation required."}), 400
    ok = action_fn(history_id, _current_user())
    if not ok:
        return jsonify({"success": False, "error": "Action failed -- check server logs."}), 500
    return jsonify({"success": True})


@app.route("/api/admin/records/<int:history_id>/delete", methods=["POST"])
@api_login_required
def api_admin_delete_record(history_id):
    return _record_admin_action(delete_history_record, history_id)


@app.route("/api/admin/records/<int:history_id>/reset_gate_in", methods=["POST"])
@api_login_required
def api_admin_reset_gate_in(history_id):
    return _record_admin_action(reset_gate_in_step, history_id)


@app.route("/api/admin/records/<int:history_id>/reset_migo_103", methods=["POST"])
@api_login_required
def api_admin_reset_migo_103(history_id):
    return _record_admin_action(reset_migo_103_step, history_id)


@app.route("/api/admin/records/<int:history_id>/reset_migo_105", methods=["POST"])
@api_login_required
def api_admin_reset_migo_105(history_id):
    return _record_admin_action(reset_migo_105_step, history_id)


@app.route("/api/admin/records/<int:history_id>/reset_miro", methods=["POST"])
@api_login_required
def api_admin_reset_miro(history_id):
    return _record_admin_action(reset_miro_step, history_id)


@app.route("/api/admin/records/<int:history_id>/revert_extracted_data_approval", methods=["POST"])
@api_login_required
def api_admin_revert_extracted_data_approval(history_id):
    return _record_admin_action(revert_extracted_data_approval, history_id)


@app.route("/api/admin/records/<int:history_id>/revert_gst_approval", methods=["POST"])
@api_login_required
def api_admin_revert_gst_approval(history_id):
    return _record_admin_action(revert_gst_approval, history_id)


@app.route("/api/admin/records/<int:history_id>/revert_approval", methods=["POST"])
@api_login_required
def api_admin_revert_approval(history_id):
    # FIX (2026-08-11): combined Extracted Data + GST approval revert into
    # one action -- see admin_operations.revert_approval's docstring.
    return _record_admin_action(revert_approval, history_id)


@app.route("/api/admin/records/action_log")
@api_login_required
def api_admin_action_log():
    # FIX (2026-08-11): lets admin_records.html refresh the "Recent actions"
    # table in place after a bulk apply instead of doing a full
    # window.location.reload() -- see admin_records.html's runBulkActions().
    if not _has_role("record_admin"):
        return jsonify({"success": False, "error": "Permission denied."}), 403
    return jsonify({"success": True, "actions": get_admin_action_log(limit=50)})


# ============================================================
# QUEUE STATUS POLLING
# ============================================================

@app.route("/api/queue_status/<int:job_id>")
@api_login_required
def api_queue_status(job_id):
    job = get_job_status(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    return jsonify({"success": True, "job": job})


# ============================================================
# SAVE EXTRACTED DATA — three sub-tab endpoints
# ============================================================

@app.route("/api/save_extracted_invoice/<int:history_id>", methods=["POST"])
@api_login_required
def save_extracted_invoice(history_id):
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if (history.get("approval_status") or "pending") == "approved":
        return jsonify({"success": False, "error": "Record already approved — editing locked."}), 403

    data = request.get_json(silent=True) or {}

    if save_invoice_to_db(history_id, data):
        _auto_populate_form_tables(history_id)

        # v27 (2026-08-14, client request): no longer auto-retriggers GST
        # verification when the seller GSTIN is edited here. GST is now
        # strictly on-demand (see api_gst_run/api_gst_bulk_run) -- if
        # Compliance corrects a GSTIN after already approving Extracted
        # Data with a wrong one, re-running GST for it is on them, same as
        # any other post-approval correction. The GST Verification admin
        # page's "Needs Re-run" / stale-result cases are exactly what that
        # page is for.

        return jsonify({"success": True, "message": "Invoice data saved"})
    return jsonify({"success": False, "error": "Failed to save"}), 500


@app.route("/api/save_extracted_eway/<int:history_id>", methods=["POST"])
@api_login_required
def save_extracted_eway(history_id):
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if (history.get("approval_status") or "pending") == "approved":
        return jsonify({"success": False, "error": "Record already approved — editing locked."}), 403

    data = request.get_json(silent=True) or {}
    if save_ewaybill_to_db(history_id, data):
        _auto_populate_form_tables(history_id)
        return jsonify({"success": True, "message": "E-Way Bill data saved"})
    return jsonify({"success": False, "error": "Failed to save"}), 500


@app.route("/api/save_extracted_lr/<int:history_id>", methods=["POST"])
@api_login_required
def save_extracted_lr(history_id):
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if (history.get("approval_status") or "pending") == "approved":
        return jsonify({"success": False, "error": "Record already approved — editing locked."}), 403

    data = request.get_json(silent=True) or {}
    if save_lr_to_db(history_id, data):
        _auto_populate_form_tables(history_id)
        return jsonify({"success": True, "message": "LR data saved"})
    return jsonify({"success": False, "error": "Failed to save"}), 500


# ============================================================
# v13: PARTIAL-DOCUMENT SCENARIOS — goods delivery mode / EWB exemption
# reasons / extras. See database/scenario_operations.py and
# schema_migration_v13.sql.
# ============================================================

@app.route("/api/save_delivery_mode/<int:history_id>", methods=["POST"])
@api_login_required
def api_save_delivery_mode(history_id):
    """
    Set goods_delivery_mode -- required when the LR document is missing
    (Invoice + E-Way Bill present, or Invoice only). Same edit gate as the
    rest of Extracted Data (Compliance/SuperAdmin), and write-once: once
    set, this cannot be changed (client instruction — no later editing).
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if (history.get("approval_status") or "pending") == "approved":
        return jsonify({"success": False, "error": "Record already approved — editing locked."}), 403
    if history.get("goods_delivery_mode"):
        return jsonify({"success": False, "error": "Delivery mode already set — cannot be changed."}), 403

    body = request.get_json(silent=True) or {}
    mode = (body.get("mode") or "").strip()
    if mode not in DELIVERY_MODE_LABELS:
        return jsonify({"success": False, "error": "Invalid delivery mode."}), 400

    if not set_goods_delivery_mode(history_id, mode, _current_user()):
        return jsonify({"success": False, "error": "DB update failed"}), 500

    append_remark(history_id, delivery_mode_remark_text(mode), "compliance", _current_user())

    return jsonify({"success": True, "mode": mode, "label": DELIVERY_MODE_LABELS[mode]})


@app.route("/api/save_ewb_exemption/<int:history_id>", methods=["POST"])
@api_login_required
def api_save_ewb_exemption(history_id):
    """
    Set ewb_exemption_reasons -- required when the E-Way Bill document is
    missing (Invoice + LR present, or Invoice only). Multi-select; write-once
    same as delivery mode above.
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if (history.get("approval_status") or "pending") == "approved":
        return jsonify({"success": False, "error": "Record already approved — editing locked."}), 403
    if history.get("ewb_exemption_reasons"):
        return jsonify({"success": False, "error": "Exemption reasons already set — cannot be changed."}), 403

    body = request.get_json(silent=True) or {}
    reasons = body.get("reasons") or []
    if not isinstance(reasons, list) or not reasons or any(r not in EWB_EXEMPTION_LABELS for r in reasons):
        return jsonify({"success": False, "error": "Select at least one valid exemption reason."}), 400

    if not set_ewb_exemption_reasons(history_id, reasons, _current_user()):
        return jsonify({"success": False, "error": "DB update failed"}), 500

    append_remark(history_id, ewb_exemption_remark_text(reasons), "compliance", _current_user())

    return jsonify({
        "success": True,
        "reasons": reasons,
        "labels": [EWB_EXEMPTION_LABELS[r] for r in reasons]
    })


@app.route("/api/save_category/<int:history_id>", methods=["POST"])
@api_login_required
def api_save_category(history_id):
    """
    v26: set history.category -- compulsory (defaults to 'stores' at the
    DB level too, see schema_migration_v26.sql), but NOT write-once like
    goods_delivery_mode/ewb_exemption_reasons above. Compliance can change
    it as often as they like while the record is still editable. Only
    used to pre-select (not lock) Gate In's own Category dropdown --
    CATEGORY_TO_GATEIN_CODE.
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if (history.get("approval_status") or "pending") == "approved":
        return jsonify({"success": False, "error": "Record already approved — editing locked."}), 403

    body = request.get_json(silent=True) or {}
    category = (body.get("category") or "").strip()
    if category not in CATEGORY_LABELS:
        return jsonify({"success": False, "error": "Invalid category."}), 400

    if not set_category(history_id, category, _current_user()):
        return jsonify({"success": False, "error": "DB update failed"}), 500

    return jsonify({"success": True, "category": category, "label": CATEGORY_LABELS[category]})


# ============================================================
# APPROVE / HOLD
# ============================================================

@app.route("/api/approve/<int:history_id>", methods=["POST"])
@api_login_required
def api_approve(history_id):
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    # v13: mirror the client-side block on the Extracted Data tab -- a
    # record missing LR and/or E-Way Bill must have the corresponding
    # picker answered before Approve, not just visually disabled. Doesn't
    # apply once already approved (can't happen twice) or to records that
    # never went through this route (e.g. legacy pre-v13 records where
    # both docs are simply absent and neither column will ever be set --
    # those are only reachable here if they have all 3 docs already).
    details_for_gate = get_history_details_by_id(history_id)
    has_eway = bool(details_for_gate.get("ewaybill_data"))
    has_lr   = bool(details_for_gate.get("lr_data"))
    if not has_lr and not history.get("goods_delivery_mode"):
        return jsonify({"success": False, "error": "Select a goods delivery mode before approving — LR document is missing."}), 400
    if not has_eway and not history.get("ewb_exemption_reasons"):
        return jsonify({"success": False, "error": "Select an E-Way Bill exemption reason before approving — E-Way Bill is missing."}), 400

    if not set_approval_status(history_id, _current_user()):
        return jsonify({"success": False, "error": "Failed to approve"}), 500

    details = get_history_details_by_id(history_id)
    inv = details.get("invoice_data") or {}

    create_notification(
        history_id=history_id,
        title="Documents Approved",
        message=f"Invoice {inv.get('invoice_number') or '#'+str(history_id)} approved by {_current_user()} — ready for Gate In.",
        notification_type="approve",
        role_target="gate_in"
    )

    send_approval_notification(
        history_id=history_id,
        invoice_number=inv.get("invoice_number"),
        approved_by=_current_user()
    )

    return jsonify({"success": True, "message": "Record approved"})


@app.route("/api/hold/<int:history_id>", methods=["POST"])
@api_login_required
def api_hold(history_id):
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if history.get("gate_in"):
        return jsonify({"success": False, "error": "Cannot hold — Gate In already completed."}), 400

    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "Hold reason required"}), 400

    if not set_hold_status(history_id, _current_user(), reason):
        return jsonify({"success": False, "error": "Failed to hold"}), 500

    create_notification(
        history_id=history_id,
        title="Record on Hold",
        message=f"Record {history_id} put on hold by {_current_user()}: {reason}",
        notification_type="hold",
        role_target="all"
    )
    return jsonify({"success": True, "message": "Record placed on hold"})


# ============================================================
# OCR RETRY
# ============================================================

@app.route("/api/rerun_ocr/<int:history_id>", methods=["POST"])
@api_login_required
def api_rerun_ocr(history_id):
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if (history.get("ocr_status") or "") != "failed":
        return jsonify({"success": False, "error": "Only failed records can be re-run"}), 400

    failed_path = get_ocr_failed_path(history_id)
    if not failed_path or not os.path.isdir(failed_path):
        return jsonify({"success": False, "error": "Failed folder not found"}), 404

    retry_count = increment_ocr_retry(history_id)
    files_processed = 0
    invoice_succeeded = False

    for filename in os.listdir(failed_path):
        if not filename.lower().endswith(".pdf"):
            continue
        file_path = os.path.join(failed_path, filename)
        # Detect doc type from filename
        from services.folder_watcher import _detect_doc_type
        doc_type = _detect_doc_type(filename)
        if not doc_type:
            continue

        # FIX (2026-08-10): this used to run OCR directly against file_path
        # (inside failed_path -- folder_watcher's own separate failed/ tree,
        # e.g. G:\Material_inward\failed\<group>_<timestamp>\) and save
        # extracted["filename"] = filename without ever copying the PDF into
        # config.UPLOAD_FOLDER. view_document/download_document/
        # get_document_thumbnail (this file) all locate files via
        # _find_file(), which only searches UPLOAD_FOLDER/
        # UPLOAD_PROCESSED_FOLDER/UPLOAD_FAILED_FOLDER (config.UPLOAD_FOLDER's
        # own subtree) -- never folder_watcher's WATCH_FOLDER/FAILED_FOLDER
        # tree. So a re-run's OCR data saved to the DB fine (filename was
        # non-empty), but the file itself was never findable -- the
        # Documents tab card rendered (filename check passed) with a broken
        # preview and a 404 on View/Download. Mirrors the exact
        # copy-then-process pattern folder_watcher.py's _process_batch()
        # already uses for the normal auto-intake path (same
        # f"h{history_id}_{filename}" naming) so a re-run's file ends up
        # discoverable the same way a first-pass file is.
        safe_name = f"h{history_id}_{filename}"
        upload_dest = os.path.join(config.UPLOAD_FOLDER, safe_name)
        try:
            os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
            shutil.copy2(file_path, upload_dest)
        except Exception as copy_err:
            logger.error(f"Re-run OCR: failed to copy {filename} into UPLOAD_FOLDER: {copy_err}")
            continue

        try:
            extracted = process_document(doc_type, upload_dest, safe_name)
            if extracted:
                extracted["filename"] = safe_name
                if doc_type == "invoice":
                    save_invoice_to_db(history_id, extracted)
                    invoice_succeeded = True
                    # v27 (2026-08-14, client request): no longer auto-triggers
                    # GST verification after an OCR re-run either -- fully
                    # on-demand now, same as every other GST trigger point.
                elif doc_type == "ewaybill": save_ewaybill_to_db(history_id, extracted)
                elif doc_type == "lr":       save_lr_to_db(history_id, extracted)
                files_processed += 1
        except Exception as e:
            logger.error(f"Re-run OCR error: {e}")

    # Invoice is the anchor document every downstream tab/workflow step
    # keys off (same rule now enforced in folder_watcher.py._process_batch()
    # for the automated intake path). Only mark the record "success" if the
    # Invoice specifically was recovered this retry -- previously any
    # successfully re-extracted file (even just E-Way Bill or LR alone)
    # was enough to flip the whole record to "success", which could leave
    # a record marked successful with no Invoice data and an empty Invoice
    # tab, exactly what happened for history_id 42 on 2026-08-05.
    if invoice_succeeded:
        _auto_populate_form_tables(history_id)
        set_ocr_status(history_id, "success")
        return jsonify({"success": True, "message": f"OCR retry succeeded — {files_processed} document(s)", "retry_count": retry_count})

    # FIX (2026-08-11): manual-upload parity. The /upload/<doctype> route
    # lets a user upload invoice/ewaybill/lr as separate requests (one tab at
    # a time), so it's possible for Invoice to have already succeeded on its
    # own earlier while, say, E-Way Bill failed and is the only thing sitting
    # in the failed folder now. In that case this retry batch never touches
    # an invoice file at all, so invoice_succeeded above stays False even
    # though the anchor document is already saved -- without this check the
    # record would stay stuck on "failed" forever despite a fully successful
    # retry, which is worse than the original 404 (silently wrong, not just
    # broken). Falls back to checking the DB directly for existing invoice
    # data rather than only what this particular retry call re-extracted.
    if files_processed > 0:
        details = get_history_details_by_id(history_id)
        if details.get("invoice_data"):
            _auto_populate_form_tables(history_id)
            set_ocr_status(history_id, "success")
            return jsonify({"success": True, "message": f"OCR retry succeeded — {files_processed} document(s)", "retry_count": retry_count})

    if files_processed > 0:
        # Some other document(s) re-extracted and were saved above, but the
        # Invoice specifically still failed -- leave the record as "failed"
        # rather than silently marking it "success" with an empty Invoice
        # tab. The other document(s) are not lost: they're already saved,
        # so the next Re-run only needs to actually recover the Invoice.
        return jsonify({
            "success": False,
            "error": f"Invoice could not be re-extracted ({files_processed} other document(s) saved) — record remains failed until the Invoice succeeds.",
            "retry_count": retry_count
        }), 500

    return jsonify({"success": False, "error": "OCR retry failed", "retry_count": retry_count}), 500


# ============================================================
# NOTIFICATIONS API
# ============================================================
@app.route('/api/notifications/read_all', methods=['POST'])
@login_required
def mark_all_notifications_read():
    if not config.ENABLE_INAPP_NOTIFICATIONS:
        return jsonify({'success': True})
    from database.notifications_operations import mark_all_read
    mark_all_read(
    username=session.get("username"),
    user_step_roles=session.get("step_roles", "all")
)
    return jsonify({'success': True})


@app.route("/api/notifications/unread")
@api_login_required
def api_notifications_unread():
    if not config.ENABLE_INAPP_NOTIFICATIONS:
        return jsonify({"success": True, "notifications": []})
    notifications = get_unread_for_user(
        username=session.get("username"),
        user_step_roles=session.get("step_roles", "all")
    )
    return jsonify({"success": True, "notifications": notifications})


@app.route("/api/notifications/<int:notif_id>/mark_read", methods=["POST"])
@api_login_required
def api_notifications_mark_read(notif_id):
    return jsonify({"success": mark_as_read(notif_id)})


# @app.route("/api/notifications/mark_all_read", methods=["POST"])
# @api_login_required
# def api_notifications_mark_all_read():
#     count = mark_all_as_read_for_user(session.get("username"))
#     return jsonify({"success": True, "marked": count})


# ============================================================
# MIGO MATCHED PAIRS (for MIGO 105 page)
# ============================================================

@app.route("/api/migo_matched_pairs/<int:history_id>")
@api_login_required
def api_migo_matched_pairs(history_id):
    migo = get_migo_entry(history_id)
    if not migo:
        return jsonify({"success": True, "items": []})
    items = migo.get("items_data") or []
    return jsonify({"success": True, "items": items})


# ============================================================
# DOCUMENT UPLOAD
# ============================================================

@app.route("/upload/<doctype>", methods=["POST"])
@api_login_required
def upload_document(doctype):
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    # FIX (2026-08-11): "others" added for G-drive parity -- no OCR, just
    # attached to the record the same way folder_watcher.py's
    # _process_batch() handles an Others file dropped in G-drive.
    valid_types = ["invoice", "ewaybill", "lr", "others"]
    if doctype not in valid_types:
        return jsonify({"error": f"Invalid document type: {doctype}"}), 400
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    filename = file.filename

    try:
        history_id = session.get("current_history_id")
        if not history_id:
            history_id = create_history_record()
            session["current_history_id"] = history_id

        if doctype == "others":
            safe_name = f"h{history_id}_{filename}"
            others_path = os.path.join(config.UPLOAD_FOLDER, safe_name)
            os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
            file.save(others_path)
            _attach_others_document(history_id, safe_name, filename)
            # v22: Others has no OCR to succeed/fail -- same as
            # folder_watcher.py's own Others handling, "attached" is the
            # only outcome, so always claim as success.
            _dedupe_watch_folder(filename, success=True)
            return jsonify({
                "success": True,
                "history_id": history_id,
                "message": "Others document attached"
            })

        file_path = os.path.join(config.UPLOAD_FOLDER, filename)
        file.save(file_path)

        if not _run_ocr_and_save(doctype, file_path, filename, history_id, original_filename=filename):
            return jsonify({"error": "OCR failed — file moved to failed/"}), 500

        _auto_populate_form_tables(history_id)
        details = get_history_details_by_id(history_id)
        return jsonify({
            "success": True,
            "history_id": history_id,
            "data": details.get(f"{doctype}_data") or {},
            "message": f"{doctype.upper()} processed"
        })
    except Exception as e:
        logger.error(f"Upload error {doctype}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/process_all", methods=["POST"])
@api_login_required
def process_all():
    if not config.ALLOW_USER_UPLOAD:
        blocked = _require_role_edit("compliance")
        if blocked:
            return blocked

    files = {
        "invoice":  request.files.get("invoice"),
        "ewaybill": request.files.get("ewaybill"),
        "lr":       request.files.get("lr")
    }
    # "others" kept separate from the OCR-required check below -- Others
    # alone (with none of invoice/ewaybill/lr) shouldn't be treated as a
    # valid /process_all submission, same as before this fix.
    others_file = request.files.get("others")
    if not any(f and f.filename for f in files.values()):
        return jsonify({"error": "No files uploaded"}), 400

    history_id = create_history_record()
    if not history_id:
        return jsonify({"error": "Failed to create history record"}), 500

    results = {}
    for doctype, file in files.items():
        if not file or not file.filename:
            continue
        filename = f"h{history_id}_{file.filename}"
        file_path = os.path.join(config.UPLOAD_FOLDER, filename)
        file.save(file_path)
        results[doctype] = _run_ocr_and_save(doctype, file_path, filename, history_id, original_filename=file.filename)

    # FIX (2026-08-11): "Others" doctype support, mirroring folder_watcher.py's
    # _process_batch() -- no OCR, just attached via history_extras so
    # doc_consolidator.py picks it up into the DMS-bound consolidated PDF the
    # same way a G-drive-dropped Others file would be. Doesn't touch `results`
    # / the ocr_status logic below, which only concerns invoice/ewaybill/lr.
    #
    # FIX (2026-08-13): this block used to appear TWICE in a row here (a
    # copy-paste artifact from wiring in _dedupe_watch_folder on 2026-08-12)
    # -- every /process_all submission with an Others file ran this entire
    # save-and-attach sequence twice, saving the same file to the same path
    # twice (harmless) but calling add_history_extra() twice, so a single
    # uploaded Others file always produced TWO history_extras rows for it.
    # Confirmed as the cause of a record showing 2 attached "Others"
    # documents from what the user experienced as one upload. Removed the
    # duplicate; also switched to _attach_others_document() (new
    # 2026-08-13) so re-submitting Others for the same record now replaces
    # the previous one instead of ever accumulating extras at all.
    if others_file and others_file.filename:
        safe_others_name = f"h{history_id}_{others_file.filename}"
        others_path = os.path.join(config.UPLOAD_FOLDER, safe_others_name)
        try:
            os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
            others_file.save(others_path)
            _attach_others_document(history_id, safe_others_name, others_file.filename)
            _dedupe_watch_folder(others_file.filename, success=True)
        except Exception as e:
            logger.error(f"Failed to attach Others document for history_id={history_id}: {e}")

    # FIX (2026-08-11): "Others" doctype support, mirroring folder_watcher.py's
    # _process_batch() -- no OCR, just attached via history_extras so
    # doc_consolidator.py picks it up into the DMS-bound consolidated PDF the
    # same way a G-drive-dropped Others file would be. Doesn't touch `results`
    # / the ocr_status logic below, which only concerns invoice/ewaybill/lr.
    if others_file and others_file.filename:
        safe_others_name = f"h{history_id}_{others_file.filename}"
        others_path = os.path.join(config.UPLOAD_FOLDER, safe_others_name)
        try:
            os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
            others_file.save(others_path)
            add_history_extra(history_id, safe_others_name, others_file.filename, doc_type="others")
        except Exception as e:
            logger.error(f"Failed to attach Others document for history_id={history_id}: {e}")

    session["current_history_id"] = history_id
    _auto_populate_form_tables(history_id)
    if any(results.values()):
        set_ocr_status(history_id, "success")
    else:
        # FIX (2026-08-11): previously this always called
        # set_ocr_status(history_id, "failed") with NO failed_path, which
        # overwrote (wiped to NULL) the failed_path _run_ocr_and_save() had
        # just set per-doctype above -- get_ocr_failed_path() would come back
        # empty and /api/rerun_ocr always 404'd ("Failed folder not found")
        # for a manual upload, even though the failed file(s) were sitting
        # right there in uploads/failed/h{history_id}/. Re-pass the same
        # folder so this aggregate call doesn't clobber it.
        failed_folder = os.path.join(config.UPLOAD_FAILED_FOLDER, f"h{history_id}")
        set_ocr_status(history_id, "failed", failed_path=failed_folder)

    return jsonify({"success": True, "history_id": history_id, "results": results})


@app.route("/api/upload_missing/<int:history_id>/<doctype>", methods=["POST"])
@api_login_required
def api_upload_missing_document(history_id, doctype):
    """
    FIX (2026-08-11): lets a user add a document to a record that was
    already started -- e.g. only Invoice was uploaded via /process_all
    earlier, and E-Way Bill/LR need to be added later. Previously there was
    no way to do this at all: /process_all always creates a brand new
    history_id every time it's submitted, and the only other upload route
    (/upload/<doctype>) relied on a session-stored current_history_id that
    nothing in the UI ever actually called. Takes history_id explicitly in
    the URL instead (tied to the record the Documents tab is already
    showing), so there's no session-state guessing involved.

    Blocked once the record's Extracted Data is approved -- save_invoice_to_db/
    save_ewaybill_to_db/save_lr_to_db are unconditional upserts with no
    approval check of their own, so without this gate a re-upload here would
    silently overwrite already-approved data. Gate In can't run until
    approval_status = 'approved' anyway (_check_step_allowed), so blocking
    on approval here also transitively prevents document changes after Gate
    In has posted -- no separate check needed for that.
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    valid_types = ["invoice", "ewaybill", "lr", "others"]
    if doctype not in valid_types:
        return jsonify({"success": False, "error": f"Invalid document type: {doctype}"}), 400
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"success": False, "error": "No file provided"}), 400

    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if (history.get("approval_status") or "") == "approved":
        return jsonify({
            "success": False,
            "error": "This record is already approved — revert the approval from Admin Records before adding or replacing documents."
        }), 400

    file = request.files["file"]
    filename = file.filename

    try:
        if doctype == "others":
            safe_name = f"h{history_id}_{filename}"
            others_path = os.path.join(config.UPLOAD_FOLDER, safe_name)
            os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
            file.save(others_path)
            _attach_others_document(history_id, safe_name, filename)
            _dedupe_watch_folder(filename, success=True)
            return jsonify({"success": True, "history_id": history_id, "message": "Others document attached"})

        safe_name = f"h{history_id}_{filename}"
        file_path = os.path.join(config.UPLOAD_FOLDER, safe_name)
        os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
        file.save(file_path)

        if not _run_ocr_and_save(doctype, file_path, safe_name, history_id, original_filename=filename):
            return jsonify({
                "success": False,
                "error": f"OCR failed for {doctype.upper()} — it's been moved to this record's failed folder, use Re-run OCR from Extracted Data once it shows as failed."
            }), 500

        details = get_history_details_by_id(history_id)
        # _run_ocr_and_save's success path never touches ocr_status (only its
        # failure branches do) -- if this record was previously "failed"
        # (e.g. the doc being added/replaced right now is the one that
        # failed originally), leaving ocr_status stale would keep showing
        # "failed" even though this upload just fixed it. Same invoice-anchor
        # rule as /api/rerun_ocr's own fallback check.
        if details.get("invoice_data"):
            set_ocr_status(history_id, "success")

        _auto_populate_form_tables(history_id)
        return jsonify({
            "success": True,
            "history_id": history_id,
            "data": details.get(f"{doctype}_data") or {},
            "message": f"{doctype.upper()} uploaded"
        })
    except Exception as e:
        logger.error(f"Upload-missing error {doctype} h{history_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# WORKFLOW ENDPOINTS — Gate In / MIGO 103 / MIGO 105 / MIRO
# ============================================================

@app.route("/save_gatein", methods=["POST"])
@api_login_required
def save_gatein():
    blocked = _require_role_edit("gate_in")
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    history_id = data.get("history_id")
    if not history_id:
        return jsonify({"success": False, "error": "Missing history_id"}), 400

    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    allowed, reason = _check_step_allowed(history, "gate_in")
    if not allowed:
        return jsonify({"success": False, "error": reason}), 400

    # v17.1: server-side mirror of validateGateIn() in gate_in.html --
    # see _validate_required_fields()'s docstring for why this was added.
    po_flow_type = (data.get("po_flow_type") or "truck_with_po").strip()
    valid_flow_types = {
        "truck_with_po",   "truck_without_po",
        "hand_with_po",    "hand_without_po",
        "courier_with_po", "courier_without_po",
    }
    if po_flow_type not in valid_flow_types:
        return jsonify({"success": False, "error": f"Invalid po_flow_type: {po_flow_type!r}"}), 400
    # "is_hand" here really means "not truck" -- Hand Delivery and Courier
    # both skip Truck No/License No and use the "Person Name" label (see
    # gate_in.html's onDeliveryTypeChange(), which treats them identically
    # except for what rf_runner.py sends SAP as the truck-number placeholder).
    is_hand       = not po_flow_type.startswith("truck_")
    is_without_po = po_flow_type.endswith("_without_po")

    required = [
        ("gateInDate", "Gate In Date"),
        ("gateInTime", "Gate In Time"),
        ("vendorName", "Vendor Name"),
        # FIX (2026-08-13): Vendor Name / Vendor Code split -- Vendor Code is
        # the field that actually posts to SAP now (see the max-10-char
        # check below and execute_gate_in_sap()), so it's required
        # independently of Vendor Name, not just a max-length constraint on it.
        ("vendorCode", "Vendor Code"),
        ("driverName", "Person Name" if is_hand else "Driver Name"),
        ("category",   "Category"),
        ("material",   "Material"),
        ("challanNo",  "Challan No"),
        ("challanQty", "Challan Quantity"),
    ]
    if not is_hand:
        required += [("truckNo", "Truck No"), ("licenseNo", "License No")]
    if not is_without_po:
        required.append(("purchaseOrder", "Purchase Order"))

    err = _validate_required_fields(data, required)
    if err:
        return jsonify({"success": False, "error": err}), 400

    # FIX (2026-08-13): Same 10-char SAP vendor-code limit validateGateIn()
    # enforces client-side (LIFNR field length) -- now checked on Vendor
    # Code, not Vendor Name (see the gate_in_entries.vendor_code split).
    # Vendor Name stays free-text now; Vendor Code is what has to fit the
    # SAP code format by the time it's posted.
    if len(str(data.get("vendorCode") or "").strip()) > 10:
        return jsonify({
            "success": False,
            "error": "Vendor Code must be 10 characters or fewer -- it should be the SAP vendor code. Use Fetch to resolve it."
        }), 400

    upsert_gatein_entry(history_id, data)
    set_po_flow_type(history_id, po_flow_type)
    # v16: recorded so gate_in_entries.submitted_by can be set once this
    # job actually posts (or fails) -- see rf_queue_worker.py._process_gate_in
    # and database/gatein_operations.py.update_gatein_rf_result(). This is
    # schema/plumbing only for now; the zgatein_update PO-backfill flow that
    # will actually use this value is still under design.
    data["_submitted_by_username"] = session.get("username")
    job_id, err = _enqueue_sap_job(history_id, "gate_in", data)
    if err:
        return err
    if not job_id:
        return jsonify({"success": False, "error": "Gate In already processing."}), 409
    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})


@app.route("/api/run_migo_103", methods=["POST"])
@api_login_required
def run_migo_103():
    blocked = _require_role_edit("migo_103")
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    history_id = data.get("history_id")
    if not history_id:
        return jsonify({"success": False, "error": "Missing history_id"}), 400

    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    allowed, reason = _check_step_allowed(history, "migo_103")
    if not allowed:
        return jsonify({"success": False, "error": reason}), 400

    # v17.1: server-side mirror of validateMigo103() in migo_103.html.
    # PO Number is unconditionally mandatory here (unlike Gate In's, which
    # is exempted for without_po) -- for a without_po record this is the
    # guard's own point of entering the real PO, which _process_migo_103
    # then reads (payload["purchaseOrder"]/["migoPoNumber"]) to log the
    # Pending PO Update for Gate In backfill. See rf_queue_worker.py.
    required = [
        ("migoPoNumber",     "Purchase Order No"),
        ("migoDocDate",      "Document Date"),
        ("migoPostDate",     "Posting Date"),
        ("migoDeliveryNote", "Delivery Note"),
        ("migoHeaderText",   "Header Text"),
    ]
    err = _validate_required_fields(data, required)
    if err:
        return jsonify({"success": False, "error": err}), 400
    items_data = data.get("items_data") or []
    if not isinstance(items_data, list) or len(items_data) == 0:
        return jsonify({
            "success": False,
            "error": "At least one matched line item is required before posting MIGO 103."
        }), 400

    upsert_migo_entry(history_id, data)
    job_id, err = _enqueue_sap_job(history_id, "migo_103", data)
    if err:
        return err
    if not job_id:
        return jsonify({"success": False, "error": "MIGO 103 already processing."}), 409
    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})

@app.route("/api/run_migo_105", methods=["POST"])
@api_login_required
def run_migo_105():
    blocked = _require_role_edit("migo_105")
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    history_id = data.get("history_id")
    if not history_id:
        return jsonify({"success": False, "error": "Missing history_id"}), 400

    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    allowed, reason = _check_step_allowed(history, "migo_105")
    if not allowed:
        return jsonify({"success": False, "error": reason}), 400

    # v17.1: server-side mirror of validateMigo105() in migo_105.html --
    # previously the ONLY field re-validated server-side on this route was
    # material_doc_number (below); storageLocation was mandatory client-side
    # but never checked here.
    err = _validate_required_fields(data, [("storageLocation", "Storage Location")])
    if err:
        return jsonify({"success": False, "error": err}), 400

    migo_entry = get_migo_entry(history_id)

    # Get mat doc — UI override takes priority over DB value
    mat_doc = (
        data.get("material_doc_number_override", "").strip() or
        (migo_entry or {}).get("material_doc_number", "").strip() or
        history.get("material_doc_number", "").strip() or
        ""
    )

    if not mat_doc:
        return jsonify({
            "success": False,
            "error": "Material Doc Number missing — ensure MIGO 103 completed."
        }), 400

    # If user typed a new mat doc, save it to DB immediately
    if data.get("material_doc_number_override", "").strip():
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE migo_entries 
                           SET material_doc_number = %s, updated_at = CURRENT_TIMESTAMP
                           WHERE history_id = %s""",
                        (mat_doc, history_id)
                    )
                    cur.execute(
                        """UPDATE history
                           SET material_doc_number = %s, updated_at = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (mat_doc, history_id)
                    )
            logger.info(
                f"Material doc number manually updated for "
                f"history_id={history_id}: {mat_doc}"
            )
        except Exception as e:
            logger.error(f"Failed to save manual mat doc override: {e}")

    save_migo_105_fields(history_id, data)

    line_batches = data.get("line_batches") or []
    if line_batches:
        update_migo_105_items_with_batches(history_id, line_batches)

    rf_payload = {
        "material_doc_number":          mat_doc,
        "material_doc_number_override": mat_doc,
        "migo_105_storage_loc":         data.get("storageLocation"),
        "migo_105_vendor_invoice":      data.get("vendorInvoiceDetail"),
        "migo_105_remarks":             data.get("remarks105"),
    }

    job_id, err = _enqueue_sap_job(history_id, "migo_105", rf_payload)
    if err:
        return err
    if not job_id:
        return jsonify({"success": False, "error": "MIGO 105 already processing."}), 409
    return jsonify({
        "success": True,
        "job_id": job_id,
        "poll_url": f"/api/queue_status/{job_id}"
    })

# @app.route("/api/run_migo_105", methods=["POST"])
# @api_login_required
# def run_migo_105():
#     data = request.get_json(silent=True) or {}
#     history_id = data.get("history_id")
#     if not history_id:
#         return jsonify({"success": False, "error": "Missing history_id"}), 400

#     history = get_history_by_id(history_id)
#     if not history:
#         return jsonify({"success": False, "error": "Record not found"}), 404

#     allowed, reason = _check_step_allowed(history, "migo_105")
#     if not allowed:
#         return jsonify({"success": False, "error": reason}), 400

#     migo_entry = get_migo_entry(history_id)
#     material_doc = (migo_entry or {}).get("material_doc_number") or history.get("material_doc_number")
#     if not material_doc:
#         return jsonify({"success": False, "error": "Material Doc Number missing — ensure MIGO 103 completed."}), 400

#     save_migo_105_fields(history_id, data)

#     # Save per-line batches if provided
#     line_batches = data.get("line_batches") or []
#     if line_batches:
#         update_migo_105_items_with_batches(history_id, line_batches)

#     rf_payload = {
#         "material_doc_number":     material_doc,
#         "migo_105_storage_loc":    data.get("storageLocation"),
#         "migo_105_vendor_invoice": data.get("vendorInvoiceDetail"),
#         "migo_105_remarks":        data.get("remarks105"),
#     }
#     job_id = enqueue_rf_job(history_id, "migo_105", rf_payload)
#     if not job_id:
#         return jsonify({"success": False, "error": "MIGO 105 already processing."}), 409
#     return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})


@app.route("/api/run_miro", methods=["POST"])
@api_login_required
def run_miro():
    blocked = _require_role_edit("miro")
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    history_id = data.get("history_id")
    if not history_id:
        return jsonify({"success": False, "error": "Missing history_id"}), 400

    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    allowed, reason = _check_step_allowed(history, "miro")
    if not allowed:
        return jsonify({"success": False, "error": reason}), 400

    # v17.1: server-side mirror of validateMiro() in miro.html.
    required = [
        ("miroInvoiceDate",   "Invoice Date"),
        ("miroReference",     "Reference (Bill No.)"),
        ("miroAmount",        "Amount"),
        ("miroPurchaseOrder", "Purchase Order"),
    ]
    err = _validate_required_fields(data, required)
    if err:
        return jsonify({"success": False, "error": err}), 400

    upsert_miro_entry(history_id, data)
    details = get_history_details_by_id(history_id)
    inv = details.get("invoice_data") or {}
    invoice_number = inv.get("invoice_number") or data.get("miroReference") or ""

    rf_payload = {
        "miroReference":     invoice_number,
        "miroInvoiceDate":   data.get("miroInvoiceDate") or inv.get("invoice_date") or "",
        "miroPurchaseOrder": data.get("miroPurchaseOrder") or inv.get("po_number") or "",
    }

    job_id, err = _enqueue_sap_job(history_id, "miro", rf_payload)
    if err:
        return err
    if not job_id:
        return jsonify({"success": False, "error": "MIRO already processing."}), 409
    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})

# ============================================================
# DATA FETCH
# ============================================================

@app.route("/api/gatein/<int:history_id>")
@api_login_required
def api_get_gatein(history_id):
    data = get_gatein_entry(history_id)
    return jsonify({"success": True, "data": data}) if data else (jsonify({"success": False}), 404)


@app.route("/api/po_data/<int:history_id>")
@api_login_required
def api_get_po_data(history_id):
    items = get_po_line_items(history_id)
    return jsonify({"success": True, "data": items})


@app.route("/api/vehicle_lookup/<truck_number>")
@api_login_required
def vehicle_lookup(truck_number):
    """Look up driver details for a given truck number (vehicle master)."""
    truck_number = truck_number.strip()
    if not truck_number:
        return jsonify({"success": False, "error": "Truck number required"}), 400
    try:
        drivers = get_drivers_by_truck(truck_number)
        return jsonify({"success": True, "drivers": drivers, "count": len(drivers)})
    except Exception as e:
        logger.error(f"vehicle_lookup error truck={truck_number}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vendor_lookup")
@api_login_required
def vendor_lookup():
    """
    Fuzzy-search supplier_master by name. Used both by the Gate In tab's
    'Fetch Vendor Code' button (called once, with whatever's currently in
    Vendor Name) and by its live type-ahead search (called repeatedly,
    debounced, as the user types).
    """
    query = request.args.get("name", "").strip()
    if not query:
        return jsonify({"success": False, "error": "name required"}), 400
    try:
        # FIX: this override was hardcoding 10 regardless of
        # search_suppliers' own default -- raised alongside that default,
        # see the comment above search_suppliers() for why.
        candidates = search_suppliers(query, limit=100)
        return jsonify({"success": True, "candidates": candidates, "count": len(candidates)})
    except Exception as e:
        logger.error(f"vendor_lookup error name={query}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vendor_lookup/verify")
@api_login_required
def vendor_lookup_verify():
    """
    Exact-match check: is whatever's currently sitting in the Vendor Name
    field an actual SAP vendor code (from supplier_master), or is it still
    free-text (OCR'd name, or a name the user typed but never resolved via
    Fetch/type-ahead)? Used by Gate In's submit-time validation to block
    posting to SAP with a vendor NAME in the vendor_name slot -- SAP needs
    the code there, not the name.
    """
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"success": False, "error": "code required"}), 400
    try:
        supplier = get_supplier_by_code(code)
        return jsonify({"success": True, "valid": bool(supplier), "supplier": supplier or None})
    except Exception as e:
        logger.error(f"vendor_lookup_verify error code={code}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/set_po_flow_type/<int:history_id>", methods=["POST"])
@api_login_required
def api_set_po_flow_type(history_id):
    """Manually set po_flow_type on a history record (used from Gate In tab)."""
    data = request.get_json(silent=True) or {}
    po_flow_type = (data.get("po_flow_type") or "").strip()
    valid = {
        "truck_with_po",   "truck_without_po",
        "hand_with_po",    "hand_without_po",
        "courier_with_po", "courier_without_po",
    }
    if po_flow_type not in valid:
        return jsonify({"success": False, "error": f"Invalid po_flow_type: {po_flow_type!r}"}), 400
    ok = set_po_flow_type(history_id, po_flow_type)
    if ok:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "DB update failed"}), 500


@app.route("/api/run_po_fetch/<int:history_id>", methods=["POST"])
@api_login_required
def run_po_fetch(history_id):
    """
    Manually enqueue a po_fetch job (ME23N by PO number).
    Used from MIGO 103 tab for without_po flows where the user enters the PO manually.
    """
    data = request.get_json(silent=True) or {}
    po_number = (data.get("po_number") or "").strip()
    if not po_number:
        return jsonify({"success": False, "error": "po_number required"}), 400

    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    # v16: po_fetch (ME23N line-item read) deliberately always uses the
    # shared spl_rpa/.env SAP login, never a per-user LDAP credential --
    # it's a read-only PO lookup, not an attributable posting, so there's
    # no audit-trail reason to route it through _enqueue_sap_job(). See
    # config/.env comments next to SAP_USERNAME/SAP_PASSWORD.
    job_id = enqueue_rf_job(
        history_id, "po_fetch",
        {"po_number": po_number, "history_id": history_id}
    )
    if not job_id:
        return jsonify({"success": False, "error": "PO fetch already in queue."}), 409
    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})


@app.route("/api/run_po_list_fetch", methods=["POST"])
@api_login_required
def run_po_list_fetch():
    data = request.get_json(silent=True) or {}
    history_id  = data.get("history_id")
    vendor_name = data.get("vendor_name", "").strip()
    if not history_id:
        return jsonify({"success": False, "error": "Missing history_id"}), 400
    if not vendor_name:
        return jsonify({"success": False, "error": "Vendor name required"}), 400

    # v16: po_list_fetch (ME2N open-PO lookup by vendor) is also always
    # shared spl_rpa/.env, same reasoning as po_fetch above -- read-only
    # lookup, not an attributable posting.
    job_id = enqueue_rf_job(
        history_id, "po_list_fetch",
        {"vendor_name": vendor_name, "history_id": history_id}
    )
    if not job_id:
        return jsonify({"success": False, "error": "PO list fetch already in queue."}), 409
    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})


@app.route("/api/migo/<int:history_id>")
@api_login_required
def api_get_migo(history_id):
    data = get_migo_entry(history_id)
    return jsonify({"success": True, "data": data}) if data else (jsonify({"success": False}), 404)


@app.route("/api/miro/<int:history_id>")
@api_login_required
def api_get_miro(history_id):
    data = get_miro_entry(history_id)
    return jsonify({"success": True, "data": data}) if data else (jsonify({"success": False}), 404)


@app.route("/api/history/<int:history_id>")
@api_login_required
def api_get_history(history_id):
    data = get_history_by_id(history_id)
    if data:
        for k, v in data.items():
            if hasattr(v, "isoformat"):
                data[k] = v.isoformat()
        return jsonify({"success": True, "data": data})
    return jsonify({"success": False}), 404


# ============================================================
# STORAGE LOCATIONS API
# ============================================================

@app.route("/api/storage_locations")
@api_login_required
def api_storage_locations():
    locations = get_all_storage_locations(active_only=True)
    return jsonify({"success": True, "data": locations})


@app.route("/api/storage_locations/add", methods=["POST"])
@api_login_required
@admin_required
def api_add_storage_location():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()
    description = data.get("description", "").strip()
    if not code or not description:
        return jsonify({"success": False, "error": "Code and description required"}), 400
    return jsonify({"success": add_storage_location(code, description)})


@app.route("/api/storage_locations/update", methods=["POST"])
@api_login_required
@admin_required
def api_update_storage_location():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()
    description = data.get("description", "").strip()
    is_active = data.get("is_active", True)
    if not code:
        return jsonify({"success": False, "error": "Code required"}), 400
    return jsonify({"success": update_storage_location(code, description, is_active)})


# ============================================================
# USER MANAGEMENT
# ============================================================

@app.route("/add_user_web", methods=["POST"])
@api_login_required
@admin_required
def add_user_web():
    data = request.get_json(silent=True) or {}
    username  = data.get("username", "").strip()
    auth_type = (data.get("auth_type", "local") or "local").strip().lower()
    password  = data.get("password", "")
    confirm   = data.get("confirm_password", "")
    role      = data.get("role", "User")
    name      = data.get("name", "").strip()
    email     = data.get("email", "").strip()
    email_notif = bool(data.get("email_notifications_enabled", False))
    step_roles  = (data.get("step_roles", "") or "").strip()
    admin_edit  = bool(data.get("admin_edit", True))

    if auth_type not in ("local", "ldap"):
        return jsonify({"status": False, "message": "Invalid auth_type"}), 400

    # v15: LDAP users need only username + role -- name is optional
    # (defaults to username in add_user()), no password at all, since AD
    # is the credential check at login time, not this app.
    if auth_type == "ldap":
        if not username or not role:
            return jsonify({"status": False, "message": "Username and role required"}), 400
    else:
        if not all([username, password, confirm, role, name]):
            return jsonify({"status": False, "message": "Username, name, role and password required"}), 400
        if password != confirm:
            return jsonify({"status": False, "message": "Passwords do not match"}), 400

    success = add_user(username, password, role, name, email, email_notif, step_roles, admin_edit, auth_type)
    return jsonify({"status": success, "message": "User created" if success else "Failed — username may exist"})


@app.route("/edit_user_web", methods=["POST"])
@api_login_required
@admin_required
def edit_user_web():
    data     = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    confirm  = data.get("confirm_password", "").strip()
    role     = data.get("role", "User")
    name     = data.get("name")
    email    = data.get("email")
    email_notif = data.get("email_notifications_enabled")
    step_roles  = data.get("step_roles")
    admin_edit  = data.get("admin_edit")
    auth_type   = data.get("auth_type")  # None = leave unchanged

    if auth_type is not None and auth_type.strip().lower() not in ("local", "ldap"):
        return jsonify({"status": False, "message": "Invalid auth_type"}), 400
    if password and password != confirm:
        return jsonify({"status": False, "message": "Passwords do not match"}), 400

    if not username:
        return jsonify({"status": False, "message": "Username required"}), 400

    success = update_user(
        username,
        password=password if password else None,
        role=role if role else None,
        email=email,
        email_notifications_enabled=email_notif,
        step_roles=step_roles,
        admin_edit=admin_edit,
        auth_type=auth_type.strip().lower() if auth_type else None,
        name=name
    )
    return jsonify({"status": success, "message": "Updated" if success else "Not found"})


@app.route("/delete_user_web", methods=["POST"])
@api_login_required
@admin_required
def delete_user_web():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    if not username:
        return jsonify({"status": False, "message": "Username required"}), 400
    if username == _current_user():
        return jsonify({"status": False, "message": "Cannot delete own account"}), 403
    target = next((u for u in get_all_users() if u["username"] == username), None)
    if target and target.get("role") == "SuperAdmin":
        return jsonify({"status": False, "message": "Cannot delete SuperAdmin users"}), 403
    return jsonify({"status": delete_user(username), "message": "Deleted"})


# ============================================================
# DOCUMENT FILE SERVING
# ============================================================

@app.route("/download_all_documents/<int:history_id>")
@login_required
def download_all_documents(history_id):
    """
    Zips whichever of invoice/e-way bill/LR files exist for this record and
    sends the archive. The 'Download All Documents' button previously called
    /download_all_documents (no history_id, no matching route at all --
    every click was a 404). This is the first real implementation.
    """
    details = get_history_details_by_id(history_id)
    if not details.get("history"):
        return "Record not found", 404

    doc_sources = {
        "invoice":  details.get("invoice_data"),
        "ewaybill": details.get("ewaybill_data"),
        "lr":       details.get("lr_data"),
    }

    buf = io.BytesIO()
    added_any = False
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doctype, data in doc_sources.items():
            filename = (data or {}).get("filename")
            if not filename:
                continue
            file_path = _find_file(filename)
            if file_path:
                zf.write(file_path, arcname=filename)
                added_any = True

    if not added_any:
        return "No documents available for this record", 404

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"documents-history-{history_id}.zip"
    )


@app.route("/view_document/<doctype>/<filename>")
@login_required
def view_document(doctype, filename):
    file_path = _find_file(filename)
    if not file_path:
        return "File not found", 404
    return send_file(file_path, mimetype="application/pdf")


@app.route("/download_document/<doctype>/<filename>")
@login_required
def download_document(doctype, filename):
    file_path = _find_file(filename)
    if not file_path:
        return "File not found", 404
    return send_file(file_path, as_attachment=True, download_name=filename)


@app.route("/get_document_thumbnail/<doctype>/<filename>")
@login_required
def get_document_thumbnail(doctype, filename):
    file_path = _find_file(filename)
    if not file_path:
        return "File not found", 404

    # Cache key includes mtime so a re-uploaded/re-run-OCR file (same name,
    # new content) invalidates automatically instead of serving a stale image.
    try:
        mtime = int(os.path.getmtime(file_path))
    except OSError:
        mtime = 0
    cache_key = hashlib.sha1(f"{filename}:{mtime}".encode("utf-8")).hexdigest()
    cache_path = os.path.join(THUMBNAIL_CACHE_FOLDER, f"{cache_key}.jpg")

    if os.path.exists(cache_path):
        return send_file(cache_path, mimetype="image/jpeg")

    try:
        import fitz
        doc = fitz.open(file_path)
        # The preview box (documents.html) is only ~250px tall — 150 DPI was
        # rendering a full-resolution page (1-1.6MB PNG) for that, on every
        # single request, with no caching. 72 DPI + JPEG is plenty for a
        # thumbnail and cuts payload size roughly 15-20x.
        pix = doc[0].get_pixmap(dpi=72)
        img = pix.tobytes("jpg", jpg_quality=70)
        doc.close()
        with open(cache_path, "wb") as f:
            f.write(img)
        return Response(img, mimetype="image/jpeg")
    except Exception as e:
        logger.error(f"Thumbnail error {filename}: {e}")
        return str(e), 500


@app.route("/delete_document/<doctype>/<filename>", methods=["DELETE"])
@api_login_required
def delete_document(doctype, filename):
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"success": False, "error": "Invalid filename"}), 400

    file_path = _find_file(filename)
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            logger.error(f"Failed to delete file {filename}: {e}")
            return jsonify({"success": False, "error": "Could not delete file"}), 500

    match = re.match(r"h(\d+)_", filename)
    if match:
        history_id = int(match.group(1))
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    if doctype == "invoice":   cur.execute("DELETE FROM invoice_data WHERE id = %s", (history_id,))
                    elif doctype == "ewaybill": cur.execute("DELETE FROM ewaybill_data WHERE id = %s", (history_id,))
                    elif doctype == "lr":       cur.execute("DELETE FROM lr_data WHERE id = %s", (history_id,))
        except Exception as e:
            logger.error(f"Failed to clear DB data: {e}")

    return jsonify({"success": True, "message": "Document deleted"})


@app.route("/delete_all_documents/<int:history_id>", methods=["DELETE"])
@api_login_required
def delete_all_documents(history_id):
    """
    'Delete All Documents' button called deleteAllDocuments() with no
    matching JS function and no backend route -- clicking it did nothing
    but throw a console ReferenceError. First real implementation.
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    details = get_history_details_by_id(history_id)
    if not details.get("history"):
        return jsonify({"success": False, "error": "Record not found"}), 404

    doc_sources = {
        "invoice":  details.get("invoice_data"),
        "ewaybill": details.get("ewaybill_data"),
        "lr":       details.get("lr_data"),
    }

    deleted_any = False
    for doctype, data in doc_sources.items():
        filename = (data or {}).get("filename")
        if not filename:
            continue
        file_path = _find_file(filename)
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                deleted_any = True
            except Exception as e:
                logger.error(f"Failed to delete file {filename}: {e}")

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM invoice_data WHERE id = %s", (history_id,))
                cur.execute("DELETE FROM ewaybill_data WHERE id = %s", (history_id,))
                cur.execute("DELETE FROM lr_data WHERE id = %s", (history_id,))
    except Exception as e:
        logger.error(f"Failed to clear DB data for history_id={history_id}: {e}")
        return jsonify({"success": False, "error": "Files removed but DB cleanup failed"}), 500

    return jsonify({"success": True, "message": "All documents deleted", "deleted": deleted_any})


@app.route("/api/dms_upload/retry/<int:history_id>", methods=["POST"])
@api_login_required
def api_dms_upload_retry(history_id):
    """
    Manual retry for a record whose consolidated PDF didn't make it to DMS
    on its own (dms_status stuck at 'staged'/'pending', or the earlier
    automatic dms_upload RF job simply failed/timed out). Enqueues the
    exact same "dms_upload" RF-queue step _enqueue_dms_upload() uses
    automatically after Gate In (for without_po flows) or PO Fetch (for
    with_po flows, itself chained off Gate In) -- run_dms_upload() (see
    _process_dms_upload in rf_queue_worker.py) always processes the WHOLE
    staging folder and skips files already uploaded, so re-running it here
    is safe and can't double-upload or disturb any other pending record.
    enqueue_rf_job() itself blocks duplicate submission if a dms_upload
    job for this history_id is already pending/running.

    FIX: this used to gate on history.migo_103, a stale carryover from
    before v18 moved the DMS staging+upload trigger to right after Gate In
    -- meaning a DMS upload failure between Gate In and MIGO 103 had no
    retry path at all until MIGO 103 happened, even though the document
    was already staged and ready well before that. Gated on gate_in now,
    matching what actually triggers staging.
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked

    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if not history.get("gate_in"):
        return jsonify({"success": False, "error": "DMS upload only runs after Gate In is done."}), 400
    if history.get("dms_status") == "uploaded":
        return jsonify({"success": False, "error": "This record's document is already uploaded to DMS."}), 400

    job_id = enqueue_rf_job(history_id, "dms_upload", {"history_id": history_id})
    if not job_id:
        return jsonify({"success": False, "error": "A DMS upload is already queued or running for this record."}), 409
    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})


@app.route("/api/dms/sync_link/<int:history_id>", methods=["POST"])
@api_login_required
def api_dms_sync_link(history_id):
    """
    FIX: closes a real contradiction seen on the Documents tab -- a record
    can have history.dms_status == 'uploaded' (the upload robot posted
    successfully) while dms_document_links has no row for it yet, because
    the link-import step (services/dms_links_import.py) runs as its own
    pass right after the upload and can miss a row (unmatched filename,
    a transient error, etc. -- see that module's own imported/unmatched/
    errors counters). Previously the Documents tab had no way to tell
    these two states apart: it showed "not yet uploaded" + a Retry Upload
    button purely because dms_document_link was missing, but clicking that
    button hit this exact route's sibling above, which correctly refuses
    because dms_status really does already say 'uploaded' -- so the user
    saw "not uploaded, click here" immediately followed by "already
    uploaded" on the same click. Re-uploading was never the right fix for
    this state anyway (the file's already in Contentverse; doing it again
    risks a duplicate) -- the actual fix is re-running the link import,
    which is safe to repeat any time (upsert keyed on filename, see that
    module's own docstring) and doesn't touch SAP, Contentverse, or the
    upload robot at all, just re-reads the links Excel file.
    """
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    from services.dms_links_import import run_dms_links_import
    try:
        summary = run_dms_links_import()
    except Exception as e:
        logger.error(f"DMS link sync failed for history_id={history_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"Link sync failed: {e}"}), 500

    link = get_dms_document_link(history_id)
    if link:
        return jsonify({"success": True, "document_link": link, "summary": summary})
    return jsonify({
        "success": False,
        "error": (
            "Link sync ran but this record's document still wasn't found in "
            "the DMS links file. It may not have been exported by Contentverse "
            "yet, or the filename didn't match — check with DMS/IT if this "
            "persists."
        ),
        "summary": summary
    }), 404


@app.route("/api/dms/regen_link/<int:history_id>", methods=["POST"])
@api_login_required
def api_dms_regen_link(history_id):
    """
    Runs dms_upload.robot's "Generate Link For Single Document" test case
    for this one record -- the next step after Sync Link (above) has
    already run and genuinely found nothing, meaning the link-generation
    step itself never wrote a row for this file the first time (not just
    that the DB import hadn't caught up). Read-only from Contentverse's
    side: it doesn't re-upload, re-index, or move anything, it just
    re-fetches a share link for a document that's already there.

    Queued through the normal RF job queue (same robot_lock the batch
    upload uses, via services/dms_upload_runner.py's run_dms_link_regen)
    so it can't collide with SAP jobs or a batch upload running at the
    same time.
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked

    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    doc_path = history.get("consolidated_doc_path") or ""
    if not doc_path:
        return jsonify({
            "success": False,
            "error": "No consolidated document on file for this record — nothing to regenerate a link for."
        }), 400

    target_file_name = os.path.splitext(os.path.basename(doc_path))[0]

    job_id = enqueue_rf_job(history_id, "dms_link_regen", {"target_file_name": target_file_name})
    if not job_id:
        return jsonify({"success": False, "error": "A link regeneration is already queued or running for this record."}), 409
    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})


# ============================================================
# PENDING PO UPDATES (v17) — zgatein_update, decoupled from MIGO 103.
# See database/pending_po_operations.py and rf_queue_worker.py's
# _process_migo_103/_process_update_gatein_po for the background.
# ============================================================
from database.pending_po_operations import (
    get_pending_po_updates_for_user, get_pending_po_update
)


@app.route("/api/pending_po_updates")
@api_login_required_view_only
def api_pending_po_updates():
    """
    List pending PO backfills the current user should see on the History
    page's panel. Deliberately view_only (not gated behind an assigned
    role) for the same reason /history itself is -- reading this list is
    harmless; the actual /run route below still requires the gate_in role.
    SuperAdmin sees every pending row regardless of target as a backstop.
    """
    username = session.get("username")
    items = get_pending_po_updates_for_user(username, is_superadmin=_is_superadmin())
    for it in items:
        if it.get("requested_at"):
            it["requested_at"] = it["requested_at"].strftime("%Y-%m-%d %H:%M")
    return jsonify({"success": True, "items": items})


@app.route("/api/pending_po_updates/<int:history_id>/run", methods=["POST"])
@api_login_required
def api_pending_po_updates_run(history_id):
    """
    Triggered by "Update PO" on the History page panel. Runs
    zgatein_update under the CALLING user's own live session credential --
    same _enqueue_sap_job path every other posting route uses, so there is
    no window where anyone's credential is held past this immediate
    submission.
    """
    blocked = _require_role_edit("gate_in")
    if blocked:
        return blocked

    item = get_pending_po_update(history_id)
    if not item:
        return jsonify({"success": False, "error": "No pending PO update for this record."}), 404

    # Visibility check mirrors api_pending_po_updates() above: the
    # target_username owner or, for legacy records with no captured
    # submitter, any gate_in-role user. SuperAdmin always allowed through.
    target = item.get("target_username")
    if target and target != session.get("username") and not _is_superadmin():
        return jsonify({
            "success": False,
            "error": "This pending PO update is assigned to a different user."
        }), 403

    job_id, err = _enqueue_sap_job(history_id, "update_gatein_po", {
        "gate_in_number": item["gate_in_number"],
        "po_number":      item["po_number"],
        "history_id":     history_id,
        # v17: read back by _process_update_gatein_po to mark the
        # pending_po_updates row resolved by whoever actually ran this.
        "_submitted_by_username": session.get("username"),
    })
    if err:
        return err
    if not job_id:
        return jsonify({"success": False, "error": "Already processing."}), 409
    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})


# ============================================================
# ============================================================
# GST APPROVAL ROUTES
# ============================================================
from database.gst_operations import (
    get_gst_approval, approve_gst, hold_gst, reset_gst_for_rerun,
    list_gst_verification_status
)
from services.gst_runner import trigger_async, is_running
from database.remarks_operations import get_remark, upsert_remark, get_comments, add_comment


@app.route("/api/gst/status/<int:history_id>")
@api_login_required
def api_gst_status(history_id):
    """
    Read-only status check -- called once on GST Approval tab load, and
    every 5 s WHILE a run is actively in progress (never to start one).

    v27 (2026-08-14, client request): this used to also call
    trigger_async(history_id) on every call, which meant simply opening
    the tab silently started the bot. GST verification is now strictly
    on-demand -- it only ever starts from api_gst_run/api_gst_bulk_run
    (fresh) or api_gst_rerun (reset + restart), both explicit button
    clicks. This endpoint just reports whatever the current state is:
      - "not_run"  -- no gst_approval row exists yet, nothing running
      - "checking" -- a bot thread is actively running for this record
      - "done"     -- a stored result exists (success, partial, or error)
    """
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    if is_running(history_id):
        return jsonify({"status": "checking"})

    row = get_gst_approval(history_id)
    if not row:
        return jsonify({"status": "not_run"})

    data = {}
    for k, v in row.items():
        if hasattr(v, "strftime"):
            data[k] = v.strftime("%d-%m-%Y %H:%M")
        else:
            data[k] = v
    data["status"] = "done"
    return jsonify(data)


@app.route("/api/gst/run/<int:history_id>", methods=["POST"])
@api_login_required
def api_gst_run(history_id):
    """
    First-time, explicit "Run GST Verification" trigger -- the ONLY way
    (along with api_gst_bulk_run below) GST verification ever starts now
    that every auto-trigger has been removed (v27, 2026-08-14). Refuses
    if a result already exists for this record -- that's what Re-run
    (api_gst_rerun above) is for, so the reset-and-restart safety checks
    there always apply once a row exists.
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    if is_running(history_id):
        return jsonify({
            "success": False,
            "error": "A GST check is already in progress for this record — please wait for it to finish."
        }), 409

    if get_gst_approval(history_id):
        return jsonify({
            "success": False,
            "error": "GST verification has already been run for this record — use Re-run instead."
        }), 400

    trigger_async(history_id)
    return jsonify({"success": True, "message": "GST verification started"})


@app.route("/api/gst/bulk_run", methods=["POST"])
@api_login_required
def api_gst_bulk_run():
    """
    Bulk trigger from the GST Verification admin page's multi-select --
    accepts a list of history_ids and, for EACH one independently, picks
    the same action a single Run/Re-run click would: fresh trigger_async()
    if no gst_approval row exists yet, or reset_gst_for_rerun()+
    trigger_async(force=True) if one does. Records that are currently
    running or already approved are silently skipped (not errors -- a
    mixed batch selection is expected/normal), same rules api_gst_run and
    api_gst_rerun already enforce individually, just applied per-id here
    instead of failing the whole batch over one ineligible record.

    Actual browser concurrency is still capped by gst_runner.py's slot
    pools (2 e-invoice + 2 taxpayer-search slots) regardless of how many
    ids are submitted here -- this just fires them all off, the existing
    pool naturally queues the rest.
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    body = request.get_json(silent=True) or {}
    ids = body.get("history_ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"success": False, "error": "No records selected."}), 400

    results = {}
    for hid in ids:
        try:
            hid = int(hid)
        except (TypeError, ValueError):
            continue
        history = get_history_by_id(hid)
        if not history:
            results[hid] = "not_found"
            continue
        if is_running(hid):
            results[hid] = "skipped_running"
            continue
        row = get_gst_approval(hid)
        if row and row.get("approval_status") == "approved":
            results[hid] = "skipped_approved"
            continue
        if row:
            reset_gst_for_rerun(hid)
            trigger_async(hid, force=True)
            results[hid] = "rerun_started"
        else:
            trigger_async(hid)
            results[hid] = "run_started"

    return jsonify({"success": True, "results": results})


@app.route("/gst_verification")
@login_required
def gst_verification_page():
    """
    v27 (2026-08-14, client request): admin page listing every GST-eligible
    record (has invoice data) with its verification status, and letting
    Compliance/SuperAdmin multi-select and bulk-run/re-run. Same access
    rule as the GST actions themselves (_has_role already returns True for
    any SuperAdmin, view-only or not -- editing/running is still gated per
    action by _require_role_edit inside the API routes below).
    """
    if not _has_role("compliance"):
        return redirect(url_for("history_page"))
    return render_template(
        "gst_verification_admin.html",
        username=session.get("username"),
        role=session.get("role"),
    )


@app.route("/api/gst/verification_list")
@api_login_required
def api_gst_verification_list():
    if not _has_role("compliance"):
        return jsonify({"success": False, "error": "Permission denied."}), 403
    search = request.args.get("q", "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    result = list_gst_verification_status(search=search, page=page, per_page=50)

    records = []
    for r in result["records"]:
        hid = r["id"]
        if is_running(hid):
            status = "checking"
        elif r.get("gst_approval_status") is None and r.get("checked_at") is None:
            status = "not_run"
        elif r.get("gst_approval_status") == "approved":
            status = "approved"
        elif r.get("bot_error"):
            status = "needs_rerun"
        else:
            status = "pending_review"
        records.append({
            "id": hid,
            "invoice_number": r.get("invoice_number"),
            "seller_name": r.get("seller_name"),
            "seller_gstin": r.get("seller_gstin"),
            "created_at": r["created_at"].strftime("%d-%m-%Y %H:%M") if r.get("created_at") else None,
            "checked_at": r["checked_at"].strftime("%d-%m-%Y %H:%M") if r.get("checked_at") else None,
            "status": status,
        })

    return jsonify({"success": True, "total": result["total"], "records": records})


@app.route("/api/gst/approve/<int:history_id>", methods=["POST"])
@api_login_required
def api_gst_approve(history_id):
    """Approve the GST verification for this record, unlocking Gate In."""
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    row = get_gst_approval(history_id)
    if not row:
        return jsonify({"success": False, "error": "GST check not run yet"}), 400
    if row.get("approval_status") == "approved":
        return jsonify({"success": False, "error": "Already approved"}), 400

    user = _current_user()
    if not approve_gst(history_id, user):
        return jsonify({"success": False, "error": "DB update failed"}), 500

    return jsonify({"success": True, "message": "GST approved", "approval_by": user})


@app.route("/api/gst/hold/<int:history_id>", methods=["POST"])
@api_login_required
def api_gst_hold(history_id):
    """Place the GST verification on hold. Reason is optional."""
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    row = get_gst_approval(history_id)
    if not row:
        return jsonify({"success": False, "error": "GST check not run yet"}), 400

    body   = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    user   = _current_user()

    if not hold_gst(history_id, user, reason):
        return jsonify({"success": False, "error": "DB update failed"}), 500

    return jsonify({"success": True, "message": "GST placed on hold", "held_by": user})


@app.route("/api/gst/rerun/<int:history_id>", methods=["POST"])
@api_login_required
def api_gst_rerun(history_id):
    """
    Re-run GST verification — resets existing results and fires bots again.
    Used when the user suspects the extracted GSTIN was wrong.
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    # Hard block on an approved record. Previously missing entirely --
    # reset_gst_for_rerun() unconditionally sets approval_status back to
    # 'pending' and clears approval_by/approval_at, so clicking this button
    # on an already-approved record would have silently un-approved it with
    # no warning. A human decision to approve should never be reversible by
    # a re-run click. (trigger_async() in gst_runner.py enforces this same
    # rule at the bot-trigger level too, as a second line of defense -- this
    # check exists so the user gets a clear error instead of the DB reset
    # happening first and the bot trigger only silently declining after.)
    row = get_gst_approval(history_id)
    if row and row.get("approval_status") == "approved":
        return jsonify({"success": False, "error": "Record is already approved — re-run is disabled."}), 403

    # Refuse if a bot run is already in progress for this record. Previously
    # missing: reset_gst_for_rerun() below wipes the gst_approval row back to
    # blank/pending and re-locks Gate In (gst_check=0) UNCONDITIONALLY, with
    # no check for whether a bot thread was already actively running. Two
    # Re-run clicks close together -- e.g. an impatient double-click, or a
    # second click while the first run's 30-90s bot cycle is still going --
    # would wipe the row out from under the still-running first attempt,
    # which then finishes later and overwrites that reset with its own
    # result anyway. trigger_async(force=True)'s own _running check silently
    # no-ops the second bot launch, so no second thread actually starts --
    # but the DB reset and the "re-verification started" response already
    # happened, telling the user something restarted when nothing did.
    # Checking is_running() first stops the reset from ever firing in that
    # case, and gives the user an honest, specific reason instead.
    if is_running(history_id):
        return jsonify({
            "success": False,
            "error": "A GST check is already in progress for this record — please wait for it to finish."
        }), 409

    if not reset_gst_for_rerun(history_id):
        return jsonify({"success": False, "error": "DB reset failed"}), 500

    trigger_async(history_id, force=True)
    return jsonify({"success": True, "message": "GST re-verification started"})


@app.route("/api/gst/screenshot/<int:history_id>/<portal>")
@login_required
def api_gst_screenshot(history_id, portal):
    """Serve the portal screenshot PNG stored on disk."""
    row = get_gst_approval(history_id)
    if not row:
        return "Not found", 404

    if portal == "einvoice":
        path = row.get("einvoice_screenshot") or ""
    elif portal == "taxpayer":
        path = row.get("taxpayer_screenshot") or ""
    else:
        return "Invalid portal", 400

    if not path or not os.path.isfile(path):
        return "Screenshot not found on disk", 404

    return send_file(path, mimetype="image/png")


# ============================================================
# REMARKS & COMMENTS
# One record-wide Remark (write-once: set by Compliance, then permanently
# locked -- see api_save_remark below) plus a full, append-only comment
# history (every post kept, each tagged with who posted it -- see
# database/remarks_operations.py). Rendered by the shared
# templates/tabs/_remarks_panel.html partial, included at the bottom of
# every tab so it's visible regardless of which tab is active.
# ============================================================

@app.route("/api/remarks/<int:history_id>")
@login_required
def api_get_remarks(history_id):
    """
    Read-only: returns the Remark (plus a `locked` flag -- true once a
    Remark has ever been saved, since it can never be edited or replaced
    after that) and the full chronological comment history, each comment
    tagged with both its role and the individual username who posted it.
    Anyone who can view the record at all can read this -- same visibility
    as the record itself, no per-role gating on reads.
    """
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    remark = get_remark(history_id)
    comments = get_comments(history_id)

    def _fmt(dt):
        return dt.strftime("%d-%m-%Y %H:%M") if hasattr(dt, "strftime") else dt

    return jsonify({
        "success": True,
        "remark": {
            "text": (remark or {}).get("remark_text") or "",
            "updated_by_role": (remark or {}).get("updated_by_role") or "",
            "updated_at": _fmt((remark or {}).get("updated_at")) if remark else None,
            "locked": bool(remark and (remark.get("remark_text") or "").strip()),
        },
        "comments": [
            {
                "role": c["role"],
                "username": c.get("username") or "",
                "text": c["comment_text"],
                "updated_at": _fmt(c["created_at"]),
            }
            for c in comments
        ],
    })


@app.route("/api/remarks/<int:history_id>", methods=["POST"])
@api_login_required
def api_save_remark(history_id):
    """
    Set the single record-wide Remark. Gated to the Compliance role (or a
    SuperAdmin with edit rights) -- same rule as every other field
    Compliance owns on Extracted Data / GST Approval, since the Remark is
    meant to be authored by whoever is reviewing those two tabs.

    Write-once: once a Remark has ever been saved for this record, it is
    permanently locked -- this call is rejected outright, regardless of
    role. Nobody (including SuperAdmin) can edit or replace it afterwards;
    later reviewers use Comments to add their own input instead. This
    mirrors the route-layer-enforces-locks convention used elsewhere in
    this codebase (e.g. goods_delivery_mode, ewb_exemption_reasons) --
    the db layer (upsert_remark) still technically supports overwriting,
    but the route never lets a second write through.
    """
    blocked = _require_role_edit("compliance")
    if blocked:
        return blocked
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    existing = get_remark(history_id)
    if existing and (existing.get("remark_text") or "").strip():
        return jsonify({"success": False, "error": "Remark already set — it cannot be edited or replaced. Add a Comment instead."}), 403

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "error": "Remark cannot be empty"}), 400
    if len(text) > 1000:
        return jsonify({"success": False, "error": "Remark is too long (max 1000 characters)"}), 400

    role_tag = "SuperAdmin" if _is_superadmin() else "compliance"
    if not upsert_remark(history_id, text, role_tag, _current_user()):
        return jsonify({"success": False, "error": "DB update failed"}), 500

    return jsonify({"success": True, "message": "Remark saved"})


@app.route("/api/comments/<int:history_id>", methods=["POST"])
@api_login_required
def api_add_comment(history_id):
    """
    Add (or, from the UI's point of view, overwrite) the current user's
    role's comment. Role is never trusted from the client body as-is --
    a SuperAdmin with edit rights always posts as "SuperAdmin" regardless
    of what's sent; a regular user must hold the role they're posting as
    (their own current_roles_list), so nobody can post a comment
    attributed to a role they don't actually have.
    """
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "error": "Comment cannot be empty"}), 400
    if len(text) > 500:
        return jsonify({"success": False, "error": "Comment is too long (max 500 characters)"}), 400

    if _is_superadmin():
        if not _admin_can_edit():
            return jsonify({"success": False, "error": "View-only admin access — editing disabled."}), 403
        role_tag = "SuperAdmin"
    else:
        requested_role = (body.get("role") or "").strip()
        my_roles = _current_roles()
        if not my_roles:
            return jsonify({"success": False, "error": "Your account has no assigned role to comment as."}), 403
        if requested_role and requested_role in my_roles:
            role_tag = requested_role
        elif len(my_roles) == 1:
            role_tag = next(iter(my_roles))
        else:
            return jsonify({
                "success": False,
                "error": "You hold more than one role — please specify which role to comment as."
            }), 400

    if not add_comment(history_id, role_tag, text, _current_user()):
        return jsonify({"success": False, "error": "DB update failed"}), 500

    return jsonify({"success": True, "message": "Comment saved", "role": role_tag})


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(e):
    if request.accept_mimetypes.accept_json:
        return jsonify({"error": "Not found"}), 404
    return redirect(url_for("history_page"))


@app.errorhandler(500)
def server_error(e):
    logger.error(f"500: {e}")
    if request.accept_mimetypes.accept_json:
        return jsonify({"error": "Server error"}), 500
    return redirect(url_for("history_page"))


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    app.run(
        host=config.HOST,
        port=config.PORT,
        debug=(config.ENV == "development"),
        use_reloader=False
    )
