
"""
services/rf_runner.py — Robot Framework execution wrapper.

FIX 10: Data cleaning applied to all values before passing to RF scripts.
Strips currency symbols, commas, unit suffixes so SAP never receives dirty OCR values.
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

logger = get_logger(__name__)


# ============================================================
# FIX 10: DATA CLEANING
# ============================================================

def _clean_value(raw) -> str:
    """
    Strip currency symbols, thousands commas, unit suffixes,
    and extra whitespace from OCR-extracted values before
    passing to Robot Framework / SAP.

    "₹1,23,456.00"  →  "123456.00"
    "100 EA"         →  "100"
    "  4567890  "    →  "4567890"
    None / ""        →  ""
    """
    if not raw:
        return ""
    cleaned = str(raw).strip()
    for symbol in ["₹", "$", "€", "£", "¥", "₩"]:
        cleaned = cleaned.replace(symbol, "")
    cleaned = cleaned.replace(",", "")
    # Strip unit suffixes — keep only the numeric part (first token)
    parts = cleaned.split()
    cleaned = parts[0] if parts else ""
    return cleaned.strip()


def _clean_dict(data: dict, keys: list) -> dict:
    """
    Return a copy of data with _clean_value applied to the given keys.
    """
    result = dict(data)
    for key in keys:
        if key in result:
            result[key] = _clean_value(result.get(key))
    return result


# ============================================================
# RF SCRIPT EXECUTOR
# ============================================================

def _wait_for_sap_free(max_wait_seconds: int = 240, check_interval: int = 30) -> bool:
    """
    Check if saplogon.exe is running (being used by another process).
    Waits up to max_wait_seconds, checking every check_interval seconds.
    Returns True if SAP is free, False if still busy after timeout.
    """
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
        logger.info(f"SAP is busy (another process running). Waiting {check_interval}s... ({int(elapsed)}s elapsed)")
        time.sleep(check_interval)


def _force_kill_sap() -> None:
    """
    Kills any hung SAP processes for a clean start.
    Called before launching any RF script.
    """
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
    Wake up the SAP GUI RDP session before running RF scripts.
    On Windows Server, SAP GUI may be in a disconnected/locked RDP session
    and the scripting engine becomes unresponsive without reconnecting first.

    TODO: Add your tscon command here when available.
    This is the same tscon approach used in your other SAP automation processes.

    Example (replace SESSION_ID and PASSWORD with your values):
        import subprocess
        subprocess.run(["tscon", "SESSION_ID", "/dest:console", "/password:PASSWORD"], check=False)
        time.sleep(3)  # Wait for session to reconnect

    To find your session ID: run 'query session' in cmd on the server.
    """
    # Uncomment and configure when tscon file is available:
    # import subprocess
    # subprocess.run(["tscon", "YOUR_SESSION_ID", "/dest:console", "/password:YOUR_PASSWORD"], check=False)
    # time.sleep(3)
    pass


def _to_sap_date(date_str: str) -> str:
    """
    Convert YYYY-MM-DD (HTML form format) to DD.MM.YYYY (SAP format).
    SAP date fields require DD.MM.YYYY format.
    Returns as-is if conversion fails.
    """
    if not date_str:
        return ""
    try:
        from datetime import datetime
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return date_str  # already in different format or empty


def _run_rf_script(
    script_name: str,
    variables: dict,
    timeout_seconds: int = 120
    
) -> dict:
    """
    Run a Robot Framework script as a subprocess with the given variable overrides.
    Checks SAP is free before running. Wakes RDP session via tscon if needed.
    Returns: {success, output, error, output_dir}
    """
    # Wake SAP GUI session (tscon — configure when available)
    # This reconnects the RDP session so SAP GUI scripting engine is responsive
    if "po_fetch" in script_name:
        _force_kill_sap()
 
    _wake_sap_session()

    # Wait for SAP to be free (max 4 minutes)
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
        # Escape colons in values to prevent RF variable parsing issues
        safe_value = str(value).replace(":", "\\:")
        cmd += ["--variable", f"{key}:{safe_value}"]
    cmd.append(script_path)

    logger.info(f"Running RF: {script_name} | Variables: {list(variables.keys())}")

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
    
    


def _extract_marked_value(output: str, marker: str) -> Optional[str]:
    """
    Parse RESULT:<marker>:<value> lines from RF script output.
    RF scripts log: Log    RESULT:GATE_IN_NUMBER:5000012345    level=INFO
    """
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
    """
    Execute gate_in.robot. Returns {success, gate_in_number, error}.
    FIX 10: Cleans numeric/currency fields before passing.
    """
    cleaned = _clean_dict(data, ["challanQty", "numPersons"])

    variables = {
        "VENDOR_NAME":    cleaned.get("vendorName", ""),
        "TRANSPORTER":    cleaned.get("transporter", ""),
        "TRUCK_NO":       cleaned.get("truckNo", ""),
        "DRIVER_NAME":    cleaned.get("driverName", ""),
        "LICENSE_NO":     cleaned.get("licenseNo", ""),
        "CONTAINER_NO":   cleaned.get("containerNo", ""),
        "CATEGORY":       cleaned.get("category", ""),
        "MATERIAL":       cleaned.get("material", ""),
        "CHALLAN_NO":     cleaned.get("challanNo", ""),
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
    if not gin:
        return {
            "success": False,
            "error": "Gate In posted but Gate In Number not captured from SAP status bar.",
            "gate_in_number": None
        }
    return {"success": True, "gate_in_number": gin, "error": None}
    


# ============================================================
# MIGO 103
# ============================================================

# def execute_migo_103_sap(data: dict) -> dict:
#     logger.info(f"MIGO 103 payload keys: {list(data.keys())}")
#     logger.info(f"items_data value: {data.get('items_data')}")
#     """
#     Execute migo_103.robot. Returns {success, material_doc_number, error}.
#     Passes matched pairs as ITEMS_JSON — supports n lines dynamically.
#     """
#     cleaned = _clean_dict(data, ["challanQty", "migoAmount"])

#     items_data = cleaned.get("items_data", [])
#     if isinstance(items_data, str):
#         try:
#             items_data = json.loads(items_data)
#         except Exception:
#             items_data = []
#     if not isinstance(items_data, list):
#         items_data = []

 

#     variables = {
#         "PO_NUMBER":      cleaned.get("purchaseOrder", "") or cleaned.get("migoPoNumber", ""),
#         "DOC_DATE":       _to_sap_date(cleaned.get("migoDocDate", "")),
#         "POST_DATE":      _to_sap_date(cleaned.get("migoPostDate", datetime.now().strftime("%Y-%m-%d"))),
#         "DELIVERY_NOTE":  cleaned.get("migoDeliveryNote", ""),
#         "BILL_OF_LADING": cleaned.get("migoBillOfLading", ""),
#         "GR_SLIP_NO":     cleaned.get("migoGRSlipNo", ""),
#         "HEADER_TEXT":    cleaned.get("migoHeaderText", ""),
#         "REMARKS":        cleaned.get("migoRemarks", ""),
#         "ITEMS_JSON":     json.dumps(items_data),
#     }

#     result = _run_rf_script("migo_103.robot", variables, timeout_seconds=300)
#     if not result["success"]:
#         return {"success": False, "error": result["error"], "material_doc_number": None}

#     # mat_doc = _extract_marked_value(result["output"], "MATERIAL_DOC_NUMBER")
#     # if not mat_doc:
#     #     return {
#     #         "success": False,
#     #         "error": "MIGO 103 posted but Material Document Number not captured.",
#     #         "material_doc_number": None
#     #     }
#     # return {"success": True, "material_doc_number": mat_doc, "error": None}

#     mat_doc = _extract_marked_value(result["output"], "MATERIAL_DOC_NUMBER")
#     if not mat_doc:
#         return {
#             "success": False,
#             "error": "MIGO 103 posted but Material Document Number not captured.",
#             "material_doc_number": None
#         }
#     # DEMO: accept DRY_RUN as a valid response — remove at deployment
#     if mat_doc == "DRY_RUN":
#         mat_doc = f"DRYRUN-{datetime.now().strftime('%H%M%S')}"
#         logger.warning(f"DRY RUN mode — using dummy mat doc: {mat_doc}")
#     return {"success": True, "material_doc_number": mat_doc, "error": None}


def execute_migo_103_sap(data: dict) -> dict:
    logger.info(f"MIGO 103 payload keys: {list(data.keys())}")
    logger.info(f"items_data value: {data.get('items_data')}")
    """
    Execute migo_103.robot. Returns {success, material_doc_number, error}.
    Passes matched pairs as ITEMS_JSON_B64 — base64 encoded to avoid RF colon parsing issues.
    """
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
    # DEMO: accept DRY_RUN as a valid response — remove at deployment
    if mat_doc == "DRY_RUN":
        mat_doc = f"DRYRUN-{datetime.now().strftime('%H%M%S')}"
        logger.warning(f"DRY RUN mode — using dummy mat doc: {mat_doc}")
    return {"success": True, "material_doc_number": mat_doc, "error": None}
# def execute_migo_103_sap(data: dict) -> dict:
#     """
#     Execute migo_103.robot. Returns {success, material_doc_number, error}.
#     FIX 10: Cleans quantity and amount fields.
#     """
#     cleaned = _clean_dict(data, ["challanQty", "migoAmount"])

#     # Build individual line variables from items_data
#     items_data = cleaned.get("items_data", [])
#     if isinstance(items_data, str):
#         try:
#             items_data = json.loads(items_data)
#         except Exception:
#             items_data = []
#     if not isinstance(items_data, list):
#         items_data = []

#     # Pad to 5 lines
#     while len(items_data) < 5:
#         items_data.append({})

#     line_vars = {}
#     for i in range(5):
#         n = i + 1
#         item = items_data[i] if i < len(items_data) else {}
#         line_vars[f"LINE{n}_QTY_ACTUAL"]   = str(item.get("qty_actual", "") or "")
#         line_vars[f"LINE{n}_QTY_EXPECTED"] = str(item.get("qty_expected", "") or "")
#         line_vars[f"LINE{n}_TEXT"]         = str(item.get("material", "") or "")

#     variables = {
#         "PO_NUMBER":      cleaned.get("purchaseOrder", "") or cleaned.get("migoPoNumber", ""),
#         "DOC_DATE":       _to_sap_date(cleaned.get("migoDocDate", "")),
#         "POST_DATE":      _to_sap_date(cleaned.get("migoPostDate", datetime.now().strftime("%Y-%m-%d"))),
#         "DELIVERY_NOTE":  cleaned.get("migoDeliveryNote", ""),
#         "BILL_OF_LADING": cleaned.get("migoBillOfLading", ""),
#         "GR_SLIP_NO":     cleaned.get("migoGRSlipNo", ""),
#         "HEADER_TEXT":    cleaned.get("migoHeaderText", ""),
#         "REMARKS":        cleaned.get("migoRemarks", ""),
#         "TOTAL_LINES":    str(max(1, sum(1 for item in items_data[:5] if item))),
#         **line_vars,
#     }

#     result = _run_rf_script("migo_103.robot", variables, timeout_seconds=300)
#     if not result["success"]:
#         return {"success": False, "error": result["error"], "material_doc_number": None}

#     mat_doc = _extract_marked_value(result["output"], "MATERIAL_DOC_NUMBER")
#     if not mat_doc:
#         return {
#             "success": False,
#             "error": "MIGO 103 posted but Material Document Number not captured from SAP status bar.",
#             "material_doc_number": None
#         }
#     return {"success": True, "material_doc_number": mat_doc, "error": None}


# # ============================================================
# # MIGO 105
# # ============================================================

def execute_migo_105_sap(data: dict) -> dict:
    """
    Execute migo_105.robot. Returns {success, error}.
    FIX 10: Cleans vendor invoice amount (grand total from OCR).
    """
    cleaned = _clean_dict(data, ["migo_105_vendor_invoice"])

    variables = {
        "MATERIAL_DOC_NUMBER": data.get("material_doc_number", ""),
        "STORAGE_LOCATION":    data.get("migo_105_storage_loc", ""),
        "BATCH":               data.get("migo_105_batch", ""),
        "VENDOR_INVOICE":      cleaned.get("migo_105_vendor_invoice", ""),
        "REMARKS":             data.get("migo_105_remarks", ""),
        # "REMARKS":             data.get("migo_105_remarks", "") or data.get("remarks105", ""),  # ADD

        "POST_DATE":           _to_sap_date(datetime.now().strftime("%Y-%m-%d")),
    }

    result = _run_rf_script("migo_105.robot", variables, timeout_seconds=300)
    if not result["success"]:
        return {"success": False, "error": result["error"]}
    return {"success": True, "error": None}


# ============================================================
# MIRO
# ============================================================

def execute_miro_sap(data: dict) -> dict:
    """
    Execute miro.robot. Returns {success, error}.
    Bot: ZMM35 → Parked Docs (no date filter) → Execute →
         Find row by material_doc_number → fill date + reference →
         Simulate → Withholding Tax → delete rows except 194Q →
         Simulate → Post.
    FIX 10: Reference number cleaned of any stray characters.
    """
    cleaned = _clean_dict(data, ["miroReference", "invoice_number"])

    variables = {
        "MATERIAL_DOC_NUMBER": data.get("material_doc_number", ""),
        "POSTING_DATE":        _to_sap_date(datetime.now().strftime("%Y-%m-%d")),
        "REFERENCE_NUMBER":    cleaned.get("miroReference", "") or cleaned.get("invoice_number", ""),
        "INVOICE_DATE":        _to_sap_date(data.get("miroInvoiceDate", "")),
        "PO_NUMBER":           data.get("miroPurchaseOrder", ""),
    }

    result = _run_rf_script("miro.robot", variables, timeout_seconds=300)
    if not result["success"]:
        return {"success": False, "error": result["error"]}
    return {"success": True, "error": None}

def execute_po_fetch_sap(data: dict) -> dict:
    """
    Execute po_fetch.robot. Returns {success, po_items, error}.
    po_items is a list of dicts:
        [{"item_no": "10", "material": "21027696", "short_text": "...",
          "po_qty": "1", "unit": "EA", "delivery_date": "08.06.2026"}, ...]
 
    Called automatically after Gate In succeeds — PO number comes from
    gate_in_entries.purchase_order for the same history_id.
    """
    po_number = str(data.get("po_number", "") or data.get("purchaseOrder", "") or "").strip()
 
    if not po_number:
        logger.warning("execute_po_fetch_sap called with empty PO number — skipping.")
        return {"success": False, "error": "PO number is empty", "po_items": []}
 
    variables = {
        "PO_NUMBER": po_number,
    }
 
    result = _run_rf_script("po_fetch.robot", variables, timeout_seconds=120)
    if not result["success"]:
        return {"success": False, "error": result["error"], "po_items": []}
 
    raw_json = _extract_marked_value(result["output"], "PO_DATA")
    if not raw_json:
        return {
            "success": False,
            "error": "PO fetch ran but no PO_DATA found in output.",
            "po_items": []
        }
 
    try:
        po_items = json.loads(raw_json)
        if not isinstance(po_items, list):
            po_items = []
        logger.info(
            f"PO fetch successful — PO={po_number} "
            f"{len(po_items)} line(s) parsed."
        )
        return {"success": True, "po_items": po_items, "error": None}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse PO_DATA JSON for PO={po_number}: {e}")
        logger.error(f"Raw value was: {raw_json[:500]}")
        return {
            "success": False,
            "error": f"PO data JSON parse failed: {e}",
            "po_items": []
        }
 

def execute_po_list_fetch_sap(data: dict) -> dict:
    """
    Execute po_list_fetch.robot.
    Returns {success, po_list, error}.
    po_list is a list of dicts:
        [{"po_number": "4500012345", "vendor": "...",
          "creation_date": "01.04.2026", "open_qty": "10"}, ...]
    """
    import json as _json
 
    vendor_name = str(data.get("vendor_name", "") or "").strip()
    if not vendor_name:
        logger.warning("execute_po_list_fetch_sap called with empty vendor_name.")
        return {"success": False, "error": "Vendor name is empty", "po_list": []}
 
    variables = {
        "VENDOR_NAME": vendor_name,
    }
 
    result = _run_rf_script("po_list_fetch.robot", variables, timeout_seconds=120)
    if not result["success"]:
        return {"success": False, "error": result["error"], "po_list": []}
 
    raw_json = _extract_marked_value(result["output"], "PO_LIST")
    if not raw_json:
        return {
            "success": False,
            "error": "PO list fetch ran but no PO_LIST found in output.",
            "po_list": []
        }
 
    try:
        po_list = _json.loads(raw_json)
        if not isinstance(po_list, list):
            po_list = []
        logger.info(
            f"PO list fetch successful — vendor='{vendor_name}' "
            f"{len(po_list)} PO(s) found."
        )
        return {"success": True, "po_list": po_list, "error": None}
    except _json.JSONDecodeError as e:
        logger.error(f"Failed to parse PO_LIST JSON: {e}")
        return {"success": False, "error": f"JSON parse failed: {e}", "po_list": []}
 