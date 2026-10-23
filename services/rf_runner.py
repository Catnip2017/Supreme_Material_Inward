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
 
 
def _normalize_qty(raw) -> str:
    """
    FIX (2026-10-22): "418.0000" -> "418", "12.5000" -> "12.5".

    Invoice OCR/extraction hands us quantities with 4 decimals. SAP only
    accepts as many decimals as the unit allows -- litres/kg take 3, pieces
    take 0 -- so typing "418.0000" into MIGO's quantity field gets rejected
    with "Input must be in the format _.___.___.__~.___", the item screen
    locks, and the bot dies later on an unrelated field (confirmed: MIGO 103,
    PO 4100036177, unit L). Trailing zeros carry no value, so dropping them
    is always safe. A quantity that still has too many REAL decimals
    (e.g. 12.3456) is deliberately NOT rounded here -- rounding a received
    quantity is a business decision -- it is passed through unchanged, so SAP
    itself will reject it.
    Anything that is not a plain number (text, negatives, blanks) is
    returned unchanged.
    """
    s = str(raw).strip()
    plain = s.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", plain):
        return s
    if "." in plain:
        plain = plain.rstrip("0").rstrip(".")
    return plain or "0"


def _sap_msg_or(sap_text, fallback: str) -> str:
    """
    CHANGED (2026-10-22): the error shown to the user on a failed posting is
    SAP's own message, nothing else -- SAP's text already says why the entry
    was not made. The short fallback is used only when SAP gave no text at
    all (e.g. the bot crashed before reaching the status bar).
    """
    s = (sap_text or "").strip()
    return s if s else fallback


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


# MULTI-BOT FIX (2026-09-03, corrected 2026-09-08): every script that calls
# Coordinated SAP Login registers itself in the shared refcount file
# (C:\temp\sap_bot_refcount.txt) and only fully kills SAP when it's the
# last one registered. A script NOT in this set never calls Register Bot
# Start, so it was never counted there -- treating it as coordinated here
# would wrongly decrement some OTHER, real bot's registration instead of
# its own.
#
# CORRECTED 2026-09-08: this set used to list only po_fetch.robot and
# migo_103.robot -- true back on 2026-09-03 when the multi-bot conversion
# was still rolling out one script at a time, but by now (confirmed by
# grepping every .robot file's Settings section) ALL twelve SAP-GUI bots
# import sap_bot_coordination.py and go through Coordinated SAP Login, so
# all twelve register in the shared refcount. Leaving the other ten out of
# this set meant that if THEIR subprocess timed out, the code fell into
# the `else` branch below: _force_kill_sap() ran immediately and
# unconditionally (killing SAP for every other bot concurrently running,
# not just the timed-out one) AND the timed-out bot was never deregistered
# from the shared refcount file, leaking it upward permanently. That leak
# is also a plausible contributor to users needing to relogin more than
# expected -- a refcount that never truly returns to 0 confuses the
# separate shutdown-watcher cleanup that runs after the last bot exits.
# dms_upload.robot is intentionally excluded -- it's Selenium-based (DMS
# document upload), not a SAP GUI bot, and never touches the refcount file.
_COORDINATED_SCRIPTS = {
    "po_fetch.robot",
    "po_list_fetch.robot",
    "migo_103.robot",
    "migo_105.robot",
    "migo_101.robot",
    "migo_reversal.robot",
    "gate_in.robot",
    "zgatein_update.robot",
    "zgrn.robot",
    "miro.robot",
    "migo103_link.robot",
    "migo105_link.robot",
    "migo101_link.robot",
    "miro_link.robot",
}

_REFCOUNT_FILE = r"C:\temp\sap_bot_refcount.txt"
_REFCOUNT_LOCK_DIR = r"C:\temp\sap_bot_refcount.lock"


def _acquire_refcount_lock(max_wait_seconds: float = 30) -> bool:
    """
    Python-side equivalent of sap_coordination.resource's Acquire Refcount
    Lock -- same directory-creation-as-mutex convention, same paths, so a
    Python-side cleanup here can never race an RF bot's own
    register/deregister call.
    """
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        try:
            os.makedirs(_REFCOUNT_LOCK_DIR)
            return True
        except FileExistsError:
            time.sleep(0.2)
        except Exception:
            return False
    return False


def _release_refcount_lock() -> None:
    try:
        os.rmdir(_REFCOUNT_LOCK_DIR)
    except Exception:
        pass


def _deregister_bot_and_get_remaining() -> int:
    """
    Python-side equivalent of sap_coordination.resource's Register Bot
    End -- used when a script is force-terminated from this side (a
    subprocess.run() timeout) and therefore never gets to run its own
    Coordinated SAP Close. Without this, that bot's registration would
    leak forever: the counter would never come back down to 0, so no
    future bot would ever be recognized as "the last one out," and SAP
    would never get fully shut down again through the normal path.
    Decrements the same counter file the RF side uses, under the same
    lock, and returns the count remaining after the decrement.
    """
    got_lock = _acquire_refcount_lock()
    try:
        try:
            with open(_REFCOUNT_FILE) as f:
                current = int((f.read() or "0").strip() or "0")
        except Exception:
            current = 0
        new_count = max(0, current - 1)
        try:
            with open(_REFCOUNT_FILE, "w") as f:
                f.write(str(new_count))
        except Exception as e:
            logger.warning(f"Could not update SAP bot refcount file: {e}")
        return new_count
    finally:
        if got_lock:
            _release_refcount_lock()
 
 
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
    extra_env: Optional[dict] = None,
    history_id=None
) -> dict:
    # MULTI-BOT FIX (2026-09-03): this used to (a) unconditionally
    # taskkill SAP before every po_fetch.robot run, and (b) refuse to even
    # start ANY script for up to 240s if saplogon.exe was already running
    # for ANY reason, failing the job outright afterward. Both were built
    # for a world where only one script ever ran at a time -- under real
    # concurrency (spl_rpa running up to 6 windows at once), "SAP is
    # already running" is the NORMAL, expected state, not a busy signal to
    # wait out. Every SAP bot now handles "is SAP already open" itself via
    # sap_coordination.resource's Coordinated SAP Login (reuse the running
    # process, open a new window under it, only kill-and-restart if it's
    # genuinely stale) -- so this Python-side gate has nothing left to do
    # except get in the way. Removed outright rather than reworked.
    _t_start = time.time()   # timing log only -- see [RFTiming] in the finally below
    _wake_sap_session()

    script_path = os.path.join(config.RF_SCRIPTS_PATH, script_name)
    if not os.path.exists(script_path):
        msg = f"RF script not found: {script_path}"
        logger.error(msg)
        return {"success": False, "error": msg, "output": ""}

    # FIX (2026-08-21): logs used to all land flat in one folder, named only
    # <script>_<timestamp> -- with no history_id anywhere, finding every log
    # for a specific record meant opening folders one at a time and reading
    # the RESULT: markers inside each. Restructured into
    # <step>/<date>/h<history_id>_<time>/ so logs are (a) segregated by which
    # bot ran (one flat folder no longer holds every step type mixed
    # together), (b) segregated by day (a single folder won't grow to
    # thousands of entries), and (c) directly searchable by record --
    # `h340_` in a folder name is now enough to find every run for
    # history_id=340 across every step, without opening anything.
    hid_label = f"h{history_id}" if history_id not in (None, "", 0) else "h_unknown"
    step_label = script_name.replace(".robot", "")
    now = datetime.now()
    date_label = now.strftime("%Y-%m-%d")
    time_label = now.strftime("%H%M%S")
    output_dir = os.path.join(
        config.RF_OUTPUT_PATH,
        step_label,
        date_label,
        f"{hid_label}_{time_label}"
    )
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        "python", "-m", "robot",
        "--outputdir", output_dir,
        "--loglevel", "DEBUG",
        "--nostatusrc",
        # Embeds history_id into the RF log/report's own suite metadata too
        # (visible in log.html's "Metadata" section and output.xml), not
        # just the folder name -- so it's still identifiable even if a log
        # file gets copied/moved out of its folder.
        "--metadata", f"History_ID:{hid_label}",
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
 
    _t_run_start = time.time()
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
        #
        # MULTI-BOT FIX (2026-09-03): this used to always fully kill SAP
        # here, unconditionally -- fine when only one script ever ran at a
        # time, but under real concurrency a timeout in ONE bot must not
        # nuke every OTHER bot's still-active session too. For a
        # coordinated script (see _COORDINATED_SCRIPTS above), deregister
        # it from the shared refcount on its behalf (it never got to do
        # this itself -- the process was hard-killed before its own
        # Coordinated SAP Close could run) and only fully kill SAP if that
        # was the last bot still registered. Note: this bot's OWN window
        # may still be left open as an orphan even when other bots are
        # correctly left running -- there's no way from here to identify
        # and close just that one window, unlike the RF-side close which
        # has ${session} bound. Worth an occasional manual/scheduled check
        # for leftover orphaned windows, same as always.
        if script_name in _COORDINATED_SCRIPTS:
            remaining = _deregister_bot_and_get_remaining()
            if remaining > 0:
                logger.warning(
                    f"RF script '{script_name}' timed out, but {remaining} "
                    "other SAP bot(s) are still registered as active -- "
                    "leaving SAP running for them rather than killing "
                    "everything. This bot's own SAP window may be left "
                    "orphaned; a manual check may be needed."
                )
            else:
                logger.warning(
                    f"RF script '{script_name}' timed out and no other SAP "
                    "bot is registered as active -- fully clearing SAP."
                )
                _force_kill_sap()
        else:
            _force_kill_sap()
        return {"success": False, "error": msg, "output": timeout_combined, "output_dir": output_dir}
    except Exception as e:
        msg = f"Unexpected error running '{script_name}': {e}"
        logger.error(msg, exc_info=True)
        return {"success": False, "error": msg, "output": ""}
    finally:
        try:
            _t_end = time.time()
            logger.info(
                f"[RFTiming] script={script_name} history_id={history_id} "
                f"prep={_t_run_start - _t_start:.1f}s run={_t_end - _t_run_start:.1f}s "
                f"total={_t_end - _t_start:.1f}s"
            )
        except Exception:
            pass
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
 
    # FIX (2026-08-14): REVERTED the spl_rpa force from below -- client now
    # wants Gate In back on each submitter's own per-user SAP login, same
    # as MIGO 103/105/MIRO. gate_in.robot itself never needed any change --
    # it's had the SAP_USER_OVERRIDE/SAP_PASS_OVERRIDE env-var check
    # (falling back to shared .env when neither is set) since v16, it just
    # never actually received one, because (a) app.py's _enqueue_sap_job()
    # forced auth_type to "local" for the "gate_in" step specifically, and
    # (b) this call never passed extra_env even if a credential had been
    # attached. Both fixed together -- see _enqueue_sap_job's
    # FORCE_LOCAL_STEPS (now just {"update_gatein_po"}) and the
    # extra_env= added below.
    #
    # update_gatein_po (zgatein_update) is UNCHANGED -- still always
    # spl_rpa, client decision, since it can also run unattended via the
    # v21 auto-trigger with no live user credential available at all.
    #
    # Old FIX this replaces, kept for context: "Gate In is now forced onto
    # the shared spl_rpa .env SAP login for every submitter, regardless of
    # LDAP identity -- explicit client decision." That decision changed;
    # this comment is the record of why.
    result = _run_rf_script(
        "gate_in.robot", variables, timeout_seconds=180,
        extra_env=_sap_credential_env(data), history_id=data.get("history_id")
    )
    if not result["success"]:
        # FIX (2026-08-27): result["error"] here is _run_rf_script's own
        # generic exit-code-failure message, which embeds the bot machine's
        # raw internal Windows path (RF_OUTPUT_PATH/output_dir) -- see the
        # identical fix applied to execute_zgrn_print_sap and the other
        # execute_*_sap functions below. Logged server-side only now; the
        # user-facing message stays generic.
        logger.error(f"Gate In script failure: {result['error']}")
        return {
            "success": False,
            "error": "Gate In did not complete — the automation hit an error before finishing. Check the RF log or try again.",
            "gate_in_number": None
        }
 
    gin = _extract_marked_value(result["output"], "GATE_IN_NUMBER")
    gate_sap_msg = _extract_marked_value(result["output"], "GATE_IN_STATUS_MSG")
 
   
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
                "error": _sap_msg_or(gate_sap_msg, "Gate In failed — nothing was posted. Try again."),
                "gate_in_number": None
            }
        return {
            "success": False,
            "error": _sap_msg_or(gate_sap_msg, "Gate In: SAP gave no Gate In number. Check SAP before retrying."),
            "gate_in_number": None
        }
 
    # ── FIX: MANUAL_CHECK_REQUIRED means SAP didn't return a number ──
    if gin == "MANUAL_CHECK_REQUIRED":
        return {
            "success": False,
            "error": _sap_msg_or(gate_sap_msg, "Gate In submitted but SAP gave no Gate In number. Check SAP (zmmtmn) before retrying."),
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
                _item["qty_expected"] = _normalize_qty(_item["qty_expected"])[:13]
            if _item.get("qty_actual") is not None:
                _item["qty_actual"] = _normalize_qty(_item["qty_actual"])[:13]
            # ADDED (2026-08-21): item_no (PO Item Number / EBELP) is what
            # migo_103.robot now matches each line against instead of array
            # position -- SAP's MIGO grid silently drops PO lines already
            # fully received, so "the Nth item we sent" was never reliably
            # "the Nth row in SAP's grid" (confirmed root cause of the
            # wrong-material-code GRIN issue). item_no is read off the real
            # PO by po_fetch.robot and carried through the matching UI
            # untouched -- capped defensively to SAP's own EBELP field
            # width (5 digits, e.g. "00130").
            if _item.get("item_no") is not None:
                _item["item_no"] = str(_item["item_no"])[:5]
 
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
        extra_env=_sap_credential_env(data), history_id=data.get("history_id")
    )
    if not result["success"]:
        # FIX (2026-08-27): see execute_gate_in_sap's identical fix above --
        # result["error"] leaks the bot machine's raw internal path.
        logger.error(f"MIGO 103 script failure: {result['error']}")
        return {
            "success": False,
            "error": "MIGO 103 did not complete — the automation hit an error before finishing. Check the RF log or try again.",
            "material_doc_number": None,
            "item_results": []
        }

    mat_doc = _extract_marked_value(result["output"], "MATERIAL_DOC_NUMBER")

    # FIX (2026-08-10): capture SAP's own status-bar text (see migo_103.robot's
    # Capture Final SAP Status, which now emits this unconditionally in
    # teardown) and append it to whichever failure message gets returned
    # below, so the user sees exactly what SAP showed (e.g. "Purchase order
    # 4100035670 not yet released") instead of only a generic canned string.
    # Empty/None if the marker wasn't found or SAP's status bar was blank.
    sap_status = _extract_marked_value(result["output"], "SAP_STATUS_MSG")

    # ADDED (2026-08-21): per-item match/post outcome. migo_103.robot now
    # matches by PO Item Number (EBELP) instead of array position, and an
    # item can legitimately come back "item not found" (already fully
    # received, dropped from SAP's grid) without failing the whole run.
    # Logged BEFORE Post is clicked in the robot script, so it's present
    # even on a MANUAL_CHECK_REQUIRED or crashed run -- surfaced here so
    # the caller (rf_queue_worker.py) can show/store which items actually
    # got matched vs. skipped, instead of only one aggregate result.
    item_results_raw = _extract_marked_value(result["output"], "ITEM_RESULTS")
    item_results = []
    if item_results_raw:
        try:
            item_results = json.loads(item_results_raw)
        except Exception:
            logger.warning(f"Could not parse ITEM_RESULTS marker as JSON: {item_results_raw[:300]}")

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
                "error": _sap_msg_or(sap_status, "MIGO 103 failed — nothing was posted. Try again."),
                "material_doc_number": None,
                "item_results": item_results
            }
        return {
            "success": False,
            "error": _sap_msg_or(sap_status, "MIGO 103: SAP gave no document number. Check SAP before retrying."),
            "material_doc_number": None,
            "item_results": item_results
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
    # ADDED (2026-09-04): migo_103.robot now checks the status bar right
    # after the PO number is entered, and stops immediately with this
    # distinct "PO_NOT_RELEASED" marker if SAP rejects it there (e.g.
    # "Purchasing document 4100035963 not yet released") -- confirmed as
    # a real case via output_23.xml, where the script instead used to
    # press on into a screen that doesn't work for a blocked PO, and the
    # user ended up seeing "Function code cannot be selected" (the LAST,
    # unrelated status-bar text by the time the script gave up) instead
    # of the real reason. Handled as its own case, separate from the
    # MANUAL_CHECK_REQUIRED branch below, because that one's message
    # says "MIGO 103 was posted (Post was clicked)... retrying risks
    # creating a duplicate GR" -- which would be actively wrong here:
    # this is caught before Post is ever clicked, so nothing was posted
    # and there's no duplicate-GR risk.
    if mat_doc == "PO_NOT_RELEASED":
        return {
            "success": False,
            "error": _sap_msg_or(sap_status, "PO not released in SAP — nothing was posted."),
            "material_doc_number": None,
            "item_results": item_results
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
            "error": _sap_msg_or(sap_status, "MIGO 103 posted but SAP gave no document number. Check SAP (MB03) before retrying — a retry may duplicate it."),
            "material_doc_number": None,
            "item_results": item_results
        }

    return {
        "success": True,
        "material_doc_number": mat_doc,
        "error": None,
        "item_results": item_results
    }
 
 
# ============================================================
# MIGO 103 REVERSAL — SAP action A03 (ADDED 2026-08-21)
#
# Runs migo_reversal.robot against an already-posted MIGO 103 material
# document. This is a REAL, final SAP posting (Post is live, confirmed
# 2026-08-21) -- it creates a NEW SAP document that references the
# original, it does not undo/delete the original GR. history.migo_103 /
# material_doc_number must stay untouched; the caller (rf_queue_worker.py)
# is expected to record the reversal via db_operations.mark_migo_103_reversed
# using this function's returned reversal_doc_number, not overwrite the
# original posting's own record.
# ============================================================

def execute_migo_103_reversal_sap(data: dict) -> dict:
    """
    data: {"material_doc_number": str, "history_id": int}

    Returns {"success": bool, "error": str|None, "reversal_doc_number":
    str|None, "raw_result": dict} -- raw_result is the full parsed
    REVERSAL_STATUS payload (matdoc_entry_status/header_text_status/
    post_status included) for logging/debugging even on failure.
    """
    mat_doc = str(data.get("material_doc_number", "") or "").strip()
    history_id = data.get("history_id", "")
    if not mat_doc:
        return {
            "success": False,
            "error": f"Missing material_doc_number — cannot run MIGO reversal for history_id={history_id}",
            "reversal_doc_number": None,
            "raw_result": {}
        }

    variables = {
        "MATERIAL_DOC_NUMBER": mat_doc,
    }

    # Per-user credential override, same as execute_migo_103_sap -- a
    # reversal is an attributable SAP posting, not a shared read-only
    # lookup like po_fetch.
    result = _run_rf_script(
        "migo_reversal.robot", variables, timeout_seconds=240,
        extra_env=_sap_credential_env(data), history_id=data.get("history_id")
    )
    if not result["success"]:
        # FIX (2026-08-27): see execute_gate_in_sap's identical fix above --
        # result["error"] leaks the bot machine's raw internal path.
        logger.error(f"MIGO Reversal script failure: {result['error']}")
        return {
            "success": False,
            "error": "MIGO Reversal did not complete — the automation hit an error before finishing. Check the RF log or try again.",
            "reversal_doc_number": None,
            "raw_result": {}
        }

    status_raw = _extract_marked_value(result["output"], "REVERSAL_STATUS")
    if not status_raw:
        if not _rf_test_actually_passed(result["output"]):
            return {
                "success": False,
                "error": (
                    "MIGO Reversal not done — the automation script errored "
                    "out before completing (see the RF log for the exact "
                    "step and error). Check SAP manually before retrying."
                ),
                "reversal_doc_number": None,
                "raw_result": {}
            }
        return {
            "success": False,
            "error": (
                "MIGO Reversal ran but no result was captured. "
                "Check SAP manually before retrying."
            ),
            "reversal_doc_number": None,
            "raw_result": {}
        }

    try:
        parsed = json.loads(status_raw)
    except Exception as e:
        logger.error(f"Could not parse REVERSAL_STATUS marker as JSON: {status_raw[:300]} ({e})")
        return {
            "success": False,
            "error": (
                "MIGO Reversal ran but its result could not be read. "
                "Check SAP manually before retrying."
            ),
            "reversal_doc_number": None,
            "raw_result": {}
        }

    if parsed.get("status") != "COMPLETED":
        step = parsed.get("step", "unknown step")
        message = parsed.get("message") or parsed.get("post_status") or "no SAP message captured"
        return {
            "success": False,
            "error": (
                f"MIGO Reversal did not complete (failed at {step}). "
                f'SAP showed: "{message}". Check SAP manually (TCODE: MIGO) '
                "before retrying -- retrying without checking risks a "
                "duplicate reversal attempt."
            ),
            "reversal_doc_number": None,
            "raw_result": parsed
        }

    reversal_doc_number = parsed.get("reversal_doc_number") or None
    return {
        "success": True,
        "error": None,
        "reversal_doc_number": reversal_doc_number,
        "raw_result": parsed
    }


# ============================================================
# MIGO 101 / MIGO 105 REVERSAL (ADDED 2026-10-14, client request)
#
# Uses the EXACT same bot as the MIGO 103 reversal above (migo_reversal.robot,
# SAP action A03) -- that robot only ever receives one thing, a material
# document number, and cancels it. It has no idea which step the number came
# from; the caller (app.py route -> rf_queue_worker handler) is what decides
# which document is passed, always from the record's OWN column for that step
# (101 -> history.migo_101_doc_number, 105 -> history.migo_105_doc_number).
#
# Written as one shared helper + two thin wrappers so 101 and 105 cannot
# drift apart. execute_migo_103_reversal_sap above is left exactly as it was.
#
# Extra safety vs the 103 version: the robot echoes back the document it
# worked on ("matdoc" in REVERSAL_STATUS); if that does not equal the number
# this function sent, the result is treated as a failure (and says to check
# SAP manually, since SAP may already have posted something).
# ============================================================

def _execute_step_reversal_sap(data: dict, step_label: str) -> dict:
    """
    data: {"material_doc_number": str, "history_id": int}
    step_label: "MIGO 101" or "MIGO 105" -- used in messages/logs only.

    Same return shape as execute_migo_103_reversal_sap.
    """
    mat_doc = str(data.get("material_doc_number", "") or "").strip()
    history_id = data.get("history_id", "")
    if not mat_doc:
        return {
            "success": False,
            "error": f"Missing material_doc_number — cannot run {step_label} reversal for history_id={history_id}",
            "reversal_doc_number": None,
            "raw_result": {}
        }

    logger.info(f"{step_label} reversal starting — history_id={history_id} doc={mat_doc}")

    result = _run_rf_script(
        "migo_reversal.robot", {"MATERIAL_DOC_NUMBER": mat_doc}, timeout_seconds=240,
        extra_env=_sap_credential_env(data), history_id=data.get("history_id")
    )
    if not result["success"]:
        logger.error(f"{step_label} Reversal script failure: {result['error']}")
        return {
            "success": False,
            "error": f"{step_label} Reversal did not complete — the automation hit an error before finishing. Check the RF log or try again.",
            "reversal_doc_number": None,
            "raw_result": {}
        }

    status_raw = _extract_marked_value(result["output"], "REVERSAL_STATUS")
    if not status_raw:
        if not _rf_test_actually_passed(result["output"]):
            return {
                "success": False,
                "error": (
                    f"{step_label} Reversal not done — the automation script errored "
                    "out before completing (see the RF log for the exact "
                    "step and error). Check SAP manually before retrying."
                ),
                "reversal_doc_number": None,
                "raw_result": {}
            }
        return {
            "success": False,
            "error": (
                f"{step_label} Reversal ran but no result was captured. "
                "Check SAP manually before retrying."
            ),
            "reversal_doc_number": None,
            "raw_result": {}
        }

    try:
        parsed = json.loads(status_raw)
    except Exception as e:
        logger.error(f"Could not parse REVERSAL_STATUS marker as JSON: {status_raw[:300]} ({e})")
        return {
            "success": False,
            "error": (
                f"{step_label} Reversal ran but its result could not be read. "
                "Check SAP manually before retrying."
            ),
            "reversal_doc_number": None,
            "raw_result": {}
        }

    # Document-number echo check (see header comment).
    echoed = str(parsed.get("matdoc") or "").strip()
    if echoed and echoed != mat_doc:
        logger.error(
            f"{step_label} reversal DOC MISMATCH — history_id={history_id} "
            f"sent={mat_doc} bot_reported={echoed}"
        )
        return {
            "success": False,
            "error": (
                f"{step_label} Reversal result did not match the document that was sent "
                f"(sent {mat_doc}, bot reported {echoed}). Nothing has been recorded -- "
                "check SAP manually (TCODE: MIGO) before retrying."
            ),
            "reversal_doc_number": None,
            "raw_result": parsed
        }

    if parsed.get("status") != "COMPLETED":
        step = parsed.get("step", "unknown step")
        message = parsed.get("message") or parsed.get("post_status") or "no SAP message captured"
        return {
            "success": False,
            "error": (
                f"{step_label} Reversal did not complete (failed at {step}). "
                f'SAP showed: "{message}". Check SAP manually (TCODE: MIGO) '
                "before retrying -- retrying without checking risks a "
                "duplicate reversal attempt."
            ),
            "reversal_doc_number": None,
            "raw_result": parsed
        }

    reversal_doc_number = parsed.get("reversal_doc_number") or None
    logger.info(
        f"{step_label} reversal done — history_id={history_id} "
        f"original={mat_doc} reversal={reversal_doc_number}"
    )
    return {
        "success": True,
        "error": None,
        "reversal_doc_number": reversal_doc_number,
        "raw_result": parsed
    }


def execute_migo_101_reversal_sap(data: dict) -> dict:
    """Reverses a posted MIGO 101 document. data["material_doc_number"] must
    be this record's own MIGO 101 document (history.migo_101_doc_number)."""
    return _execute_step_reversal_sap(data, "MIGO 101")


def execute_migo_105_reversal_sap(data: dict) -> dict:
    """Reverses a posted MIGO 105 document. data["material_doc_number"] must
    be this record's own MIGO 105 document (history.migo_105_doc_number) --
    NOT the MIGO 103 document that migo_entries.material_doc_number holds."""
    return _execute_step_reversal_sap(data, "MIGO 105")


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
        extra_env=_sap_credential_env(data), history_id=data.get("history_id")
    )
    if not result["success"]:
        # FIX (2026-08-27): see execute_gate_in_sap's identical fix above --
        # result["error"] leaks the bot machine's raw internal path.
        logger.error(f"MIGO 105 script failure: {result['error']}")
        return {
            "success": False,
            "error": "MIGO 105 did not complete — the automation hit an error before finishing. Check the RF log or try again."
        }
 
    miro_doc = _extract_marked_value(result["output"], "MIRO_DOC_NUMBER")

    # ADDED: same popup-capture fix as execute_migo_103_sap -- migo_105.robot
    # now reads SAP's popup/status-bar message before dismissing it (a
    # popup like a deficit-quantity check often carries the real reason a
    # manual check is required, which the status bar alone won't show).
    sap_status = _extract_marked_value(result["output"], "MIGO105_STATUS_MSG")

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
                "error": _sap_msg_or(sap_status, "MIGO 105 failed — nothing was posted. Try again.")
            }
        logger.error(
            "MIGO 105 robot completed but no document number captured. "
            f"Check robot log: {result.get('output_dir')}"
        )
        return {
            "success": False,
            "error": _sap_msg_or(sap_status, "MIGO 105: SAP gave no document number. Check SAP before retrying.")
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
            "error": _sap_msg_or(sap_status, "MIGO 105 posted but SAP gave no document number. Check SAP before retrying — a retry may duplicate it.")
        }
    # ─────────────────────────────────────────────────────────────────
 
    return {"success": True, "error": None, "miro_doc_number": miro_doc}
 
 
# ============================================================
# MIGO 101 — Goods Receipt against an Outbound Delivery. Combines
# migo_103's per-line EBELP-matching payload style with migo_105's
# ability to carry a batch value per line, in ONE items blob
# (ITEMS_JSON_B64) -- unlike migo_105, MIGO 101 does its own matching (no
# separate confirmed-pairs source to read from), so there is no need for
# a second ITEMS_JSON_BATCH pass the way execute_migo_105_sap has.
# ============================================================

def execute_migo_101_sap(data: dict) -> dict:
    logger.info(f"MIGO 101 payload keys: {list(data.keys())}")
    cleaned = _clean_dict(data, ["vendorInvoiceDetail"])

    items_data = cleaned.get("items_data", [])
    if isinstance(items_data, str):
        try:
            items_data = json.loads(items_data)
        except Exception:
            items_data = []
    if not isinstance(items_data, list):
        items_data = []

    # Same defensive field-length caps as execute_migo_103_sap -- matches
    # SAP's DDIC field widths so a direct API call bypassing the browser
    # can't ship an oversized value into SAP. batch is capped to 10 chars
    # -- SAP's CHARG (batch) field width, same as migo_105.robot's
    # ctxtGOITEM-CHARG target.
    for _item in items_data:
        if not isinstance(_item, dict):
            continue
        if _item.get("short_text"):
            _item["short_text"] = str(_item["short_text"])[:40]
        if _item.get("qty_expected") is not None:
            _item["qty_expected"] = _normalize_qty(_item["qty_expected"])[:13]
        if _item.get("qty_actual") is not None:
            _item["qty_actual"] = _normalize_qty(_item["qty_actual"])[:13]
        if _item.get("item_no") is not None:
            _item["item_no"] = str(_item["item_no"])[:5]
        if _item.get("batch") is not None:
            _item["batch"] = str(_item["batch"])[:10]

    items_json_str = json.dumps(items_data)
    items_json_b64 = base64.b64encode(items_json_str.encode()).decode()

    # Same lstrip("0") backstop as execute_migo_103_sap -- app.py's shared
    # render route already strips the leading zeros off Header Text (GIN
    # fallback) before display/submission, but this covers the same value
    # if it ever arrives here unstripped from somewhere else.
    header_text_raw_101 = cleaned.get("migo101HeaderText", "") or ""
    header_text_clean_101 = header_text_raw_101.lstrip("0") or header_text_raw_101

    # Document Date / Posting Date -- same fields, same conversion, same
    # "fall back to today" idea as execute_migo_103_sap's own DOC_DATE/
    # POST_DATE (see _to_sap_date's docstring for the YYYY-MM-DD ->
    # DD.MM.YYYY conversion SAP expects).
    variables = {
        "DELIVERY_NUMBER":  _s(cleaned.get("outboundDeliveryNumber", ""))[:10],
        "DOC_DATE":         _to_sap_date(cleaned.get("migo101DocDate", "")),
        "POST_DATE":        _to_sap_date(cleaned.get("migo101PostDate", datetime.now().strftime("%Y-%m-%d"))),
        "BILL_OF_LADING":   _s(cleaned.get("migo101BillOfLading", ""))[:16],
        "GR_SLIP_NO":       _s(cleaned.get("migo101GRSlipNo", ""))[:10],
        "HEADER_TEXT":      header_text_clean_101[:25],
        "STORAGE_LOCATION": _s(cleaned.get("storageLocation", "")),
        "VENDOR_INVOICE":   _s(cleaned.get("vendorInvoiceDetail", "")),
        "REMARKS":          _s(cleaned.get("remarks101", ""))[:40],
        "ITEMS_JSON_B64":   items_json_b64,
    }

    result = _run_rf_script(
        "migo_101.robot", variables, timeout_seconds=300,
        extra_env=_sap_credential_env(data), history_id=data.get("history_id")
    )
    if not result["success"]:
        # Same generic-message pattern as every other execute_*_sap here --
        # result["error"] embeds the bot machine's raw internal path, kept
        # server-log-only.
        logger.error(f"MIGO 101 script failure: {result['error']}")
        return {
            "success": False,
            "error": "MIGO 101 did not complete — the automation hit an error before finishing. Check the RF log or try again.",
            "migo_101_doc_number": None,
            "item_results": []
        }

    mat_doc = _extract_marked_value(result["output"], "MIGO101_DOC_NUMBER")
    sap_status = _extract_marked_value(result["output"], "MIGO101_STATUS_MSG")

    # Per-item match/post outcome, same idea as MIGO 103's ITEM_RESULTS --
    # logged BEFORE Post is clicked, so it's present even on a
    # MANUAL_CHECK_REQUIRED/DELIVERY_NOT_VALID/crashed run.
    item_results_raw = _extract_marked_value(result["output"], "ITEM_RESULTS")
    item_results = []
    if item_results_raw:
        try:
            item_results = json.loads(item_results_raw)
        except Exception:
            logger.warning(f"Could not parse ITEM_RESULTS marker as JSON: {item_results_raw[:300]}")

    if not mat_doc:
        if not _rf_test_actually_passed(result["output"]):
            return {
                "success": False,
                "error": _sap_msg_or(sap_status, "MIGO 101 failed — nothing was posted. Try again."),
                "migo_101_doc_number": None,
                "item_results": item_results
            }
        return {
            "success": False,
            "error": _sap_msg_or(sap_status, "MIGO 101: SAP gave no document number. Check SAP before retrying."),
            "migo_101_doc_number": None,
            "item_results": item_results
        }

    # Mirrors execute_migo_103_sap's PO_NOT_RELEASED handling -- caught by
    # migo_101.robot BEFORE Post is ever clicked (right after the delivery
    # number is entered, same status-bar rejection-pattern check
    # outbond_1.robot already had), so nothing was posted and there is no
    # duplicate-GR risk, unlike the MANUAL_CHECK_REQUIRED case below.
    if mat_doc == "DELIVERY_NOT_VALID":
        return {
            "success": False,
            "error": _sap_msg_or(sap_status, "SAP rejected this Outbound Delivery Number — nothing was posted."),
            "migo_101_doc_number": None,
            "item_results": item_results
        }

    if mat_doc == "MANUAL_CHECK_REQUIRED":
        return {
            "success": False,
            "error": _sap_msg_or(sap_status, "MIGO 101 posted but SAP gave no document number. Check SAP (MB03) before retrying — a retry may duplicate it."),
            "migo_101_doc_number": None,
            "item_results": item_results
        }

    return {
        "success": True,
        "migo_101_doc_number": mat_doc,
        "error": None,
        "item_results": item_results
    }


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
        extra_env=_sap_credential_env(data), history_id=data.get("history_id")
    )
    if not result["success"]:
        # FIX (2026-08-27): see execute_gate_in_sap's identical fix above --
        # result["error"] leaks the bot machine's raw internal path.
        logger.error(f"MIRO script failure: {result['error']}")
        return {
            "success": False,
            "error": "MIRO did not complete — the automation hit an error before finishing. Check the RF log or try again."
        }
 
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
    result = _run_rf_script("po_fetch.robot", variables, timeout_seconds=2700, history_id=data.get("history_id"))
    # MULTI-BOT FIX (2026-09-03): this used to unconditionally force-kill
    # SAP here after every po_fetch run, success or failure -- built for a
    # world where po_fetch always logged in fresh and nothing downstream
    # expected SAP to still be open afterward. That's no longer true:
    # po_fetch.robot's own Close SAP Session (via sap_coordination.resource's
    # Coordinated SAP Close) already correctly closes just its own window
    # and only fully kills SAP if it was the last bot still registered.
    # This blind kill was undoing that on every single run, regardless of
    # whether other bots were still active. Removed outright -- the
    # script's own teardown is the one source of truth for this now. (A
    # timeout, which skips that teardown entirely, is still handled
    # separately in _run_rf_script's own TimeoutExpired handler above.)
    if not result["success"]:
        # FIX (2026-08-27): see execute_gate_in_sap's identical fix above --
        # result["error"] leaks the bot machine's raw internal path.
        logger.error(f"PO Fetch script failure: {result['error']}")
        return {
            "success": False,
            "error": "PO Fetch did not complete — the automation hit an error before finishing. Check the RF log or try again.",
            "po_items": []
        }
 
    raw_json = _extract_marked_value(result["output"], "PO_DATA")
    if not raw_json:
        return {"success": False, "error": "PO fetch ran but no PO_DATA found.", "po_items": []}
 
    try:
        po_items = json.loads(raw_json)
        if not isinstance(po_items, list):
            po_items = []

        # FIX (2026-08-24): po_fetch.robot now detects an invalid/non-existent
        # PO number (SAP status bar "Document ... does not exist") and stops
        # immediately instead of reading whatever was left on screen from a
        # previous PO -- that used to silently return stale/wrong line items.
        # But that bot-side fix returns a single-element list shaped like
        # {"status": "PO_NOT_FOUND", "message": ..., "po_number": ...}, which
        # has none of the real line-item keys (item_no/material_code/qty/...).
        # Without this check, the code below it would have treated that as
        # "1 real line item" and saved a blank/garbage row to the database
        # while reporting success -- worse than the original bug, since it
        # would look like it worked. Catch that shape here and report it as
        # the clear failure it actually is.
        if (
            len(po_items) == 1
            and isinstance(po_items[0], dict)
            and po_items[0].get("status") == "PO_NOT_FOUND"
        ):
            not_found_msg = po_items[0].get("message") or f"PO {po_number} does not exist in SAP."
            logger.warning(f"PO fetch: PO={po_number} not found in SAP — {not_found_msg}")
            return {
                "success": False,
                "error": f"PO {po_number} does not exist in SAP. Please check the PO number for typos. (SAP: {not_found_msg})",
                "po_items": [],
            }

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
    result = _run_rf_script("po_list_fetch.robot", variables, timeout_seconds=120, history_id=data.get("history_id"))
    if not result["success"]:
        # FIX (2026-08-27): see execute_gate_in_sap's identical fix above --
        # result["error"] leaks the bot machine's raw internal path.
        logger.error(f"PO List Fetch script failure: {result['error']}")
        return {
            "success": False,
            "error": "PO List Fetch did not complete — the automation hit an error before finishing. Check the RF log or try again.",
            "po_list": []
        }
 
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
        extra_env=_sap_credential_env(data), history_id=history_id
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
    # FIX (2026-08-14): removed extra_env=_sap_credential_env(data) here --
    # client decision, migo103_link should always use the shared spl_rpa
    # account. Note this line was already effectively a no-op before this
    # change too: this job is only ever enqueued via _enqueue_link_attach()
    # (rf_queue_worker.py), which calls enqueue_rf_job() directly from a
    # background-worker context, never through app.py's _enqueue_sap_job()
    # -- so `data` here never actually contained _sap_username/
    # _sap_password in practice, and _sap_credential_env(data) always
    # returned {}. Removing it makes the always-spl_rpa behavior explicit
    # and intentional instead of an accident of how this job happens to
    # get enqueued today.
    variables = {
        "MATERIAL_DOC_NUMBER": data.get("material_doc_number", ""),
        "DOCUMENT_LINK":       data.get("document_link", ""),
    }
    result = _run_rf_script(
        "migo103_link.robot", variables, timeout_seconds=180,
        history_id=data.get("history_id")
    )
    if not result["success"]:
        # FIX (2026-08-27): see execute_gate_in_sap's identical fix above --
        # result["error"] leaks the bot machine's raw internal path.
        logger.error(f"migo103_link script failure: {result['error']}")
        return {
            "success": False,
            "error": "migo103_link did not complete — the automation hit an error before finishing. Check the RF log or try again."
        }
 
    status_val = _extract_marked_value(result["output"], "MIGO103_LINK_STATUS")
    if status_val and status_val.upper() == "SUCCESS":
        return {"success": True, "error": None}
    # The robot-log folder is a server path -- logged for IT, never shown to the user.
    logger.error(
        "migo103_link robot ran but did not confirm the link was attached. "
        f"Check robot log: {result.get('output_dir')}"
    )
    return {
        "success": False,
        "error": "migo103_link ran but did not confirm the link was attached. Try again."
    }
 
 
def execute_migo101_link_sap(data: dict) -> dict:
    # ADDED (2026-09-17): MIGO 101's own link-attach job -- same contract
    # as execute_migo103_link_sap/execute_migo105_link_sap above (this was
    # the deliberately-deferred piece _process_migo_101's docstring used to
    # flag; see rf_queue_worker.py). Always spl_rpa, same client decision
    # as its two siblings above -- no extra_env=_sap_credential_env(data).
    variables = {
        "MATERIAL_DOC_NUMBER": data.get("material_doc_number", ""),
        "DOCUMENT_LINK":       data.get("document_link", ""),
    }
    result = _run_rf_script(
        "migo101_link.robot", variables, timeout_seconds=180,
        history_id=data.get("history_id")
    )
    if not result["success"]:
        logger.error(f"migo101_link script failure: {result['error']}")
        return {
            "success": False,
            "error": "migo101_link did not complete — the automation hit an error before finishing. Check the RF log or try again."
        }

    status_val = _extract_marked_value(result["output"], "MIGO101_LINK_STATUS")
    if status_val and status_val.upper() == "SUCCESS":
        return {"success": True, "error": None}
    # The robot-log folder is a server path -- logged for IT, never shown to the user.
    logger.error(
        "migo101_link robot ran but did not confirm the link was attached. "
        f"Check robot log: {result.get('output_dir')}"
    )
    return {
        "success": False,
        "error": "migo101_link ran but did not confirm the link was attached. Try again."
    }


def execute_migo105_link_sap(data: dict) -> dict:
    # FIX (2026-08-14): same as execute_migo103_link_sap above -- removed
    # extra_env=_sap_credential_env(data), client decision to keep this on
    # spl_rpa explicitly (was already a no-op in practice; see that
    # function's comment for why).
    variables = {
        "MATERIAL_DOC_NUMBER": data.get("material_doc_number", ""),
        "DOCUMENT_LINK":       data.get("document_link", ""),
    }
    result = _run_rf_script(
        "migo105_link.robot", variables, timeout_seconds=180,
        history_id=data.get("history_id")
    )
    if not result["success"]:
        # FIX (2026-08-27): see execute_gate_in_sap's identical fix above --
        # result["error"] leaks the bot machine's raw internal path.
        logger.error(f"migo105_link script failure: {result['error']}")
        return {
            "success": False,
            "error": "migo105_link did not complete — the automation hit an error before finishing. Check the RF log or try again."
        }
 
    status_val = _extract_marked_value(result["output"], "MIGO105_LINK_STATUS")
    if status_val and status_val.upper() == "SUCCESS":
        return {"success": True, "error": None}
    # The robot-log folder is a server path -- logged for IT, never shown to the user.
    logger.error(
        "migo105_link robot ran but did not confirm the link was attached. "
        f"Check robot log: {result.get('output_dir')}"
    )
    return {
        "success": False,
        "error": "migo105_link ran but did not confirm the link was attached. Try again."
    }
 
 
def execute_miro_link_sap(data: dict) -> dict:
    # FIX (2026-08-31, client decision): removed extra_env=_sap_credential_env(data)
    # -- miro_link should always use the shared spl_rpa login, same as its
    # two siblings (migo103_link/migo105_link, see the identical FIX note
    # on execute_migo103_link_sap above). Previously this was the one
    # inconsistent bot in the "link" family -- it still accepted a
    # per-user LDAP credential override if one happened to be attached to
    # the job, unlike migo103_link/migo105_link which were deliberately
    # forced to spl_rpa. Confirmed with client this was not intentional.
    variables = {
        "MATERIAL_DOC_NUMBER": data.get("material_doc_number", ""),
        "DOCUMENT_LINK":       data.get("document_link", ""),
    }
    result = _run_rf_script(
        "miro_link.robot", variables, timeout_seconds=180,
        history_id=data.get("history_id")
    )
    if not result["success"]:
        # FIX (2026-08-27): see execute_gate_in_sap's identical fix above --
        # result["error"] leaks the bot machine's raw internal path.
        logger.error(f"miro_link script failure: {result['error']}")
        return {
            "success": False,
            "error": "miro_link did not complete — the automation hit an error before finishing. Check the RF log or try again."
        }
 
    status_val = _extract_marked_value(result["output"], "MIRO_LINK_STATUS")
    if status_val and status_val.upper() == "SUCCESS":
        return {"success": True, "error": None}
    # The robot-log folder is a server path -- logged for IT, never shown to the user.
    logger.error(
        "miro_link robot ran but did not confirm the link was attached. "
        f"Check robot log: {result.get('output_dir')}"
    )
    return {
        "success": False,
        "error": "miro_link ran but did not confirm the link was attached. Try again."
    }


# ============================================================
# ZGRN PRINT — GR Certificate download to PDF (ADDED 2026-08-14,
# REWRITTEN 2026-09-03)
#
# Frontend: a "Print" button next to MIGO 103's Post button (only
# meaningful once MIGO 103 has actually posted -- needs a real material
# document number). Always spl_rpa -- client decision, never per-user
# (mirrors po_fetch/po_list_fetch's existing exception, not the MIGO
# 103/105/MIRO per-user pattern).
#
# REWRITTEN (2026-09-03): zgrn.robot no longer prints to PDF via a native
# Windows Save dialog at all -- it ticks SAP's own "Download for MIP"
# checkbox (chkLC_MIP) alongside "Capital Goods Declaration" (chkLS_CGD),
# which makes SAP write the PDF(s) straight into ZGRN_PRINT_FOLDER itself.
# That removes almost the entire native-dialog surface that made the old
# flow fragile (see pdf_helper.py's history) -- the one dialog left is a
# same-day-reprint overwrite confirmation, handled inside the robot script
# itself now, not here.
#
# Like before, zgrn.robot has no SAP status-bar/error text to key off, so
# success still isn't decided by a RESULT: marker alone -- that only
# confirms the script reached a known-good point, not that a file was
# actually produced. The real check is still whether the expected file(s)
# exist in ZGRN_PRINT_FOLDER afterward, with a fresh-enough mtime. What
# changed is the naming: SAP itself now names the files, deterministically,
# as "<material_doc_number>_GRN.pdf" and "<material_doc_number>_GCD.pdf"
# (confirmed against a real run, 2026-09-03) -- note GCD, not CGD, and no
# date component, unlike the old script's own self-chosen naming.
# ============================================================

def execute_zgrn_print_sap(data: dict) -> dict:
    """
    Run zgrn.robot to download a posted MIGO 103 document's GR Certificate
    (and Goods Certificate of Delivery, when SAP produces one) as PDFs.

    data: {"material_doc_number": str, "history_id": int,
           "print_twice": bool (default True) -- see note below}

    Returns {"success": bool, "error": str|None, "files": {"grn": path|None,
    "cgd": path|None}, "note": str|None} -- rf_queue_worker.py's
    _process_zgrn_print() reads `files` to know what to copy into
    UPLOAD_FOLDER and attach. The "cgd" key name is kept as-is even though
    the real file/SAP term is GCD -- it's just this app's own existing
    internal doc_type label (zgrn_cgd), unrelated to SAP's filename.

    print_twice no longer controls the robot's behaviour (the new script
    always ticks both checkboxes in a single execute -- there's no
    separate print pass to skip). Kept as an input here only to decide
    whether a missing GCD file is worth a note: the new script has no
    "this document has no GCD component" signal the way the old one did
    (ZGRN_CGD_STATUS:NOT_APPLICABLE) -- SAP silently decides that on its
    own, so a missing GCD file is no longer treated as a failure requiring
    a retry, only flagged for a human to glance at if one was expected.
    """
    mat_doc = str(data.get("material_doc_number", "") or "").strip()
    history_id = data.get("history_id", "")
    if not mat_doc:
        return {
            "success": False,
            "error": f"Missing material_doc_number — cannot run zgrn print for history_id={history_id}",
            "files": {"grn": None, "cgd": None},
        }

    print_twice = bool(data.get("print_twice", True))
    variables = {
        "MIGO_NUMBER": mat_doc,
    }
    # Note: OUTPUT_FOLDER/PRINT_TWICE are no longer read by zgrn.robot --
    # the download folder is fixed SAP-side (C:\zgrn_print\, see
    # config.ZGRN_MIP_FOLDER -- NOT config.ZGRN_PRINT_FOLDER, that was the
    # old app-root-derived path used by the Print-to-PDF flow this
    # replaced, and no longer matches where files actually land), and both
    # checkboxes are always ticked in one execute -- so they're no longer
    # passed as RF variables here.

    # REMOVED (2026-09-03, client instruction): this used to pre-wait on
    # is_robot_locked() before starting, to avoid colliding with
    # dms_upload.robot's own native file-picker dialog on the same
    # desktop. Removed now because it was checking the WRONG thing since
    # this session's robot_lock.py rewrite: that lock is now a shared
    # refcount (multiple Material-Inward bots are meant to hold it at
    # once -- that's the whole point of the multi-bot concurrency work),
    # so is_robot_locked() just meant "at least one bot of any kind is
    # currently running somewhere," not "the desktop is genuinely busy in
    # a way that would collide with ZGRN." It was pointlessly delaying
    # ZGRN's Print button (up to 10 minutes) any time an unrelated SAP job
    # for a different user happened to be in flight, with no real
    # collision protection to show for it. _run_rf_script() below still
    # acquires/releases robot_lock around this run itself, same as every
    # other bot -- that part is unchanged.
    #
    # Genuinely still possible (accepted per client decision): if
    # dms_upload.robot's own native file dialog/browser happens to be on
    # screen at the exact same moment ZGRN's own native "overwrite
    # confirmation" dialog (same-day reprint) pops up, the two could
    # interfere with each other. Both windows are brief, and the client
    # has chosen to accept this small, narrow overlap risk rather than
    # keep the wait.
    #
    # Deliberately no extra_env= -- always spl_rpa, see module comment above.
    run_started_at = time.time()
    result = _run_rf_script("zgrn.robot", variables, timeout_seconds=180, history_id=history_id)

    output = result.get("output") or ""
    # New script's vocabulary: SUCCESS / SUCCESS_OVERWRITTEN / NO_FILE_CREATED
    # (never "DONE" -- that was the old script's marker). Any of the three
    # means the script reached a known-good point; absence means it
    # crashed/errored before getting there.
    status_val = _extract_marked_value(output, "ZGRN_PRINT_STATUS")
    script_reached_end = bool(status_val)

    grn_path = os.path.join(config.ZGRN_MIP_FOLDER, f"{mat_doc}_GRN.pdf")
    gcd_path = os.path.join(config.ZGRN_MIP_FOLDER, f"{mat_doc}_GCD.pdf")

    def _fresh(path):
        # Same reasoning as before the rewrite: the filename is fully
        # deterministic, so a same-day reprint (or SAP's own
        # overwrite-in-place) lands on the exact same path an earlier run
        # may have already written. Requires the file's mtime to be after
        # this run started, not just that it exists, so a stale leftover
        # file can't masquerade as this run's success.
        try:
            return os.path.isfile(path) and os.path.getmtime(path) >= run_started_at
        except OSError:
            return False

    grn_exists = _fresh(grn_path)
    gcd_exists = _fresh(gcd_path)

    files = {
        "grn": grn_path if grn_exists else None,
        "cgd": (gcd_path if gcd_exists else None),
    }

    # GRN is always required. GCD is attached when present, but its
    # absence is no longer a hard failure -- see docstring for why we can
    # no longer reliably tell "no GCD applies here" apart from "GCD
    # silently didn't download." Attach what we got and note the gap
    # rather than force a retry that may just be re-confirming a document
    # that never had a GCD to begin with.
    if grn_exists:
        note = None
        if print_twice and not gcd_exists:
            note = (
                "GR Certificate downloaded. No Certificate of Goods "
                "Delivery was found for this document — either it does "
                "not apply here, or it didn't download; check SAP "
                "manually (TCODE: zgrn) if one was expected."
            )
        return {"success": True, "error": None, "files": files, "note": note}

    if not script_reached_end and not result["success"]:
        return {
            "success": False,
            "error": (
                "ZGRN print did not complete — the SAP automation didn't "
                "finish in time or hit an error. Check SAP manually "
                "(TCODE: zgrn) before retrying."
            ),
            "files": files,
        }

    # FIX (2026-08-27, still applies): don't leak the bot machine's raw
    # internal path to the frontend -- _process_zgrn_print()
    # (rf_queue_worker.py) passes this string through unchanged. Logged
    # server-side only; the user-facing message stays generic.
    logger.error(
        f"ZGRN print: expected GRN PDF not found in "
        f"{config.ZGRN_MIP_FOLDER} after the run. Status marker: {status_val!r}"
    )
    return {
        "success": False,
        "error": (
            "ZGRN print ran but the expected GR Certificate PDF was not "
            "created. Check SAP manually (TCODE: zgrn) before retrying."
        ),
        "files": files,
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