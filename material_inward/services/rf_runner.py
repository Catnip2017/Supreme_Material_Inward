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
 
# FIX: same CWD-dependent dotenv issue as config/config.py and
# dms_upload_runner.py -- dotenv_values() with no path searches upward from
# the current working directory, which is normally fine for the Flask app
# (launched from project root) but not guaranteed for every process that
# might import/run this module. Anchor to this file's own project-root
# location so the RF subprocess env is never silently missing .env values.
_RF_RUNNER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RF_RUNNER_ENV_PATH = os.path.join(_RF_RUNNER_ROOT, ".env")
 
 
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
 
 
def _s(value) -> str:
    """
    Coerce None to '' so it never reaches _run_rf_script's `str(value)` and
    gets posted into SAP as the literal 4-character word "None". Unlike
    _clean_value(), this does NOT strip whitespace/symbols or truncate to
    the first word -- free-text fields (vendor name, note, remarks, delivery
    note, etc.) need every word intact, only the None-vs-missing-key gap
    needs closing. A dict.get(key, "") default only kicks in when the key is
    ABSENT; a direct API POST (or any caller building the payload from a
    nullable DB column) can send the key present with value None, which
    .get() happily returns as-is.
    """
    return value if value is not None else ""
 
 
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
 
    FIX: removed the "PrepareSAPGui" scheduled-task call (schtasks /Run /TN
    PrepareSAPGui) that used to run here. Confirmed via application.log
    (2026-08-05, 16:00-17:10 window) it was failing with "Access is denied"
    on 100% of ~18 calls in that window -- a Windows Task Scheduler ACL
    issue on the machine (the account running run_server.py isn't allowed
    to trigger that task), not something fixable from this codebase. Client
    confirmed it's no longer needed. Checked first for any concurrency risk
    from removing it (i.e. whether it was quietly preventing SAP session
    clashes between overlapping bot runs) -- traced every RobotLock
    Acquired/Released pair across that same window, including through one
    process crash-restart, found no double-acquisition; claim_next_pending_
    job() in rf_queue_operations.py also guards against concurrent claims
    via a running-status check + FOR UPDATE SKIP LOCKED. Safe to remove --
    it was never actually running successfully anyway, so nothing
    functional depended on it. Kept the sleep-prevention and final render
    wait, which are unrelated to the scheduled task and still make sense.
    """
    import ctypes
 
    # Step 1: Prevent sleep
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
 
    # Step 2: wait for display to fully render
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
    timeout_seconds: int = 120,
    extra_env: Optional[dict] = None
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
        # FIX: this used to escape every ":" in the value as "\:", on the
        # assumption robot's `--variable NAME:VALUE` CLI syntax needed it to
        # avoid an embedded colon being mistaken for another name:value
        # split. Verified empirically (robot 7.x) that this is false --
        # `--variable` only ever splits on the FIRST colon after the name,
        # so "MATERIAL:SIZE:-10" already correctly yields value "SIZE:-10"
        # with no escaping at all, and it isn't stripped back out afterward
        # either -- the literal backslash stayed in ${MATERIAL} and got
        # typed straight into SAP. That's a real, confirmed production bug:
        # "PLAIN PAPER LABELS IN ROLL FORM SIZE:-10" is exactly 40 chars
        # (Gate In's own SAP-length cap on the Material field), but with
        # this escaping applied it became "...SIZE\:-10" -- 41 chars -- and
        # SAP rejected it. The escaping was pure downside: unnecessary for
        # robot's parser and actively capable of pushing an
        # already-correctly-truncated value back over its SAP field limit
        # for any value containing a colon. Removed outright rather than
        # reworked, since nothing here needs it.
        cmd += ["--variable", f"{key}:{value}"]
    cmd.append(script_path)
 
    logger.info(f"Running RF: {script_name} | Variables: {list(variables.keys())}")
 
    subprocess_env = {**os.environ, **dotenv_values(_RF_RUNNER_ENV_PATH)}
    if extra_env:
        # v16: per-user SAP credential override (LDAP users only) -- see
        # _sap_credential_env(). Added as new env var names on top of the
        # merged .env values, never overwriting SAP_USERNAME/SAP_PASSWORD
        # themselves, so the robot script's own .env fallback logic keeps
        # working untouched for local/test accounts. Never log the value.
        subprocess_env.update(extra_env)
        logger.info(
            f"RF: {script_name} using per-user SAP credential override for "
            f"'{extra_env.get('SAP_USER_OVERRIDE')}' (password not logged)."
        )
 
    acquire_robot_lock(script_name)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=subprocess_env,
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
 
    except subprocess.TimeoutExpired as timeout_exc:
        msg = f"RF script '{script_name}' timed out after {timeout_seconds}s"
        logger.error(msg)
        # FIX (2026-08-11): this used to return "output": "" unconditionally
        # -- but Python's subprocess.run() (with capture_output=True) still
        # attaches whatever stdout/stderr WAS captured before the kill onto
        # the TimeoutExpired exception itself (timeout_exc.stdout/.stderr).
        # Confirmed real-world case: a zgatein_update run whose SAP work
        # genuinely finished and printed RESULT:GATEIN_UPDATE_STATUS:SUCCESS
        # to stdout, but then ran long in its own teardown/cleanup (SAP GUI
        # slowness) past the 180s timeout -- Python killed it before the
        # subprocess could exit normally, and the caller's early
        # `if not result["success"]` check threw the whole thing away
        # without ever looking at output, discarding the already-earned
        # success marker and reporting a false failure to the frontend even
        # though the robot's own log clearly showed success. Capturing this
        # here lets callers that check `output` before giving up on
        # `success` (see execute_update_gatein_po_sap) recover the real
        # outcome instead of just going by the exit-level timeout.
        timeout_stdout = getattr(timeout_exc, "stdout", None) or ""
        timeout_stderr = getattr(timeout_exc, "stderr", None) or ""
        timeout_combined = (timeout_stdout or "") + "\n" + (timeout_stderr or "")
        # FIX: real production bug -- when subprocess.run(timeout=...) fires,
        # Python kills the immediate `python -m robot` child process, but
        # saplogon.exe (launched by the robot script's own `Start Process`
        # keyword) is a grandchild, not attached via a Windows Job Object, so
        # it survives as an orphan. The robot script's own [Teardown] Close
        # SAP Session -- which does the real taskkill -- never runs either,
        # because the whole process was hard-killed from outside before it
        # could get there. Net effect: SAP is left open after a timeout, with
        # nothing left to close it, until someone kills it by hand. Seen
        # concretely with po_fetch.robot on POs with many line items (its
        # per-item HSN + Open Quantity loops each cost several seconds, and
        # its old 120s timeout could not cover a large enough item count) --
        # but this same orphaning risk applies to a timeout on ANY script, so
        # the cleanup belongs here centrally rather than only in
        # execute_po_fetch_sap.
        _force_kill_sap()
        return {"success": False, "error": msg, "output": timeout_combined, "output_dir": output_dir}
    except Exception as e:
        msg = f"Unexpected error running '{script_name}': {e}"
        logger.error(msg, exc_info=True)
        return {"success": False, "error": msg, "output": ""}
    finally:
        release_robot_lock()
 
 
def _sap_credential_env(data: dict) -> dict:
    """
    v16: extracts this job's per-user SAP override credential, if any (see
    services/credential_cache.py + services/rf_queue_worker.py — payload
    keys "_sap_username"/"_sap_password", set only for LDAP-authenticated
    users' jobs). Returns {} for local/.env-fallback jobs, which is the
    common case for test accounts and is exactly today's behavior.
 
    Returned as a dict meant for the RF subprocess's *environment*, not
    as --variable CLI arguments -- passing a password as a command-line
    argument would make it visible to Task Manager/ps on the SAP bot
    machine for the life of the process, which the CLI-variable route
    used for every other field does not need to avoid (none of those are
    secrets). Robot scripts read SAP_USER_OVERRIDE/SAP_PASS_OVERRIDE from
    the environment in Initialize SAP And Login and prefer them over the
    shared .env SAP_USERNAME/SAP_PASSWORD when both are present.
    """
    user = data.get("_sap_username")
    pw   = data.get("_sap_password")
    if user and pw:
        return {"SAP_USER_OVERRIDE": user, "SAP_PASS_OVERRIDE": pw}
    return {}
 
 
def _extract_marked_value(output: str, marker: str) -> Optional[str]:
    pattern = rf"RESULT:{re.escape(marker)}:(.+)"
    match = re.search(pattern, output)
    if match:
        value = match.group(1).strip()
        logger.info(f"Parsed '{marker}' = '{value}'")
        return value
    logger.warning(f"Marker '{marker}' not found in RF output.")
    return None
 
 
def _rf_test_actually_passed(output: str) -> bool:
    """
    FIX: distinguishes a genuine Robot Framework test failure (the script
    errored/crashed before completing -- e.g. "Cannot find element with
    id ...") from a test that ran to completion (including clicking Post)
    but a downstream marker just wasn't found in its output afterward.
 
    _run_rf_script invokes `robot` with --nostatusrc, which forces the
    subprocess's OS exit code to 0 regardless of whether the actual test
    passed or failed -- so result["success"] alone can't tell these two
    cases apart, and confirmed in production (history_id=39) that this was
    producing a misleading "posted but Material Document Number not
    captured" message for a run that had actually errored out at Step 2 and
    never got anywhere near Post. Robot Framework's own final summary line
    (e.g. "1 test, 0 passed, 1 failed") is unaffected by --nostatusrc and is
    stable across RF versions, so it's used here instead of trying to
    parse "| FAIL |"/"| PASS |" lines (which can appear multiple times per
    suite/keyword and are harder to pin down reliably).
    """
    match = re.search(r"(\d+)\s+tests?,\s+(\d+)\s+passed,\s+(\d+)\s+failed", output)
    if not match:
        # Can't find the summary line at all -- don't invent a false
        # "it passed" reading; treat as genuinely failed/unknown.
        return False
    failed_count = int(match.group(3))
    return failed_count == 0
 
 
# ============================================================
# GATE IN
# ============================================================
 
def execute_gate_in_sap(data: dict) -> dict:
    cleaned = _clean_dict(data, ["challanQty", "numPersons"])
    challan_raw = cleaned.get("challanNo", "")
    challan_numeric = re.sub(r'[^0-9]', '', challan_raw)
 
    # Hand delivery (and now Courier -- see gate_in.html's deliveryType radios)
    # sends truckNo/licenseNo as '' since those fields are hidden/inapplicable
    # when there's no truck involved, regardless of whether a PO exists.
    # - TRUCK_NO: SAP's ctxtP_TR_NO may not accept a blank value, so this one
    #   still substitutes a fixed placeholder instead of '' -- applies to
    #   both the _with_po and _without_po variant of whichever non-truck
    #   delivery type was selected, since truck_no is blanked by delivery
    #   type, not by PO availability. Courier gets its own placeholder
    #   ("BY COURIER") so SAP shows which of the two non-truck deliveries
    #   this was, instead of both looking identical as "BYHAND".
    # - LICENSE_NO: goes in blank now -- no placeholder.
    # - PURCHASE_ORDER: this is independent of delivery type -- any delivery
    #   type can still have a real PO. Blank only when "Without PO" was
    #   actually selected (*_without_po). Every *_with_po flow sends the
    #   real PO number through, regardless of delivery type.
    # containerNo and everything else is left exactly as sent -- not touched.
    po_flow_type  = (cleaned.get("po_flow_type") or "").strip().lower()
    is_without_po = po_flow_type.endswith("_without_po")
    # delivery type is always the first underscore-segment of po_flow_type
    # ("truck"/"hand"/"courier" -- none of which contain an underscore
    # themselves), regardless of which _with_po/_without_po suffix follows.
    delivery_type = po_flow_type.split("_")[0] if po_flow_type else ""
 
    truck_no_placeholder = "BY COURIER" if delivery_type == "courier" else "BYHAND"
    truck_no_clean       = cleaned.get("truckNo", "") or truck_no_placeholder
    license_no_clean    = cleaned.get("licenseNo", "")
    purchase_order_clean = "" if is_without_po else cleaned.get("purchaseOrder", "")
 
    variables = {
        # FIX (2026-08-13): Vendor Name / Vendor Code split -- this SAP
        # posting variable is still named VENDOR_NAME (matches
        # gate_in.robot's field mapping, not worth renaming), but the value
        # it needs was always the SAP vendor CODE, not the display name.
        # Reads from the new vendorCode field now instead of vendorName,
        # which is display/search text only from here on (see
        # gate_in_entries.vendor_code, schema_migration_v25).
        "VENDOR_NAME":    _s(cleaned.get("vendorCode", "")),
        "TRANSPORTER":    _s(cleaned.get("transporter", "")),
        "TRUCK_NO":       _s(truck_no_clean),
        "DRIVER_NAME":    _s(cleaned.get("driverName", "")),
        "LICENSE_NO":     _s(license_no_clean),
        "CONTAINER_NO":   _s(cleaned.get("containerNo", "")),
        "CATEGORY":       _s(cleaned.get("category", "")),
        # FIX: defense-in-depth 40-char cap (matches SAP's txtP_MATNR DDIC
        # limit) -- gatein_operations.upsert_gatein_entry now caps this at
        # write time for anything saved going forward, but this backstop
        # covers any row already in the DB from before that fix landed.
        "MATERIAL":       _s(cleaned.get("material", ""))[:40],
        # "CHALLAN_NO":     cleaned.get("challanNo", ""),
        "CHALLAN_NO": challan_numeric,
        "CHALLAN_QTY":    _s(cleaned.get("challanQty", "")),
        "BOE_NO":         _s(cleaned.get("boeNo", "")),
        "PURCHASE_ORDER": _s(purchase_order_clean),
        "NUM_PERSONS":    _s(cleaned.get("numPersons", "1")) or "1",
        "GATE_PASS_NO":   _s(cleaned.get("gatePassNo", "")),
        "NOTE":           _s(cleaned.get("note", "")),
        "GATE_IN_DATE":   _to_sap_date(data.get("gateInDate", "")),
        "GATE_IN_TIME":   _s(data.get("gateInTime", "")),
    }
 
    # FIX: Gate In is now forced onto the shared spl_rpa .env SAP login
    # for every submitter, regardless of LDAP identity -- explicit client
    # decision. Same pattern as execute_po_fetch_sap's existing exception
    # to the v16 per-user credential mechanism (see credential_cache.py):
    # deliberately no extra_env override passed here, so _run_rf_script's
    # subprocess always falls through to .env's SAP_USERNAME/SAP_PASSWORD.
    # MIGO 103/105 and MIRO are UNCHANGED -- they still use each LDAP
    # user's own SAP login as before; this exception is Gate In only.
    result = _run_rf_script(
        "gate_in.robot", variables, timeout_seconds=180
    )
    if not result["success"]:
        return {"success": False, "error": result["error"], "gate_in_number": None}
 
    gin = _extract_marked_value(result["output"], "GATE_IN_NUMBER")
    sap_msg = _extract_marked_value(result["output"], "GATE_IN_STATUS_MSG") or "No status bar message captured"
 
   
    if not gin:
        # FIX: same class of misleading message fixed in execute_migo_103_sap
        # -- "posted but not captured" implies SAP posting happened when a
        # genuine script crash (never reached the point of posting) is
        # lumped in with the rarer case where Post really was clicked but
        # the GIN just didn't parse from the status bar. Distinguish via RF's
        # own pass/fail summary so a hard crash says plainly "not done, try
        # again" instead of implying an ambiguous partial success.
        if not _rf_test_actually_passed(result["output"]):
            return {
                "success": False,
                "error": (
                    "Gate In not done — the automation script errored out "
                    "before completing (see the RF log for the exact step "
                    "and error). No Gate In was posted in SAP. Try again."
                ),
                "gate_in_number": None
            }
        return {
            "success": False,
            "error": (
                "Gate In ran but no Gate In Number was captured from the "
                "SAP status bar. Check SAP manually before retrying."
            ),
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
 
    # FIX: SAP's item-text field (txtGOITEM-SGTXT, filled once per line from
    # REMARKS today) and header text field are both ~40-char SAP fields, same
    # class of issue as Gate In's Material field -- nothing capped these
    # before, so an oversized OCR/typed value would ship straight through to
    # SAP with no server-side backstop. Also capping each item's own
    # short_text here even though migo_103.robot doesn't read it yet today
    # (see separate note to the team about SGTXT currently being filled from
    # REMARKS for every line, not from each item's own short_text) -- this
    # way it's already safe the moment that gets wired in.
    # FIX: server-side char-limit backstops matching each field's real SAP
    # DDIC length (client-confirmed 2026-08-04) -- mirrors the maxlength
    # attributes on migo_103.html's inputs, so a direct API call bypassing
    # the browser can't ship an oversized value into SAP either. qty is
    # capped the same way per line (13 chars, matches SAP's quantity field).
    for _item in items_data:
        if isinstance(_item, dict) and _item.get("short_text"):
            _item["short_text"] = str(_item["short_text"])[:40]
        if isinstance(_item, dict):
            if _item.get("qty_expected") is not None:
                _item["qty_expected"] = str(_item["qty_expected"])[:13]
            if _item.get("qty_actual") is not None:
                _item["qty_actual"] = str(_item["qty_actual"])[:13]
 
    items_json_str = json.dumps(items_data)
    items_json_b64 = base64.b64encode(items_json_str.encode()).decode()
 
    # v20: strip leading zeros off the Gate In Number before it goes into
    # SAP's header text field -- SAP itself zero-pads GIN (e.g.
    # "0000038656") but the client doesn't want that padding posted.
    # Backstop for the same strip already applied to what's displayed/
    # submitted from migo_103.html (see app.py's view_detail()) -- kept
    # here too in case this is ever called with an unstripped value from
    # somewhere else.
    header_text_raw = cleaned.get("migoHeaderText", "") or ""
    header_text_clean = header_text_raw.lstrip("0") or header_text_raw
 
    variables = {
        "PO_NUMBER":      (_s(cleaned.get("purchaseOrder", "")) or _s(cleaned.get("migoPoNumber", "")))[:10],
        "DOC_DATE":       _to_sap_date(cleaned.get("migoDocDate", "")),
        "POST_DATE":      _to_sap_date(cleaned.get("migoPostDate", datetime.now().strftime("%Y-%m-%d"))),
        "DELIVERY_NOTE":  _s(cleaned.get("migoDeliveryNote", ""))[:16],
        "BILL_OF_LADING": _s(cleaned.get("migoBillOfLading", ""))[:16],
        "GR_SLIP_NO":     _s(cleaned.get("migoGRSlipNo", ""))[:10],
        "HEADER_TEXT":    header_text_clean[:25],
        "REMARKS":        (cleaned.get("migoRemarks", "") or "")[:40],
        "ITEMS_JSON_B64": items_json_b64,
    }
 
    result = _run_rf_script(
        "migo_103.robot", variables, timeout_seconds=300,
        extra_env=_sap_credential_env(data)
    )
    if not result["success"]:
        return {"success": False, "error": result["error"], "material_doc_number": None}
 
    mat_doc = _extract_marked_value(result["output"], "MATERIAL_DOC_NUMBER")
 
    # FIX (2026-08-10): capture SAP's own status-bar text (see migo_103.robot's
    # Capture Final SAP Status, which now emits this unconditionally in
    # teardown) and append it to whichever failure message gets returned
    # below, so the user sees exactly what SAP showed (e.g. "Purchase order
    # 4100035670 not yet released") instead of only a generic canned string.
    # Empty/None if the marker wasn't found or SAP's status bar was blank.
    sap_status = _extract_marked_value(result["output"], "SAP_STATUS_MSG")
    sap_status_suffix = f' SAP showed: "{sap_status}"' if sap_status else ""
 
    if not mat_doc:
        # FIX: don't say "posted" when the script actually errored out
        # before ever reaching Post -- see _rf_test_actually_passed's
        # docstring. Confirmed against history_id=39's real RF log: the
        # test failed with "Cannot find element with id ..." at Step 2 and
        # never got near the Post button, but this branch was reporting
        # "MIGO 103 posted but Material Document Number not captured" --
        # worded as if SAP posting might have happened ambiguously, when it
        # provably never did. Now distinguishes the two cases explicitly.
        if not _rf_test_actually_passed(result["output"]):
            return {
                "success": False,
                "error": (
                    "MIGO 103 not done — the automation script errored out "
                    "before completing (see the RF log for the exact step "
                    "and error). No SAP document was created. Try again."
                    + sap_status_suffix
                ),
                "material_doc_number": None
            }
        return {
            "success": False,
            "error": (
                "MIGO 103 ran but no Material Document Number was captured "
                "from the SAP status bar. Check SAP manually before retrying."
                + sap_status_suffix
            ),
            "material_doc_number": None
        }
 
    # FIX: real bug, confirmed against history_id=39's flow -- migo_103.robot's
    # own Step 6 (Read Status Bar With Retry / regexp match) returns the
    # literal string "MANUAL_CHECK_REQUIRED" when Post was clicked but no
    # digit pattern was found in the status bar (see migo_103.robot's Fill
    # MIGO 103 And Post, the "IF len($matches) == 0" branch). That string is
    # non-empty, so the `if not mat_doc:` check above never catches it --
    # this fell straight through to "success: True" with
    # material_doc_number = "MANUAL_CHECK_REQUIRED" (a literal string, not a
    # real SAP document number). Consequences: history.migo_103 gets marked
    # done, migo_entries.material_doc_number gets set to that garbage
    # string, and _process_migo_103 (rf_queue_worker.py) enqueues
    # migo103_link.robot to go search Contentverse/SAP for a document
    # literally named "MANUAL_CHECK_REQUIRED" -- which doesn't exist, so
    # that bot fails or misbehaves next, and MIGO 105 (which needs MIGO
    # 103's real material_doc_number as its own posting input) inherits the
    # same garbage value. execute_gate_in_sap already has the equivalent
    # guard for its own "MANUAL_CHECK_REQUIRED" case (see gin ==
    # "MANUAL_CHECK_REQUIRED" above) -- this mirrors that exact pattern.
    if mat_doc == "MANUAL_CHECK_REQUIRED":
        return {
            "success": False,
            "error": (
                "MIGO 103 was posted (Post was clicked) but no material "
                "document number could be read from the SAP status bar. "
                "Check SAP manually (TCODE: MIGO / MB03) before retrying -- "
                "retrying without checking risks creating a duplicate GR."
                + sap_status_suffix
            ),
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
        "MATERIAL_DOC_NUMBER": _s(data.get("material_doc_number", ""))[:10],
        "STORAGE_LOCATION":    _s(data.get("migo_105_storage_loc", "")),
        "VENDOR_INVOICE":      _s(cleaned.get("migo_105_vendor_invoice", "")),
        "REMARKS":             _s(data.get("migo_105_remarks", "")),
        "POST_DATE":           _to_sap_date(datetime.now().strftime("%Y-%m-%d")),
        "ITEMS_JSON_BATCH":    items_json_b64,
    }
 
    result = _run_rf_script(
        "migo_105.robot", variables, timeout_seconds=300,
        extra_env=_sap_credential_env(data)
    )
    if not result["success"]:
        return {"success": False, "error": result["error"]}
 
    miro_doc = _extract_marked_value(result["output"], "MIRO_DOC_NUMBER")
 
    # ── FIX: MIGO 105 does generate a doc — missing means it didn't post ──
    if not miro_doc:
        # Same crashed-vs-ambiguous distinction as execute_gate_in_sap /
        # execute_migo_103_sap above -- a genuine script crash shouldn't be
        # worded as if something might have posted.
        if not _rf_test_actually_passed(result["output"]):
            logger.error(
                "MIGO 105 script errored out before completing. "
                f"Check robot log: {result.get('output_dir')}"
            )
            return {
                "success": False,
                "error": (
                    "MIGO 105 not done — the automation script errored out "
                    "before completing (see the RF log for the exact step "
                    "and error). No SAP document was created. Try again."
                )
            }
        logger.error(
            "MIGO 105 robot completed but no document number captured. "
            f"Check robot log: {result.get('output_dir')}"
        )
        return {
            "success": False,
            "error": (
                "MIGO 105 ran but did not capture a document number from SAP. "
                "Check SAP manually before retrying."
            )
        }
 
    # FIX: same class of bug just found and fixed in execute_migo_103_sap
    # above (see that function's comment for the full explanation) --
    # migo_105.robot's own status-bar-not-found branch returns the literal
    # string "MANUAL_CHECK_REQUIRED" (non-empty), so the `if not miro_doc:`
    # check above never catches it and this was falling through to
    # "success: True" with miro_doc_number = "MANUAL_CHECK_REQUIRED", which
    # would then get stored as MIGO 105's material_doc_number and mark
    # history.migo_105 done even though Post's outcome was never confirmed.
    if miro_doc == "MANUAL_CHECK_REQUIRED":
        return {
            "success": False,
            "error": (
                "MIGO 105 was posted (Post was clicked) but no document "
                "number could be read from the SAP status bar. Check SAP "
                "manually before retrying -- retrying without checking "
                "risks creating a duplicate posting."
            )
        }
    # ─────────────────────────────────────────────────────────────────
 
    return {"success": True, "error": None, "miro_doc_number": miro_doc}
 
 
# ============================================================
# MIRO
# ============================================================
 
def execute_miro_sap(data: dict) -> dict:
    variables = {
        "REFERENCE_NUMBER": _s(data.get("miroReference", ""))[:16],
        "INVOICE_DATE":     _to_sap_date(data.get("miroInvoiceDate", "")),
        "PO_NUMBER":        _s(data.get("miroPurchaseOrder", ""))[:10],
        "POSTING_DATE":     _to_sap_date(datetime.now().strftime("%Y-%m-%d")),
    }
    result = _run_rf_script(
        "miro.robot", variables, timeout_seconds=300,
        extra_env=_sap_credential_env(data)
    )
    if not result["success"]:
        return {"success": False, "error": result["error"]}
 
    fi_doc = _extract_marked_value(result["output"], "FI_DOC_NUMBER")
 
    # ── FIX: treat missing FI_DOC_NUMBER as failure ──────────────────
    if not fi_doc:
        # Same crashed-vs-ambiguous distinction as the other three bots
        # above -- a genuine script crash shouldn't be worded as if
        # something might have posted.
        if not _rf_test_actually_passed(result["output"]):
            logger.error(
                "MIRO script errored out before completing. "
                f"Check robot log: {result.get('output_dir')}"
            )
            return {
                "success": False,
                "error": (
                    "MIRO not done — the automation script errored out "
                    "before completing (see the RF log for the exact step "
                    "and error). No SAP document was created. Try again."
                )
            }
        logger.error(
            f"MIRO robot completed but FI_DOC_NUMBER not found in output. "
            f"Check robot logs at: {result.get('output_dir')}"
        )
        return {
            "success": False,
            "error": (
                "MIRO ran but did not capture a document number from SAP. "
                "Check SAP manually before retrying."
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
 
    # v16: po_fetch (ME23N line-item read) always uses the shared spl_rpa
    # .env SAP login -- deliberately no extra_env/credential override here.
    # It's a read-only PO lookup used to populate MIGO's line items, not an
    # attributable posting, so the per-user LDAP credential mechanism (see
    # credential_cache.py) intentionally never reaches this bot. See the
    # SAP_USERNAME/SAP_PASSWORD comment block in .env for the full picture.
    variables = {"PO_NUMBER": po_number}
    # FIX: was 120s -- too tight for a PO with many line items. Fetch PO
    # Line Items runs TWO separate per-item loops (Open Quantity: ~3s/item
    # for the next-item click+read; HSN/SAC: ~2.5s/item for the combo
    # select+India-tab read), on top of login/navigation overhead -- a PO
    # with 20+ lines (seen concretely on the "41xxxxxxxx" PO series, which
    # runs notably more items than most) could exceed 120s well before
    # finishing, hard-killing the whole run via subprocess timeout. That
    # skips the script's own Close SAP Session teardown entirely (RF never
    # gets a chance to run it) and previously left SAP orphaned open with
    # nothing left to close it -- see the _force_kill_sap() cleanup now
    # added to _run_rf_script's own TimeoutExpired handler as a backstop,
    # but raising this to a realistic ceiling is the actual fix: no
    # legitimate PO should need anywhere near 2700s.
    result = _run_rf_script("po_fetch.robot", variables, timeout_seconds=2700)
    # FIX: real root cause of "bot finished, looped through everything, but
    # SAP was still open at the end" -- po_fetch.robot's own Close SAP
    # Session only killed saplogon.exe, never saplgpad.exe/sapgui.exe (fixed
    # directly in that file now, to match every other script's pattern).
    # This is a Python-side backstop on top of that fix, not a replacement
    # for it: mirrors the existing _force_kill_sap() already called before
    # po_fetch runs (see the top of _run_rf_script) with a matching one
    # after, regardless of success/failure -- every other bot does its own
    # fresh login at the start of its own run, so nothing downstream expects
    # a SAP session to still be alive right after po_fetch finishes.
    _force_kill_sap()
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
 
    # v16: po_list_fetch (ME2N open-PO lookup) always uses the shared
    # spl_rpa .env SAP login too -- same reasoning as po_fetch above.
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
#
# v17: no longer coupled to MIGO 103 at all (SAP confirmed posting order
# between the two doesn't matter). For a without_po record, MIGO 103
# capturing a real PO number just logs a pending_po_updates row (see
# rf_queue_worker.py._process_migo_103 + database/pending_po_operations.py)
# targeted at the original Gate In submitter (gate_in_entries.submitted_by).
# THAT person triggers this job themselves, whenever they get to it, from
# the History page's Pending PO Updates panel (app.py's
# /api/pending_po_updates/<id>/run) -- so the SAP credential used here is
# always the same person who did the original Gate In, not whoever ran
# MIGO 103. See rf_queue_worker.py._process_update_gatein_po.
#
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
 
    result = _run_rf_script(
        # FIX (2026-08-11): bumped 180s -> 300s (matches migo_103's timeout)
        # -- 180s was tight for a full SAP login + navigation + grid update +
        # teardown under normal SAP GUI slowness, and a run that finished
        # its real work but overran teardown was being reported as a hard
        # failure purely from the outer timeout (see the FIX note on
        # subprocess.TimeoutExpired in _run_rf_script above).
        "zgatein_update.robot", variables, timeout_seconds=300,
        extra_env=_sap_credential_env(data)
    )
 
    # FIX (2026-08-11): check the success marker BEFORE giving up on
    # result["success"] -- confirmed production case: the robot's own log
    # showed RESULT:GATEIN_UPDATE_STATUS:SUCCESS (the SAP update genuinely
    # went through), but the outer subprocess hit its timeout during
    # teardown/cleanup afterward, so result["success"] was False and this
    # used to short-circuit straight to a failure response without ever
    # looking at what the robot actually accomplished. Now the marker is
    # checked first, using whatever output was captured (full output on a
    # normal failure, partial output on a timeout -- see _run_rf_script) --
    # only falls through to a failure if no success marker is present
    # either way.
    status_val = _extract_marked_value(result.get("output") or "", "GATEIN_UPDATE_STATUS")
    if status_val and status_val.upper() == "SUCCESS":
        return {"success": True, "error": None}
 
    if not result["success"]:
        # FIX (2026-08-11): this used to pass result["error"] straight
        # through, which for a timeout/exit-code failure is a raw technical
        # string containing the server's internal log file path (see
        # _run_rf_script's "RF script failed (exit code N). Logs: <path>" /
        # "timed out after Ns" messages) -- shown as-is in the History
        # page's Pending PO Updates panel and in an alert(). Replaced with a
        # clean, non-technical message; the raw detail is still in the
        # server log (logger.error already captured it) for anyone who
        # needs to dig in.
        return {
            "success": False,
            "error": (
                "PO update did not complete — the SAP automation didn't "
                "finish in time or hit an error. You can retry from the "
                "Pending PO Updates panel; if it keeps failing, check SAP "
                "manually (TCODE: zgatein_update) before retrying again."
            )
        }
 
    return {
        "success": False,
        "error": (
            "PO update ran but SAP did not confirm success. Check SAP "
            "manually (TCODE: zgatein_update) before retrying."
        )
    }
 
 
# ============================================================
# DMS LINK ATTACH — MIGO 103 / MIGO 105 / MIRO (v18)
#
# Three separate, standalone robot scripts (NOT embedded in gate_in.robot /
# migo_103.robot / migo_105.robot / miro.robot) -- client decision: a DMS
# link failure must never be able to make the underlying SAP posting look
# failed, so each of these runs as its own rf_queue job, always AFTER the
# corresponding posting job has already succeeded and reported its own
# result independently.
#
# Placeholder script names below (migo103_link.robot / migo105_link.robot /
# miro_link.robot) -- not yet added to robot_scripts/, awaiting the actual
# scripts. Whoever adds them needs to either match this contract or this
# file needs updating to match theirs:
#   - reads MATERIAL_DOC_NUMBER and DOCUMENT_LINK via --variable (same
#     mechanism as every other script here, see _run_rf_script)
#   - opens/re-opens the relevant document in SAP using MATERIAL_DOC_NUMBER
#     the same way robot_scripts/migo_invoice_link.robot's "Enter Material
#     Document Number" step does (a persisted, already-posted document --
#     not the create-transaction screen)
#   - emits its own RESULT marker distinct from the posting step's own
#     marker, so a link-attach failure is distinguishable from a posting
#     failure: RESULT:MIGO103_LINK_STATUS:SUCCESS/FAILED,
#     RESULT:MIGO105_LINK_STATUS:SUCCESS/FAILED,
#     RESULT:MIRO_LINK_STATUS:SUCCESS/FAILED
# ============================================================
 
def execute_migo103_link_sap(data: dict) -> dict:
    variables = {
        "MATERIAL_DOC_NUMBER": data.get("material_doc_number", ""),
        "DOCUMENT_LINK":       data.get("document_link", ""),
    }
    result = _run_rf_script(
        "migo103_link.robot", variables, timeout_seconds=180,
        extra_env=_sap_credential_env(data)
    )
    if not result["success"]:
        return {"success": False, "error": result["error"]}
 
    status_val = _extract_marked_value(result["output"], "MIGO103_LINK_STATUS")
    if status_val and status_val.upper() == "SUCCESS":
        return {"success": True, "error": None}
    return {
        "success": False,
        "error": (
            "migo103_link robot ran but did not confirm the link was "
            f"attached. Check robot log: {result.get('output_dir')}"
        )
    }
 
 
def execute_migo105_link_sap(data: dict) -> dict:
    variables = {
        "MATERIAL_DOC_NUMBER": data.get("material_doc_number", ""),
        "DOCUMENT_LINK":       data.get("document_link", ""),
    }
    result = _run_rf_script(
        "migo105_link.robot", variables, timeout_seconds=180,
        extra_env=_sap_credential_env(data)
    )
    if not result["success"]:
        return {"success": False, "error": result["error"]}
 
    status_val = _extract_marked_value(result["output"], "MIGO105_LINK_STATUS")
    if status_val and status_val.upper() == "SUCCESS":
        return {"success": True, "error": None}
    return {
        "success": False,
        "error": (
            "migo105_link robot ran but did not confirm the link was "
            f"attached. Check robot log: {result.get('output_dir')}"
        )
    }
 
 
def execute_miro_link_sap(data: dict) -> dict:
    variables = {
        "MATERIAL_DOC_NUMBER": data.get("material_doc_number", ""),
        "DOCUMENT_LINK":       data.get("document_link", ""),
    }
    result = _run_rf_script(
        "miro_link.robot", variables, timeout_seconds=180,
        extra_env=_sap_credential_env(data)
    )
    if not result["success"]:
        return {"success": False, "error": result["error"]}
 
    status_val = _extract_marked_value(result["output"], "MIRO_LINK_STATUS")
    if status_val and status_val.upper() == "SUCCESS":
        return {"success": True, "error": None}
    return {
        "success": False,
        "error": (
            "miro_link robot ran but did not confirm the link was "
            f"attached. Check robot log: {result.get('output_dir')}"
        )
    }
 
 
# ============================================================
# DMS UPLOAD (Contentverse) — v18
#
# Chained into the same rf_queue right after po_fetch (or after gate_in on
# without-PO flows that skip po_fetch), instead of a standalone Windows
# Task Scheduler timer -- see services/rf_queue_worker.py._process_dms_upload
# and services/dms_upload_runner.py.run_dms_upload(), which this reuses
# as-is rather than duplicating its quarantine/lock/links-import logic.
#
# No per-record --variable inputs: dms_upload robots process whatever is
# currently sitting in DMS_STAGING_FOLDER with dms_status='staged' (which,
# right after gate_in, is normally just this one record, but may include
# older stragglers if a previous run failed) -- see run_dms_upload().
# ============================================================