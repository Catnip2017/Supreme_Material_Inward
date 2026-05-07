"""
app.py — Material Inward Process — Final Production Application.

Features:
- Role-based access (Admin / User)
- Admin-only upload sidebar
- Phase rollout via ENABLED_STEPS env var
- PDF moved to uploads/processed/ or uploads/failed/ after OCR
- Storage location master (Admin managed)
- RF queue (one job at a time)
- Record locking
- Session expiry detection
"""

import os
import shutil
import json
import threading
import time
import traceback
from datetime import datetime
from functools import wraps
from database.po_operations import get_po_line_items


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
    update_history_step, lock_record, unlock_record,
    unlock_stale_locks, save_invoice_to_db,
    save_ewaybill_to_db, save_lr_to_db,
    get_history_search, get_today_counts
)
from database.gatein_operations import (
    upsert_gatein_entry, get_gatein_entry, map_ocr_to_gatein
)
from database.migo_operations import (
    upsert_migo_entry, save_migo_105_fields,
    get_migo_entry, map_ocr_to_migo
)
from database.miro_operations import (
    upsert_miro_entry, get_miro_entry, map_ocr_to_miro
)
from database.rf_queue_operations import enqueue_rf_job, get_job_status
from database.user_operations import (
    verify_user, get_all_users, add_user, update_user, delete_user
)
from database.storage_location_operations import (
    get_all_storage_locations, add_storage_location, update_storage_location
)
from services.extract import process_document
from services.rf_queue_worker import start_worker

logger = get_logger(__name__)

# ============================================================
# APP SETUP
# ============================================================

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_FILE_SIZE_BYTES
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False

# Ensure all upload folders exist
for folder in [config.UPLOAD_FOLDER, config.UPLOAD_PROCESSED_FOLDER, config.UPLOAD_FAILED_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# ============================================================
# STARTUP
# ============================================================

with app.app_context():
    try:
        init_pool()
        logger.info("DB pool ready.")
    except Exception as e:
        logger.critical(f"DB pool failed: {e}")

# AFTER:

_rf_worker = start_worker()  # noqa: F841
 
# Start intake method based on env switch
_intake_method = os.getenv("INTAKE_METHOD", "folder").lower()
if _intake_method == "folder":
    from services.folder_watcher import start_folder_watcher
    start_folder_watcher()
    logger.info("Intake: Folder watcher started.")
else:
    logger.info("Intake: Mail poller mode — run mail_poller.py via Task Scheduler.")
 
 
# 2. Add cleanup loop after the stale lock thread block:
 
def _clear_old_records():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM history WHERE created_at < NOW() - INTERVAL '2 months'"
                )
        logger.info("Old records cleared (>2 months).")
    except Exception as e:
        logger.error(f"Record cleanup error: {e}")
 
def _cleanup_loop():
    while True:
        time.sleep(86400)  # check once a day
        try:
            from datetime import date
            if date.today().day == 1:  # run on 1st of month
                _clear_old_records()
        except Exception as e:
            logger.error(f"Cleanup loop error: {e}")
 
threading.Thread(target=_cleanup_loop, daemon=True, name="RecordCleanup").start()
 
 



# ============================================================
# HELPERS
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
    return (True, "")

    # rules = {
    #     "gate_in":  (True, ""),
    #     "migo_103": (bool(history.get("gate_in")), "Complete Gate In first."),
    #     "migo_105": (bool(history.get("gate_in")) and bool(history.get("migo_103")),
    #                  "Complete Gate In and MIGO 103 first."),
    #     "miro":     (bool(history.get("gate_in")) and bool(history.get("migo_103")) and bool(history.get("migo_105")),
    #                  "Complete Gate In, MIGO 103, and MIGO 105 first."),
    # }
    # return rules.get(step, (False, f"Unknown step: {step}"))


def _move_file(src_path: str, dest_folder: str) -> str:
    """Move a file to dest_folder. Returns new path."""
    os.makedirs(dest_folder, exist_ok=True)
    filename = os.path.basename(src_path)
    dest_path = os.path.join(dest_folder, filename)
    try:
        shutil.move(src_path, dest_path)
        return dest_path
    except Exception as e:
        logger.error(f"Failed to move {src_path} to {dest_folder}: {e}")
        return src_path


def _find_file(filename: str) -> str:
    """Find a file in uploads/, uploads/processed/, or uploads/failed/."""
    for folder in [config.UPLOAD_FOLDER, config.UPLOAD_PROCESSED_FOLDER, config.UPLOAD_FAILED_FOLDER]:
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            return path
    return ""


def _auto_populate_form_tables(history_id: int) -> None:
    try:
        details = get_history_details_by_id(history_id)
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
    """
    Run OCR on a file, save to DB, move to processed/ or failed/.
    Returns True on success.
    """
    try:
        extracted = process_document(doctype, file_path, filename)
        if not extracted:
            logger.warning(f"OCR returned no data for {doctype} — moving to failed/")
            _move_file(file_path, config.UPLOAD_FAILED_FOLDER)
            return False

        extracted["filename"] = filename
        if doctype == "invoice":
            save_invoice_to_db(history_id, extracted)
        elif doctype == "ewaybill":
            save_ewaybill_to_db(history_id, extracted)
        elif doctype == "lr":
            save_lr_to_db(history_id, extracted)

        _move_file(file_path, config.UPLOAD_PROCESSED_FOLDER)
        logger.info(f"OCR success for {doctype}, moved to processed/")
        return True

    except Exception as e:
        logger.error(f"OCR error for {doctype}: {e}", exc_info=True)
        _move_file(file_path, config.UPLOAD_FAILED_FOLDER)
        return False


# ============================================================
# CONTEXT PROCESSOR — pass enabled steps to all templates
# ============================================================

@app.context_processor
def inject_globals():
    return {
        "enabled_steps": config._ENABLED_STEPS_RAW.lower(),
        "is_step_enabled": config.is_step_enabled,
        "is_admin": _is_admin(),
        "current_role": session.get("role", ""),
        "current_username": session.get("username", ""),
        "allow_user_upload": os.getenv("ALLOW_USER_UPLOAD", "false").lower() == "true",
        "show_dashboard_counts": os.getenv("SHOW_DASHBOARD_COUNTS", "false").lower() == "true",
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
            session["username"] = user["username"]
            session["role"]     = user["role"]
            session["name"]     = user["name"]
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
    if os.getenv("SHOW_DASHBOARD_COUNTS", "false").lower() == "true":
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
    result = get_history_search(
        search=search, status=status,
        date_from=date_from, date_to=date_to,
        page=page, per_page=20
    )
    return jsonify(result)


@app.route("/change_my_password", methods=["POST"])
@api_login_required
@admin_required
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

    from database.user_operations import update_user
    success = update_user(session.get("username"), new_password, session.get("role"))
    if success:
        return jsonify({"success": True, "message": "Password updated successfully"})
    return jsonify({"success": False, "error": "Failed to update password"}), 500


@app.route("/view/<int:history_id>")
@login_required
def view_detail(history_id):
    try:
        details  = get_history_details_by_id(history_id)
        history  = details.get("history")
        if not history:
            return redirect(url_for("history_page"))

        lock_result  = lock_record(history_id, _current_user())
        lock_warning = None if lock_result.get("success") else lock_result.get("message")

        gatein_data = get_gatein_entry(history_id) or {}
        migo_data   = get_migo_entry(history_id)   or {}
        miro_data   = get_miro_entry(history_id)   or {}

        if history.get("gate_in_number") and not migo_data.get("migo_header_text"):
            migo_data["migo_header_text"] = history["gate_in_number"]
        if history.get("material_doc_number"):
            migo_data["material_doc_number"] = history["material_doc_number"]

        po_data = get_po_line_items(history_id)
 
        return render_template(
            "index.html",
            history=history,
            history_id=history_id,
            invoice_data=details.get("invoice_data"),
            ewaybill_data=details.get("ewaybill_data"),
            lr_data=details.get("lr_data"),
            gatein_data=gatein_data,
            migo_data=migo_data,
            miro_data=miro_data,
            po_data=po_data,
            lock_warning=lock_warning,
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
    """Admin-only/If-user-allowed: start a new manual entry with upload sidebar."""
    allow_user_upload = os.getenv("ALLOW_USER_UPLOAD", "false").lower() == "true"
    if not _is_admin() and not allow_user_upload:
        return redirect(url_for("history_page"))
    session.pop("current_history_id", None)
    return render_template(
        "index.html",
        history=None, history_id=None,
        invoice_data=None, ewaybill_data=None, lr_data=None,
        gatein_data=None, migo_data=None, miro_data=None,
        lock_warning=None,
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
# RECORD LOCKING
# ============================================================

@app.route("/api/lock/<int:history_id>", methods=["POST"])
@api_login_required
def api_lock(history_id):
    return jsonify(lock_record(history_id, _current_user()))


@app.route("/api/unlock/<int:history_id>", methods=["POST"])
@api_login_required
def api_unlock(history_id):
    return jsonify({"success": unlock_record(history_id, _current_user())})


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
# DOCUMENT UPLOAD (Admin only via sidebar)
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

        success = _run_ocr_and_save(doctype, file_path, filename, history_id)
        if not success:
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
    allow_user_upload = os.getenv("ALLOW_USER_UPLOAD", "false").lower() == "true"
    if not _is_admin() and not allow_user_upload:
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

    return jsonify({"success": True, "history_id": history_id, "results": results})


# ============================================================
# GATE IN
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

    upsert_gatein_entry(history_id, data)
    job_id = enqueue_rf_job(history_id, "gate_in", data)
    if not job_id:
        return jsonify({"success": False, "error": "Gate In already processing. Please wait."}), 409

    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})


# ============================================================
# MIGO 103
# ============================================================

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


# ============================================================
# MIGO 105
# ============================================================

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
    material_doc = (migo_entry or {}).get("material_doc_number") or history.get("material_doc_number")
    if not material_doc:
        return jsonify({"success": False, "error": "Material Doc Number missing — ensure MIGO 103 completed."}), 400

    save_migo_105_fields(history_id, data)
    rf_payload = {
        "material_doc_number":     material_doc,
        "migo_105_storage_loc":    data.get("storageLocation"),
        "migo_105_batch":          data.get("batch"),
        "migo_105_vendor_invoice": data.get("vendorInvoiceDetail"),
        "migo_105_remarks":        data.get("remarks105"),
    }
    job_id = enqueue_rf_job(history_id, "migo_105", rf_payload)
    if not job_id:
        return jsonify({"success": False, "error": "MIGO 105 already processing."}), 409

    return jsonify({"success": True, "job_id": job_id, "poll_url": f"/api/queue_status/{job_id}"})


# ============================================================
# MIRO
# ============================================================

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

    material_doc = history.get("material_doc_number")
    if not material_doc:
        migo_entry = get_migo_entry(history_id)
        material_doc = (migo_entry or {}).get("material_doc_number")
    if not material_doc:
        return jsonify({"success": False, "error": "Material Doc Number missing."}), 400

    upsert_miro_entry(history_id, data)
    details = get_history_details_by_id(history_id)
    inv = details.get("invoice_data") or {}

    rf_payload = {
        "material_doc_number": material_doc,
        "miroReference":       data.get("miroReference") or inv.get("invoice_number", ""),
        "miroInvoiceDate":     data.get("miroInvoiceDate"),
        "miroPurchaseOrder":   data.get("miroPurchaseOrder") or inv.get("po_number", ""),
        "invoice_number":      inv.get("invoice_number", ""),
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
    """
    Return PO line items fetched after Gate In for a given history_id.
    Called by MIGO screen JS on page load to populate the PO table.
    Returns empty list (not 404) if PO fetch hasn't run yet —
    MIGO should still be usable without PO data.
    """
    items = get_po_line_items(history_id)
    return jsonify({"success": True, "data": items})


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
        history_id,
        "po_list_fetch",
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
    """Used by MIGO 105 dropdown to fetch active locations."""
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
    success = add_storage_location(code, description)
    return jsonify({"success": success})


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
    success = update_storage_location(code, description, is_active)
    return jsonify({"success": success})


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
    if not all([username, password, confirm, role, name]):
        return jsonify({"status": False, "message": "All fields required"}), 400
    if password != confirm:
        return jsonify({"status": False, "message": "Passwords do not match"}), 400
    success = add_user(username, password, role, name)
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
    if not username or not password or password != confirm:
        return jsonify({"status": False, "message": "All fields required and passwords must match"}), 400
    success = update_user(username, password, role)
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
    success = delete_user(username)
    return jsonify({"status": success, "message": "Deleted" if success else "Not found"})


# ============================================================
# DOCUMENT FILE SERVING
# ============================================================

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
    try:
        import fitz
        doc = fitz.open(file_path)
        pix = doc[0].get_pixmap(dpi=150)
        img = pix.tobytes("png")
        doc.close()
        return Response(img, mimetype="image/png")
    except Exception as e:
        logger.error(f"Thumbnail error {filename}: {e}")
        return str(e), 500



# ============================================================
# DOCUMENT DELETE
# ============================================================

@app.route("/delete_document/<doctype>/<filename>", methods=["DELETE"])
@api_login_required
def delete_document(doctype, filename):
    """Delete a document file and clear its data from DB."""
    import re
    # Security check — no path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"success": False, "error": "Invalid filename"}), 400

    file_path = _find_file(filename)
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            logger.error(f"Failed to delete file {filename}: {e}")
            return jsonify({"success": False, "error": "Could not delete file"}), 500

    # Extract history_id from filename (format: h{id}_filename.pdf)
    match = re.match(r"h(\d+)_", filename)
    if match:
        history_id = int(match.group(1))
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    if doctype == "invoice":
                        cur.execute("DELETE FROM invoice_data WHERE id = %s", (history_id,))
                    elif doctype == "ewaybill":
                        cur.execute("DELETE FROM ewaybill_data WHERE id = %s", (history_id,))
                    elif doctype == "lr":
                        cur.execute("DELETE FROM lr_data WHERE id = %s", (history_id,))
        except Exception as e:
            logger.error(f"Failed to clear DB data for {doctype} history_id={history_id}: {e}")

    logger.info(f"Document deleted: {filename} ({doctype})")
    return jsonify({"success": True, "message": "Document deleted"})

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
        use_reloader=False  # Must be False — reloader kills background threads
    )