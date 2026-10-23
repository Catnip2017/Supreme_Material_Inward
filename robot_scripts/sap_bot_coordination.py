"""
sap_bot_coordination.py

Reusable SAP multi-bot coordination logic as a single Python library.

Handles BOTH multi-bot scenarios:
  1. DIFFERENT SAP user IDs running concurrently (e.g. SPL_RPA + DEEPA).
  2. SAME SAP user ID running multiple concurrent GUI sessions, via
     session.createSession().

FIX 1: pure OS-process actions use direct Python subprocess calls, not
delegated through Robot's Process library.

FIX 2: the refcount lock around the login sequence uses a 10-minute
budget.

FIX 3: same-ID concurrency via session.createSession() -- confirmed
working correctly (multiple windows opening under one login, correct
reuse of an already-logged-in user's session, no popup).

FIX 4 / FIX 6 (superseded): tried preventing premature shutdown by
declaring an expected bot count per .robot file -- too fragile in
practice across many separate files.

FIX 7 (superseded as the SOLE fix, still kept as a safety net):
observation-based cooldown before the full saplogon.exe shutdown,
instead of a declared count. This part was correct and stays in place
below -- but it only governs WHEN to kill saplogon.exe as a whole
process. It never explained why closing ONE bot's window could take
down OTHER bots' windows too.

FIX 8 (THIS VERSION) -- THE ACTUAL ROOT CAUSE:

  Confirmed by a real run: 4 SPL bots + 2 DEEPA bots. When one SPL bot
  finished, it killed the OTHER SPL bots' windows -- but DEEPA's
  windows were unaffected. That SPL-only, DEEPA-untouched pattern is
  the key clue: it matches exactly how SAP GUI's session/connection
  hierarchy works, not a refcount timing issue (which we'd already
  ruled out through several rounds of tuning).

  Here's what's actually happening: the FIRST bot for a given username
  to log in fresh becomes session[0] of that username's CONNECTION.
  Every other bot for the SAME username reuses that connection via
  .createSession(), becoming session[1], session[2], etc. -- siblings
  under that one connection. A DIFFERENT username gets its own,
  entirely separate connection.

  The bug: closing a session by typing "/nex" into the OK-code field
  (a transaction-code-level command) behaves differently depending on
  WHICH session you send it to. Sending /nex to session[0] -- the
  connection's PRIMARY session -- can end the WHOLE CONNECTION on many
  SAP GUI versions, not just that one window, taking every sibling
  session under it down as a side effect. Sending /nex to a session
  created via createSession() (session[1], [2], ...) closes only that
  window, as expected. Since bot 1 in the failing run was, by
  definition, the one that logged in fresh (making it session[0] of
  the SPL connection), its /nex close cascaded to its SPL siblings --
  while DEEPA's entirely separate connection was untouched, exactly
  matching what was observed.

  THE FIX: close sessions at the WINDOW level instead --
  session.findById("wnd[0]").close() -- rather than via the /nex
  transaction code. This operates on that one window specifically and
  does not carry the same connection-wide cascading behavior. A
  generic confirmation-popup dismissal is included in case SAP shows a
  "close this session?" prompt. /nex is kept ONLY as a fallback if
  .close() raises (e.g. an older SAP GUI Scripting API that doesn't
  expose it), since a working fallback is better than a hard failure,
  but .close() is now the default path.

FIX 5: the self-healing "stale process" kill only fires when this bot
is very likely the only one active (refcount <=1).

FIX 8/9 (SUPERSEDED BY FIX 10): earlier versions closed sessions via
wnd[0].close(), which routes through SAP's normal interactive close
handling and can show a "Log Off?" confirmation popup when closing the
last session under a connection -- requiring the code to detect and
click that popup correctly. This worked but added fragile popup-
handling logic for something that has a cleaner, fully non-interactive
alternative.

FIX 10 (THIS VERSION): closes each session via the CONNECTION's own
CloseSession() API method instead -- e.g. connection.CloseSession("ses[1]").
This ends that one specific session directly, with NO confirmation
dialog ever appearing at all, and without touching any sibling
sessions under the same connection. No popup detection, no button
text-matching -- the session is simply gone once CloseSession() returns.

Install once:
    pip install robotframework pywin32

Import into any .robot file with:
    Library    SapGuiLibrary
    Library    ${CURDIR}/sap_bot_coordination.py

Then, in that .robot file's own *** Keywords ***:

    Initialize SAP And Login
        ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
        ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
        ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')
        ${session}=    Coordinated SAP Login    ${LOGON_PATH}    ${CONN_NAME}    ${CLIENT}    ${SAP_USERNAME}    ${SAP_PASSWORD}
        Set Suite Variable    ${session}

    Close SAP Session
        Coordinated Close SAP Session    ${session}

Files used:
  C:\\temp\\sap_bot_refcount.txt          -- active count
  C:\\temp\\sap_bot_refcount.lock         -- mutex directory
"""
import os
import subprocess
import sys
import time

import win32com.client
from robot.libraries.BuiltIn import BuiltIn

REFCOUNT_FILE = r"C:\temp\sap_bot_refcount.txt"
REFCOUNT_LOCK_DIR = r"C:\temp\sap_bot_refcount.lock"

SHORT_LOCK_MAX_ATTEMPTS = 150       # 150 x 0.2s = 30s
SHORT_LOCK_POLL_INTERVAL = 0.2

LOGIN_LOCK_MAX_ATTEMPTS = 3000      # 3000 x 0.2s = 600s = 10 minutes
LOGIN_LOCK_POLL_INTERVAL = 0.2

NEW_SESSION_WAIT_TIMEOUT = 5
NEW_SESSION_POLL_INTERVAL = 0.5

# --- TUNE THIS if needed ------------------------------------------------
SHUTDOWN_COOLDOWN_SECONDS = 60
SHUTDOWN_COOLDOWN_POLL_INTERVAL = 3
# -------------------------------------------------------------------------


def _run(keyword, *args):
    return BuiltIn().run_keyword(keyword, *args)


def _run_status(keyword, *args):
    return BuiltIn().run_keyword_and_return_status(keyword, *args)


def _run_ignore_error(keyword, *args):
    BuiltIn().run_keyword_and_ignore_error(keyword, *args)


def _saplogon_is_running():
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq saplogon.exe"],
        capture_output=True,
        text=True,
    )
    return "saplogon.exe" in result.stdout


def _start_saplogon(sap_logon_path):
    subprocess.Popen([sap_logon_path])


def _kill_saplogon(with_tree=False):
    args = ["taskkill", "/F", "/IM", "saplogon.exe"]
    if with_tree:
        args.append("/T")
    subprocess.run(args, capture_output=True)


def acquire_refcount_lock(max_attempts=SHORT_LOCK_MAX_ATTEMPTS, poll_interval=SHORT_LOCK_POLL_INTERVAL):
    os.makedirs(r"C:\temp", exist_ok=True)
    for _ in range(max_attempts):
        try:
            os.mkdir(REFCOUNT_LOCK_DIR)
            return
        except FileExistsError:
            time.sleep(poll_interval)
    BuiltIn().log(
        f"Could not acquire refcount lock after {max_attempts * poll_interval:.0f}s of retries -- "
        "proceeding anyway to avoid a permanent deadlock.",
        level="WARN",
    )


def release_refcount_lock():
    try:
        os.rmdir(REFCOUNT_LOCK_DIR)
    except OSError:
        pass


def _read_counter(path):
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        raw = f.read().strip()
    return int(raw) if raw else 0


def _write_counter(path, value):
    with open(path, "w") as f:
        f.write(str(value))


def coordinated_register_bot_start():
    acquire_refcount_lock(SHORT_LOCK_MAX_ATTEMPTS, SHORT_LOCK_POLL_INTERVAL)
    try:
        current_active = _read_counter(REFCOUNT_FILE)
        new_active = current_active + 1
        _write_counter(REFCOUNT_FILE, new_active)
        BuiltIn().log_to_console(
            f"SAP bot refcount: {current_active} -> {new_active} (this bot registered as active)"
        )
    finally:
        release_refcount_lock()


def coordinated_register_bot_end():
    acquire_refcount_lock(SHORT_LOCK_MAX_ATTEMPTS, SHORT_LOCK_POLL_INTERVAL)
    try:
        current_active = _read_counter(REFCOUNT_FILE)
        new_active = max(0, current_active - 1)
        _write_counter(REFCOUNT_FILE, new_active)
        BuiltIn().log_to_console(
            f"SAP bot refcount: {current_active} -> {new_active} (this bot deregistering)"
        )
        return new_active
    finally:
        release_refcount_lock()


def _active_count():
    acquire_refcount_lock(SHORT_LOCK_MAX_ATTEMPTS, SHORT_LOCK_POLL_INTERVAL)
    try:
        return _read_counter(REFCOUNT_FILE)
    finally:
        release_refcount_lock()


def coordinated_dismiss_any_popup():
    if _run_status("Element Should Be Present", "wnd[1]"):
        _run_ignore_error("Click Element", "wnd[1]/tbar[0]/btn[0]")
        time.sleep(1)


def _get_scripting_engine():
    sap_gui_auto = win32com.client.GetObject("SAPGUI")
    return sap_gui_auto.GetScriptingEngine


def _find_existing_session_for_user(username, connection_name):
    try:
        application = _get_scripting_engine()
    except Exception:  # noqa: BLE001
        return None

    target_user = username.strip().upper()
    target_conn = connection_name.strip().upper()

    try:
        connection_count = application.Children.Count
    except Exception:  # noqa: BLE001
        return None

    for i in range(connection_count):
        try:
            connection = application.Children(i)
            conn_description = str(connection.Description).strip().upper()
        except Exception:  # noqa: BLE001
            continue

        if target_conn not in conn_description and conn_description not in target_conn:
            continue

        try:
            session_count = connection.Children.Count
        except Exception:  # noqa: BLE001
            continue

        for j in range(session_count):
            try:
                sess = connection.Children(j)
                sess_user = str(sess.Info.User).strip().upper()
            except Exception:  # noqa: BLE001
                continue
            if sess_user == target_user:
                return sess

    return None


def _create_new_session_window(existing_session, timeout=NEW_SESSION_WAIT_TIMEOUT, poll_interval=NEW_SESSION_POLL_INTERVAL):
    connection = existing_session.Parent
    before_count = connection.Children.Count

    existing_session.createSession()

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if connection.Children.Count > before_count:
                return connection.Children(connection.Children.Count - 1)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(poll_interval)

    return connection.Children(connection.Children.Count - 1)


def _select_multi_logon_end_other_session(username):
    """Handles SAP's "License Information for Multiple Logons" popup
    (wnd[1]) by selecting "Continue with this logon and end any other
    logons in the system" -- instead of leaving SAP's own default
    ("Terminate this logon") selected.

    Two-step approach:

    1. Try the field ID "wnd[1]/usr/radMULTI_LOGON_OPT1" directly first.
       This is not a guess -- it's the exact ID the client's own older
       robot code (gate_in.robot, before multi-bot coordination) used
       for this same choice, and it's confirmed to really work: it's
       the same ID whose use is documented (in sap_coordination.resource)
       as having once ended a different, live zgrn_print job's session --
       which only happens if selecting it truly does end the other
       logon. (The earlier, DIFFERENT id "wnd[1]/usr/radMULTI_LOGON_OPT2"
       was a guess for the *other* button and is confirmed NOT to exist
       on this dialog -- "Cannot find element with id..." in a real RF
       log, 2026-09-04. OPT1 was never actually shown to be wrong.)

    2. If that ID isn't found (e.g. a different dialog variant), fall
       back to scanning wnd[1]'s own control tree for GuiRadioButton
       controls and picking the one whose OWN TEXT contains "continue"
       -- matching by the button's actual label, not by its position/
       order in the control tree. Position was tried before and a
       run still ended up on "Terminate this logon", so order in the
       tree is not trustworthy here.

    If neither step can positively identify the "Continue..." button,
    this deliberately selects nothing and returns False -- it never
    guesses, and never clicks/confirms anything itself. It is the
    caller's job to only proceed with THIS login if this returns True,
    and to back off safely (like the old, proven Cancel/F12 behavior)
    otherwise -- see the "confirm before commit" note where this is
    called from _do_coordinated_sap_login().

    Returns True only when a button was positively identified AND
    successfully selected. Returns False in every other case. Never
    raises -- worst case it just logs a warning and returns False.
    """
    selected_by_id = _run_status("Select Radio Button", "wnd[1]/usr/radMULTI_LOGON_OPT1")
    if selected_by_id:
        BuiltIn().log_to_console(
            f"Multi-logon popup for {username}: selected 'Continue with this logon "
            "and end any other logons' via wnd[1]/usr/radMULTI_LOGON_OPT1."
        )
        return True

    BuiltIn().log(
        f"wnd[1]/usr/radMULTI_LOGON_OPT1 wasn't found for {username} -- falling back "
        "to scanning the popup's own controls for the right radio button by its text.",
        "WARN",
    )

    try:
        lib = BuiltIn().get_library_instance("SapGuiLibrary")
        popup = lib.session.findById("wnd[1]")
    except Exception as exc:  # noqa: BLE001
        BuiltIn().log(f"Could not access the multi-logon popup directly: {exc}", "WARN")
        return False

    radio_buttons = []

    def _walk(control):
        try:
            children = control.Children
            count = children.Count
        except Exception:  # noqa: BLE001
            return
        for i in range(count):
            try:
                child = children(i)
            except Exception:  # noqa: BLE001
                continue
            try:
                if str(child.Type) == "GuiRadioButton":
                    radio_buttons.append(child)
            except Exception:  # noqa: BLE001
                pass
            _walk(child)

    _walk(popup)

    if not radio_buttons:
        BuiltIn().log(
            f"Multi-logon popup for {username} didn't have the radio buttons "
            "expected -- cannot confirm which one is 'Continue...'.",
            "WARN",
        )
        return False

    target = None
    for rb in radio_buttons:
        try:
            text = str(rb.Text).lower()
        except Exception:  # noqa: BLE001
            continue
        if "continue" in text:
            target = rb
            break

    if target is None:
        BuiltIn().log(
            f"Could not identify the 'Continue with this logon' radio button by "
            f"its text for {username} -- refusing to guess.",
            "WARN",
        )
        return False

    try:
        target.Selected = True
        BuiltIn().log_to_console(
            f"Multi-logon popup for {username}: selected 'Continue with this logon "
            "and end any other logons' (matched by button text) -- ending the other "
            "session so this login can proceed."
        )
        return True
    except Exception as exc:  # noqa: BLE001
        BuiltIn().log(f"Could not select the multi-logon radio button: {exc}", "WARN")
        return False


def select_multi_logon_end_other_session(username):
    """Public alias for _select_multi_logon_end_other_session(), so
    sap_coordination.resource (the separate, pure-RF coordination
    implementation used by migo103_link.robot, migo105_link.robot, and
    miro_link.robot) can call this exact same logic as a keyword
    ("Select Multi Logon End Other Session") instead of maintaining its
    own separate copy of the popup-handling code. One implementation,
    shared by both coordination paths -- avoids the two of them quietly
    drifting apart, which is what had left this popup handled two
    different (and differently wrong) ways across the codebase before
    2026-09-04."""
    return _select_multi_logon_end_other_session(username)


def coordinated_sap_login(sap_logon_path, connection_name, client, username, password, expected_total_bots=None):
    coordinated_register_bot_start()
    acquire_refcount_lock(LOGIN_LOCK_MAX_ATTEMPTS, LOGIN_LOCK_POLL_INTERVAL)
    try:
        try:
            return _do_coordinated_sap_login(sap_logon_path, connection_name, client, username, password)
        except Exception:
            # FIX 12 (2026-09-04) -- THE REFCOUNT LEAK BUG:
            #
            # coordinated_register_bot_start() above already incremented
            # the shared counter before any of the real login/window work
            # runs. Until this fix, if that work then failed for any
            # reason, nothing ever undid that increment -- the only place
            # that normally decrements is coordinated_close_sap_session(),
            # called from this bot's own RF [Teardown], and Teardown never
            # gets a working ${session} to close when Setup itself failed.
            #
            # Confirmed as a real incident, not a theoretical one:
            # 2026-09-03 23:13-23:44, history_id 630/631, 18 straight
            # "Cannot maximize window wnd[0], is the window actually
            # open?" failures during login. Each one registered as active,
            # then failed, then its own [Teardown] *also* failed on
            # "Variable '${session}' not found" before it could ever call
            # the decrement. That drove the shared counter from 0 to 18
            # and it never came back down -- which is exactly why
            # saplogon.exe was still sitting open hours later with no
            # bots actually running: the "safe to fully close" check only
            # fires once the counter genuinely reaches 0, and 18 phantom
            # "active" bots that don't really exist made that impossible.
            #
            # The rollback below decrements REFCOUNT_FILE directly rather
            # than calling coordinated_register_bot_end() -- that helper
            # acquires this exact same lock we are still holding a few
            # lines up (it is not reentrant), so calling it here would
            # just stall for ~30s before giving up and proceeding anyway.
            # This mirrors the same direct-decrement approach already
            # used for the multi-logon-conflict case in
            # sap_coordination.resource's own Coordinated SAP Login.
            current_active = _read_counter(REFCOUNT_FILE)
            rolled_back = max(0, current_active - 1)
            _write_counter(REFCOUNT_FILE, rolled_back)
            BuiltIn().log_to_console(
                f"SAP bot refcount: {current_active} -> {rolled_back} "
                "(rolled back -- this bot's login/setup failed, so it never "
                "became a real active session and must not stay counted as one)."
            )
            raise
    finally:
        release_refcount_lock()


def _do_coordinated_sap_login(sap_logon_path, connection_name, client, username, password):
    """The actual login/window-setup work, split out from
    coordinated_sap_login() so that function can wrap this in a single
    try/except and roll back this bot's refcount registration if
    anything here fails (see FIX 12 in the caller). No behavior change
    from before the split -- same steps, same order, same return
    values."""
    already_running = _saplogon_is_running()
    if not already_running:
        BuiltIn().log_to_console("SAP Logon not running -- starting fresh.")
        _start_saplogon(sap_logon_path)
        time.sleep(5)

    existing_session = None
    if already_running:
        existing_session = _find_existing_session_for_user(username, connection_name)

    if existing_session is not None:
        BuiltIn().log_to_console(
            f"SAP user {username} is already logged in elsewhere -- "
            "creating a new GUI window on that existing login (no re-login, no popup)."
        )
        new_session = _create_new_session_window(existing_session)
        try:
            new_session.findById("wnd[0]").maximize()
        except Exception:  # noqa: BLE001
            pass

        # ADDED (2026-09-08): _create_new_session_window() above only
        # waits for the new window to APPEAR as a child object in SAP's
        # connection tree (Children.Count going up) -- it does not wait
        # for that window's own SAP Easy Access screen to finish
        # rendering and become interactive. The fresh-login path below
        # has a real time.sleep(8) before it ever hands control back for
        # exactly this reason; this reuse path had nothing at all, going
        # straight from "window object exists" to returning control.
        # Suspected root cause of a real failure (zgrn.robot, h683,
        # 2026-09-08): Initialize SAP And Login completed in only 1.4s
        # (far too fast for a fresh login, so almost certainly this
        # reuse path), and the very next step -- Run Transaction zgrn,
        # typing into the T-code field immediately -- came back with
        # "Cannot find element with id 'wnd[0]/usr/txtLS_MBLNR-LOW'",
        # meaning the ZGRN screen it should have landed on never
        # actually loaded. A short settle delay here costs a couple of
        # seconds on the (fast, common) reuse path but gives the new
        # window's content time to actually finish rendering before any
        # bot starts typing into it.
        time.sleep(2)

        lib = BuiltIn().get_library_instance("SapGuiLibrary")
        lib.session = new_session
        return new_session

    connected = _run_status("Connect To Session")
    if not connected:
        current_active = _read_counter(REFCOUNT_FILE)
        if current_active <= 1:
            BuiltIn().log_to_console(
                "Could not connect to the existing SAP Logon process, and no other bots appear "
                "active -- treating it as stale, killing it and starting fresh."
            )
            _kill_saplogon(with_tree=True)
            time.sleep(2)
            _start_saplogon(sap_logon_path)
            time.sleep(5)
            for _ in range(10):
                if _run_status("Connect To Session"):
                    break
                time.sleep(2)
        else:
            BuiltIn().log_to_console(
                f"Could not connect to the existing SAP Logon process, but {current_active} other "
                "bot(s) appear active -- retrying without killing the shared process."
            )
            connected = False
            for _ in range(15):
                time.sleep(2)
                if _run_status("Connect To Session"):
                    connected = True
                    break
            if not connected:
                raise RuntimeError(
                    "Could not connect to SAP Logon after repeated retries, and other bots "
                    "appear active -- refusing to kill saplogon.exe out from under them."
                )

    _run("Open Connection", connection_name)

    _run("Input Text", "wnd[0]/usr/txtRSYST-MANDT", client)
    _run("Input Text", "wnd[0]/usr/txtRSYST-BNAME", username)
    _run("Input Password", "wnd[0]/usr/pwdRSYST-BCODE", password)
    _run("Click Element", "wnd[0]/tbar[0]/btn[0]")
    time.sleep(3)

    login_popup = _run_status("Element Should Be Present", "wnd[1]")
    if login_popup:
        # FIX 13 (2026-09-04) -- part of THE REAL "Cannot maximize window"
        # ROOT CAUSE:
        #
        # Confirmed with a real RF log the user captured (2026-09-04, SAP
        # user up_pawar): the old code here tried Select Radio Button on
        # "wnd[1]/usr/radMULTI_LOGON_OPT2" -- and SAP's own scripting
        # engine logged that call failing outright: "Cannot find element
        # with id 'wnd[1]/usr/radMULTI_LOGON_OPT2'". That field ID does
        # not exist on the actual "License Information for Multiple
        # Logons" popup SAP shows for this.
        #
        # FIX 14 (2026-09-04, confirmed by client): these SAP IDs
        # (up_pawar, rk_yadav, dv_vidwans, etc.) are server-side/bot-only
        # -- there is no separate real person logging into them by hand
        # elsewhere, so "the other session" this popup is complaining
        # about is always some other automation attempt, never live human
        # work. That removes the one reason a brief version of this fix
        # was reverted earlier the same day (worry about logging out a
        # real employee) -- confirmed explicitly not a real scenario
        # here, so back to: always select "Continue with this logon and
        # end any other logons" and carry on with THIS login, instead of
        # this bot backing off and leaving the job to fail/retry.
        #
        # Selects that radio button by walking wnd[1]'s own control tree
        # for the actual GuiRadioButton controls and picking the first
        # one (which this dialog always lists first), instead of
        # guessing a specific field ID -- the earlier attempt at exactly
        # that ("wnd[1]/usr/radMULTI_LOGON_OPT2") failed outright with
        # "Cannot find element with id..." on this exact system
        # (confirmed via a real RF log, 2026-09-04), so a second guessed
        # name is no more trustworthy.
        #
        # STILL WORTH KNOWING: this is a different risk than the one
        # ruled out above. If two bots under this SAME shared ID
        # genuinely start their logins within the same few seconds on
        # THIS server, the createSession() reuse path above (which is
        # what normally avoids ever reaching this popup for that case)
        # can lose that race, and this popup fires for both -- ending
        # the other one here would then end a real, currently-running
        # job's own session, not a stale leftover. That's the same
        # mechanism that once crashed a live zgrn_print job (see
        # sap_coordination.resource's documented history). Accepted as
        # an edge case, not something this change removes.
        # CONFIRM BEFORE COMMIT (2026-09-04): only click OK on this popup
        # if _select_multi_logon_end_other_session() actually confirms it
        # selected "Continue with this logon..." -- never click OK "blind"
        # on whatever SAP had pre-selected by default, because that
        # default is "Terminate this logon", i.e. exactly the wrong one.
        # If it can't confirm, back off the SAME safe way FIX 13 did
        # (Cancel + fail cleanly, so this bot's own job retries) instead
        # of guessing.
        confirmed = _select_multi_logon_end_other_session(username)
        if confirmed:
            _run_ignore_error("Click Element", "wnd[1]/tbar[0]/btn[0]")
            time.sleep(2)
            BuiltIn().log(
                f"Multi-logon popup handled for {username} -- selected 'end other "
                "logons' and continuing with this new login.",
                "INFO",
            )
        else:
            _run_ignore_error("Send VKey", "wnd[1]", "12")
            raise RuntimeError(
                f"SAP multi-logon conflict for {username} -- another session for this "
                "username is already logged in elsewhere, and this bot could not "
                "positively confirm the 'Continue with this logon' button on the popup "
                "shown, so it cancelled its own new logon instead of guessing. This job "
                "can safely retry."
            )

    time.sleep(8)
    coordinated_dismiss_any_popup()
    _run("Maximize Window", "0")

    lib = BuiltIn().get_library_instance("SapGuiLibrary")
    session = lib.session
    return session


def coordinated_check_session_alive(session):
    try:
        _ = session.findById("wnd[0]").Text
        return True
    except Exception:  # noqa: BLE001
        return False


def _close_this_window_only(session):
    """FIX 10: closes THIS session directly via the CONNECTION's
    CloseSession() API method, instead of clicking the window's own
    close control -- this is the correct, purely programmatic way to
    end one specific session via SAP GUI Scripting. Unlike wnd[0].close()
    (which routes through SAP's normal interactive close handling and
    can pop up "Unsaved data will be lost -- log off?" when this is the
    last session under a connection), CloseSession() ends the session
    directly with NO confirmation dialog involved at all -- nothing to
    detect, nothing to click.

    Still session-specific, not connection-wide: closing one session
    this way does not touch sibling sessions under the same connection
    (the earlier /nex-based bug, where closing session[0] could
    cascade into ending the whole connection, doesn't apply here --
    CloseSession() always targets exactly the session ID given).

    Falls back to /nex only if CloseSession() itself is unavailable
    (e.g. a very old SAP GUI Scripting API), so a working fallback
    exists even in the unlikely case the primary path fails."""
    try:
        connection = session.Parent
        session_id = str(session.Id)          # e.g. ".../con[0]/ses[1]"
        ses_part = session_id.rsplit("/", 1)[-1]   # "ses[1]"
        connection.CloseSession(ses_part)
        return
    except Exception:  # noqa: BLE001
        pass

    # Fallback: /nex, only reached if CloseSession() itself was unavailable.
    try:
        session.findById("wnd[0]/tbar[0]/okcd").text = "/nex"
        session.findById("wnd[0]").sendVKey(0)
    except Exception:  # noqa: BLE001
        pass


def _spawn_shutdown_watcher():
    """FIX 11 (2026-09-03): the up-to-60s "wait and see, then maybe
    kill saplogon.exe" cooldown used to run right here, inline, inside
    the finishing bot's own process -- which meant that process (and
    therefore rf_runner.py's subprocess.run(), and therefore the job's
    done/failed status the frontend polls for) could not finish for up
    to a full extra minute after the bot's actual SAP work was already
    done, purely because it happened to be the last bot out. Confirmed
    for real: a PO Fetch that had already fetched its data and closed
    its own window still left the frontend showing "fetching" for
    close to a minute, only flipping once saplogon.exe itself finally
    shut down.

    Handing the wait-and-kill decision to a separate DETACHED process
    lets this bot's own process exit immediately once its window is
    closed, while the shared shutdown decision still happens exactly
    the same way (same 60s window, same recheck interval, same
    double-taskkill) -- just off to the side, where it can't block
    anyone's UI.

    stdout/stderr/stdin are all explicitly redirected to DEVNULL (not
    inherited) -- this matters specifically on Windows: if the watcher
    inherited this process's stdout/stderr pipes, rf_runner.py's
    subprocess.run(capture_output=True) would keep waiting for those
    pipes to close even after THIS process exits, since the detached
    child would still be holding them open, silently reintroducing the
    exact delay this fix removes. DETACHED_PROCESS + CREATE_NEW_
    PROCESS_GROUP lets the watcher keep running after this process
    exits, fully independent of it."""
    watcher_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "sap_shutdown_watcher.py"
    )
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            [sys.executable, watcher_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )
        BuiltIn().log_to_console(
            "Active refcount reached 0 -- handed the shutdown-watch cooldown off to a "
            "background watcher (this bot's own window is already closed; this bot's "
            "process does not need to wait for it)."
        )
    except Exception as exc:  # noqa: BLE001
        # Fallback: if spawning the detached watcher fails for any reason (e.g. a
        # locked-down environment that blocks DETACHED_PROCESS), fall back to the
        # old inline wait so SAP still gets cleaned up -- worse for this one bot's
        # response time, but never worse than before this fix existed.
        BuiltIn().log(
            f"Could not spawn detached shutdown watcher ({exc}) -- falling back to "
            "waiting inline.", "WARN"
        )
        _inline_shutdown_wait()


def _inline_shutdown_wait():
    """Fallback only -- see _spawn_shutdown_watcher(). This is the
    original synchronous cooldown loop (FIX 7), kept as a safety net
    for the rare case the detached watcher can't be spawned at all."""
    elapsed = 0
    while elapsed < SHUTDOWN_COOLDOWN_SECONDS:
        time.sleep(SHUTDOWN_COOLDOWN_POLL_INTERVAL)
        elapsed += SHUTDOWN_COOLDOWN_POLL_INTERVAL
        current = _active_count()
        if current > 0:
            BuiltIn().log_to_console(
                f"Another bot became active during the cooldown (refcount={current}) -- backing off. "
                "That bot (or a later one) will be responsible for the eventual shutdown."
            )
            return

    BuiltIn().log(
        f"Active refcount stayed at 0 for the full {SHUTDOWN_COOLDOWN_SECONDS}s cooldown -- "
        "shutting down saplogon.exe fully...", "INFO"
    )
    _kill_saplogon(with_tree=False)
    time.sleep(2)
    _kill_saplogon(with_tree=True)
    BuiltIn().log("SAP process closed and terminated.", "INFO")


def coordinated_close_sap_session(session):
    """Always closes THIS bot's own session window (see FIX 8 for HOW
    -- window-level close, not the connection-cascading /nex). Only
    fully shuts down saplogon.exe once the active count genuinely stays
    at 0 for the whole SHUTDOWN_COOLDOWN_SECONDS window (FIX 7) -- as
    of FIX 11 (2026-09-03), that wait now runs in a detached background
    watcher instead of inline here, so THIS bot's own process (and the
    job status the frontend is waiting on) is free to finish right
    away. See _spawn_shutdown_watcher()."""
    remaining_active = coordinated_register_bot_end()

    _close_this_window_only(session)
    time.sleep(2)

    if remaining_active > 0:
        BuiltIn().log_to_console(
            f"Another bot (or bots) still active (refcount={remaining_active}) -- "
            "this bot's window is closed, but saplogon.exe stays running for the other bot(s)."
        )
        return

    _spawn_shutdown_watcher()
