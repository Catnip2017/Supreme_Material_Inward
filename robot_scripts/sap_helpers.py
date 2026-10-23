import win32com.client
import subprocess
import os
import time
import pywinauto

# def set_combo_via_vbs(element_path: str, index: int):
#     """Run a VBScript to set the combo key — bypasses COM dispatch issues."""
#     key = "   " + str(index)
#     vbs = f'''
# Set SapGuiAuto = GetObject("SAPGUI")
# Set App = SapGuiAuto.GetScriptingEngine
# Set Connection = App.Children(0)
# Set Session = Connection.Children(0)
# Session.FindById("{element_path}").SetFocus
# Session.FindById("{element_path}").Key = "{key}"
# '''
#     with open("C:/temp/set_combo.vbs", "w") as f:
#         f.write(vbs)
#     subprocess.run(["cscript", "//nologo", "C:/temp/set_combo.vbs"], check=True)

def set_combo_via_vbs(element_path: str, index: int):
    key = "   " + str(index)
    os.makedirs("C:/temp", exist_ok=True)
    vbs = f'''
Set SapGuiAuto = GetObject("SAPGUI")
Set App = SapGuiAuto.GetScriptingEngine
Set Connection = App.Children(0)
Set Session = Connection.Children(0)
Session.FindById("{element_path}").SetFocus
Session.FindById("{element_path}").Key = "{key}"
'''
    with open("C:/temp/set_combo.vbs", "w") as f:
        f.write(vbs)
    subprocess.run(["cscript", "//nologo", "C:/temp/set_combo.vbs"], check=True)
def scroll_table_via_vbs(table_path: str, position: int):
    """Scroll a SAP GUI table control to the given first-visible-row position.

    FIX (2026-08-10): po_fetch.robot's STEP 1 grid-read loop calls a keyword
    named "Scroll Table Via Vbs" every 5 rows to advance the visible ME23N
    item grid -- but this keyword never existed anywhere in this file. The
    call is wrapped in Run Keyword And Ignore Error in the .robot script, so
    it failed silently on every run, the grid view never actually scrolled,
    and the read loop just kept re-reading the same first 5 physical rows
    over and over (up to its 100-row hard cap) instead of ever seeing a
    genuinely blank Itm cell. Confirmed via a real run's output.xml against
    PO 4100035702 (5 real line items) logging "Found 100 line item(s)".
    That inflated count then drove STEP 2's Open Qty loop to click Next Item
    ~95 times past the real last item, which is what destabilized/disconnected
    the SAP GUI scripting session (RPC server unavailable) and produced the
    "gets stuck" symptom on multi-item POs.

    Uses the table control's standard VerticalScrollbar.Position property
    (the documented SAP GUI Scripting API for classic GuiTableControl
    scrolling -- ${TABLE} in po_fetch.robot, tblSAPLMEGUITC_1211, is a
    classic table control, not an ALV grid). Same VBS-via-cscript pattern
    as set_combo_via_vbs above, for consistency with the existing approach
    in this file. Uses a separate temp filename (scroll_table.vbs, not
    set_combo.vbs) so the two calls can't clobber each other's script file.
    """
    os.makedirs("C:/temp", exist_ok=True)
    vbs = f'''
Set SapGuiAuto = GetObject("SAPGUI")
Set App = SapGuiAuto.GetScriptingEngine
Set Connection = App.Children(0)
Set Session = Connection.Children(0)
Session.FindById("{table_path}").VerticalScrollbar.Position = {position}
'''
    with open("C:/temp/scroll_table.vbs", "w") as f:
        f.write(vbs)
    subprocess.run(["cscript", "//nologo", "C:/temp/scroll_table.vbs"], check=True)

def set_sap_combo_key(element_path: str, index: int):
    key = "   " + str(index)
    sap_gui = win32com.client.GetObject("SAPGUI")
    app = sap_gui.GetScriptingEngine()
    connection = app.Children(0)
    session = connection.Children(0)
    element = session.FindById(element_path)
    element.SetFocus()
    element.Key = key

def set_sap_focus(element_path: str):
    sap_gui = win32com.client.GetObject("SAPGUI")
    app = sap_gui.GetScriptingEngine()
    connection = app.Children(0)
    session = connection.Children(0)
    session.FindById(element_path).SetFocus()


def get_delflag_status(table_path, row, session=None):
    """
    ADDED (2026-08-12): reads IconName/Tooltip/Text of the deletion-status
    button in a PO line-item grid row directly via the SAP GUI Scripting
    COM API. Robot's SapGuiLibrary has no generic 'Get Property' keyword,
    so this bypasses it and talks to the SAP session object directly.

    po_fetch.robot's Fetch PO Line Items now calls this per row in STEP 1
    to tell a genuinely deleted PO line (IconName 'B_DELE') apart from a
    normal active one -- a deleted line still renders a row in the grid, so
    without this it was indistinguishable from a real line with similar
    data, which is what was being reported as line items "repeating".
    Deleted rows are still returned (row_status="Deleted" in the JSON
    output) rather than dropped, so the app can grey them out instead of
    making them look like they silently vanished.

    FIX (2026-08-24): this used to always re-derive the session itself via
    GetScriptingEngine.Children(0).Children(0) -- the exact same "always
    grabs the FIRST connection/session that exists" bug already fixed at
    the .robot level (see po_fetch.robot's/migo_103.robot's Connect To Sap
    Session, which now binds to SapGuiLibrary's own ${lib.session} instead
    of re-deriving by index). That .robot-level fix never touched this
    Python helper, which is a separate library and does its own independent
    COM lookup -- so this call could still silently read delflag status
    from the WRONG SAP window whenever more than one SAP connection/session
    exists (e.g. a leftover window from an earlier run, or a user with SAP
    Logon open separately on the same machine), producing a wrong or
    stale Active/Deleted flag with no error at all. Now accepts the
    already-correctly-bound session object as an optional argument --
    po_fetch.robot passes its own ${session} (bound via ${lib.session})
    in -- and only falls back to the old Children(0).Children(0) lookup if
    no session is passed, for backward compatibility with any other caller.
    """
    if session is None:
        try:
            sap_gui_auto = win32com.client.GetObject("SAPGUI")
            application = sap_gui_auto.GetScriptingEngine
            connection = application.Children(0)
            session = connection.Children(0)
        except Exception as e:
            return f"ERROR:SESSION:{e}"

    path = f"{table_path}/btnMEPO1211-STATUSICON[0,{row}]"
    try:
        btn = session.findById(path)
    except Exception as e:
        return f"ERROR:NOTFOUND:{e}"

    icon_name = ""
    tooltip = ""
    text = ""
    try:
        icon_name = btn.IconName
    except Exception:
        pass
    try:
        tooltip = btn.Tooltip
    except Exception:
        pass
    try:
        text = btn.Text
    except Exception:
        pass

    return f"IconName='{icon_name}' | Tooltip='{tooltip}' | Text='{text}'"


def check_abap_session_killed(timeout=1.0):
    """
    ADDED (2026-08-29): SAP can kill an ABAP session server-side mid-run
    (e.g. a real production case: "SPA: SAP system message: ABAP session
    closed by system (location SPLPRDAPP_SPA_00-W18)", history_id=511) and
    put up a native Windows message box to say so. This dialog is NOT part
    of the SAP GUI Scripting object tree -- it's a plain Win32 message box
    owned by the OS, not a GuiModalWindow -- so once the underlying ABAP
    session is dead, session.findById('wnd[1]') throws the exact same
    "Cannot find Element" COM error whether this dialog is genuinely on
    screen or there's really nothing there. That's confirmed to be why
    migo_103.robot's own popup-capture (Capture And Dismiss Popup Message,
    which reads wnd[1] via the SAP session object) can never see this
    class of failure -- it isn't a blind spot in that keyword's logic, the
    text simply isn't reachable through SAP's own COM API once the session
    is gone. Reading it via pywinauto instead (OS-level window read,
    independent of whether the SAP session behind it is still alive) is
    the only way to actually get this text back.

    Same window-matching lesson already learned the hard way in
    pdf_helper.py's ZGRN print-dialog fixes applies here too: match on
    BOTH title AND class_name="#32770" (the standard Win32 message-box
    class) -- title text alone was previously confirmed to falsely match a
    SAP-owned window in a different context (the ZGRN "Print:" window
    incident), so the same defensive filter is used here from the start
    rather than waiting to get burned by it again.

    Deliberately fast (default 1s) and fully swallowed by design -- this
    is meant to be called only at existing "nothing else was captured"
    checkpoints, not on every step, so it should never meaningfully slow
    down a normal run. Must NEVER raise: any pywinauto/COM problem here is
    treated as "nothing found" (returns ""), never as a script-breaking
    error -- a bug in this detection path must not be able to take down
    an otherwise-working MIGO 103 run.

    Returns the captured message text (and best-effort clicks OK to
    dismiss it) if a matching dialog is found, else "".
    """
    try:
        deadline = time.time() + timeout
        matches = []
        while time.time() < deadline:
            try:
                matches = pywinauto.Desktop(backend="win32").windows(
                    title_re="SAP GUI for Windows.*", class_name="#32770"
                )
            except Exception:
                matches = []
            if matches:
                break
            time.sleep(0.2)

        if not matches:
            return ""

        dlg = matches[-1]

        text_parts = []
        try:
            for ctrl in dlg.children():
                try:
                    t = (ctrl.window_text() or "").strip()
                    if t and t.upper() not in ("OK",):
                        text_parts.append(t)
                except Exception:
                    continue
        except Exception:
            pass
        message = " | ".join(p for p in text_parts if p).strip()

        # Best-effort dismiss -- a failure to click OK should not stop us
        # from at least returning the text we already captured above.
        try:
            dlg.set_focus()
            try:
                dlg["OK"].click()
            except Exception:
                dlg.type_keys("{ENTER}")
        except Exception:
            pass

        return message or "SAP GUI system message (text could not be read)"
    except Exception:
        return ""