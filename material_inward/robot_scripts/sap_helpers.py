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