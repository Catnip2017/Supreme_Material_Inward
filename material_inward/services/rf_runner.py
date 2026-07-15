"""
services/rf_runner.py — Robot Framework execution wrapper.

v4 changes:
- execute_migo_105_sap now passes ITEMS_JSON_BATCH (base64-encoded JSON of
  per-line batch values) — robot code reads this for per-line batch entry.
- Global BATCH variable removed (was used incorrectly with single batch for all lines).
"""

import os
import time
import re
import subprocess
import json
import base64

from datetime import datetime
from typing import Optional
from dotenv import dotenv_values

from config.config import config
from config.logger import get_logger
from services.robot_lock import acquire_robot_lock, release_robot_lock

logger = get_logger(__name__)


# ============================================================
# DATA CLEANING
# ============================================================

def _clean_value(raw) -> str:
    if not raw:
        return ""
    cleaned = str(raw).strip()
    for symbol in ["₹", "$", "€", "£", "¥", "₩"]:
        cleaned = cleaned.replace(symbol, "")
    cleaned = cleaned.replace(",", "")
    parts = cleaned.split()
    cleaned = parts[0] if parts else ""
    return cleaned.strip()


def _clean_dict(data: dict, keys: list) -> dict:
    result = dict(data)
    for key in keys:
        if key in result:
            result[key] = _clean_value(result.get(key))
    return result


# ============================================================
# RF SCRIPT EXECUTOR HELPERS
# ============================================================

def _wait_for_sap_free(max_wait_seconds: int = 240, check_interval: int = 30) -> bool:
    import subprocess as sp
    start = time.time()
    while True:
        result = sp.run(
            ["tasklist", "/FI", "IMAGENAME eq saplogon.exe", "/NH"],
            capture_output=True, text=True
        )
        sap_running = "saplogon.exe" in result.stdout
        if not sap_running:
            logger.info("SAP is free — proceeding with RF script.")
            return True
        elapsed = time.time() - start
        if elapsed >= max_wait_seconds:
            logger.warning(f"SAP still busy after {max_wait_seconds}s — giving up.")
            return False
        logger.info(f"SAP busy. Waiting {check_interval}s... ({int(elapsed)}s elapsed)")
        time.sleep(check_interval)


def _force_kill_sap() -> None:
    import subprocess as sp
    logger.info("Clearing existing SAP sessions...")
    try:
        sp.run(["taskkill", "/F", "/IM", "saplogon.exe", "/T"], capture_output=True)
        sp.run(["taskkill", "/F", "/IM", "sapgui.exe", "/T"], capture_output=True)
        time.sleep(2)
    except Exception as e:
        logger.warning(f"SAP cleanup note: {e}")


def _wake_sap_session() -> None:
    """
    Wake RDP session and ensure display is active before SAP launches.
    Calls the same sequence used by the standalone wake script.
    """
    import ctypes
    import subprocess as sp

    # Step 1: Prevent sleep
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)

    # Step 2: tscon via scheduled task
    try:
        result = sp.run(
            ["schtasks", "/Run", "/TN", "PrepareSAPGui"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            logger.info("PrepareSAPGui triggered.")
            time.sleep(6)
        else:
            logger.warning(f"PrepareSAPGui failed: {result.stderr.strip()}")
    except Exception as e:
        logger.warning(f"PrepareSAPGui call failed (non-fatal): {e}")

    # Step 3: Simulate mouse movement to wake display
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        ctypes.windll.user32.SetCursorPos(pt.x + 1, pt.y)
        time.sleep(0.1)
        ctypes.windll.user32.SetCursorPos(pt.x, pt.y)
        logger.info("Mouse activity simulated — display active.")
    except Exception as e:
        logger.warning(f"Mouse simulation failed (non-fatal): {e}")

    # Step 4: Final wait for display to fully render
    time.sleep(3)
    logger.info("Session wake sequence complete.")


def _to_sap_date(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        from datetime import datetime
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return date_str


def _run_rf_script(
    script_name: str,
    variables: dict,
    timeout_seconds: int = 120
) -> dict:
    if script_name == "po_fetch.robot":
        _force_kill_sap()
    _wake_sap_session()

    sap_free = _wait_for_sap_free(max_wait_seconds=240, check_interval=30)
    if not sap_free:
        return {
            "success": False,
            "error": "SAP is currently in use. Please try again in a few minutes.",
            "output": ""
        }
    script_path = os.path.join(config.RF_SCRIPTS_PATH, script_name)
    if not os.path.exists(script_path):
        msg = f"RF script not found: {script_path}"
        logger.error(msg)
        return {"success": False, "error": msg, "output": ""}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(
        config.RF_OUTPUT_PATH,
        f"{script_name.replace('.robot', '')}_{timestamp}"
    )
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        "python", "-m", "robot",
        "--outputdir", output_dir,
        "--loglevel", "DEBUG",
        "--nostatusrc",
    ]
    for key, value in variables.items():
        safe_value = str(value).replace(":", "\\:")
        cmd += ["--variable", f"{key}:{safe_value}"]
    cmd.append(script_path)

    logger.info(f"Running RF: {script_name} | Variables: {list(variables.keys())}")

    acquire_robot_lock(script_name)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={**os.environ, **dotenv_values()},
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        combined = stdout + "\n" + stderr

        logger.debug(f"RF stdout (first 2000 chars): {stdout[:2000]}")
        if stderr:
            logger.warning(f"RF stderr: {stderr[:500]}")

        if result.returncode == 0:
            logger.info(f"RF script '{script_name}' completed successfully.")
            return {"success": True, "output": combined, "error": None, "output_dir": output_dir}
        else:
            msg = f"RF script failed (exit code {result.returncode}). Logs: {output_dir}"
            logger.error(msg)
            return {"success": False, "output": combined, "error": msg, "output_dir": output_dir}

    except subprocess.TimeoutExpired:
        msg = f"RF script '{script_name}' timed out after {timeout_seconds}s"
        logger.error(msg)
        return {"success": False, "error": msg, "output": ""}
    except Exception as e:
        msg = f"Unexpected error running '{script_name}': {e}"
        logger.error(msg, exc_info=True)
        return {"success": False, "error": msg, "output": ""}
    finally:
        release_robot_lock()


def _extract_marked_value(output: str, marker: str) -> Optional[str]:
    pattern = rf"RESULT:{re.escape(marker)}:(.+)"
    match = re.search(pattern, output)
    if match:
        value = match.group(1).strip()
        logger.info(f"Parsed '{marker}' = '{value}'")
        return value
    logger.warning(f"Marker '{marker}' not found in RF output.")
    return None


# ============================================================
# GATE IN
# ============================================================

def execute_gate_in_sap(data: dict) -> dict:
    cleaned = _clean_dict(data, ["challanQty", "numPersons"])
    challan_raw = cleaned.get("challanNo", "")
    challan_numeric = re.sub(r'[^0-9]', '', challan_raw)

    # Hand delivery sends truckNo/licenseNo as '' (see gate_in.html) since those
    # fields are hidden/inapplicable. SAP's ctxtP_TR_NO / txtP_DIR_LI may not
    # accept a blank value, so substitute a fixed placeholder instead of ''.
    # containerNo and everything else is left exactly as sent -- not touched.
    truck_no_clean   = cleaned.get("truckNo", "") or "BYHAND"
    license_no_clean = cleaned.get("licenseNo", "") or "NONE"

    variables = {
        "VENDOR_NAME":    cleaned.get("vendorName", ""),
        "TRANSPORTER":    cleaned.get("transporter", ""),
        "TRUCK_NO":       truck_no_clean,
        "DRIVER_NAME":    cleaned.get("driverName", ""),
        "LICENSE_NO":     license_no_clean,
        "CONTAINER_NO":   cleaned.get("containerNo", ""),
        "CATEGORY":       cleaned.get("category", ""),
        "MATERIAL":       cleaned.get("material", ""),
        # "CHALLAN_NO":     cleaned.get("challanNo", ""),
        "CHALLAN_NO": challan_numeric, 
        "CHALLAN_QTY":    cleaned.get("challanQty", ""),
        "BOE_NO":         cleaned.get("boeNo", ""),
        "PURCHASE_ORDER": cleaned.get("purchaseOrder", ""),
        "NUM_PERSONS":    cleaned.get("numPersons", "1"),
        "GATE_PASS_NO":   cleaned.get("gatePassNo", ""),
        "NOTE":           cleaned.get("note", ""),
        "GATE_IN_DATE":   _to_sap_date(data.get("gateInDate", "")),
        "GATE_IN_TIME":   data.get("gateInTime", ""),
    }

    result = _run_rf_script("gate_in.robot", variables, timeout_seconds=180)
    if not result["success"]:
        return {"success": False, "error": result["error"], "gate_in_number": None}

    gin = _extract_marked_value(result["output"], "GATE_IN_NUMBER")
    sap_msg = _extract_marked_value(result["output"], "GATE_IN_STATUS_MSG") or "No status bar message captured"

    
    if not gin:
        return {
            "success": False,
            "error": "Gate In posted but Gate In Number not captured from SAP status bar.",
            "gate_in_number": None
        }

    # ── FIX: MANUAL_CHECK_REQUIRED means SAP didn't return a number ──
    if gin == "MANUAL_CHECK_REQUIRED":
        return {
            "success": False,
            "error": (
                f"Gate In submitted but no GIN captured. "
                f"SAP status bar said: '{sap_msg}'. "
                f"Check SAP manually (TCODE: zmmtmn)."
            ),
            "gate_in_number": None
        }
    # ─────────────────────────────────────────────────────────────────

    return {"success": True, "gate_in_number": gin, "error": None}


# ============================================================
# MIGO 103
# ============================================================

def execute_migo_103_sap(data: dict) -> dict:
    logger.info(f"MIGO 103 payload keys: {list(data.keys())}")
    cleaned = _clean_dict(data, ["challanQty", "migoAmount"])

    items_data = cleaned.get("items_data", [])
    if isinstance(items_data, str):
        try:
            items_data = json.loads(items_data)
        except Exception:
            items_data = []
    if not isinstance(items_data, list):
        items_data = []

    items_json_str = json.dumps(items_data)
    items_json_b64 = base64.b64encode(items_json_str.encode()).decode()

    variables = {
        "PO_NUMBER":      cleaned.get("purchaseOrder", "") or cleaned.get("migoPoNumber", ""),
        "DOC_DATE":       _to_sap_date(cleaned.get("migoDocDate", "")),
        "POST_DATE":      _to_sap_date(cleaned.get("migoPostDate", datetime.now().strftime("%Y-%m-%d"))),
        "DELIVERY_NOTE":  cleaned.get("migoDeliveryNote", ""),
        "BILL_OF_LADING": cleaned.get("migoBillOfLading", ""),
        "GR_SLIP_NO":     cleaned.get("migoGRSlipNo", ""),
        "HEADER_TEXT":    cleaned.get("migoHeaderText", ""),
        "REMARKS":        cleaned.get("migoRemarks", ""),
        "ITEMS_JSON_B64": items_json_b64,
    }

    result = _run_rf_script("migo_103.robot", variables, timeout_seconds=300)
    if not result["success"]:
        return {"success": False, "error": result["error"], "material_doc_number": None}

    mat_doc = _extract_marked_value(result["output"], "MATERIAL_DOC_NUMBER")
    if not mat_doc:
        return {
            "success": False,
            "error": "MIGO 103 posted but Material Document Number not captured.",
            "material_doc_number": None
        }
    return {"success": True, "material_doc_number": mat_doc, "error": None}


# ============================================================
# MIGO 105 — per-line batch via ITEMS_JSON_BATCH
# ============================================================

def execute_migo_105_sap(data: dict) -> dict:
    """
    Pass per-line batch values to robot via ITEMS_JSON_BATCH (base64 of JSON).

    Robot decodes:
      [{"line": 1, "batch": "BATCH001"}, {"line": 2, "batch": ""}, ...]

    Empty batch string = robot must skip Batch tab interaction (SAP auto-generates).
    """
    cleaned = _clean_dict(data, ["migo_105_vendor_invoice"])

    # Build batch list from items_data (set by upsert_migo_entry on save)
    items_data = data.get("items_data") or []
    if isinstance(items_data, str):
        try:
            items_data = json.loads(items_data)
        except Exception:
            items_data = []
    if not isinstance(items_data, list):
        items_data = []

    batches = []
    for item in items_data:
        batches.append({
            "line":  item.get("line"),
            "batch": (item.get("batch") or "").strip(),
        })

    items_json_str = json.dumps(batches)
    items_json_b64 = base64.b64encode(items_json_str.encode()).decode()

    variables = {
        "MATERIAL_DOC_NUMBER": data.get("material_doc_number", ""),
        "STORAGE_LOCATION":    data.get("migo_105_storage_loc", ""),
        "VENDOR_INVOICE":      cleaned.get("migo_105_vendor_invoice", ""),
        "REMARKS":             data.get("migo_105_remarks", ""),
        "POST_DATE":           _to_sap_date(datetime.now().strftime("%Y-%m-%d")),
        "ITEMS_JSON_BATCH":    items_json_b64,
    }

    result = _run_rf_script("migo_105.robot", variables, timeout_seconds=300)
    if not result["success"]:
        return {"success": False, "error": result["error"]}

    miro_doc = _extract_marked_value(result["output"], "MIRO_DOC_NUMBER")
    
    # ── FIX: MIGO 105 does generate a doc — missing means it didn't post ──
    if not miro_doc:
        logger.error(
            "MIGO 105 robot completed but no document number captured. "
            f"Check robot log: {result.get('output_dir')}"
        )
        return {
            "success": False,
            "error": (
                "MIGO 105 ran but did not capture a document number from SAP. "
                "Check SAP manually and robot logs."
            )
        }
    # ─────────────────────────────────────────────────────────────────

    return {"success": True, "error": None, "miro_doc_number": miro_doc}


# ============================================================
# MIRO
# ============================================================

def execute_miro_sap(data: dict) -> dict:
    variables = {
        "REFERENCE_NUMBER": data.get("miroReference", ""),
        "INVOICE_DATE":     _to_sap_date(data.get("miroInvoiceDate", "")),
        "PO_NUMBER":        data.get("miroPurchaseOrder", ""),
        "POSTING_DATE":     _to_sap_date(datetime.now().strftime("%Y-%m-%d")),
    }
    result = _run_rf_script("miro.robot", variables, timeout_seconds=300)
    if not result["success"]:
        return {"success": False, "error": result["error"]}

    fi_doc = _extract_marked_value(result["output"], "FI_DOC_NUMBER")

    # ── FIX: treat missing FI_DOC_NUMBER as failure ──────────────────
    if not fi_doc:
        logger.error(
            f"MIRO robot completed but FI_DOC_NUMBER not found in output. "
            f"SAP may not have posted. Check robot logs at: {result.get('output_dir')}"
        )
        return {
            "success": False,
            "error": (
                "MIRO robot ran but did not capture a document number from SAP. "
                "The document may not have been posted. "
                "Check SAP manually and the robot log for details."
            )
        }
    # ─────────────────────────────────────────────────────────────────

    return {"success": True, "error": None, "fi_doc_number": fi_doc}
# ============================================================
# PO FETCH
# ============================================================

def execute_po_fetch_sap(data: dict) -> dict:
    po_number = str(data.get("po_number", "") or data.get("purchaseOrder", "") or "").strip()
    if not po_number:
        logger.warning("execute_po_fetch_sap called with empty PO number — skipping.")
        return {"success": False, "error": "PO number is empty", "po_items": []}

    variables = {"PO_NUMBER": po_number}
    result = _run_rf_script("po_fetch.robot", variables, timeout_seconds=120)
    if not result["success"]:
        return {"success": False, "error": result["error"], "po_items": []}

    raw_json = _extract_marked_value(result["output"], "PO_DATA")
    if not raw_json:
        return {"success": False, "error": "PO fetch ran but no PO_DATA found.", "po_items": []}

    try:
        po_items = json.loads(raw_json)
        if not isinstance(po_items, list):
            po_items = []
        logger.info(f"PO fetch successful — PO={po_number} {len(po_items)} line(s).")
        return {"success": True, "po_items": po_items, "error": None}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse PO_DATA JSON for PO={po_number}: {e}")
        return {"success": False, "error": f"PO data JSON parse failed: {e}", "po_items": []}


# ============================================================
# PO LIST FETCH
# ============================================================

def execute_po_list_fetch_sap(data: dict) -> dict:
    vendor_name = str(data.get("vendor_name", "") or "").strip()
    if not vendor_name:
        return {"success": False, "error": "Vendor name is empty", "po_list": []}

    variables = {"VENDOR_NAME": vendor_name}
    result = _run_rf_script("po_list_fetch.robot", variables, timeout_seconds=120)
    if not result["success"]:
        return {"success": False, "error": result["error"], "po_list": []}

    raw_json = _extract_marked_value(result["output"], "PO_LIST")
    if not raw_json:
        return {"success": False, "error": "No PO_LIST in output.", "po_list": []}

    try:
        po_list = json.loads(raw_json)
        if not isinstance(po_list, list):
            po_list = []
        return {"success": True, "po_list": po_list, "error": None}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON parse failed: {e}", "po_list": []}

# ============================================================
# ZGATEIN UPDATE — update PO on an existing gate in entry
# Called only for without_po flows after MIGO 103 completes.
# Element paths in the robot are placeholders — SAP team must
# provide real paths from a GUI recording of zgatein_update tcode.
# ============================================================

def execute_update_gatein_po_sap(data: dict) -> dict:
    """
    Run zgatein_update.robot to backfill the PO number on a
    gate in entry that was originally created with PO = "NA".
    """
    gin        = str(data.get("gate_in_number", "") or "").strip()
    po_number  = str(data.get("po_number",      "") or "").strip()
    history_id = data.get("history_id", "")

    if not gin or not po_number:
        return {
            "success": False,
            "error": (
                f"Missing gate_in_number or po_number — "
                f"cannot run zgatein_update for history_id={history_id}"
            )
        }

    variables = {
        "GATE_IN_NUMBER": gin,
        "PO_NUMBER":      po_number,
        "HISTORY_ID":     str(history_id),
    }

    result = _run_rf_script("zgatein_update.robot", variables, timeout_seconds=180)
    if not result["success"]:
        return {"success": False, "error": result["error"]}

    status_val = _extract_marked_value(result["output"], "GATEIN_UPDATE_STATUS")
    if status_val and status_val.upper() == "SUCCESS":
        return {"success": True, "error": None}

    return {
        "success": False,
        "error": (
            "zgatein_update robot ran but did not confirm success. "
            f"Check robot log: {result.get('output_dir')}"
        )
    }
