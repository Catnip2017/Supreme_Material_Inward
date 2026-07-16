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
    update_invoice_irn
)
from database.vehicle_master_operations import get_drivers_by_truck
from database.supplier_operations import search_suppliers
from database.gatein_operations import (
    upsert_gatein_entry, get_gatein_entry, map_ocr_to_gatein
)
from database.migo_operations import (
    upsert_migo_entry, save_migo_105_fields,
    get_migo_entry, map_ocr_to_migo,
    update_migo_105_items_with_batches
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
from services.extract import process_document
from services.rf_queue_worker import start_worker
from services.mail_service import send_approval_notification

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
# DECORATORS / HELPERS
# ============================================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
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
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "Admin":
            return jsonify({"success": False, "error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


def _current_user() -> str:
    return session.get("username", "unknown")


def _is_admin() -> bool:
    return session.get("role") == "Admin"


def _check_step_allowed(history: dict, step: str) -> tuple:
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


def _run_ocr_and_save(doctype: str, file_path: str, filename: str, history_id: int) -> bool:
    try:
        extracted = process_document(doctype, file_path, filename)
        if not extracted:
            _move_file(file_path, config.UPLOAD_FAILED_FOLDER)
            return False
        extracted["filename"] = filename
        if doctype == "invoice":
            save_invoice_to_db(history_id, extracted)
            # Auto-start GST verification as soon as invoice (seller_gstin) is saved
            try:
                from services.gst_runner import trigger_async as _gst_trigger
                _gst_trigger(history_id)
            except Exception as _gst_err:
                logger.warning(f"GST auto-trigger failed (non-fatal): {_gst_err}")
        elif doctype == "ewaybill":
            save_ewaybill_to_db(history_id, extracted)
        elif doctype == "lr":
            save_lr_to_db(history_id, extracted)
        _move_file(file_path, config.UPLOAD_PROCESSED_FOLDER)
        return True
    except Exception as e:
        logger.error(f"OCR error for {doctype}: {e}", exc_info=True)
        _move_file(file_path, config.UPLOAD_FAILED_FOLDER)
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
        "current_role": session.get("role", ""),
        "current_username": session.get("username", ""),
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
            session["step_roles"] = user.get("step_roles", "all")
            logger.info(f"Login: {username} ({user['role']})")
            return redirect(url_for("history_page"))
        logger.warning(f"Failed login: {username}")
        return render_template("login.html", error="Invalid username or password")
    return render_template("login.html")


@app.route("/logout")
def logout():
    logger.info(f"Logout: {session.get('username')}")
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# PAGE ROUTES
# ============================================================

@app.route("/history")
@login_required
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
@api_login_required
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
@login_required
def view_detail(history_id):
    try:
        details = get_history_details_by_id(history_id)
        history = details.get("history")
        if not history:
            return redirect(url_for("history_page"))

        gatein_data = get_gatein_entry(history_id) or {}
        migo_data   = get_migo_entry(history_id)   or {}
        miro_data   = get_miro_entry(history_id)   or {}

        if history.get("gate_in_number") and not migo_data.get("migo_header_text"):
            migo_data["migo_header_text"] = history["gate_in_number"]
        if history.get("material_doc_number"):
            migo_data["material_doc_number"] = history["material_doc_number"]

        po_data = get_po_line_items(history_id)

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

        return render_template(
            "index.html",
            history=history,
            history_id=history_id,
            invoice_data=details.get("invoice_data"),
            ewaybill_data=details.get("ewaybill_data"),
            ewb_expired=ewb_expired,
            lr_data=details.get("lr_data"),
            gatein_data=gatein_data,
            migo_data=migo_data,
            miro_data=miro_data,
            po_data=po_data,
            username=session.get("username"),
            role=session.get("role"),
            from_history=True
        )
    except Exception as e:
        logger.error(f"view_detail error {history_id}: {e}", exc_info=True)
        return redirect(url_for("history_page"))


@app.route("/new_entry")
@login_required
def new_entry():
    if not _is_admin() and not config.ALLOW_USER_UPLOAD:
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
    if not _is_admin():
        return redirect(url_for("history_page"))
    users = get_all_users()
    storage_locations = get_all_storage_locations(active_only=False)
    return render_template(
        "user_management.html",
        users=users,
        storage_locations=storage_locations,
        username=session.get("username"),
        current_username=session.get("username"),
        role=session.get("role")
    )


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
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404
    if (history.get("approval_status") or "pending") == "approved":
        return jsonify({"success": False, "error": "Record already approved — editing locked."}), 403

    data = request.get_json(silent=True) or {}
    if save_invoice_to_db(history_id, data):
        _auto_populate_form_tables(history_id)
        return jsonify({"success": True, "message": "Invoice data saved"})
    return jsonify({"success": False, "error": "Failed to save"}), 500


@app.route("/api/save_extracted_eway/<int:history_id>", methods=["POST"])
@api_login_required
def save_extracted_eway(history_id):
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
# APPROVE / HOLD
# ============================================================

@app.route("/api/approve/<int:history_id>", methods=["POST"])
@api_login_required
def api_approve(history_id):
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

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

    for filename in os.listdir(failed_path):
        if not filename.lower().endswith(".pdf"):
            continue
        file_path = os.path.join(failed_path, filename)
        # Detect doc type from filename
        from services.folder_watcher import _detect_doc_type
        doc_type = _detect_doc_type(filename)
        if not doc_type:
            continue
        try:
            extracted = process_document(doc_type, file_path, filename)
            if extracted:
                extracted["filename"] = filename
                if doc_type == "invoice":   save_invoice_to_db(history_id, extracted)
                elif doc_type == "ewaybill": save_ewaybill_to_db(history_id, extracted)
                elif doc_type == "lr":       save_lr_to_db(history_id, extracted)
                files_processed += 1
        except Exception as e:
            logger.error(f"Re-run OCR error: {e}")

    if files_processed > 0:
        _auto_populate_form_tables(history_id)
        set_ocr_status(history_id, "success")
        return jsonify({"success": True, "message": f"OCR retry succeeded — {files_processed} document(s)", "retry_count": retry_count})

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
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    valid_types = ["invoice", "ewaybill", "lr"]
    if doctype not in valid_types:
        return jsonify({"error": f"Invalid document type: {doctype}"}), 400
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    filename = file.filename
    file_path = os.path.join(config.UPLOAD_FOLDER, filename)
    file.save(file_path)

    try:
        history_id = session.get("current_history_id")
        if not history_id:
            history_id = create_history_record()
            session["current_history_id"] = history_id

        if not _run_ocr_and_save(doctype, file_path, filename, history_id):
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
    if not _is_admin() and not config.ALLOW_USER_UPLOAD:
        return jsonify({"error": "Admin access required"}), 403

    files = {
        "invoice":  request.files.get("invoice"),
        "ewaybill": request.files.get("ewaybill"),
        "lr":       request.files.get("lr")
    }
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
        results[doctype] = _run_ocr_and_save(doctype, file_path, filename, history_id)

    session["current_history_id"] = history_id
    _auto_populate_form_tables(history_id)
    if any(results.values()):
        set_ocr_status(history_id, "success")
    else:
        set_ocr_status(history_id, "failed")

    return jsonify({"success": True, "history_id": history_id, "results": results})


# ============================================================
# WORKFLOW ENDPOINTS — Gate In / MIGO 103 / MIGO 105 / MIRO
# ============================================================

@app.route("/save_gatein", methods=["POST"])
@api_login_required
def save_gatein():
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

    upsert_gatein_entry(history_id, data)
    po_flow_type = (data.get("po_flow_type") or "truck_with_po").strip()
    set_po_flow_type(history_id, po_flow_type)
    job_id = enqueue_rf_job(history_id, "gate_in", data)
    if not job_id:
        return jsonify({"success": False, "error": "Gate In already processing."}), 409
    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})


@app.route("/api/run_migo_103", methods=["POST"])
@api_login_required
def run_migo_103():
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

    upsert_migo_entry(history_id, data)
    job_id = enqueue_rf_job(history_id, "migo_103", data)
    if not job_id:
        return jsonify({"success": False, "error": "MIGO 103 already processing."}), 409
    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})

@app.route("/api/run_migo_105", methods=["POST"])
@api_login_required
def run_migo_105():
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

    job_id = enqueue_rf_job(history_id, "migo_105", rf_payload)
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

    upsert_miro_entry(history_id, data)
    details = get_history_details_by_id(history_id)
    inv = details.get("invoice_data") or {}
    invoice_number = inv.get("invoice_number") or data.get("miroReference") or ""

    rf_payload = {
        "miroReference":     invoice_number,
        "miroInvoiceDate":   data.get("miroInvoiceDate") or inv.get("invoice_date") or "",
        "miroPurchaseOrder": data.get("miroPurchaseOrder") or inv.get("po_number") or "",
    }

    job_id = enqueue_rf_job(history_id, "miro", rf_payload)
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
        candidates = search_suppliers(query, limit=10)
        return jsonify({"success": True, "candidates": candidates, "count": len(candidates)})
    except Exception as e:
        logger.error(f"vendor_lookup error name={query}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/set_po_flow_type/<int:history_id>", methods=["POST"])
@api_login_required
def api_set_po_flow_type(history_id):
    """Manually set po_flow_type on a history record (used from Gate In tab)."""
    data = request.get_json(silent=True) or {}
    po_flow_type = (data.get("po_flow_type") or "").strip()
    valid = {"truck_with_po", "truck_without_po", "hand_with_po", "hand_without_po"}
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
    username = data.get("username", "").strip()
    password = data.get("password", "")
    confirm  = data.get("confirm_password", "")
    role     = data.get("role", "User")
    name     = data.get("name", "").strip()
    email    = data.get("email", "").strip()
    email_notif = bool(data.get("email_notifications_enabled", False))
    step_roles  = data.get("step_roles", "all").strip() or "all"

    if not all([username, password, confirm, role, name]):
        return jsonify({"status": False, "message": "Username, name, role and password required"}), 400
    if password != confirm:
        return jsonify({"status": False, "message": "Passwords do not match"}), 400

    success = add_user(username, password, role, name, email, email_notif, step_roles)
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
    email    = data.get("email")
    email_notif = data.get("email_notifications_enabled")
    step_roles  = data.get("step_roles")

    if not username:
        return jsonify({"status": False, "message": "Username required"}), 400
    if password and password != confirm:
        return jsonify({"status": False, "message": "Passwords do not match"}), 400

    success = update_user(
        username,
        password=password if password else None,
        role=role if role else None,
        email=email,
        email_notifications_enabled=email_notif,
        step_roles=step_roles
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


# ============================================================
# ============================================================
# GST APPROVAL ROUTES
# ============================================================
from database.gst_operations import (
    get_gst_approval, approve_gst, hold_gst, reset_gst_for_rerun
)
from services.gst_runner import trigger_async, is_running


@app.route("/api/gst/status/<int:history_id>")
@api_login_required
def api_gst_status(history_id):
    """
    Poll endpoint called every 5 s by the GST Approval tab.
    Triggers bots on first call if not already running.
    Returns {"status":"checking"} while bots run, then the full
    gst_approval row (plus status="done") when complete.
    """
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    trigger_async(history_id)

    if is_running(history_id):
        return jsonify({"status": "checking"})

    row = get_gst_approval(history_id)
    if not row:
        return jsonify({"status": "checking"})

    data = {}
    for k, v in row.items():
        if hasattr(v, "strftime"):
            data[k] = v.strftime("%d-%m-%Y %H:%M")
        else:
            data[k] = v
    data["status"] = "done"
    return jsonify(data)


@app.route("/api/gst/approve/<int:history_id>", methods=["POST"])
@api_login_required
def api_gst_approve(history_id):
    """Approve the GST verification for this record, unlocking Gate In."""
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


@app.route("/api/gst/save_irn/<int:history_id>", methods=["POST"])
@api_login_required
def api_gst_save_irn(history_id):
    """
    Save an edited IRN (Invoice Reference Number) from the GST Approval
    tab. Targeted update of invoice_data.irn only -- see
    database.db_operations.update_invoice_irn(). Editable until GST
    approval_status becomes 'approved' (server-side lock, mirrors the
    client-side check that also blocks the Approve button on an
    unsaved edit).
    """
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

    row = get_gst_approval(history_id)
    if row and row.get("approval_status") == "approved":
        return jsonify({"success": False, "error": "GST already approved — IRN is locked."}), 403

    body = request.get_json(silent=True) or {}
    irn = (body.get("irn") or "").strip()

    if not update_invoice_irn(history_id, irn):
        return jsonify({"success": False, "error": "DB update failed"}), 500

    return jsonify({"success": True, "message": "IRN saved"})


@app.route("/api/gst/rerun/<int:history_id>", methods=["POST"])
@api_login_required
def api_gst_rerun(history_id):
    """
    Re-run GST verification — resets existing results and fires bots again.
    Used when the user suspects the extracted GSTIN was wrong.
    """
    history = get_history_by_id(history_id)
    if not history:
        return jsonify({"success": False, "error": "Record not found"}), 404

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
