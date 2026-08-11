import win32com.client
import subprocess
import os

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