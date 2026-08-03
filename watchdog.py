"""
watchdog.py — Standalone health-check / auto-restart monitor for Material Inward.

WHY THIS IS A SEPARATE SCRIPT, NOT CODE INSIDE app.py:
    If it ran inside the Flask process (a background thread, say), a hang in
    that same process would hang the checker too -- the one failure mode
    this exists to catch. It has to run as its own OS process, on its own
    schedule (Windows Task Scheduler — see the "SCHEDULING" section at the
    bottom of this file for the exact command), completely independent of
    whether app.py is alive or not.

WHAT THIS DOES NOT REPLACE:
    start_server.bat already restarts run_server.py automatically whenever
    the Python process itself exits with a crash code (see its :restart
    loop). That's the right tool for "the process died." It CANNOT detect
    "the process is still running but stuck/hung and no longer answering
    requests" -- there's no exit code to react to in that case, the process
    just sits there. That gap is what this script closes. When it detects a
    hung/unresponsive app, it kills the stuck process and lets
    start_server.bat's own already-correct restart loop bring it back --
    this script deliberately does NOT reimplement app-launch logic, except
    as a last-resort fallback if nothing reclaims the port at all (e.g. the
    server rebooted and start_server.bat was never relaunched).

CHECKS, IN ORDER, EACH RUN:
    1. Network/DB reachability -- raw TCP connect to DB_HOST:DB_PORT, plus a
       check that the G: NAS drive (used by the folder watcher) is mapped
       and responding. If either is down, restarting the app would not fix
       anything external -- so this branch only logs + alerts, it never
       kills/restarts anything.
    2. App liveness -- HTTP GET http://127.0.0.1:<PORT>/health (a lightweight
       route in app.py that does a real `SELECT 1`, so a hung/exhausted DB
       pool is caught, not just "is the port open"). Only reached if step 1
       passed, so a failure here means the app process itself is the
       problem, not its dependencies.
    3. Nginx process check -- independent of steps 1/2. Restarted if not
       running. (start_server.bat only starts Nginx once, at boot; nothing
       currently re-checks it afterward.)

RESTART ACTION (only on step 2 failure):
    - Rate-limited via a small state file so a prolonged outage doesn't
      trigger a kill attempt every single run (default cooldown: 3 minutes).
    - Finds the PID bound to PORT via `netstat`, confirms it's actually a
      python.exe/pythonw.exe process (never kills an unrelated process that
      happens to be on that port), then `taskkill /F`.
    - Waits up to 45s for something to reclaim the port (start_server.bat's
      own loop should do this within ~5-10s of the kill).
    - If nothing reclaims the port in that window, launches start_server.bat
      directly as a fallback, in case the supervisor loop itself isn't
      running (e.g. after a server reboot with no console re-opened).

ALERTING:
    Best-effort email to ADMIN_EMAIL (same .env setting the app already uses
    for OCR-failure alerts) on any DEGRADED/RESTART event, rate-limited
    per alert-type so a prolonged outage sends one email, not one every run.
    Deliberately does NOT reuse services/mail_service.py (that module pulls
    in database/user_operations.py, i.e. a live DB call, to build its
    recipient list -- exactly the kind of dependency this script must not
    have, since it has to keep working during a DB outage). This file's own
    _send_alert() is self-contained: stdlib smtplib only, straight to
    ADMIN_EMAIL, no DB read.

LOGGING:
    C:\\material_inward\\logs\\watchdog_YYYYMMDD.log -- same directory
    start_server.bat already logs to.
"""

import json
import os
import smtplib
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime
from email.mime.text import MIMEText

APP_DIR  = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

from config.config import config  # noqa: E402  (path insert must come first)

LOG_DIR    = r"C:\material_inward\logs"
STATE_FILE = os.path.join(LOG_DIR, "watchdog_state.json")
NGINX_DIR  = os.getenv("NGINX_DIR", r"C:\Program Files\nginx\nginx-1.28.3")

DB_TIMEOUT_SECONDS     = 5
HTTP_TIMEOUT_SECONDS   = 8
NAS_CHECK_TIMEOUT      = 5
RESTART_COOLDOWN_SECS  = 180     # don't attempt another kill within this window
PORT_RECLAIM_WAIT_SECS = 45      # how long to wait for start_server.bat's own loop
ALERT_COOLDOWN_SECS    = 900     # 15 min -- one email per sustained incident, not one per run


# ============================================================
# Logging (plain text, dated file + console -- same convention as
# start_server.bat's own :log subroutine)
# ============================================================

def _log(msg: str) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    log_file = os.path.join(LOG_DIR, f"watchdog_{datetime.now().strftime('%Y%m%d')}.log")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # logging must never itself crash the watchdog


# ============================================================
# Tiny state file — restart cooldown + alert cooldown tracking only.
# Deliberately not a database row: this script must keep working when the
# database is exactly the thing that's down.
# ============================================================

def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        _log(f"WARNING: could not write state file: {e}")


def _seconds_since(state: dict, key: str) -> float:
    ts = state.get(key)
    if not ts:
        return float("inf")
    return time.time() - ts


# ============================================================
# Alerting — stdlib smtplib only, no DB dependency (see module docstring).
# ============================================================

def _send_alert(subject: str, body: str, alert_key: str) -> None:
    state = _load_state()
    if _seconds_since(state, f"alert_{alert_key}") < ALERT_COOLDOWN_SECS:
        _log(f"(alert '{alert_key}' suppressed — sent within the last {ALERT_COOLDOWN_SECS // 60} min)")
        return
    state[f"alert_{alert_key}"] = time.time()
    _save_state(state)

    if not config.ADMIN_EMAIL or not config.EMAIL_SENDER or not config.EMAIL_PASSWORD:
        _log("(alert email not sent — SMTP/ADMIN_EMAIL not configured in .env)")
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = f"[Material Inward Watchdog] {subject}"
        msg["From"] = config.EMAIL_SENDER
        msg["To"] = config.ADMIN_EMAIL
        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
            server.sendmail(config.EMAIL_SENDER, [config.ADMIN_EMAIL], msg.as_string())
        _log(f"Alert email sent: {subject}")
    except Exception as e:
        _log(f"WARNING: failed to send alert email: {e}")


# ============================================================
# Step 1 — Network / DB / NAS reachability
# ============================================================

def _check_db() -> bool:
    try:
        with socket.create_connection((config.DB_HOST, config.DB_PORT), timeout=DB_TIMEOUT_SECONDS):
            return True
    except Exception as e:
        _log(f"DB reachability check failed ({config.DB_HOST}:{config.DB_PORT}): {e}")
        return False


def _check_nas() -> bool:
    """G:\\ existence check, run in a thread with a hard timeout -- a dead/
    unresponsive SMB mount can otherwise hang a plain os.path.exists() call
    well past any reasonable check interval."""
    result = {"ok": False}

    def _worker():
        try:
            # Forward slash deliberately -- unambiguous on Windows (unlike a
            # raw string ending in backslashes, which is easy to get subtly
            # wrong: r"G:\\" is actually TWO trailing backslashes, not one).
            result["ok"] = os.path.exists("G:/")
        except Exception:
            result["ok"] = False

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(NAS_CHECK_TIMEOUT)
    if t.is_alive():
        _log("NAS (G:) check timed out — treating as unreachable.")
        return False
    if not result["ok"]:
        _log("NAS (G:) drive not accessible.")
    return result["ok"]


# ============================================================
# Step 2 — App liveness via /health
# ============================================================

def _check_app_health() -> bool:
    url = f"http://127.0.0.1:{config.PORT}/health"
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                _log(f"/health returned HTTP {resp.status}")
                return False
            body = json.loads(resp.read().decode("utf-8"))
            if not body.get("db"):
                _log(f"/health responded but reported db=false: {body}")
                return False
            return True
    except Exception as e:
        _log(f"/health check failed ({url}): {e}")
        return False


# ============================================================
# Step 3 — Nginx
# ============================================================

def _is_nginx_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq nginx.exe"],
            capture_output=True, text=True, timeout=10
        )
        return "nginx.exe" in out.stdout.lower()
    except Exception as e:
        _log(f"WARNING: could not check nginx process list: {e}")
        return True  # don't try to start a second copy on an inconclusive check


def _ensure_nginx_running() -> None:
    if _is_nginx_running():
        return
    _log("Nginx not running — starting it.")
    try:
        subprocess.Popen(
            ["nginx.exe"], cwd=NGINX_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        )
        _send_alert(
            "Nginx was down, restarted",
            f"Nginx was not running at {datetime.now()} and has been relaunched from {NGINX_DIR}.",
            alert_key="nginx_restart",
        )
    except Exception as e:
        _log(f"ERROR: failed to start nginx: {e}")
        _send_alert(
            "Nginx down — restart FAILED",
            f"Nginx was not running and the watchdog failed to restart it: {e}\n"
            f"Manual action needed.",
            alert_key="nginx_restart_failed",
        )


# ============================================================
# Restart action (app process only — only reached on a step-2 failure)
# ============================================================

def _find_pid_on_port(port: int) -> int | None:
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        )
        for line in out.stdout.splitlines():
            if f":{port} " in line and "LISTENING" in line.upper():
                parts = line.split()
                return int(parts[-1])
    except Exception as e:
        _log(f"WARNING: netstat lookup failed: {e}")
    return None


def _is_python_process(pid: int) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=10
        )
        name = out.stdout.lower()
        return "python.exe" in name or "pythonw.exe" in name
    except Exception as e:
        _log(f"WARNING: could not verify process image for PID {pid}: {e}")
        return False


def _port_is_listening(port: int) -> bool:
    return _find_pid_on_port(port) is not None


def _restart_app() -> None:
    state = _load_state()
    if _seconds_since(state, "last_restart_attempt") < RESTART_COOLDOWN_SECS:
        _log(f"Restart skipped — another restart was attempted within the last "
             f"{RESTART_COOLDOWN_SECS} seconds (cooldown, avoids a restart loop).")
        _send_alert(
            "App unresponsive — restart still in cooldown",
            "The health check is still failing but a restart was already attempted "
            "recently. If this persists, check the server manually.",
            alert_key="restart_cooldown",
        )
        return

    state["last_restart_attempt"] = time.time()
    _save_state(state)

    pid = _find_pid_on_port(config.PORT)
    if pid and _is_python_process(pid):
        _log(f"App unresponsive — killing stuck process PID {pid} on port {config.PORT}.")
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        except Exception as e:
            _log(f"WARNING: taskkill failed: {e}")
    elif pid:
        _log(f"WARNING: port {config.PORT} is held by PID {pid}, which is NOT a python "
             f"process — not killing it. Investigate manually.")
    else:
        _log(f"App unresponsive and nothing is listening on port {config.PORT} at all "
             f"(process already dead, not just hung).")

    # Give start_server.bat's own restart loop a chance to bring it back up
    # on its own before we fall back to launching it ourselves.
    waited = 0
    reclaimed = False
    while waited < PORT_RECLAIM_WAIT_SECS:
        time.sleep(5)
        waited += 5
        if _port_is_listening(config.PORT):
            reclaimed = True
            break

    if reclaimed:
        _log(f"Port {config.PORT} reclaimed after {waited}s — restart succeeded "
             f"(start_server.bat's own loop handled it).")
        _send_alert(
            "App was unresponsive — auto-restarted successfully",
            f"The health check failed, the watchdog terminated the stuck process, "
            f"and a new instance came back up on port {config.PORT} within {waited}s.",
            alert_key="restart_success",
        )
        return

    _log(f"Port {config.PORT} still not reclaimed after {PORT_RECLAIM_WAIT_SECS}s — "
         f"start_server.bat's loop does not appear to be running. Launching it directly.")
    bat_path = os.path.join(APP_DIR, "start_server.bat")
    try:
        os.startfile(bat_path)  # noqa: this module only ever runs on Windows
        _send_alert(
            "App was down — watchdog launched start_server.bat directly",
            f"The health check failed and no supervisor process reclaimed port "
            f"{config.PORT} within {PORT_RECLAIM_WAIT_SECS}s, so the watchdog launched "
            f"{bat_path} itself. This usually means start_server.bat wasn't running at "
            f"all (e.g. after a server reboot) -- worth checking it's set to start "
            f"automatically.",
            alert_key="fallback_launch",
        )
    except Exception as e:
        _log(f"ERROR: failed to launch start_server.bat: {e}")
        _send_alert(
            "App is DOWN — watchdog could not restart it",
            f"The health check failed, the stuck process was terminated (or was already "
            f"dead), and the watchdog's attempt to launch {bat_path} itself also failed: "
            f"{e}\nManual intervention needed immediately.",
            alert_key="restart_totally_failed",
        )


# ============================================================
# Main
# ============================================================

def main() -> None:
    _log("── Watchdog check starting ──")

    db_ok  = _check_db()
    nas_ok = _check_nas()

    if not db_ok or not nas_ok:
        problems = []
        if not db_ok:
            problems.append(f"database ({config.DB_HOST}:{config.DB_PORT}) unreachable")
        if not nas_ok:
            problems.append("NAS (G:) drive unreachable")
        msg = "; ".join(problems)
        _log(f"DEGRADED — {msg}. Not restarting the app: a restart cannot fix an "
             f"external network/DB/NAS outage, and would just add an unnecessary "
             f"second outage on top of this one.")
        _send_alert(
            "Network/DB/NAS degraded",
            f"The watchdog detected: {msg}.\n\n"
            f"The application itself has NOT been restarted -- this class of problem "
            f"is external to the app and a restart would not fix it. Please check the "
            f"database server / NAS / network connection directly.",
            alert_key="network_degraded",
        )
        _ensure_nginx_running()
        _log("── Watchdog check complete (degraded — dependency outage) ──\n")
        return

    app_ok = _check_app_health()
    if app_ok:
        _log("OK — DB reachable, NAS reachable, app healthy.")
    else:
        _log("App health check FAILED while DB/NAS are both fine — treating as a "
             "hung or crashed app process.")
        _restart_app()

    _ensure_nginx_running()
    _log("── Watchdog check complete ──\n")


if __name__ == "__main__":
    main()


# ============================================================
# SCHEDULING — register this to run automatically, every 2 minutes,
# independent of any logged-in user session (so it survives someone closing
# a console window, and runs even before anyone logs into the server after a
# reboot). Run this ONCE, from an elevated (Administrator) Command Prompt, on
# the production server, adjusting the venv python.exe / script paths to
# match where this file actually lives there:
#
#   schtasks /create /tn "MaterialInward_Watchdog" ^
#     /tr "\"C:\Users\ctn_suresh\Agents\material_inward_FINAL (2)\material_inward_FINAL\material_inward\venv\Scripts\python.exe\" \"C:\Users\ctn_suresh\Agents\material_inward_FINAL (2)\material_inward_FINAL\material_inward\watchdog.py\"" ^
#     /sc minute /mo 2 /ru SYSTEM /rl HIGHEST /f
#
# To verify it's registered:      schtasks /query /tn "MaterialInward_Watchdog"
# To run it once immediately:     schtasks /run /tn "MaterialInward_Watchdog"
# To remove it:                   schtasks /delete /tn "MaterialInward_Watchdog" /f
# ============================================================
